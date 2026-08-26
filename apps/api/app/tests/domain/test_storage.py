"""Tests for `app/domain/storage.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Key validation is the security-relevant part: a storage layer that accepts a
caller-supplied key with `..` in it is arbitrary file write wearing a
different name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.storage import FilesystemObjectStore


@pytest.fixture
def store(tmp_path: Path) -> FilesystemObjectStore:
    return FilesystemObjectStore(tmp_path / "objects")


def test_bytes_round_trip(store: FilesystemObjectStore) -> None:
    store.put("tenant/thing.jpg", b"hello")
    assert store.get("tenant/thing.jpg") == b"hello"


def test_a_missing_key_reads_as_none(store: FilesystemObjectStore) -> None:
    """Absence is an ordinary answer, not a fault.

    The caller distinguishes tenant-scoped absence from a real error itself.
    """
    assert store.get("tenant/nothing.jpg") is None


def test_writing_twice_replaces(store: FilesystemObjectStore) -> None:
    store.put("k.jpg", b"first")
    store.put("k.jpg", b"second")
    assert store.get("k.jpg") == b"second"


def test_the_root_is_created_if_absent(tmp_path: Path) -> None:
    root = tmp_path / "does" / "not" / "exist"
    FilesystemObjectStore(root)
    assert root.is_dir()


def test_nested_keys_create_their_directories(store: FilesystemObjectStore) -> None:
    """Tenant-scoped keys are nested by construction."""
    store.put("a/b/c/thing.jpg", b"x")
    assert store.get("a/b/c/thing.jpg") == b"x"


def test_no_partial_file_is_left_behind(store: FilesystemObjectStore, tmp_path: Path) -> None:
    """Writes land atomically.

    A crash part-way through a direct write would leave a truncated file that
    later reads as a valid but corrupt image.
    """
    store.put("k.jpg", b"data")
    leftovers = list((tmp_path / "objects").rglob("*.partial"))
    assert leftovers == []


# --- keys that could escape the root ----------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "../escape.jpg",
        "tenant/../../escape.jpg",
        "a/../../b.jpg",
        "/absolute.jpg",
        "",
        "  ",
        "tenant//double.jpg",
        "./relative.jpg",
    ],
)
def test_a_traversing_key_is_refused(store: FilesystemObjectStore, key: str) -> None:
    """Otherwise the store is an arbitrary file write primitive.

    The keys this module builds are always safe; this guards the case where a
    future caller derives one from user input.
    """
    with pytest.raises(ValueError, match="unsafe storage key"):
        store.put(key, b"x")


@pytest.mark.parametrize("key", ["../escape.jpg", "/etc/passwd", ""])
def test_reads_are_validated_too(store: FilesystemObjectStore, key: str) -> None:
    """A read primitive that escapes is an arbitrary file read."""
    with pytest.raises(ValueError, match="unsafe storage key"):
        store.get(key)


def test_a_refused_write_leaves_nothing_behind(
    store: FilesystemObjectStore, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="unsafe storage key"):
        store.put("../escaped.jpg", b"x")
    assert not (tmp_path / "escaped.jpg").exists()


def test_an_ordinary_key_is_accepted(store: FilesystemObjectStore) -> None:
    """The validator must not be so strict it rejects real keys.

    A guard that blocks legitimate input gets loosened by whoever hits it
    next, usually further than it needs to be.
    """
    store.put("11111111-1111-1111-1111-111111111111/abc123.jpg", b"x")
    assert store.get("11111111-1111-1111-1111-111111111111/abc123.jpg") == b"x"
