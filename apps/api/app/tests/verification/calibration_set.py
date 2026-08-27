"""The ten calibration items used to check inter-rater agreement.

AI-012's testing requirement: two verifiers independently label the same ten
items and their labels are compared before the rubric is rolled out. This is
that set, as data.

Each item carries the label the rubric yields and the clause it follows from.
The clause reference is the load-bearing part: an item whose expected label
cannot be traced to a specific rule is not testing the rubric, it is testing
whoever wrote the fixture. Where a case is genuinely ambiguous the expected
label is ``UNCERTAIN`` — that is the rubric working, not the fixture hedging.

The set deliberately includes the failure modes the rubric was written to
catch, rather than ten easy cases: an unsupported citation on plausible
content, a split parameter table, a value divorced from its conditions, and
two cases the rubric explicitly declines to settle.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas.verification import VerificationLabel


@dataclass(frozen=True)
class CalibrationItem:
    """One item in the calibration set.

    Attributes:
        item_id: Stable identifier, so a disagreement can be discussed by name.
        content: The chunk as a verifier sees it.
        citation: The citation attached to the chunk.
        source_says: What the cited location actually contains. Stands in for
            the verifier opening the manual.
        expected: The label the rubric yields.
        rubric_clause: The section of ``docs/verification-rubric.md`` the
            expected label follows from.
    """

    item_id: str
    content: str
    citation: str
    source_says: str
    expected: VerificationLabel
    rubric_clause: str


CALIBRATION_SET: tuple[CalibrationItem, ...] = (
    CalibrationItem(
        item_id="cal-01",
        content="The MCB is rated 63 A at an ambient of 40 °C.",
        citation="ABB S200 catalogue, section 4.2.1, page 27",
        source_says="Section 4.2.1 states: rated current 63 A at 40 °C ambient.",
        expected=VerificationLabel.CORRECT,
        rubric_clause="correct/2 — the cited location states the content",
    ),
    CalibrationItem(
        item_id="cal-02",
        content="The MCB is rated 80 A at an ambient of 40 °C.",
        citation="ABB S200 catalogue, section 4.2.1, page 27",
        source_says="Section 4.2.1 states: rated current 63 A at 40 °C ambient. The row above gives 80 A at 30 °C.",
        expected=VerificationLabel.INCORRECT,
        rubric_clause="incorrect/contradiction — source states 63 A",
    ),
    CalibrationItem(
        item_id="cal-03",
        content="Motor overload protection should be set to 1.15 times full load current.",
        citation="Siemens SIRIUS manual, section 3.4, page 41",
        source_says="Section 3.4 covers mounting and DIN rail clearances. It does not discuss overload settings.",
        expected=VerificationLabel.INCORRECT,
        rubric_clause="incorrect/unsupported citation — plausible content, cited section does not hold it up",
    ),
    CalibrationItem(
        item_id="cal-04",
        content="Rated breaking capacity is 10 kA.",
        citation="Schneider Acti9 guide, section 9.9.9, page 300",
        source_says="The document has eight sections and ends at page 212. Section 9.9.9 does not exist.",
        expected=VerificationLabel.INCORRECT,
        rubric_clause="incorrect/broken citation — cited location does not exist",
    ),
    CalibrationItem(
        item_id="cal-05",
        content=(
            "Ambient derating factors:\n"
            "30 °C — 1.00\n40 °C — 0.87\n45 °C — 0.79\n50 °C — 0.71\n55 °C — 0.61\n60 °C — 0.50"
        ),
        citation="ABB technical guide, table 6, page 88",
        source_says=(
            "Table 6 runs to twelve rows, continuing on page 89 with 65 °C through 90 °C. "
            "The chunk contains the first six rows only."
        ),
        expected=VerificationLabel.INCORRECT,
        rubric_clause="atomic blocks/parameter tables — half a table reads as a whole one",
    ),
    CalibrationItem(
        item_id="cal-06",
        content="4. Tighten the terminal screws to 2.5 Nm.\n5. Refit the cover.\n6. Restore supply.",
        citation="Siemens installation manual, section 7.2, page 55",
        source_says=(
            "Section 7.2 is a nine-step procedure beginning: 1. Isolate the supply. "
            "2. Verify dead. 3. Discharge capacitors."
        ),
        expected=VerificationLabel.INCORRECT,
        rubric_clause="atomic blocks/procedures — steps 4-9 read as a complete procedure",
    ),
    CalibrationItem(
        item_id="cal-07",
        content="The contactor is rated 25 A.",
        citation="Schneider TeSys catalogue, section 2.1, page 14",
        source_says=(
            "Section 2.1 gives 25 A for AC-1 duty at 40 °C, and 9 A for AC-3 duty. "
            "The chunk carries neither the duty class nor the ambient."
        ),
        expected=VerificationLabel.INCORRECT,
        rubric_clause="atomic blocks/a value and its conditions — a rating without its conditions is a number",
    ),
    CalibrationItem(
        item_id="cal-08",
        content="Cable sizing uses the formula I_z >= I_b / (C_a * C_g * C_i).",
        citation="ABB cable sizing guide, section 5.1, page 62",
        source_says="Section 5.1 states exactly this formula with the same three correction factors.",
        expected=VerificationLabel.CORRECT,
        rubric_clause="correct/3 — the method matches the source's method",
    ),
    CalibrationItem(
        item_id="cal-09",
        content="Minimum enclosure protection for outdoor installation is IP54.",
        citation="Schneider enclosure guide, section 8.3, page 71",
        source_says=(
            "Section 8.3 states IP54 for sheltered outdoor use and IP65 for fully exposed. "
            "It does not define which applies absent shelter information."
        ),
        expected=VerificationLabel.UNCERTAIN,
        rubric_clause="uncertain — depends on context outside the chunk that the verifier cannot see",
    ),
    CalibrationItem(
        item_id="cal-10",
        content="Short-circuit withstand is 15 kA for one second.",
        citation="ABB switchgear manual, section 11.4, page 130",
        source_says=(
            "Section 11.4 gives 15 kA for 1 s in a table, while section 11.2 gives 12.5 kA for 1 s "
            "for what appears to be the same model. The document does not reconcile them."
        ),
        expected=VerificationLabel.UNCERTAIN,
        rubric_clause="uncertain — two passages appear to conflict",
    ),
)
