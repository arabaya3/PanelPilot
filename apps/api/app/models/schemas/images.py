"""Uploaded equipment photos.

An image is the one thing a user hands us that we then feed to a model, so the
checks here are about what the *bytes* are, never about what the request said
they are. A filename ending in ``.jpg`` and a ``Content-Type: image/jpeg``
header are both attacker-controlled strings.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

# Ids are opaque tokens the client echoes back on the chat call.
ImageId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ImageFormat(StrEnum):
    """A format we are willing to store and show a model.

    Deliberately short. Every entry is a decoder we are choosing to expose to
    untrusted input, so the list grows only when a format earns its place —
    not because a browser happens to produce it.
    """

    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"

    @property
    def media_type(self) -> str:
        """The MIME type to serve this format as."""
        return f"image/{self.value}"

    @property
    def extension(self) -> str:
        """The suffix used in the storage key."""
        return "jpg" if self is ImageFormat.JPEG else self.value


class StoredImage(BaseModel):
    """A photo that has been accepted and written to storage.

    Attributes:
        image_id: Opaque handle the chat endpoint references.
        tenant_id: Owner. Carried on the record rather than looked up later so
            a reader cannot forget to scope by it.
        image_format: What the bytes actually are, as sniffed — not as declared.
        size_bytes: Size on disk, for quota and diagnostics.
    """

    image_id: ImageId
    tenant_id: ImageId
    image_format: ImageFormat
    size_bytes: int = Field(ge=1)

    @property
    def storage_key(self) -> str:
        """Where the bytes live.

        Returns:
            A tenant-scoped key. The tenant is the first path segment so a
            misconfigured bucket policy or a listing bug is scoped to one
            tenant rather than exposing the whole corpus, and so per-tenant
            deletion is a prefix operation.
        """
        return f"{self.tenant_id}/{self.image_id}.{self.image_format.extension}"


class ImageUploadResponse(BaseModel):
    """What an upload returns.

    Attributes:
        image_id: The handle to pass to the chat endpoint.
    """

    image_id: ImageId
