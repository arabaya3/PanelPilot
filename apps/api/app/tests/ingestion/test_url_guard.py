"""Tests for the crawler's request allow-list.

The crawler runs inside our network. These pin the three things a URL must be
before it is requested -- https on the default port, on the source's own
domain, and resolving only to public addresses -- one rule at a time, so a
later loosening of any one of them fails here by name rather than somewhere in
a crawl run. The crawl-level reproductions (an internal document URL, a
redirect to the metadata endpoint) are in ``test_crawler.py``.
"""

from __future__ import annotations

import socket
import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpcore
import httpx
import pytest
import trustme

from app.core.errors import ValidationError
from app.ingestion.sources import http_client
from app.ingestion.url_guard import (
    PinnedAddressError,
    PinnedBackend,
    Resolver,
    UnsafeUrlError,
    pinned_transport,
    require_fetchable,
    require_public_host,
    require_source_url,
    system_resolver,
)


def resolving_to(*addresses: str) -> Resolver:
    return lambda _host: list(addresses)


# --- the shape of the URL ----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://abb.com/a.pdf",
        "https://library.e.abb.com/public/a.pdf",
        "https://library.abb.com:443/a.pdf",
    ],
)
def test_https_on_the_sources_domain_is_accepted(url: str) -> None:
    require_source_url(url, host_suffix="abb.com")


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://library.abb.com/a.pdf", "https"),
        ("ftp://library.abb.com/a.pdf", "https"),
        ("https://library.abb.com:8080/a.pdf", "port"),
        ("https://library.abb.com:notaport/a.pdf", "port"),
        ("https://user:pw@library.abb.com/a.pdf", "credentials"),
        ("https://library.abb.com@10.0.0.5/a.pdf", "credentials"),
        ("https://evil-abb.com/a.pdf", "not on"),
        ("https://10.0.0.5/a.pdf", "not on"),
        ("https:///a.pdf", "not on"),
    ],
)
def test_anything_else_is_refused(url: str, why: str) -> None:
    with pytest.raises(UnsafeUrlError, match=why):
        require_source_url(url, host_suffix="abb.com")


def test_a_refusal_is_a_validation_error() -> None:
    # So the domain can let it reach the caller as a 422 without translating.
    assert issubclass(UnsafeUrlError, ValidationError)


# --- where the name resolves -------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "192.0.2.1",
        "::1",
        "fe80::1%eth0",
        "fd00::1",
        "ff02::1",
        "::ffff:10.0.0.5",
        "not-an-address",
    ],
)
def test_a_non_public_address_is_refused(address: str) -> None:
    with pytest.raises(UnsafeUrlError, match="non-public"):
        require_public_host("https://library.abb.com/a.pdf", resolve=resolving_to(address))


def test_one_internal_address_among_public_ones_is_enough_to_refuse() -> None:
    # The client may connect to whichever address it likes.
    with pytest.raises(UnsafeUrlError):
        require_public_host(
            "https://library.abb.com/a.pdf",
            resolve=resolving_to("93.184.215.14", "10.0.0.5"),
        )


def test_a_name_resolving_to_nothing_is_refused() -> None:
    with pytest.raises(UnsafeUrlError, match="no address"):
        require_public_host("https://library.abb.com/a.pdf", resolve=resolving_to())


@pytest.mark.parametrize("address", ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"])
def test_a_public_address_is_accepted(address: str) -> None:
    require_public_host("https://library.abb.com/a.pdf", resolve=resolving_to(address))


def test_require_fetchable_applies_both_checks() -> None:
    public = resolving_to("93.184.215.14")
    require_fetchable("https://library.abb.com/a.pdf", host_suffix="abb.com", resolve=public)
    with pytest.raises(UnsafeUrlError):
        require_fetchable("https://evil.com/a.pdf", host_suffix="abb.com", resolve=public)
    with pytest.raises(UnsafeUrlError):
        require_fetchable(
            "https://library.abb.com/a.pdf",
            host_suffix="abb.com",
            resolve=resolving_to("127.0.0.1"),
        )


# --- the system resolver -----------------------------------------------------


def test_the_system_resolver_returns_every_address(monkeypatch: pytest.MonkeyPatch) -> None:
    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800::1", 443, 0, 0)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: infos)

    assert system_resolver("library.abb.com") == ["93.184.215.14", "2606:2800::1"]


def test_an_unresolvable_name_is_refused_not_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    # "We could not check" must not resolve to "so we fetched it".
    def fail(*_a: object, **_k: object) -> object:
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fail)

    with pytest.raises(UnsafeUrlError, match="could not resolve"):
        system_resolver("nowhere.abb.com")


# --- connections are pinned to the checked addresses ---------------------------
#
# Against a real socket on loopback, because what is being proved is where the
# connection goes. Loopback is not public, so the tests that must connect widen
# `allowed`; the ones proving refusal keep the production rule.


class _Server:
    """A local HTTP server that records the Host header of each request."""

    def __init__(self, tls: ssl.SSLContext | None = None) -> None:
        hosts: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                hosts.append(self.headers["Host"])
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *_args: object) -> None:
                pass

        self.hosts = hosts
        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        if tls is not None:
            self.httpd.socket = tls.wrap_socket(self.httpd.socket, server_side=True)


def _serve(running: _Server) -> Iterator[_Server]:
    thread = threading.Thread(target=running.httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield running
    finally:
        running.httpd.shutdown()
        running.httpd.server_close()


@pytest.fixture
def server() -> Iterator[_Server]:
    yield from _serve(_Server())


def _client(resolve: Resolver, *, anywhere: bool = False) -> httpx.Client:
    transport = (
        pinned_transport(resolve, allowed=lambda _address: True)
        if anywhere
        else pinned_transport(resolve)
    )
    return httpx.Client(transport=transport)


def test_the_connection_goes_to_the_address_that_was_resolved(server: _Server) -> None:
    """The name is not resolved again: `.invalid` never resolves in real DNS."""
    with _client(resolving_to("127.0.0.1"), anywhere=True) as client:
        response = client.get(f"http://docs.rebind.invalid:{server.port}/")

    assert response.status_code == 200
    # The request is still *for* the name: Host (and, over TLS, SNI and the
    # certificate check) are unchanged; only the socket's address is pinned.
    assert server.hosts == [f"docs.rebind.invalid:{server.port}"]


def test_tls_is_verified_against_the_name_not_the_address() -> None:
    """Pinning moves the socket, not the identity the connection is for.

    The server holds a certificate for `docs.pinned.invalid` only. The pinned
    connection reaches it at 127.0.0.1 and succeeds, because SNI and the
    hostname check use the name; asked for a name the certificate does not
    cover, at the same address, it fails verification.
    """
    ca = trustme.CA()
    server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert("docs.pinned.invalid").configure_cert(server_context)
    client_context = ssl.create_default_context()
    ca.configure_trust(client_context)
    backend = PinnedBackend(resolving_to("127.0.0.1"), allowed=lambda _address: True)

    for running in _serve(_Server(tls=server_context)):
        with httpcore.ConnectionPool(ssl_context=client_context, network_backend=backend) as pool:
            ok = pool.request("GET", f"https://docs.pinned.invalid:{running.port}/")
            assert ok.status == 200
            with pytest.raises(httpcore.ConnectError, match=r"certificate|hostname"):
                pool.request("GET", f"https://other.pinned.invalid:{running.port}/")
        assert running.hosts == [f"docs.pinned.invalid:{running.port}"]


def test_a_rebound_name_is_not_connected_to(server: _Server) -> None:
    """The gap this closes: checked public, connected private.

    The pre-request check saw a public address; the lookup at connect time
    answers loopback. Before, httpx made that second lookup itself and
    connected. Now the backend makes it, checks it, and refuses.
    """
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])

    def rebinding(_host: str) -> list[str]:
        return next(answers)

    url = f"http://localhost:{server.port}/"
    require_public_host(url, resolve=rebinding)  # the check passes...
    with _client(rebinding) as client, pytest.raises(httpx.ConnectError, match="non-public"):
        client.get(url)  # ...and the connection is refused anyway

    assert server.hosts == []


def test_one_private_address_among_public_ones_is_refused(server: _Server) -> None:
    with (
        _client(resolving_to("93.184.216.34", "127.0.0.1")) as client,
        pytest.raises(httpx.ConnectError, match="non-public"),
    ):
        client.get(f"http://localhost:{server.port}/")

    assert server.hosts == []


def test_a_name_that_does_not_resolve_is_not_connected_to() -> None:
    def unresolvable(host: str) -> list[str]:
        raise UnsafeUrlError(f"could not resolve {host!r}")

    with _client(unresolvable) as client, pytest.raises(httpx.ConnectError, match="resolve"):
        client.get("http://nowhere.invalid/")


def test_a_name_resolving_to_nothing_is_not_connected_to() -> None:
    with _client(resolving_to()) as client, pytest.raises(httpx.ConnectError, match="no address"):
        client.get("http://nowhere.invalid/")


def test_the_next_checked_address_is_tried_when_one_refuses(server: _Server) -> None:
    # 127.0.0.2 routes to loopback on Linux but nothing listens there for this port.
    with _client(resolving_to("127.0.0.2", "127.0.0.1"), anywhere=True) as client:
        assert client.get(f"http://multi.invalid:{server.port}/").status_code == 200


def test_the_last_failure_is_raised_when_no_address_accepts() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # bound, not listening: refuses
        with (
            _client(resolving_to("127.0.0.1"), anywhere=True) as client,
            pytest.raises(httpx.ConnectError),
        ):
            client.get(f"http://closed.invalid:{port}/")


def test_a_unix_socket_is_never_opened() -> None:
    with pytest.raises(PinnedAddressError):
        PinnedBackend(resolving_to("93.184.216.34")).connect_unix_socket("/var/run/docker.sock")


def test_a_refusal_is_a_connect_error_to_httpx() -> None:
    """So the crawl loop's `httpx.HTTPError` handling covers it, as unreachable."""
    assert issubclass(PinnedAddressError, httpcore.ConnectError)


def test_retry_backoff_is_delegated() -> None:
    slept: list[float] = []

    class Recording(httpcore.SyncBackend):
        def sleep(self, seconds: float) -> None:
            slept.append(seconds)

    PinnedBackend(resolving_to(), inner=Recording()).sleep(0.5)

    assert slept == [0.5]


def test_the_crawlers_client_is_pinned(server: _Server) -> None:
    """Through `http_client`, as the crawler builds it.

    `localhost` resolves to loopback in real DNS, so a client that looked the
    name up itself would connect and get a 200. The pinned one asks the
    injected resolver, gets loopback, and refuses — which also fails this test
    by name if an httpx upgrade stops the transport from using our pool.
    """
    with (
        http_client(user_agent="PanelPilotBot", resolve=resolving_to("127.0.0.1")) as client,
        pytest.raises(httpx.ConnectError),
    ):
        client.get(f"http://localhost:{server.port}/")

    assert server.hosts == []


def test_the_crawlers_client_ignores_proxy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy would resolve the name itself, out of sight of the check."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    with http_client(user_agent="PanelPilotBot", resolve=resolving_to("93.184.216.34")) as client:
        assert not [mount for mount in client._mounts.values() if mount is not None]
