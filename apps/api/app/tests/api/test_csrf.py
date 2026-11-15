"""Tests for `app/api/csrf.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest

from app.api.csrf import CSRF_HEADER, check_same_site_request, origin_of
from app.core.errors import AuthorizationError

WEB = "https://app.panelpilot.example"


def _check(
    *, origin: str | None = None, referer: str | None = None, header: str | None = "1"
) -> None:
    check_same_site_request(
        origin=origin, referer=referer, csrf_header=header, trusted_origins={WEB}
    )


def _code(**kwargs: str | None) -> str | None:
    with pytest.raises(AuthorizationError) as refused:
        _check(**kwargs)
    return refused.value.code


def test_the_web_apps_own_origin_with_the_header_passes() -> None:
    _check(origin=WEB)
    _check(origin=WEB.upper() + "/")


def test_a_referer_stands_in_when_no_origin_was_sent() -> None:
    _check(referer=f"{WEB}/design?view=boards")


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"origin": "https://evil.example"},
        {"origin": "null"},
        # An opaque origin is never a reason to trust the Referer instead.
        {"origin": "null", "referer": f"{WEB}/design"},
        {"referer": "https://evil.example/page"},
        {"referer": "/relative/path"},
        # A look-alike host is another site.
        {"origin": f"{WEB}.evil.example"},
        {"origin": "http://app.panelpilot.example"},
    ],
)
def test_anything_else_is_refused_as_cross_site(kwargs: dict[str, str]) -> None:
    assert _code(**kwargs) == "csrf_origin_refused"


def test_the_custom_header_is_required_too() -> None:
    assert _code(origin=WEB, header=None) == "csrf_header_missing"
    assert _code(origin=WEB, header="") == "csrf_header_missing"


def test_no_trusted_origins_refuses_everything() -> None:
    """Unconfigured outside dev: closed, never open."""
    with pytest.raises(AuthorizationError):
        check_same_site_request(origin=WEB, referer=None, csrf_header="1", trusted_origins=())


def test_the_header_name_is_the_one_the_web_app_sends() -> None:
    assert CSRF_HEADER == "X-PanelPilot-CSRF"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://App.Example:8443/x?y#z", "https://app.example:8443"),
        ("http://localhost:3000/", "http://localhost:3000"),
        ("/only/a/path", None),
        ("not a url", None),
        ("", None),
    ],
)
def test_origin_of(url: str, expected: str | None) -> None:
    assert origin_of(url) == expected
