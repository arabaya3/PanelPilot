"""``ids``: opaque, deterministic ids that no separator can make collide."""

from __future__ import annotations

import re

from app.design.model import ids


def test_ids_are_deterministic_and_opaque() -> None:
    first = ids.derive("c", "DB-1", "Q1")
    assert first == ids.derive("c", "DB-1", "Q1")
    assert re.fullmatch(r"c-[0-9a-f]{24}", first)
    assert "DB-1" not in first


def test_part_boundaries_matter() -> None:
    assert ids.derive("c", "a:b", "c") != ids.derive("c", "a", "b:c")
    assert ids.derive("c", "a") != ids.derive("n", "a")
