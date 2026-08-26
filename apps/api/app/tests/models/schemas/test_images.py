"""Tests for `app/models/schemas/images.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.images import ImageFormat, ImageUploadResponse, StoredImage

_TENANT = "11111111-1111-1111-1111-111111111111"


def _record(**overrides: object) -> StoredImage:
    payload: dict[str, object] = {
        "image_id": "abc123",
        "tenant_id": _TENANT,
        "image_format": ImageFormat.JPEG,
        "size_bytes": 4096,
    }
    payload.update(overrides)
    return StoredImage.model_validate(payload)


def test_the_key_puts_the_tenant_first() -> None:
    """So a listing bug or a bad bucket policy is scoped to one tenant.

    It also makes per-tenant deletion a prefix operation rather than a scan.
    """
    assert _record().storage_key == f"{_TENANT}/abc123.jpg"


@pytest.mark.parametrize(
    ("image_format", "suffix"),
    [(ImageFormat.JPEG, "jpg"), (ImageFormat.PNG, "png"), (ImageFormat.WEBP, "webp")],
)
def test_each_format_has_its_own_extension(image_format: ImageFormat, suffix: str) -> None:
    """Give each format a distinct extension.

    The read path recovers the format by trying extensions, so two formats
    sharing one would make an image unreadable.
    """
    assert _record(image_format=image_format).storage_key.endswith(f".{suffix}")


def test_extensions_are_distinct() -> None:
    assert len({f.extension for f in ImageFormat}) == len(ImageFormat)


@pytest.mark.parametrize("image_format", list(ImageFormat))
def test_every_format_has_a_media_type(image_format: ImageFormat) -> None:
    assert image_format.media_type == f"image/{image_format.value}"


def test_the_accepted_formats_are_a_short_closed_set() -> None:
    """Each entry is a decoder exposed to untrusted input.

    Pinned so the list grows deliberately rather than because a browser
    happened to produce something new.
    """
    assert {f.value for f in ImageFormat} == {"jpeg", "png", "webp"}


def test_a_stored_image_must_have_content() -> None:
    """Zero bytes is not an image, and would read back as a valid record."""
    with pytest.raises(ValidationError):
        _record(size_bytes=0)


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_untenanted_record_is_refused(blank: str) -> None:
    """Refuse a record with no tenant.

    A blank tenant would put the object at the storage root, outside any
    tenant prefix and therefore outside the isolation the prefix provides.
    """
    with pytest.raises(ValidationError):
        _record(tenant_id=blank)


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_unidentified_record_is_refused(blank: str) -> None:
    with pytest.raises(ValidationError):
        _record(image_id=blank)


def test_the_upload_response_carries_only_the_id() -> None:
    """Nothing about where it is stored reaches the client.

    A storage key in the response would be a path a caller could reason about.
    """
    assert set(ImageUploadResponse(image_id="abc").model_dump()) == {"image_id"}
