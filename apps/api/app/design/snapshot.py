"""Canonical JSON and its SHA-256, which bind a saved design to its approval.

A saved revision stores the design the server built (its snapshot) and the
hash of that design's canonical JSON. An approval records the hash it
approved, and an export recomputes it from what is stored, so a design changed
after it was approved (in the database or anywhere else) is refused rather
than printed under the approval.

Canonical here means: keys sorted, no whitespace, UTF-8, non-ASCII kept as
is. Every value in a design is a string, an integer, a boolean or null once
dumped in JSON mode (``Decimal`` dumps as a string), so a JSONB round trip,
which reorders keys only, gives back the same bytes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.models.schemas.design import CompanyProfile


def canonical_json(value: Any) -> bytes:
    """The canonical JSON encoding of a JSON-shaped value.

    Args:
        value: Dicts, lists, strings, integers, booleans and ``None``.

    Returns:
        UTF-8 bytes: keys sorted, no whitespace.

    Raises:
        ValueError: If the value holds a float, whose text form is not
            canonical across encoders, or anything JSON cannot encode.
    """
    _refuse_floats(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _refuse_floats(value: Any) -> None:
    if isinstance(value, float):
        raise ValueError("a float has no canonical JSON form here; dump Decimals as strings")
    if isinstance(value, dict):
        for item in value.values():
            _refuse_floats(item)
    elif isinstance(value, list):
        for item in value:
            _refuse_floats(item)


def sha256_of(value: Any) -> str:
    """The SHA-256 of a value's canonical JSON, as 64 hex digits.

    Args:
        value: As :func:`canonical_json` takes.

    Returns:
        The lower-case hex digest.
    """
    return hashlib.sha256(canonical_json(value)).hexdigest()


def profile_sha256(company: CompanyProfile) -> str:
    """The SHA-256 of a resolved company profile, every setting in full.

    Hashing the profile as resolved, not the settings as entered, means a
    change to this software's default profile (which settings left out
    inherit) changes the hash too.

    Args:
        company: The profile a design was, or would be, made under.

    Returns:
        The lower-case hex digest.
    """
    return sha256_of(company.model_dump(mode="json"))
