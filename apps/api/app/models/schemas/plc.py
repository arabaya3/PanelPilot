"""Schemas for PLC code generation and validation.

Ladder is carried as a structured rung representation rather than as text.
FE-009 has to draw it, and a diagram reconstructed by parsing prose is a
diagram that can silently disagree with what the generator meant.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class PlcDialect(StrEnum):
    """Which vendor's flavour a request targets.

    Vendor matters because the dialects genuinely differ — Siemens SCL is not
    Rockwell's ST, and code valid in one is not always valid in the other. A
    validator that ignored the distinction would be reporting on a language
    nobody actually runs.
    """

    IEC_61131_3 = "iec-61131-3"
    SIEMENS_SCL = "siemens-scl"
    ROCKWELL_ST = "rockwell-st"
    CODESYS_ST = "codesys-st"


class PlcLanguage(StrEnum):
    """What form the output takes."""

    STRUCTURED_TEXT = "structured-text"
    LADDER = "ladder"


class ValidationStatus(StrEnum):
    """The outcome of a validation pass.

    ``INCOMPLETE`` is the load-bearing member and the reason this is not a
    boolean. AI-009 is explicit: tooling that cannot fully parse a dialect
    must say so rather than pass. An unverifiable result and a verified-correct
    one are different things, and collapsing them is how unchecked code
    acquires a tick.
    """

    #: Parsed, and no problems found.
    VALID = "valid"

    #: Parsed, and problems found.
    INVALID = "invalid"

    #: Could not be checked. Not a pass and not a failure.
    INCOMPLETE = "incomplete"


class FindingSeverity(StrEnum):
    """How much a finding matters."""

    #: The code will not compile, or will not do what it says.
    ERROR = "error"

    #: Suspicious but not provably wrong.
    WARNING = "warning"


class ValidationFinding(BaseModel):
    """One problem found in a piece of code.

    Attributes:
        code: Stable machine-readable identifier, so a UI can group findings
            without matching on prose.
        message: What is wrong, in the terms an engineer would use.
        severity: Whether this blocks a ``ready`` status.
        line: Where, when known. Absent for whole-program findings.
    """

    code: str
    message: str
    severity: FindingSeverity
    line: int | None = None


class PlcValidationResult(BaseModel):
    """The outcome of validating one piece of code.

    Attributes:
        status: Valid, invalid, or unverifiable.
        findings: What was found. May be non-empty on a ``VALID`` result when
            every finding is a warning.
        dialect: What it was checked as.
        checked_by: What did the checking, named so a reader can judge how
            much the result is worth.
    """

    status: ValidationStatus
    findings: list[ValidationFinding] = Field(default_factory=list)
    dialect: PlcDialect
    checked_by: str

    @property
    def ready(self) -> bool:
        """Whether this code may carry a ``ready`` status.

        Returns:
            ``True`` only when validation completed and found no errors.

        Deliberately false for ``INCOMPLETE``. The whole point of that status
        is that it is not a pass.
        """
        return self.status is ValidationStatus.VALID and not any(
            finding.severity is FindingSeverity.ERROR for finding in self.findings
        )


class LadderContact(BaseModel):
    """One contact or coil on a rung.

    Attributes:
        tag: The symbol it reads or writes.
        kind: ``no`` (normally open), ``nc`` (normally closed), or ``coil``.
    """

    tag: str
    kind: str


class LadderRung(BaseModel):
    """One rung, as FE-009 draws it.

    Attributes:
        comment: What the rung is for.
        inputs: Contacts in series, left to right.
        output: The coil the rung drives.
    """

    comment: str
    inputs: list[LadderContact]
    output: LadderContact


class PlcGenerationRequest(BaseModel):
    """A request to generate PLC code."""

    description: str = Field(min_length=1, max_length=4000)
    dialect: PlcDialect = PlcDialect.IEC_61131_3
    language: PlcLanguage = PlcLanguage.STRUCTURED_TEXT


class PlcGenerationResult(BaseModel):
    """Generated code, with the verdict on it.

    Attributes:
        language: What was produced.
        dialect: What it targets.
        source: Structured Text, when that is what was asked for.
        rungs: Ladder rungs, when that is what was asked for.
        validation: The validation pass over the generated code. Always
            present — generation that skipped validation would be exactly the
            plausible-looking unchecked output this task exists to prevent.
    """

    language: PlcLanguage
    dialect: PlcDialect
    source: str | None = None
    rungs: list[LadderRung] = Field(default_factory=list)
    validation: PlcValidationResult


class PlcValidationRequest(BaseModel):
    """A request to validate code the caller already has.

    Separate from generation because BE-010's review endpoint validates code
    an engineer wrote, where no generation happens at all.
    """

    source: str = Field(min_length=1, max_length=100_000)
    dialect: PlcDialect = PlcDialect.IEC_61131_3
