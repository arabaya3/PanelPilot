"""Independent review probes of the integrated M6c head (3bba9fc), DB-free.

Each strict xfail states the safe behaviour and documents a bypass that is
present today; when a probe starts passing, its xfail fails the run so the
documented limit list (docs/architecture/m6b-ai-intake.md §6.10) gets updated.
"""

# ruff: noqa: RUF001  (look-alike letters are the point)
from __future__ import annotations

import pytest

from app.design import ai_intake, safety_terms
from app.tests.domain.ai_intake_support import item

DETECTION_BYPASSES = [
    # Spaced, split or substituted letters.
    "f i r e pump",
    "f-i-r-e pump",
    "f.i.r.e. pump",
    "f1re pump",
    "spr1nkler pump",
    "sprinkIer pump",  # Latin capital I for l
    "ꜰire pump",  # U+A730 small capital F is not folded (other small capitals are)
    # Other languages and scripts.
    "Feuerlöschpumpe",
    "bomba contra incendios",
    "pompe incendie",
    "пожарный насос",
    "消防泵",
    "yangın pompası",
    # Abbreviations, trade names, standards.
    "FACP",
    "AFFF pump",
    "ESFR heads",
    "CO2 flooding system",
    "PAVA amplifier",
    "AOV actuators",
    "Inergen",
    "Novec 1230",
    "NFPA 20 pump",
    "EN 12845 pump",
]


@pytest.mark.xfail(strict=True, reason="known detection bypass (keyword list)")
@pytest.mark.parametrize("text", DETECTION_BYPASSES)
def test_detection_bypass(text: str) -> None:
    assert safety_terms.level_of(text) is not None


ITEM_BYPASSES = [
    # A possibly-safety word after ';' is another sentence: the load stays a load.
    ("Pump 15 kW on MDB; standby.", "Pump 15 kW on MDB"),
    # "it" not opening a sentence, two sentences later.
    (
        "Pump 15 kW on MDB. Lighting 2 kW. Heating 3 kW. Also, it serves the sprinkler system.",
        "Pump 15 kW on MDB",
    ),
    # The same untagged name repeated later ("Pump one" is not a tag).
    (
        "Pump one 15 kW on MDB. Lighting 2 kW on MDB. Pump one feeds the sprinklers.",
        "Pump one 15 kW on MDB",
    ),
    # A note reference.
    ("Pump 15 kW on MDB. See note 3. Note 3: this pump is the fire pump.", "Pump 15 kW on MDB"),
    # A standard instead of a term.
    ("Booster set 15 kW on MDB (NFPA 20 listed).", "Booster set 15 kW on MDB"),
]


@pytest.mark.xfail(strict=True, reason="known context bypass: the load is kept as a load")
@pytest.mark.parametrize(("text", "head"), ITEM_BYPASSES)
def test_item_context_bypass(text: str, head: str) -> None:
    payload = {
        "items": [item(text, head, "l1", "load", board="MDB", description="Pump", kind="motor")]
    }
    items, _ = ai_intake.check_output(payload, text, ["MDB"])
    assert items[0]["kind"] == "unsupported"


def _ai_block(text: str, items: list[dict[str, object]]) -> dict[str, object]:
    return {"proposal": {"input_text": text, "items": items}, "decisions": []}


@pytest.mark.xfail(
    strict=True,
    reason="a life-safety mention with no unsupported item can be clarified away (§6.10 last bullet)",
)
@pytest.mark.parametrize(
    "items",
    [
        [],  # the model left the fire pump out
        [{"id": "q1", "kind": "question", "value": {"text": "Is the fire pump by others?"}}],
        [{"id": "l1", "kind": "load", "value": {"description": "Pump"}}],
    ],
    ids=["omitted", "as_question", "missed_as_load"],
)
def test_a_life_safety_mention_is_a_safety_concern(items: list[dict[str, object]]) -> None:
    text = "Pump 15 kW on MDB. Lighting 2 kW. Heating 3 kW. Also, it serves the sprinkler system."
    assert safety_terms.level_of(text) == "life_safety"
    assert ai_intake.safety_concern(_ai_block(text, items)) is not None
