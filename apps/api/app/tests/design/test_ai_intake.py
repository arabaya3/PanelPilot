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


@pytest.mark.parametrize(
    ("field", "value", "quote"),
    [
        ("power_kw", "63", "breaker 63 A"),
        ("power_kw", "16", "cable 16 mm2"),
        ("power_kw", "400", "400 V supply"),
        ("power_kw", "5", "a 5 MW plant"),
        ("power_kw", "10", "10 kVA UPS"),
        ("power_kw", "12", "12 kWh battery"),
        ("length_m", "5.5", "pump 5.5 kW"),
        ("length_m", "16", "16 mm2 cable"),
        ("phases", 3, "3 kW heater"),
        ("power_factor", "0.85", "0.85 kW"),
    ],
)
def test_a_number_needs_its_own_unit(field: str, value: Any, quote: str) -> None:
    assert ai_intake.literal_span(field, value, quote) is None


@pytest.mark.parametrize(
    ("field", "value", "quote"),
    [
        ("power_kw", "5.5", "pump 5.5 kW"),
        ("power_kw", "2", "2 kilowatt heater"),
        ("power_kw", "3", "\u0633\u062e\u0627\u0646 3 \u0643\u064a\u0644\u0648\u0648\u0627\u0637"),
        ("length_m", "20", "20 m from the board"),
        ("length_m", "35", "35 metres"),
        ("phases", 3, "3-phase motor"),
        ("phases", 3, "3\u00d8 motor"),
        ("phases", 1, "single phase"),
        ("power_factor", "0.8", "cos phi 0.8"),
        ("power_factor", "0.9", "power factor of 0.9"),
    ],
)
def test_a_number_with_its_unit_is_read(field: str, value: Any, quote: str) -> None:
    assert ai_intake.literal_span(field, value, quote) is not None


@pytest.mark.parametrize(
    ("text", "product"),
    [
        ("Contactor ABB S203", True),
        ("Siemens 3RT2015 contactor", True),
        ("LC1D18", True),
        ("Schneider iC60N", True),
        ("Pump P-7", False),
        ("DB-1 sockets", False),
        ("AHU2 fan", False),
        ("Heater 5500W", False),
        ("Siemens motor 3 kW", False),
        ("Lighting 1.2 kW", False),
    ],
)
def test_part_numbers_are_told_from_tags(text: str, product: bool) -> None:
    assert ai_intake.product_number(text) is product


def test_a_part_number_is_never_a_load() -> None:
    text = "Main board MDB. Contactor ABB S203 on MDB."
    payload = {
        "items": [
            item(text, "Main board MDB", "b1", "board", name="MDB"),
            item(
                text,
                "Contactor ABB S203 on MDB",
                "l1",
                "load",
                board="MDB",
                description="ABB S203",
                kind="motor",
            ),
        ]
    }
    items, _ = ai_intake.check_output(payload, text, [])
    assert items[1]["kind"] == "unsupported"
    assert items[1]["value"]["reason"] == ai_intake.PRODUCT_REASON


@pytest.mark.parametrize(
    "text",
    [
        "Pump P-7 on MDB; P-7 is the fire pump.",
        "Hydrant booster on MDB.",
        "Deluge valve on MDB.",
        "Firefighting lift on MDB.",
        "Fireman's lift on MDB.",
        "Smoke extract fan on MDB.",
        "Emergency lighting on MDB.",
        "\u0645\u0636\u062e\u0629 \u0625\u0637\u0641\u0627\u0621 on MDB.",
        "\u05d4\u05d9\u05d3\u05e8\u05e0\u05d8 on MDB.",
    ],
)
def test_life_safety_reads_the_sentence_and_its_tags(text: str) -> None:
    head = text.split(" on MDB")[0]
    payload = {
        "items": [item(text, head, "l1", "load", board="MDB", description="Pump", kind="motor")]
    }
    items, _ = ai_intake.check_output(payload, text, ["MDB"])
    assert items[0]["kind"] == "unsupported"


def test_a_boards_other_loads_are_not_drawn_in() -> None:
    text = "Lights on MDB. Sprinkler panel on MDB."
    payload = {
        "items": [
            item(text, "Lights", "l1", "load", board="MDB", description="Lights", kind="lighting")
        ]
    }
    items, _ = ai_intake.check_output(payload, text, ["MDB"])
    assert items[0]["kind"] == "load"


def test_normalising_keeps_the_way_back_to_the_stored_text() -> None:
    text = "Pump ٥٫٥ kW fire​pump"  # noqa: RUF001
    plain, where = ai_intake.normalise(text)
    assert plain == "Pump 5.5 kW firepump"
    assert ai_intake.literal_span("power_kw", "5.5", text) == (5, 8)
    assert text[where[5] : where[7] + 1] == "٥٫٥"  # noqa: RUF001
    assert ai_intake.literal_span("power_kw", "5", text) is None


@pytest.mark.parametrize(
    ("field", "value", "quote"),
    [
        ("power_kw", "2", "1/2 kW"),
        ("power_kw", "1", "1/2 kW"),
        ("power_kw", "3", "ratio 3:1 kW"),
        ("length_m", "3", "3 m/s"),
        ("length_m", "5", "5 m per hour"),
    ],
)
def test_fractions_ratios_and_rates_are_not_values(field: str, value: Any, quote: str) -> None:
    assert ai_intake.literal_span(field, value, quote) is None


@pytest.mark.parametrize(
    ("text", "product"),
    [
        ("LC1-D18", True),
        ("6ES7 214-1AG40", True),
        ("S203-C16", True),
        ("ACS580-01-12A6-4", True),
        ("DB-12", False),
        ("Room 101A", False),
        ("230-400V supply", False),
    ],
)
def test_hyphenated_codes(text: str, product: bool) -> None:
    assert ai_intake.product_number(text) is product


def test_tags_compare_without_case_or_hyphen_and_pronouns_refer_back() -> None:
    text = "Pump P7 on MDB. Lights on MDB. p-7 is the fire pump."
    assert "fire pump" in ai_intake.context_of(text, (0, 7), {"mdb"})
    text = "Pump on MDB. It is the fire pump. Lights on MDB."
    context = ai_intake.context_of(text, (0, 4), {"mdb"})
    assert "fire pump" in context
    assert "Lights" not in context


def test_a_life_safety_mention_anywhere_is_an_open_item_no_decision_closes() -> None:
    items, _ = ai_intake.check_output(complete_payload(), TEXT, [])
    block = {
        "proposal": {"items": items, "input_text": TEXT + " The fire main is by others."},
        "decisions": [{"item_id": i["id"], "decision": "reject"} for i in items],
    }
    found = ai_intake.open_items(block)
    assert [(o["code"], o["subject"]["term"]) for o in found] == [
        (ai_intake.LIFE_SAFETY_MENTIONED, "fire main")
    ]
    assert found[0]["subject"]["mention_id"].startswith("m")
    assert ai_intake.open_item_line(block, found[0]) == (
        "[OPEN] The description mentions fire-pump or life-safety equipment ('fire main'): "
        "out of scope and not designed; check that no placed load is that equipment"
    )
    assert ai_intake.open_items({"proposal": {"items": [], "input_text": TEXT}}) == []


def test_an_unsupported_line_prints_a_phrase_not_a_reason_code() -> None:
    block = {
        "proposal": {
            "input_text": "Standby pump",
            "items": [
                {
                    "id": "i1",
                    "kind": "unsupported",
                    "quote": "Standby pump",
                    "value": {"text": "Standby pump", "reason": ai_intake.POSSIBLY_REASON},
                }
            ],
        }
    }
    line = ai_intake.open_item_line(block, {"code": "ai_unsupported", "subject": {"item_id": "i1"}})
    assert line == (
        "[OPEN] Not covered by this design (from AI intake): Standby pump"
        " — may be life-safety equipment; needs a person's safety review"
    )
