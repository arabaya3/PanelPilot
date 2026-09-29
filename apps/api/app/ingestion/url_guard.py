"""Which URLs the crawler may send a request to.

The crawler runs inside our network, so every URL it fetches is a request made
*from* our network. A URL that names an internal address -- a metadata
endpoint, an admin port on a private subnet, loopback -- turns the crawler into
a proxy for whoever supplied the URL, and the response is then staged where a
reviewer can read it. That is server-side request forgery, and it reached
``169.254.169.254`` through a manufacturer-hosted redirect before this module
existed.

So a URL is fetched only when all three hold:

* it is ``https`` on the default port -- every source we crawl serves its
  documents that way, and anything else is either a mistake or a probe;
* its host is the source's own domain or a subdomain of it, by the same check
  the listing extractor already applies to discovered links;
* every address the host resolves to is publicly routable.

The first two are pure and cheap, so the domain applies them before a job row
exists. The third needs DNS, so it runs immediately before each request --
including every redirect hop, since a redirect is a new URL the source chose
rather than one we vetted.

**Pinned against DNS rebinding.** Checking an address and then letting the
HTTP client resolve the name again when it connects would leave a window: a
hostile resolver answering a public address to the check and an internal one
to the connection gets past it. So the client the crawler uses
(``pinned_transport``) does not resolve names itself. Its network backend
resolves once, refuses the connection unless every address is public, and
connects to exactly those addresses -- while TLS still verifies the
certificate against the original name, because the name the connection is
*for* is unchanged. The per-hop check above stays: it refuses early, with a
clear outcome, before a request is spent; the backend is what makes the
answer it checked the one that is used.
"""

from __future__ import annotations

import ipaddress
import socket
import typing
from collections.abc import Callable
from urllib.parse import urlparse

import httpcore
import httpx
import structlog

from app.core.errors import ValidationError
from app.ingestion.sources import _on_host

logger = structlog.get_logger(__name__)

#: Resolves a hostname to the addresses a connection could reach. Injected
#: wherever it is used so tests never touch real DNS.
Resolver = Callable[[str], list[str]]


class UnsafeUrlError(ValidationError):
    """A URL the crawler must not send a request to.

    A ``ValidationError`` so the domain can let it propagate unchanged from its
    entry check and have it reach the caller as a 422: a caller who supplied
    an internal URL made an input mistake, not a server one.
    """


def system_resolver(host: str) -> list[str]:
    """Resolve a hostname with the operating system's resolver.

    Args:
        host: The hostname to resolve.

    Returns:
        Every address it resolves to, as strings.

    Raises:
        UnsafeUrlError: If the name does not resolve at all. Refused rather
            than passed through, because "we could not check" must not
            resolve to "so we fetched it".
    """
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeUrlError(f"could not resolve {host!r}: {exc}") from exc
    return [str(info[4][0]) for info in infos]


def require_source_url(url: str, *, host_suffix: str) -> None:
    """Refuse a URL that is not https on the source's own domain.

    Args:
        url: The absolute URL about to be accepted or fetched.
        host_suffix: The source's own domain, e.g. ``abb.com``.

    Raises:
        UnsafeUrlError: If the scheme, port, credentials or host fall outside
            what a source document URL looks like.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeUrlError(f"{url!r} is not an https URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError(f"{url!r} has an invalid port") from exc
    if port not in (None, 443):
        # A non-default port on a manufacturer's domain is not where their
        # documents live, and it is exactly how an internal service is named.
        raise UnsafeUrlError(f"{url!r} names a non-default port")
    if parsed.username is not None or parsed.password is not None:
        # `https://abb.com@10.0.0.5/` reads as ABB to a person and as
        # 10.0.0.5 to the HTTP client. `hostname` below already gets that
        # right; refusing credentials outright removes the ambiguity.
        raise UnsafeUrlError(f"{url!r} carries credentials")
    host = parsed.hostname
    if not host or not _on_host(host, host_suffix):
        raise UnsafeUrlError(f"{url!r} is not on {host_suffix}")


def _is_public(address: str) -> bool:
    """Report whether an address is publicly routable.

    Args:
        address: An IPv4 or IPv6 address, possibly with an IPv6 zone suffix.

    Returns:
        ``False`` for private, loopback, link-local, reserved, multicast,
        unspecified and shared-address space, and for anything unparseable.
    """
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # `::ffff:10.0.0.5` connects to 10.0.0.5; judge it by where it goes.
        ip = ip.ipv4_mapped
    # `is_global` alone is not relied on: its treatment of multicast and a
    # few reserved blocks has shifted between Python versions, and the list
    # below is the property we actually want, spelled out.
    return ip.is_global and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def require_public_host(url: str, *, resolve: Resolver) -> None:
    """Refuse a URL whose host resolves to a non-public address.

    Args:
        url: The absolute URL about to be fetched.
        resolve: Resolves the URL's host to addresses.

    Raises:
        UnsafeUrlError: If the host does not resolve, or any address it
            resolves to is not publicly routable. Any rather than all,
            because the client may connect to whichever one it likes.
    """
    host = urlparse(url).hostname or ""
    addresses = resolve(host)
    if not addresses:
        raise UnsafeUrlError(f"{host!r} resolved to no address")
    for address in addresses:
        if not _is_public(address):
            raise UnsafeUrlError(f"{host!r} resolves to non-public address {address}")


def require_fetchable(url: str, *, host_suffix: str, resolve: Resolver) -> None:
    """Apply every check a URL must pass before a request is sent to it.

    Args:
        url: The absolute URL about to be fetched.
        host_suffix: The source's own domain.
        resolve: Resolves the URL's host to addresses.

    Raises:
        UnsafeUrlError: If any check fails.
    """
    require_source_url(url, host_suffix=host_suffix)
    require_public_host(url, resolve=resolve)


class PinnedAddressError(httpcore.ConnectError):
    """A connection refused because of where the name resolved to.

    A ``ConnectError`` so httpx reports it as ``httpx.ConnectError``: to the
    crawl loop it is a request that could not be made, like any other, rather
    than an exception from outside the HTTP stack escaping it.
    """


class PinnedBackend(httpcore.NetworkBackend):
    """Opens connections only to addresses it has itself resolved and checked.

    httpcore hands the backend the hostname and port of each new connection.
    This one resolves the name, refuses unless every address is allowed, and
    connects to those addresses rather than to the name -- so there is no
    second lookup for a resolver to answer differently. TLS is layered on the
    returned stream by httpcore with the original name as the server name,
    so the certificate is still verified for the host we meant.
    """

    def __init__(
        self,
        resolve: Resolver,
        *,
        allowed: Callable[[str], bool] = _is_public,
        inner: httpcore.NetworkBackend | None = None,
    ) -> None:
        """Wrap a backend.

        Args:
            resolve: Resolves a hostname to addresses.
            allowed: Whether a connection may go to an address. Public-only
                in production; a test widens it to reach a local server.
            inner: The backend that opens the sockets; the standard sync one
                by default.
        """
        self._resolve = resolve
        self._allowed = allowed
        self._inner = inner if inner is not None else httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        """Resolve, check, and connect to the checked addresses only.

        Args:
            host: The name the connection is for.
            port: The port.
            timeout: Connect timeout, per address tried.
            local_address: Passed through.
            socket_options: Passed through.

        Returns:
            A stream to one of the checked addresses.

        Raises:
            PinnedAddressError: If the name does not resolve, or resolves to
                any address that is not allowed.
            httpcore.ConnectError: If no checked address accepts the
                connection.
        """
        try:
            addresses = self._resolve(host)
        except UnsafeUrlError as exc:
            raise PinnedAddressError(str(exc)) from exc
        if not addresses:
            raise PinnedAddressError(f"{host!r} resolved to no address")
        refused = [address for address in addresses if not self._allowed(address)]
        if refused:
            # Reaching here means the name passed the pre-request check and
            # then resolved somewhere else: the rebinding this exists to stop.
            logger.error("url_guard.connection_refused", host=host, addresses=refused)
            raise PinnedAddressError(f"{host!r} resolves to non-public address {refused[0]}")

        failure: httpcore.ConnectError | httpcore.ConnectTimeout | None = None
        for address in addresses:
            try:
                return self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                failure = exc
        assert failure is not None  # addresses is non-empty
        raise failure

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        """Refuse: nothing the crawler fetches is a local socket.

        Raises:
            PinnedAddressError: Always.
        """
        del timeout, socket_options
        raise PinnedAddressError(f"unix socket {path!r} is not a fetchable address")

    def sleep(self, seconds: float) -> None:
        """Delegate httpcore's retry backoff to the wrapped backend.

        Args:
            seconds: How long to wait.
        """
        self._inner.sleep(seconds)


def pinned_transport(
    resolve: Resolver, *, allowed: Callable[[str], bool] = _is_public
) -> httpx.HTTPTransport:
    """Build an httpx transport whose connections go through ``PinnedBackend``.

    Args:
        resolve: Resolves a hostname to addresses.
        allowed: Whether a connection may go to an address; see
            ``PinnedBackend``.

    Returns:
        A transport with httpx's defaults for TLS verification and pool
        limits, connecting only to checked addresses.

    httpx exposes no way to hand ``HTTPTransport`` a network backend, so the
    connection pool it builds is replaced with one that has ours. The pool's
    constructor is httpcore's public API; the attribute is httpx's own, and
    ``test_url_guard`` fails by name if an upgrade moves it.

    A client given this transport ignores ``HTTPS_PROXY`` and friends. That is
    intended: a proxy resolves the name itself, out of our sight, which is
    the second lookup this exists to remove.
    """
    transport = httpx.HTTPTransport()
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    transport._pool = httpcore.ConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        max_connections=limits.max_connections,
        max_keepalive_connections=limits.max_keepalive_connections,
        keepalive_expiry=limits.keepalive_expiry,
        network_backend=PinnedBackend(resolve, allowed=allowed),
    )
    return transport
