"""Generating a diagnosis in the engineer's language.

**Direct generation, not translation.** The prompt instructs the model to
answer in the target language. Generating in English and translating afterwards
puts a third error source downstream of retrieval and generation, and each
layer's mistakes compound rather than cancel.

**Technical tokens survive unchanged.** A fault code, a parameter number, a
unit and a part number are identifiers, not words. "F0001" rendered as
"ف٠٠٠١" matches nothing in the manual, nothing on the drive's display and
nothing the engineer can type into a keypad. The instruction says so
explicitly, and ``unchanged_tokens`` checks it afterwards — an instruction the
model may or may not follow is not the same as a verified property.

**Verification is a check, not a repair.** A response that translated a fault
code is not patched up: it is reported. Silently rewriting model output would
mean the next such failure passes unseen, and the failure is that the model
did not understand the constraint.
"""

from __future__ import annotations

import re
from typing import Any

from app.ai.structured_output import generate_diagnosis
from app.models.schemas.guardrail import ConfidenceDecision
from app.models.schemas.locale import Locale
from app.models.schemas.responses import StructuredDiagnosis

# What must survive a translation intact.
#
# Deliberately pattern-based rather than a dictionary: a corpus grows new part
# numbers constantly, and a list would silently stop covering them. Each
# pattern matches a shape that is an identifier in any language.
_TECHNICAL_TOKEN = re.compile(
    r"""
    (?:
        \b[A-Za-z]{1,4}-?\d{2,6}[A-Za-z]?\b   # F0001, E-024, ACS880, P-1204
      | \b\d{1,3}\.\d{2}\b                     # parameter 21.03
      | \b\d+(?:\.\d+)?\s?(?:V|A|kW|W|Hz|Nm|mm|mA|kV|rpm)\b   # 400 V, 7.5 kW
    )
    """,
    re.VERBOSE,
)


def technical_tokens(text: str) -> set[str]:
    """Extract the identifiers a translation must not touch.

    Args:
        text: Any prose.

    Returns:
        The technical tokens it contains. Case is preserved: "acs880" is not
        the model number, and a translation that lowercased it has changed an
        identifier even though the letters match.
    """
    return set(_TECHNICAL_TOKEN.findall(text))


def diagnosis_tokens(diagnosis: StructuredDiagnosis) -> set[str]:
    """Extract every technical token from a whole diagnosis.

    Args:
        diagnosis: The structured answer.

    Returns:
        Tokens from the summary and from every step, since a code appearing
        only in step 3 matters as much as one in the summary.
    """
    found = technical_tokens(diagnosis.summary)
    for step in diagnosis.steps:
        found |= technical_tokens(step.instruction)
        found |= technical_tokens(step.rationale)
    if diagnosis.equipment_model:
        found |= technical_tokens(diagnosis.equipment_model)
    return found


def unchanged_tokens(reference: StructuredDiagnosis, translated: StructuredDiagnosis) -> set[str]:
    """Report technical tokens that a translation lost or altered.

    Args:
        reference: The diagnosis in the language the corpus is written in.
        translated: The same diagnosis generated in another locale.

    Returns:
        Tokens present in the reference and missing from the translation.
        Empty means every identifier survived.

        The comparison is one-directional on purpose. A translation may
        legitimately introduce a token the reference lacks — a step that
        mentions "400 V" only when spelled out in Hebrew — but it may never
        drop or alter one, because that is the identifier the engineer needs
        to type or look up.
    """
    return diagnosis_tokens(reference) - diagnosis_tokens(translated)


def localised_system_prompt(base: str, locale: Locale) -> str:
    """Extend a system prompt with the language instruction.

    Args:
        base: The task's own system prompt.
        locale: The language to answer in. Required — see
            ``generate_localised_diagnosis`` for why there is no default.

    Returns:
        The prompt with language and token-preservation rules appended. The
        instruction is in English regardless of target: the model follows
        instructions in English reliably, and a mistranslated instruction is a
        harder failure to notice than a mistranslated answer.
    """
    return f"""{base}

Write your entire response in {locale.english_name}. Do not answer in English
and translate afterwards — compose directly in {locale.english_name}.

Leave these exactly as they appear in the evidence, in Latin characters and
Western digits, without translating, transliterating or reformatting them:

- fault and alarm codes (F0001, E-024, AL 5091)
- parameter numbers (21.03, P-1204)
- equipment model and part numbers (ACS880)
- units and their values (400 V, 7.5 kW, 50 Hz, 60 Nm)

These are identifiers, not words. An engineer types them into a keypad and
looks them up in a manual; a translated code matches nothing and is worse
than no answer.
"""


def generate_localised_diagnosis(
    client: Any,
    *,
    model: str,
    system: str,
    question: str,
    evidence_ids: set[str],
    decision: ConfidenceDecision,
    locale: Locale,
    max_tokens: int = 2048,
) -> tuple[StructuredDiagnosis | None, ConfidenceDecision]:
    """Generate a diagnosis in one locale.

    Args:
        client: An Anthropic client.
        model: Model id.
        system: The task's system prompt, before the language instruction.
        question: The engineer's question, with evidence.
        evidence_ids: Passage ids the model was shown.
        decision: The cite-or-refuse verdict for this turn.
        locale: The language to answer in. **Required, with no default.** A
            default would mean a caller that forgot to pass one silently
            produces English for an Arabic-speaking engineer — a failure that
            looks like working software right up until someone reads it.
        max_tokens: Generation ceiling.

    Returns:
        ``(diagnosis, decision)`` exactly as ``generate_diagnosis`` does. The
        guardrail still owns whether generation happens at all; this only
        decides what language it happens in.
    """
    return generate_diagnosis(
        client,
        model=model,
        system=localised_system_prompt(system, locale),
        question=question,
        evidence_ids=evidence_ids,
        decision=decision,
        max_tokens=max_tokens,
    )
