"""Tests for `app/domain/images.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The three the spec names are the three that matter: cross-tenant access,
oversized uploads, and files that are not images however they are labelled.
"""

from __future__ import annotations

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.domain import images as images_domain
from app.domain.storage import FilesystemObjectStore, ObjectStore
from app.models.schemas.images import ImageFormat, StoredImage

_TENANT = "11111111-1111-1111-1111-111111111111"
_OTHER_TENANT = "22222222-2222-2222-2222-222222222222"

# Real magic numbers, not placeholder bytes — the sniffing is the thing under
# test, so a fixture that only looks plausible would prove nothing.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 60


@pytest.fixture
def store(tmp_path: object) -> ObjectStore:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    return FilesystemObjectStore(tmp_path / "images")


# --- content is sniffed, never trusted --------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [(JPEG, ImageFormat.JPEG), (PNG, ImageFormat.PNG), (WEBP, ImageFormat.WEBP)],
)
def test_a_real_image_is_identified_by_its_bytes(data: bytes, expected: ImageFormat) -> None:
    assert images_domain.sniff_format(data) is expected


@pytest.mark.parametrize(
    ("description", "data"),
    [
        ("a shell script", b"#!/bin/sh\necho hello\n" + b"\x00" * 40),
        ("a zip archive", b"PK\x03\x04" + b"\x00" * 60),
        ("a PDF", b"%PDF-1.7\n" + b"\x00" * 60),
        ("an ELF binary", b"\x7fELF" + b"\x00" * 60),
        ("plain text", b"just some text, honestly" + b" " * 40),
        ("a WAV file", b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 60),
    ],
)
def test_a_non_image_is_rejected_whatever_it_is_called(description: str, data: bytes) -> None:
    """The filename and declared type never reach this function.

    A file named `photo.jpg` announcing `image/jpeg` can be any of these. The
    WAV case matters most: it shares the RIFF container with WebP, so a check
    that stopped at the container would accept a sound file as an image.
    """
    with pytest.raises(ValidationError, match="not a JPEG, PNG or WebP"):
        images_domain.sniff_format(data)


def test_the_rejection_message_says_renaming_will_not_help() -> None:
    """So a user retrying with a different extension is not left guessing."""
    with pytest.raises(ValidationError, match="renaming it will not help"):
        images_domain.sniff_format(b"not an image at all" + b"\x00" * 40)


def test_a_jpeg_prefix_on_a_larger_file_is_still_a_jpeg() -> None:
    """Sniffing reads a header, not the whole file — a real photo is megabytes."""
    assert images_domain.sniff_format(JPEG + b"\xab" * 100_000) is ImageFormat.JPEG


# --- size ceiling -----------------------------------------------------------


def test_an_oversized_upload_is_rejected(store: ObjectStore) -> None:
    """Enforced on bytes actually read, not on a header the client chooses."""
    oversized = JPEG + b"\x00" * images_domain.MAX_IMAGE_BYTES
    with pytest.raises(ValidationError, match="the limit is"):
        images_domain.store_image(store=store, tenant_id=_TENANT, data=oversized)


def test_an_upload_at_the_ceiling_is_accepted(store: ObjectStore) -> None:
    """The bar is met, not merely approached."""
    exact = JPEG + b"\x00" * (images_domain.MAX_IMAGE_BYTES - len(JPEG))
    assert len(exact) == images_domain.MAX_IMAGE_BYTES
    assert images_domain.store_image(store=store, tenant_id=_TENANT, data=exact).image_id


def test_an_empty_upload_is_rejected(store: ObjectStore) -> None:
    with pytest.raises(ValidationError, match="empty"):
        images_domain.store_image(store=store, tenant_id=_TENANT, data=b"")


def test_size_is_checked_before_content(store: ObjectStore) -> None:
    """An oversized file is refused without inspecting it.

    Sniffing a file we have already decided to reject wastes work and, for a
    deliberately malformed upload, is the part most worth not doing.
    """
    oversized_junk = b"\x00" * (images_domain.MAX_IMAGE_BYTES + 1)
    with pytest.raises(ValidationError, match="the limit is"):
        images_domain.store_image(store=store, tenant_id=_TENANT, data=oversized_junk)


# --- storage and tenant scoping ---------------------------------------------


def test_a_stored_image_reads_back(store: ObjectStore) -> None:
    uploaded = images_domain.store_image(store=store, tenant_id=_TENANT, data=JPEG)
    record, data = images_domain.get_image(
        store=store, image_id=uploaded.image_id, tenant_id=_TENANT
    )
    assert data == JPEG
    assert record.image_format is ImageFormat.JPEG
    assert record.size_bytes == len(JPEG)


def test_another_tenant_cannot_read_the_image(store: ObjectStore) -> None:
    """The acceptance criterion's security half.

    Image ids are handed to clients. Without tenant scoping a leaked id is a
    capability over a photograph of someone else's equipment.
    """
    uploaded = images_domain.store_image(store=store, tenant_id=_TENANT, data=JPEG)
    with pytest.raises(NotFoundError):
        images_domain.get_image(store=store, image_id=uploaded.image_id, tenant_id=_OTHER_TENANT)


def test_a_cross_tenant_read_is_not_found_rather_than_forbidden(store: ObjectStore) -> None:
    """Distinguishing them is a membership oracle.

    "Forbidden" tells a caller that an id they cannot read does exist, which
    is enough to enumerate another tenant's uploads.
    """
    uploaded = images_domain.store_image(store=store, tenant_id=_TENANT, data=JPEG)
    with pytest.raises(NotFoundError) as theirs:
        images_domain.get_image(store=store, image_id=uploaded.image_id, tenant_id=_OTHER_TENANT)
    with pytest.raises(NotFoundError) as unknown:
        images_domain.get_image(store=store, image_id="nope", tenant_id=_OTHER_TENANT)
    # Same class, and neither message reveals whether the id exists.
    assert type(theirs.value) is type(unknown.value)
    assert uploaded.image_id not in str(unknown.value)


def test_an_unknown_id_is_not_found(store: ObjectStore) -> None:
    """The message names what was asked for, so a caller can act on it.

    An empty error reaches a user as a bare status code with nothing to
    correct, and reaches a log as a line that identifies no request.
    """
    with pytest.raises(NotFoundError, match="does-not-exist"):
        images_domain.get_image(store=store, image_id="does-not-exist", tenant_id=_TENANT)


@pytest.mark.parametrize(
    ("data", "expected"),
    [(JPEG, ImageFormat.JPEG), (PNG, ImageFormat.PNG), (WEBP, ImageFormat.WEBP)],
)
def test_every_format_round_trips(store: ObjectStore, data: bytes, expected: ImageFormat) -> None:
    """The read path recovers the format without it being part of the id."""
    uploaded = images_domain.store_image(store=store, tenant_id=_TENANT, data=data)
    record, read_back = images_domain.get_image(
        store=store, image_id=uploaded.image_id, tenant_id=_TENANT
    )
    assert record.image_format is expected
    assert read_back == data


def test_two_uploads_of_identical_bytes_get_different_ids(store: ObjectStore) -> None:
    """Ids are random, not content-derived.

    A content hash would let one tenant confirm another holds an identical
    photo by observing a collision.
    """
    first = images_domain.store_image(store=store, tenant_id=_TENANT, data=JPEG)
    second = images_domain.store_image(store=store, tenant_id=_OTHER_TENANT, data=JPEG)
    assert first.image_id != second.image_id


def test_the_storage_key_is_tenant_scoped() -> None:
    """The tenant leads the key.

    A misconfigured bucket policy or a listing bug is then scoped to one
    tenant rather than the whole corpus, and per-tenant deletion is a prefix
    operation.
    """
    record = StoredImage(
        image_id="abc", tenant_id=_TENANT, image_format=ImageFormat.JPEG, size_bytes=10
    )
    assert record.storage_key.startswith(f"{_TENANT}/")
    assert record.storage_key.endswith(".jpg")
