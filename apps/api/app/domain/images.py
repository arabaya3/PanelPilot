"""Storing and retrieving equipment photos.

Separated from recognition (AI-008) on purpose: this module decides what may
be stored and who may read it, and knows nothing about what a fault display
looks like. Tangling storage rules with ML logic makes both harder to reason
about, and the security-relevant half is this one.

**Content is sniffed, never trusted.** The filename, its extension, and the
declared ``Content-Type`` are all supplied by the caller. A file named
``photo.jpg`` announcing ``image/jpeg`` can be anything at all — a script, an
archive, a polyglot crafted to be valid as two formats at once. The only
evidence worth anything is the bytes, so the format is read from the magic
number and the declaration is ignored entirely.

**Every read is tenant-scoped.** ``get_image`` takes the tenant and refuses
anything belonging to another one. An id alone would be a capability: image
ids are handed to clients, and one leaking would otherwise expose a
photograph of someone else's equipment.
"""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError, ValidationError
from app.domain.storage import ObjectStore
from app.models.schemas.images import ImageFormat, ImageUploadResponse, StoredImage

# 8 MB. Large enough for a phone photo of a drive display, small enough that a
# request cannot be used to fill a disk. Enforced on the bytes actually read,
# not on a Content-Length header the client chooses.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Enough bytes to identify every format we accept.
_SNIFF_BYTES = 16

# Magic numbers, in the order they are tested. WebP needs two checks because
# its container is RIFF, which is also used by formats we do not accept.
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_RIFF_MAGIC = b"RIFF"
_WEBP_TAG = b"WEBP"


def sniff_format(data: bytes) -> ImageFormat:
    """Identify an image by its content.

    Args:
        data: The uploaded bytes. Only the first few matter.

    Returns:
        The format the bytes actually are.

    Raises:
        ValidationError: If the bytes are not a format we accept. Note this
            fires on *content*, so a renamed executable is rejected however
            convincing its filename and declared type.
    """
    header = data[:_SNIFF_BYTES]

    if header.startswith(_JPEG_MAGIC):
        return ImageFormat.JPEG
    if header.startswith(_PNG_MAGIC):
        return ImageFormat.PNG
    # RIFF alone is not enough: RIFF also carries WAV and AVI, and accepting
    # the container would let a sound file through as an image.
    if header.startswith(_RIFF_MAGIC) and header[8:12] == _WEBP_TAG:
        return ImageFormat.WEBP

    raise ValidationError(
        "that file is not a JPEG, PNG or WebP image. The check is on the file's "
        "contents, so renaming it will not help."
    )


def store_image(
    *,
    store: ObjectStore,
    tenant_id: str,
    data: bytes,
) -> ImageUploadResponse:
    """Validate and store an uploaded photo.

    Args:
        store: Where the bytes go.
        tenant_id: The uploading tenant.
        data: The uploaded bytes.

    Returns:
        The id the chat endpoint will reference.

    Raises:
        ValidationError: If the upload is empty, over the size ceiling, or is
            not an image. Size is checked before sniffing so an oversized file
            is rejected without inspecting it, and both are checked here
            rather than at the edge — a second caller reaching this function
            gets the same rules rather than a different set.
    """
    if not data:
        raise ValidationError("the uploaded file is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationError(
            f"the image is {len(data) // 1024} KB; the limit is " f"{MAX_IMAGE_BYTES // 1024} KB"
        )

    image_format = sniff_format(data)
    record = StoredImage(
        # Random, not derived from the content or the filename. A content hash
        # would let one tenant confirm another holds an identical photo by
        # observing a collision.
        image_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        image_format=image_format,
        size_bytes=len(data),
    )
    store.put(record.storage_key, data)
    return ImageUploadResponse(image_id=record.image_id)


def get_image(
    *,
    store: ObjectStore,
    image_id: str,
    tenant_id: str,
) -> tuple[StoredImage, bytes]:
    """Load a stored photo for one tenant.

    Used by AI-008 to feed a photo to the vision model.

    Args:
        store: Where the bytes live.
        image_id: The handle returned by the upload.
        tenant_id: The tenant asking. Required, not optional — an unscoped
            read would turn a leaked id into a capability over someone else's
            photograph.

    Returns:
        The record and its bytes.

    Raises:
        NotFoundError: If no such image exists for this tenant. Deliberately
            the same error whether the id is unknown or belongs elsewhere:
            distinguishing them tells a caller that an id they cannot read
            does exist, which is a membership oracle over other tenants' data.
    """
    for image_format in ImageFormat:
        # The format is not part of the id, so the key is recovered by trying
        # each extension. Three cheap existence checks beats carrying a
        # database row for something the storage layer already knows.
        candidate = StoredImage(
            image_id=image_id,
            tenant_id=tenant_id,
            image_format=image_format,
            size_bytes=1,
        )
        data = store.get(candidate.storage_key)
        if data is not None:
            return (
                StoredImage(
                    image_id=image_id,
                    tenant_id=tenant_id,
                    image_format=image_format,
                    size_bytes=len(data),
                ),
                data,
            )

    raise NotFoundError(f"no image {image_id!r}")
