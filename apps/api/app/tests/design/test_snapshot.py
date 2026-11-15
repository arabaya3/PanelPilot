"""Tests for `app/design/snapshot.py`."""

from __future__ import annotations

import hashlib

import pytest

from app.design import profile, snapshot


def test_canonical_json_sorts_keys_and_drops_whitespace() -> None:
    assert snapshot.canonical_json({"b": 1, "a": ["é", None, True]}) == (
        '{"a":["é",null,true],"b":1}'.encode()
    )


def test_the_hash_is_of_the_canonical_bytes_and_ignores_key_order() -> None:
    first = snapshot.sha256_of({"x": "1", "y": {"b": 2, "a": 1}})
    second = snapshot.sha256_of({"y": {"a": 1, "b": 2}, "x": "1"})
    assert first == second == hashlib.sha256(b'{"x":"1","y":{"a":1,"b":2}}').hexdigest()


def test_a_float_is_refused() -> None:
    with pytest.raises(ValueError, match="float"):
        snapshot.canonical_json({"a": [1.5]})


def test_the_profile_hash_follows_any_setting() -> None:
    default = profile.default_profile()
    changed = default.model_copy(update={"spare_ways_percent": default.spare_ways_percent + 1})
    assert snapshot.profile_sha256(default) == snapshot.profile_sha256(profile.default_profile())
    assert snapshot.profile_sha256(default) != snapshot.profile_sha256(changed)
