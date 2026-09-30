"""Tests for `app/ai/tools/terminal_blocks.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

Expected terminals are read off Siemens Catalog LV 10 (10/2022), 8WH1
through-type terminals, pp. 14/44-14/46.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.tools import terminal_blocks
from app.core.errors import ValidationError


@pytest.mark.parametrize(
    ("section", "current", "article", "pe"),
    [
        # 1.5 mm² at 10 A: the 2.5 mm² terminal (0.14-4 mm², 32 A).
        ("1.5", "10", "8WH1000-0AF00", "8WH1000-0CF07"),
        # 6 mm² at 40 A: the 4 mm² terminal clamps up to 6 mm² and carries 41 A.
        ("6", "40", "8WH1000-0AG00", "8WH1000-0CG07"),
        # 6 mm² at 45 A: past 41 A, so the 6 mm² terminal (57 A).
        ("6", "45", "8WH1000-0AH00", "8WH1000-0CH07"),
        # 50 mm² at 140 A: the 35 mm² terminal clamps up to 50 mm², 150 A.
        ("50", "140", "8WH1000-0AM00", "8WH1000-0CM07"),
        # 120 mm² at 250 A: 95 mm² clamps only to 95 mm²; 150 mm² has no PE.
        ("120", "250", "8WH1000-0AS00", None),
        # 240 mm² at 400 A: the largest terminal (415 A).
        ("240", "400", "8WH1000-0AU00", None),
    ],
)
def test_the_smallest_terminal_that_clamps_and_carries_is_chosen(
    section: str, current: str, article: str, pe: str | None
) -> None:
    selection = terminal_blocks.select_terminal(
        cross_section_mm2=Decimal(section), current_a=Decimal(current)
    )
    assert selection.article == article
    assert selection.pe_article == pe
    assert selection.source.manufacturer == "Siemens"
    assert selection.source.page in (48, 49, 50)


@pytest.mark.parametrize(
    ("section", "current", "message"),
    [
        ("300", "400", "no 8WH1"),
        ("240", "450", "no 8WH1"),
        ("0", "10", "positive"),
        ("6", "NaN", "positive"),
    ],
)
def test_what_the_range_does_not_cover_is_refused(section: str, current: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        terminal_blocks.select_terminal(
            cross_section_mm2=Decimal(section), current_a=Decimal(current)
        )


def test_the_range_rises_with_the_size() -> None:
    sizes = [Decimal(t.size_mm2) for t in terminal_blocks._TERMINALS]
    currents = [Decimal(t.max_current_a) for t in terminal_blocks._TERMINALS]
    assert sizes == sorted(sizes)
    assert currents == sorted(currents)
    for terminal in terminal_blocks._TERMINALS:
        assert Decimal(terminal.rigid_min_mm2) <= Decimal(terminal.size_mm2)
        assert Decimal(terminal.size_mm2) <= Decimal(terminal.rigid_max_mm2)
