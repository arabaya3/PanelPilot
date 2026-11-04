"""Tests for `app/design/ai_intake.py`: the server's checks of untrusted output."""

from __future__ import annotations

from typing import Any

import pytest

from app.design import ai_intake
from app.models.schemas.design import ProjectDesignRequest
from app.tests.domain.ai_intake_support import (
    SAVED_REQUEST,
    TEXT,
    complete_payload,
    item,
    placements_by_item,
)


def test_a_complete_output_is_kept_with_quotes_cut_from_the_input() -> None:
    items, rejected = ai_intake.check_output(complete_payload(), TEXT, [])
    assert rejected == []
    for found in items:
        assert found["quote"] == TEXT[found["source"]["start"] : found["source"]["end"]]


@pytest.mark.parametrize(
    ("field", "value", "quote", "found"),
    [
        ("power_kw", "7.5", "pump 7,5 kW", True),
        ("power_kw", "1.2", "lights 1,200 W", False),
        ("power_kw", "1200", "lights 1,200 W", False),
        ("power_kw", "3", "3 HP motor", False),
        ("power_kw", "4", "٤ kW", True),
        ("phases", 3, "three-phase pump", True),
        ("phases", 1, "single phase", True),
        ("power_factor", "0.85", "pf .85", True),
        ("length_m", "40", "about 45 m", False),
    ],
)
def test_numbers_must_be_written_in_the_quote(
    field: str, value: Any, quote: str, found: bool
) -> None:
    assert (ai_intake.literal_span(field, value, quote) is not None) is found


@pytest.mark.parametrize(
    "text",
    ["Jockey pump on MDB", "Sprinkler panel on MDB", "مضخة حريق", "משאבת אש"],
)
def test_life_safety_is_always_unsupported(text: str) -> None:
    payload = {
        "items": [item(text, text, "l1", "load", board="MDB", description="Pump", kind="motor")]
    }
    items, rejected = ai_intake.check_output(payload, text, ["MDB"])
    assert rejected == []
    assert items[0]["kind"] == "unsupported"


def test_bad_offsets_en_masse_are_malformed_but_one_is_rejected() -> None:
    one_bad = complete_payload()
    one_bad["items"][0]["source"] = {"start": -1, "end": 3}
    _, rejected = ai_intake.check_output(one_bad, TEXT, [])
    assert rejected[0]["reason"] == "offsets_outside_input"
    all_bad = complete_payload()
    for found in all_bad["items"]:
        found["source"] = {"start": 5000, "end": 5001}
    with pytest.raises(ai_intake.ProposalMalformed):
        ai_intake.check_output(all_bad, TEXT, [])


def test_overlong_strings_and_spans_are_rejected() -> None:
    payload = complete_payload()
    payload["items"][0]["value"]["name"] = "B" * 300
    text = TEXT + " " * 400
    payload["items"][2]["source"] = {"start": 0, "end": len(text)}
    _, rejected = ai_intake.check_output(payload, text, [])
    reasons = {r["id"]: r["reason"] for r in rejected}
    assert reasons["i1"] == "invalid_value"
    assert reasons["i3"] == "span_too_long"


def test_a_save_places_only_confirmed_items_and_records_edits() -> None:
    items, _ = ai_intake.check_output(complete_payload(), TEXT, [])
    request = ProjectDesignRequest.model_validate(SAVED_REQUEST)
    placements = placements_by_item(items)
    with pytest.raises(ai_intake.PlacementError) as refused:
        ai_intake.place(request, {}, items, [], placements)
    assert refused.value.code == "ai_item_not_confirmed"
    decisions = [{"item_id": i["id"], "decision": "accept"} for i in items]
    placed = ai_intake.place(request, {}, items, decisions, placements)
    need = next(p for p in placed if p["item_id"] == "i5")
    assert need["changed_in_editor"] == ["need"]
    assert all(p["changed_in_editor"] == [] for p in placed if p["item_id"] != "i5")


def test_open_items_and_their_lines() -> None:
    items, _ = ai_intake.check_output(complete_payload(), TEXT, [])
    block = {"proposal": {"items": items}, "decisions": [{"item_id": "i7", "decision": "reject"}]}
    codes = sorted(i["code"] for i in ai_intake.open_items(block))
    assert codes == ["ai_item_unreviewed"] * 5 + ["ai_unsupported"]
    lines = [ai_intake.open_item_line(block, i) for i in ai_intake.open_items(block)]
    assert all(line and line.startswith("[OPEN] ") for line in lines)


def test_edits_fit_their_kind() -> None:
    assert ai_intake.check_edit("board", {"name": "DB-2"}) == {"name": "DB-2"}
    with pytest.raises(ValueError, match="not edited"):
        ai_intake.check_edit("question", {"text": "x"})
    with pytest.raises(ValueError, match="does not fit"):
        ai_intake.check_edit("board", {"name": "DB-2", "approved": True})
