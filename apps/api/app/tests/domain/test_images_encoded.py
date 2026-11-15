"""Upload rules against files a real encoder wrote, not hand-built headers.

`test_images.py` proves the sniffing on minimal magic numbers. These run the
same rules over what a camera or an editor actually produces — a JPEG with an
EXIF orientation tag, a PNG, a WebP, and the formats we refuse (GIF, BMP,
TIFF) — and pin the property that keeps the image surface small: the server
never decodes an upload. It sniffs sixteen bytes, stores the rest verbatim and
hands the same bytes to the vision model, so a truncated or corrupt body is
stored as-is rather than being parsed by an image library on the API host.

Pillow encodes the fixtures only; no PanelPilot code imports it. It is
installed transitively (reportlab, voyageai), so the module skips rather than
fails if a future lock drops it.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.domain import images as images_domain
from app.domain.storage import FilesystemObjectStore, ObjectStore
from app.models.schemas.images import ImageFormat

Image = pytest.importorskip("PIL.Image")

_TENANT = "11111111-1111-1111-1111-111111111111"
_EXIF_ORIENTATION = 0x0112


@pytest.fixture
def store(tmp_path: Path) -> ObjectStore:
    return FilesystemObjectStore(tmp_path / "images")


def _encode(image_format: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (20, 120, 40)).save(buffer, image_format, **options)
    return buffer.getvalue()


def _rotated_jpeg() -> bytes:
    exif = Image.Exif()
    exif[_EXIF_ORIENTATION] = 6  # "rotate 90 CW to display", as phones write it
    return _encode("JPEG", exif=exif.tobytes())


@pytest.mark.parametrize(
    ("image_format", "expected"),
    [("JPEG", ImageFormat.JPEG), ("PNG", ImageFormat.PNG), ("WEBP", ImageFormat.WEBP)],
)
def test_an_encoded_image_of_an_accepted_format_is_identified(
    image_format: str, expected: ImageFormat
) -> None:
    assert images_domain.sniff_format(_encode(image_format)) is expected


def test_a_jpeg_carrying_an_exif_orientation_is_a_jpeg() -> None:
    assert images_domain.sniff_format(_rotated_jpeg()) is ImageFormat.JPEG


@pytest.mark.parametrize("image_format", ["GIF", "BMP", "TIFF"])
def test_a_real_image_of_a_refused_format_is_rejected(image_format: str) -> None:
    """A genuine image is still refused when it is not on the allow-list."""
    with pytest.raises(ValidationError, match="not a JPEG, PNG or WebP"):
        images_domain.sniff_format(_encode(image_format))


def test_an_upload_is_stored_byte_for_byte_with_its_orientation(store: ObjectStore) -> None:
    """No decode, no re-encode: the EXIF tag the client relies on survives."""
    data = _rotated_jpeg()
    uploaded = images_domain.store_image(store=store, tenant_id=_TENANT, data=data)

    _, stored = images_domain.get_image(store=store, image_id=uploaded.image_id, tenant_id=_TENANT)

    assert stored == data
    assert Image.open(io.BytesIO(stored)).getexif()[_EXIF_ORIENTATION] == 6


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda data: data[: len(data) // 3], id="truncated"),
        pytest.param(lambda data: data[:64] + b"\x00" * (len(data) - 64), id="zeroed-body"),
    ],
)
def test_a_damaged_jpeg_is_stored_as_sent_without_being_decoded(
    store: ObjectStore, damage: object
) -> None:
    """The sniff reads the header only, so damage past it is not parsed here.

    Whether the picture is legible is the vision model's call, made on the
    same bytes; the API host never runs an image decoder over them.
    """
    assert callable(damage)
    data = damage(_encode("JPEG"))
    uploaded = images_domain.store_image(store=store, tenant_id=_TENANT, data=data)

    _, stored = images_domain.get_image(store=store, image_id=uploaded.image_id, tenant_id=_TENANT)

    assert stored == data
