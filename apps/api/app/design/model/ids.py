"""Opaque, stable ids derived from the ids they stand for.

An id is a prefix naming the collection and the first 24 hex digits of the
SHA-256 of the canonical JSON of its parts, so the same parts always give the
same id (migration and composition are deterministic), no separator can make
two different part lists collide, and no code can read meaning out of an id.
"""

from __future__ import annotations

import hashlib
import json


def derive(prefix: str, *parts: str) -> str:
    """An opaque id for ``parts`` in the collection ``prefix``.

    Args:
        prefix: A short collection tag (``"c"`` for components, ...).
        *parts: The ids it is derived from, in a fixed order.

    Returns:
        ``"<prefix>-<24 hex digits>"``.
    """
    text = json.dumps([prefix, *parts], ensure_ascii=False, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]}"
