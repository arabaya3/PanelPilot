"""M6c: keyword detection of life-safety and possibly-safety terms (``app.design.safety_terms``).

Detection is a heuristic: these tests pin what it catches and, in
``test_known_bypasses``, what it does not (docs §6.10). DB-free.
"""

# ruff: noqa: RUF001  (look-alike and dash-variant probe text)
from __future__ import annotations

import pytest

from app.design import ai_intake, safety_terms


@pytest.mark.parametrize(
    "text",
    [
        "The fіre pump is on MDB.",  # Cyrillic i
        "The ƒire pump",  # not folded: see bypasses
        "FIRE‑PUMP duty",  # non-breaking hyphen
        "fire‐pump",
        "fire‒pump",
        "fire–pump",
        "fire—pump",
        "fire―pump",
        "fire−pump",
        "fire­pump",  # soft hyphen
        "fire﹣pump",
        "fire－pump",
        "fire​pump",  # zero-width space
        "fi‍re pump",  # zero-width joiner inside the word
        "ＦＩＲＥ ＰＵＭＰ",  # full width
        "ﬁre pump",  # fi ligature
        "fïre pump",  # diaeresis
        "FΙRE PUMP",  # Greek capital iota
        "ѕprinkler",  # Cyrillic dze
        "sprіnklers",
        "Firewater pump",
        "fire-water pump",
        "fire water pump",
        "fire_pump",
        "fire.pump",
        "fire/pump",
        "firepump",
        "fire fighting lift",
        "firefighting lift",
        "firefighters lift",
        "firefighter's lift",
        "fireman's lift",
        "firemen lift",
        "Sprinklers",
        "jockey pump",
        "JOCKEY",
        "deluge valve",
        "hydrant pump",
        "smoke extract fan",
        "smoke-control damper",
        "fire alarm panel",
        "emergency lighting",
        "escape lighting",
        "life safety",
        "life-safety",
        "evacuation lift",
        "stair pressurisation fan",
        "staircase pressurization",
        "fire damper",
        "wet riser",
        "manual call point",
        "مضخة الحريق",  # Arabic fire pump
        "مَضَخَّة حَرِيق",  # with harakat
        "مضــخة حريق",  # with tatweel
        "إطفاء",  # extinguishing (hamza below)
        "اطفاء",  # without hamza
        "رشاشات",  # sprinklers
        "إنارة الطوارئ",  # emergency lighting
        "مضخة جوكي",  # jockey pump
        "משאבת כיבוי",  # Hebrew fire pump
        "מתזים",  # sprinklers
        "תאורת חירום",  # emergency lighting
        "גלאי עשן",  # smoke detector
    ],
)
def test_life_safety_terms_are_detected(text: str) -> None:
    if text.startswith("The ƒ"):
        assert safety_terms.level_of(text) != "life_safety"
        return
    assert safety_terms.level_of(text) == "life_safety", text


@pytest.mark.parametrize(
    "text",
    [
        "Emergency stop pushbutton",
        "Duty/standby pumps",
        "duty stand-by pump",
        "Alarm panel",
        "Fire",
        "the fire",
        "Critical cooling",
        "UPS for safety lighting",
        "pressurisation unit",
        "smoke",
        "escape door",
        "essential services board",
        "طوارئ",  # Arabic emergency alone
        "حريق",  # Arabic fire alone
        "חירום",  # Hebrew emergency alone
        "האש",  # Hebrew "the fire"
    ],
)
def test_ambiguous_words_are_possibly_safety(text: str) -> None:
    assert safety_terms.level_of(text) == "possibly", text


@pytest.mark.parametrize(
    "text",
    [
        "Fireplace fan 1 kW",
        "exit corridor sockets",
        "gas-fired boiler",
        "firewall cabinet",
        "firmware update",
        "Office lighting 1 kW",
        "Booster pump 5.5 kW",
        "גלאי תנועה",  # Hebrew motion detector
        "אשדוד",  # Hebrew Ashdod (holds the letters of "fire")
        "Main board MDB, 400 V three phase",
    ],
)
def test_ordinary_text_is_not_flagged(text: str) -> None:
    assert safety_terms.level_of(text) is None, text


@pytest.mark.parametrize(
    "text",
    [
        "f i r e pump",
        "f1re pump",
        "sprinker",
        "hydrent",
        "FACP on MDB",
        "NFPA 20 pump",
        "Inergen system",
        "զire pump",  # Armenian look-alike, not folded
        "the pump that keeps the building wet when it burns",
    ],
)
def test_known_bypasses(text: str) -> None:
    """Documented limits (docs §6.10): keyword detection does not recognise these."""
    assert (
        safety_terms.level_of(text) != "life_safety"
    ), f"{text!r} is now detected: move it out of the documented bypasses"


def test_mentions_carry_stored_offsets_and_stable_ids() -> None:
    text = "MDB. The FΙRE‑PUMP and an emergency stop."
    found = safety_terms.mentions(text)
    assert [(m.level, m.term, text[m.start : m.end]) for m in found] == [
        ("life_safety", "fire pump", "FΙRE‑PUMP"),
        ("possibly", "emergency", "emergency"),
    ]
    assert found == safety_terms.mentions(text)
    assert all(m.mention_id.startswith("m") and len(m.mention_id) == 17 for m in found)
    # The same words elsewhere are another mention.
    moved = safety_terms.mentions("x " + text)
    assert {m.mention_id for m in moved}.isdisjoint({m.mention_id for m in found})


def test_a_word_inside_a_term_is_that_term_only() -> None:
    assert [m.term for m in safety_terms.mentions("fire pump")] == ["fire pump"]


def test_the_skeleton_maps_back_to_the_stored_text() -> None:
    text = "a​b‑Ｃ"
    read, where = safety_terms.skeleton(text)
    assert read == "ab-c"
    assert where == [0, 2, 3, 4]


@pytest.mark.parametrize(
    ("text", "phrase"),
    [
        (
            "MDB. Pump P-7 5.5 kW on MDB. Lighting 1 kW on MDB. The former is the fire pump.",
            "Pump P-7 5.5 kW",
        ),
        (
            "MDB. Pump 5.5 kW on MDB. Fan 1 kW on MDB. The latter is not; the first one is a jockey.",
            "Pump 5.5 kW",
        ),
        ("MDB. Pump 5.5 kW on MDB. Fan 1 kW on MDB. Both serve the sprinklers.", "Fan 1 kW"),
        ("MDB. Pump A 5.5 kW. Pump B 5.5 kW. These pumps feed the hydrants.", "Pump A 5.5 kW"),
        (
            "MDB. Pump 5.5 kW on MDB. Lighting on MDB. The above pump is for the deluge.",
            "Pump 5.5 kW",
        ),
        ("MDB. Pump 5.5 kW on MDB. Lighting on MDB. Those two are standby units.", "Pump 5.5 kW"),
    ],
)
def test_far_references_are_followed(text: str, phrase: str) -> None:
    start = text.index(phrase)
    context = ai_intake.context_of(text, (start, start + len(phrase)), {"MDB"})
    assert ai_intake.safety_level(context) is not None, context


def test_a_far_reference_reads_only_later_sentences() -> None:
    text = "MDB. Both pumps feed the sprinklers. Lighting 1 kW on MDB."
    start = text.index("Lighting")
    context = ai_intake.context_of(text, (start, start + 16), {"MDB"})
    assert ai_intake.safety_level(context) is None, context
