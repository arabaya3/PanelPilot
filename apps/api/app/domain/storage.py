"""The object-storage port, and a filesystem adapter for it.

Production wants S3-compatible storage. Nothing in this repo needs an S3
client to be correct, though, so the domain depends on this small interface
and an S3 adapter can be added behind it without touching a caller — the
alternative is a boto3 import threaded through the domain, which then has to
be mocked in every test that touches an image.

**Keys are opaque to the store.** Tenant scoping is decided by whoever builds
the key (see ``StoredImage.storage_key``); the store just reads and writes.
That keeps the isolation rule in one place instead of spread across every
backend that might implement this.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    """Somewhere bytes can be put and got by key."""

    def put(self, key: str, data: bytes) -> None:
        """Store bytes under a key, replacing anything already there.

        Args:
            key: Where to store it.
            data: The bytes.
        """
        ...

    def get(self, key: str) -> bytes | None:
        """Read bytes back.

        Args:
            key: What to read.

        Returns:
            The bytes, or ``None`` if nothing is stored there.
        """
        ...


# A key is built from a tenant id and an image id, both of which are UUIDs or
# hex, plus one extension. Anything else means a caller built a key from
# something it should not have.
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")


def _validate_key(key: str) -> None:
    """Refuse a key that could escape its directory.

    Args:
        key: The proposed key.

    Raises:
        ValueError: If the key is empty, absolute, or contains a traversal
            segment. The keys this module builds are always safe; this guards
            the case where a future caller passes something derived from user
            input, which is how a storage layer turns into arbitrary file
            write.
    """
    if not key or not _SAFE_KEY.match(key):
        raise ValueError(f"unsafe storage key: {key!r}")
    # Redundant today — the pattern requires every segment to start with an
    # alphanumeric, so ".." cannot match it — and kept anyway. Traversal is
    # the failure that turns a store into arbitrary file access, and the cost
    # of a second check is one comparison. No test can distinguish it from the
    # pattern, so it is documented rather than claimed as covered.
    if ".." in key.split("/"):
        raise ValueError(f"unsafe storage key: {key!r}")


class FilesystemObjectStore:
    """An object store backed by a directory.

    For local development and tests. The interface is the same one an S3
    adapter would implement, so swapping backends is a composition-root
    change rather than a domain change.
    """

    def __init__(self, root: Path) -> None:
        """Bind the store to a directory.

        Args:
            root: Where objects live. Created if absent.
        """
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> None:
        """Store bytes under a key.

        Args:
            key: Where to store it.
            data: The bytes.

        Raises:
            ValueError: If the key could escape the root directory.
        """
        _validate_key(key)
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written to a temporary name and moved into place, so a crash
        # part-way cannot leave a truncated file that later reads as a valid
        # but corrupt image.
        staging = path.with_suffix(path.suffix + ".partial")
        staging.write_bytes(data)
        staging.replace(path)

    def get(self, key: str) -> bytes | None:
        """Read bytes back.

        Args:
            key: What to read.

        Returns:
            The bytes, or ``None`` if nothing is stored there.

        Raises:
            ValueError: If the key could escape the root directory.
        """
        _validate_key(key)
        path = self._root / key
        if not path.is_file():
            return None
        return path.read_bytes()
