"""Tests for `app/ai/retrieval/equipment_hint.py`.

Mirrors the module 1:1 -- if you add a function there, add its test here.

The titles below are the published corpus's own, so the vocabulary under test
is the one production builds today.
"""

from __future__ import annotations

import pytest

from app.ai.retrieval import equipment_hint
from app.ai.retrieval.equipment_hint import (
    build_vocabulary,
    infer_manufacturer,
    tokens,
)

TITLES = {
    "Siemens": [
        "SIMATIC S7-1200 G2 System Manual",
        "SINAMICS G120 Control Units CU240B-2/CU240E-2 List Manual, edition 02/2023",
        "SINAMICS G120 CU230P-2 Control Units List Manual, edition 02/2023",
        "SINAMICS G120C List Manual, edition 02/2023",
        "SINAMICS V20 Low voltage converters Operating Instructions",
    ],
    "ABB": [
        "ACS880 primary control program firmware manual",
        "ACS880 brake control program firmware manual",
    ],
}
VOCABULARY = build_vocabulary(TITLES)


# --- tokens -------------------------------------------------------------------


def test_a_model_token_needs_letters_and_digits() -> None:
    assert tokens("ACS880 fault 2310 on a G120") == {"ACS880", "G120"}


def test_a_fault_code_is_not_a_model() -> None:
    """2310 is a fault on several brands' drives; it names none of them."""
    assert tokens("fault 2310 then 3220") == set()


def test_hyphenated_designations_survive_whole() -> None:
    assert {"CU240B-2", "S7-1200"} <= tokens("CU240B-2 and S7-1200")


# --- the vocabulary ---------------------------------------------------------


def test_the_vocabulary_holds_product_names_and_brands() -> None:
    assert VOCABULARY["ACS880"] == "ABB"
    assert VOCABULARY["G120"] == "Siemens"
    assert VOCABULARY["SINAMICS"] == "Siemens"
    assert VOCABULARY["ABB"] == "ABB"


def test_ordinary_title_words_are_not_brand_markers() -> None:
    """With two brands, "Voltage" or "Firmware" belongs to one by accident.

    A question about low voltage must not be sent to the one manual whose
    title happens to say it.
    """
    for word in ("VOLTAGE", "FIRMWARE", "PROGRAM", "SYSTEM", "LIST", "MANUAL", "BRAKE"):
        assert word not in VOCABULARY, word


def test_a_token_two_brands_share_is_dropped() -> None:
    shared = build_vocabulary({"A": ["X100 drive"], "B": ["X100 drive"]})
    assert "X100" not in shared


# --- inference ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "brand"),
    [
        ("ACS880 shows 3220 DC link undervoltage", "ABB"),
        ("SINAMICS G120 F30001 overcurrent", "Siemens"),
        ("انفرتر ACS880 بطلع خطأ 2310 وقت التشغيل", "ABB"),
        ("acs880 trips at start", "ABB"),
        ("our Siemens drive trips", "Siemens"),
    ],
)
def test_a_question_naming_one_brands_product_is_given_that_brand(
    question: str, brand: str
) -> None:
    assert infer_manufacturer(question, vocabulary=VOCABULARY) == brand


@pytest.mark.parametrize(
    "question",
    [
        "motor trips on overcurrent",
        "low voltage problem with the brake",
        "ACS880 or G120 for a pump?",
    ],
)
def test_no_brand_is_guessed_for_none_or_several(question: str) -> None:
    """A wrong filter hides the answer; no filter only ranks it lower."""
    assert infer_manufacturer(question, vocabulary=VOCABULARY) is None


def test_an_empty_vocabulary_infers_nothing() -> None:
    assert infer_manufacturer("ACS880", vocabulary={}) is None


# --- the cached vocabulary --------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(equipment_hint, "_cache", None)


def test_the_vocabulary_is_read_once_per_ttl() -> None:
    calls: list[int] = []

    def load() -> dict[str, list[str]]:
        calls.append(1)
        return TITLES

    expired = equipment_hint.VOCABULARY_TTL_S + 1
    # Read in order: the first load's timestamp, two fresh checks, then a
    # check past the TTL and the reload's timestamp.
    clock = iter([0.0, 0.0, 10.0, expired, expired])

    def now() -> float:
        return next(clock)

    for _ in range(3):
        equipment_hint._cached_vocabulary(load=load, now=now)
    assert len(calls) == 1, "a fresh vocabulary was read again"
    equipment_hint._cached_vocabulary(load=load, now=now)
    assert len(calls) == 2, "an expired vocabulary was not reread"


def test_an_unreadable_index_means_no_hint_not_a_failure() -> None:
    def load() -> dict[str, list[str]]:
        raise ConnectionError("opensearch down")

    assert equipment_hint._cached_vocabulary(load=load) == {}
