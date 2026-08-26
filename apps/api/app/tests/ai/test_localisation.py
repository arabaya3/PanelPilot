"""Tests for `app/ai/localisation.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The acceptance criterion is one underlying diagnosis run through all three
locales with **identical technical tokens** in each. That is the test at the
bottom: the prose differs, the identifiers do not.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai import localisation
from app.models.schemas.guardrail import ConfidenceDecision, DecisionOutcome
from app.models.schemas.locale import Locale
from app.models.schemas.responses import DiagnosisStep, Severity, StructuredDiagnosis
from app.models.schemas.search import Citation

_CITATION = Citation(
    document_id="abb-acs880-fw",
    document_title="ACS880 firmware manual",
    manufacturer="ABB",
)


def _diagnosis(summary: str, instruction: str, rationale: str = "Because.") -> StructuredDiagnosis:
    return StructuredDiagnosis(
        summary=summary,
        summary_citation_ids=["p1"],
        steps=[
            DiagnosisStep(
                order=1,
                instruction=instruction,
                rationale=rationale,
                citation_ids=["p1"],
                severity=Severity.WARNING,
            )
        ],
        severity=Severity.WARNING,
    )


# --- what counts as a technical token ---------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fault F0001 is shown.", {"F0001"}),
        ("Error E-024 appeared.", {"E-024"}),
        ("Set parameter 21.03 to zero.", {"21.03"}),
        ("Replace with P-1204.", {"P-1204"}),
        ("The ACS880 tripped.", {"ACS880"}),
        ("Supply is 400 V.", {"400 V"}),
        ("Rated 7.5 kW at 50 Hz.", {"7.5 kW", "50 Hz"}),
        ("Torque to 60 Nm.", {"60 Nm"}),
    ],
)
def test_identifiers_are_recognised(text: str, expected: set[str]) -> None:
    assert localisation.technical_tokens(text) == expected


def test_ordinary_prose_has_no_technical_tokens() -> None:
    """The check must not fire on every sentence, or it means nothing."""
    assert localisation.technical_tokens("Isolate the drive and wait five minutes.") == set()


def test_case_is_part_of_the_identifier() -> None:
    """Case is part of the identifier.

    "acs880" is not the model number. A translation that lowercased it
    changed an identifier even though the letters match, and the engineer
    searching the manual will not find it.
    """
    assert localisation.technical_tokens("ACS880") != localisation.technical_tokens("acs880")


def test_tokens_are_collected_from_every_step() -> None:
    """A code appearing only in step 3 matters as much as one in the summary."""
    diagnosis = StructuredDiagnosis(
        summary="A fault occurred.",
        summary_citation_ids=["p1"],
        steps=[
            DiagnosisStep(
                order=1,
                instruction="Check the supply.",
                rationale="Baseline.",
                citation_ids=["p1"],
                severity=Severity.INFO,
            ),
            DiagnosisStep(
                order=2,
                instruction="Set parameter 21.03 to 5.",
                rationale="It bounds the ramp.",
                citation_ids=["p1"],
                severity=Severity.INFO,
            ),
        ],
        severity=Severity.INFO,
    )
    assert "21.03" in localisation.diagnosis_tokens(diagnosis)


def test_the_equipment_model_is_a_token_too() -> None:
    diagnosis = _diagnosis("A fault.", "Do it.")
    with_model = diagnosis.model_copy(update={"equipment_model": "ACS880"})
    assert "ACS880" in localisation.diagnosis_tokens(with_model)


# --- the acceptance criterion -----------------------------------------------


def test_a_translation_that_keeps_its_identifiers_passes() -> None:
    english = _diagnosis("Fault F0001 on the ACS880.", "Set parameter 21.03 to 5.")
    arabic = _diagnosis("العطل F0001 على الوحدة ACS880.", "اضبط المعامل 21.03 على القيمة 5.")
    assert localisation.unchanged_tokens(english, arabic) == set()


def test_a_translated_fault_code_is_reported() -> None:
    """The failure this exists to catch.

    A code rendered in Arabic-Indic digits matches nothing in the manual,
    nothing on the drive's display, and nothing the engineer can type.
    """
    english = _diagnosis("Fault F0001 occurred.", "Check it.")
    mangled = _diagnosis("العطل ف٠٠٠١ حدث.", "افحصه.")
    assert "F0001" in localisation.unchanged_tokens(english, mangled)


def test_a_dropped_parameter_number_is_reported() -> None:
    english = _diagnosis("A fault.", "Set parameter 21.03 to 5.")
    vague = _diagnosis("عطل.", "اضبط معامل التسارع.")
    assert "21.03" in localisation.unchanged_tokens(english, vague)


def test_a_translation_may_add_a_token_it_did_not_have() -> None:
    """The check is one-directional.

    Spelling out "400 V" where the English left it implicit is fine; dropping
    one the English had is not.
    """
    english = _diagnosis("A fault.", "Check the supply.")
    fuller = _diagnosis("عطل.", "افحص التغذية 400 V.")
    assert localisation.unchanged_tokens(english, fuller) == set()


def test_one_diagnosis_across_three_locales_keeps_identical_tokens() -> None:
    """The acceptance criterion, stated directly.

    The same underlying diagnosis in all three languages: prose differs,
    identifiers do not.
    """
    by_locale = {
        Locale.ENGLISH: _diagnosis(
            "Fault F0001 on the ACS880 at 400 V.", "Set parameter 21.03 to 5."
        ),
        Locale.ARABIC: _diagnosis(
            "العطل F0001 على الوحدة ACS880 عند 400 V.", "اضبط المعامل 21.03 على 5."
        ),
        Locale.HEBREW: _diagnosis(
            "תקלה F0001 ביחידת ACS880 במתח 400 V.", "הגדר את פרמטר 21.03 לערך 5."
        ),
    }

    reference = by_locale[Locale.ENGLISH]
    expected = localisation.diagnosis_tokens(reference)
    assert expected == {"F0001", "ACS880", "400 V", "21.03"}

    for locale, diagnosis in by_locale.items():
        assert (
            localisation.unchanged_tokens(reference, diagnosis) == set()
        ), f"{locale.value} lost a technical token"

    # And the prose really is different — three copies of the English would
    # pass every assertion above while proving nothing.
    summaries = {d.summary for d in by_locale.values()}
    assert len(summaries) == 3


# --- the prompt --------------------------------------------------------------


@pytest.mark.parametrize("locale", list(Locale))
def test_the_prompt_names_the_target_language(locale: Locale) -> None:
    prompt = localisation.localised_system_prompt("Base.", locale)
    assert locale.english_name in prompt


def test_the_prompt_forbids_translate_after_generating() -> None:
    """Direct generation, not a translation layer.

    Translating afterwards stacks a third error source downstream of
    retrieval and generation.
    """
    prompt = localisation.localised_system_prompt("Base.", Locale.ARABIC)
    assert "compose directly" in prompt


def test_the_prompt_lists_what_must_not_be_translated() -> None:
    prompt = localisation.localised_system_prompt("Base.", Locale.HEBREW)
    for example in ("F0001", "21.03", "ACS880", "400 V"):
        assert example in prompt


def test_the_prompt_says_why_not_only_what() -> None:
    """A rule with a reason survives paraphrase better than a bare rule."""
    prompt = localisation.localised_system_prompt("Base.", Locale.ARABIC)
    assert "identifiers, not words" in prompt


def test_the_base_prompt_is_preserved() -> None:
    """The task's own instructions must not be replaced by the language ones."""
    assert "Answer only from the evidence." in localisation.localised_system_prompt(
        "Answer only from the evidence.", Locale.ARABIC
    )


def test_the_instruction_is_written_in_english_whatever_the_target() -> None:
    """Instruct in English whatever the target language.

    A mistranslated instruction is harder to notice than a mistranslated
    answer, because nothing downstream reads the prompt.
    """
    prompt = localisation.localised_system_prompt("Base.", Locale.ARABIC)
    assert "Write your entire response in Arabic" in prompt


# --- generation --------------------------------------------------------------


class _Block:
    def __init__(self, kind: str, name: str, payload: Any) -> None:
        self.type = kind
        self.name = name
        self.input = payload


class _Message:
    def __init__(self, block: _Block) -> None:
        self.content = [block]


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        payload = _diagnosis("Fault F0001.", "Check it.").model_dump(mode="json")
        return _Message(_Block("tool_use", "emit_diagnosis", payload))


def _permitting() -> ConfidenceDecision:
    return ConfidenceDecision(
        outcome=DecisionOutcome.ANSWER, score=0.9, threshold=0.6, citations=[_CITATION]
    )


def test_the_locale_reaches_the_system_prompt() -> None:
    client = _FakeClient()
    localisation.generate_localised_diagnosis(
        client,
        model="m",
        system="Base.",
        question="q",
        evidence_ids={"p1"},
        decision=_permitting(),
        locale=Locale.HEBREW,
    )
    assert "Hebrew" in client.calls[0]["system"]


def test_two_locales_send_different_prompts() -> None:
    """Otherwise the parameter is decoration."""
    prompts = []
    for locale in (Locale.ARABIC, Locale.HEBREW):
        client = _FakeClient()
        localisation.generate_localised_diagnosis(
            client,
            model="m",
            system="Base.",
            question="q",
            evidence_ids={"p1"},
            decision=_permitting(),
            locale=locale,
        )
        prompts.append(client.calls[0]["system"])
    assert prompts[0] != prompts[1]


def test_the_guardrail_still_owns_whether_generation_happens() -> None:
    """Localisation decides the language, never the permission."""
    from app.models.schemas.guardrail import RefusalReason

    refused = ConfidenceDecision(
        outcome=DecisionOutcome.NO_VERIFIED_SOURCE,
        score=0.0,
        threshold=0.6,
        reason=RefusalReason.NO_EVIDENCE,
    )
    client = _FakeClient()
    diagnosis, _ = localisation.generate_localised_diagnosis(
        client,
        model="m",
        system="Base.",
        question="q",
        evidence_ids={"p1"},
        decision=refused,
        locale=Locale.ARABIC,
    )
    assert diagnosis is None
    assert client.calls == [], "a refusal reached the model through the locale path"
