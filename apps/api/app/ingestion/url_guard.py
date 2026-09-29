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

**Not pinned against DNS rebinding.** The address is checked, and then httpx
resolves the name again when it connects; a hostile resolver answering
differently the second time would get past this. Closing that needs a
transport that connects to the checked address while presenting the original
name for TLS, which is a larger change than the gap it closes for hosts that
are, by the second rule, all on three manufacturers' own domains.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from app.core.errors import ValidationError
from app.ingestion.sources import _on_host

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
