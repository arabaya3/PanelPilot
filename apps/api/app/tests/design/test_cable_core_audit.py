"""The pure cable-core audit scan (SYNTHETIC envelopes; no database)."""

from __future__ import annotations

from typing import Any

from app.design import cable_core_audit


def _envelope(cores: list[tuple[str, str, int]], schema: str = "pp-design/2") -> dict[str, Any]:
    nets = [{"id": f"n-{board}", "board_id": board} for board in sorted({b for b, _, _ in cores})]
    connections = [
        {"id": f"x{i}", "net_id": f"n-{board}", "core": {"cable_id": cable, "index": index}}
        for i, (board, cable, index) in enumerate(cores)
    ]
    return {"schema": schema, "model": {"nets": nets, "connections": connections}}


def test_finds_one_cable_id_and_core_on_two_boards_in_v2_and_v3() -> None:
    for schema in ("pp-design/2", "pp-design/3"):
        snapshot = _envelope([("MB-1", "c1-cable", 1), ("SB-1", "c1-cable", 1)], schema)
        assert cable_core_audit.shared_cores(snapshot) == [("c1-cable", 1, ["MB-1", "SB-1"])]


def test_other_cores_cables_and_v1_are_not_findings() -> None:
    snapshot = _envelope(
        [("MB-1", "c1-cable", 1), ("SB-1", "c1-cable", 2), ("SB-1", "c2-cable", 1)]
    )
    assert cable_core_audit.shared_cores(snapshot) == []
    assert cable_core_audit.shared_cores({"schema": "pp-design/1", "model": {}}) == []


def test_a_json_text_column_is_read_and_listed() -> None:
    import json

    text = json.dumps(_envelope([("MB-1", "c1-cable", 1), ("SB-1", "c1-cable", 1)]))
    assert cable_core_audit.audit([("p", 7, text)]) == ["p r7: c1-cable#1 on MB-1, SB-1"]


def test_an_unreadable_row_is_never_reported_as_clean() -> None:
    rows = [("p", 1, None), ("p", 2, '{"schema": "pp-design/2", "model": {"nets": null}}')]
    found = cable_core_audit.audit(rows)
    assert [line.split(":")[0] for line in found] == ["p r1", "p r2"]
    assert all("UNREADABLE" in line for line in found)
