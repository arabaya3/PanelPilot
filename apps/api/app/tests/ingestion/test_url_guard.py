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

import pytest

from app.core.errors import ValidationError
from app.ingestion.url_guard import (
    Resolver,
    UnsafeUrlError,
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
