"""Cross-site request refusal for the cookie-authenticated endpoints.

Every other endpoint authenticates with a bearer header, which a browser never
attaches on its own, so a cross-site page has nothing to ride on. The refresh
session cookie is the exception: the browser attaches it to any request to its
path. Two independent checks stand between that and a cross-site request:

1. **The request's origin is the web app's.** ``Origin`` when the browser sent
   one (it does on every cross-origin request and every same-origin POST in
   current browsers), else the origin of ``Referer``. Neither present is
   refused: a request that will not say where it comes from is not one the
   web app made.
2. **A custom header is present.** A cross-site form or image cannot set one,
   and a cross-site ``fetch`` that sets one needs a CORS preflight, which the
   API's CORS policy answers without credentials. The header's value is not a
   secret; its presence is the proof.

Both, rather than either: the origin check alone trusts every browser's
``Origin`` behaviour; the header alone trusts the CORS configuration. Each
covers the other's failure.

Pure functions over header values, so the rules are tested without a server.
"""

from __future__ import annotations

from collections.abc import Collection
from urllib.parse import urlsplit

from app.core.errors import AuthorizationError

# What the web app sends on every cookie-authenticated request.
CSRF_HEADER = "X-PanelPilot-CSRF"


def origin_of(url: str) -> str | None:
    """Return the ``scheme://host[:port]`` of a URL, or ``None`` if it has none.

    Args:
        url: An absolute URL, such as a ``Referer`` value.

    Returns:
        The origin, lower-cased, or ``None`` for anything without a scheme and
        host (a relative or malformed value).
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def check_same_site_request(
    *,
    origin: str | None,
    referer: str | None,
    csrf_header: str | None,
    trusted_origins: Collection[str],
) -> None:
    """Refuse a cookie-authenticated request that may not come from the web app.

    Args:
        origin: The ``Origin`` header, if sent.
        referer: The ``Referer`` header, if sent.
        csrf_header: The value of ``CSRF_HEADER``, if sent.
        trusted_origins: The web app's origins (``Settings.trusted_web_origins``).

    Raises:
        AuthorizationError: 403, with code ``csrf_origin_refused`` when the
            origin is missing or not trusted, or ``csrf_header_missing`` when
            the custom header is absent.
    """
    trusted = {entry.lower().rstrip("/") for entry in trusted_origins}
    claimed: str | None
    # An opaque origin ("null": a sandboxed frame, a privacy redirect) is kept
    # as claimed, and so refused; it is never a reason to fall back to Referer.
    if origin is not None and origin.strip() != "":
        claimed = origin.strip().lower().rstrip("/")
    elif referer:
        claimed = origin_of(referer)
    else:
        claimed = None
    if claimed is None or claimed not in trusted:
        raise AuthorizationError(
            "this request must come from the PanelPilot web app", code="csrf_origin_refused"
        )
    if not csrf_header:
        raise AuthorizationError(
            f"this request must carry the {CSRF_HEADER} header", code="csrf_header_missing"
        )
