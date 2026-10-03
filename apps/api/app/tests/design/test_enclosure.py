"""Tests for `app/design/enclosure.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

from app.design import distribution, enclosure, profile
from app.models.schemas.design import (
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    Supply,
)


def _board(sockets: int, **supply: Decimal) -> distribution.Board:  # type: ignore[name-defined]
    loads = [
        LoadInput(description=f"Sockets {i}", load=LoadKind.SOCKET, power_kw=Decimal("1.5"))
        for i in range(sockets)
    ]
    loads.append(LoadInput(description="Data", load=LoadKind.DATA, power_kw=Decimal(1)))
    return distribution.design_distribution_board(
        DistributionBoardRequest(name="DB", supply=Supply(**supply), loads=loads),
        profile.default_profile(),
    )


def _codes(board: distribution.Board) -> list[str]:  # type: ignore[name-defined]
    return [n.code for n in board.notes if n.code.startswith("enclosure")]


def test_the_smallest_enclosure_with_room_for_spares_is_named() -> None:
    board = _board(3, fault_level_ka=Decimal(10))
    (named,) = [n for n in board.notes if n.code == "enclosure_selected"]
    chosen = next(e for e in enclosure.ENCLOSURES if e.type_number == named.params["type"])
    assert named.params["order"] == chosen.order_number
    assert Decimal(named.params["free"]) >= 0
    assert "1SKC802027C0201" in named.params["source"]
    # The notes_for result is what the board carries.
    assert enclosure.notes_for(board, profile.default_profile())[0].code == "enclosure_selected"


def test_more_circuits_need_more_rows() -> None:
    small = _board(3, fault_level_ka=Decimal(10))
    large = _board(18, fault_level_ka=Decimal(10))

    def rows(board: distribution.Board) -> int:  # type: ignore[name-defined]
        (named,) = [n for n in board.notes if n.code == "enclosure_selected"]
        return int(named.params["rows"])

    assert rows(large) > rows(small)


def test_a_fault_level_above_35_ka_is_beyond_it() -> None:
    assert _codes(_board(3, fault_level_ka=Decimal(50))) == ["enclosure_beyond"]


def test_a_company_rail_length_or_an_unsized_device_names_none() -> None:
    board = _board(3, fault_level_ka=Decimal(10))
    own = profile.default_profile().model_copy(update={"usable_rail_mm": Decimal(500)})
    assert enclosure.notes_for(board, own) == []
    unsized = profile.default_profile().model_copy(update={"rail_widths_mm": {}})
    (made,) = enclosure.notes_for(board, unsized)
    assert made.code == "enclosure_unknown_widths"
