"""Tests for `app/ai/plc/validation.py`.

The acceptance criterion is a pair, and both halves matter:

> Deliberately broken test code is correctly flagged by the validation layer in
> 100% of a test batch; valid code is not false-flagged.

A validator that flags everything satisfies the first half and is useless. One
that flags nothing satisfies the second half and is worse than useless, because
its silence reads as approval. So the fixture set below runs both directions,
and the parametrised batch is the "100%" — adding a broken case that is not
caught fails here rather than reaching an engineer.
"""

from __future__ import annotations

import pytest

from app.ai.plc.validation import CHECKER, validate_plc_code
from app.models.schemas.plc import FindingSeverity, PlcDialect, ValidationStatus

# --- valid reference code -----------------------------------------------------

MOTOR_START = """PROGRAM MotorStart
VAR_INPUT
    StartButton : BOOL;
    StopButton : BOOL;
    OverloadTrip : BOOL;
END_VAR
VAR_OUTPUT
    MotorRun : BOOL;
END_VAR
    (* Stop and overload both drop the contactor. *)
    IF StartButton AND NOT StopButton AND NOT OverloadTrip THEN
        MotorRun := TRUE;
    ELSIF StopButton OR OverloadTrip THEN
        MotorRun := FALSE;
    END_IF;
END_PROGRAM"""

LEVEL_CONTROL = """PROGRAM LevelControl
VAR_INPUT
    LevelPercent : REAL;
END_VAR
VAR_OUTPUT
    FillValve : BOOL;
    Alarm : BOOL;
END_VAR
VAR
    LowSetpoint : REAL := 20.0;
    HighSetpoint : REAL := 80.0;
END_VAR
    IF LevelPercent < LowSetpoint THEN
        FillValve := TRUE;
    END_IF;
    IF LevelPercent > HighSetpoint THEN
        FillValve := FALSE;
        Alarm := TRUE;
    END_IF;
END_PROGRAM"""

COUNTER_LOOP = """PROGRAM Counting
VAR
    Index : INT;
    Total : INT;
    Step : INT := 1;
END_VAR
    Total := 0;
    FOR Index := 1 TO 10 BY Step DO
        Total := Total + Index;
    END_FOR;
END_PROGRAM"""

WHILE_LOOP = """PROGRAM Draining
VAR_INPUT
    TankFull : BOOL;
END_VAR
VAR_OUTPUT
    DrainValve : BOOL;
END_VAR
    WHILE TankFull DO
        DrainValve := TRUE;
    END_WHILE;
END_PROGRAM"""

COMMENTED = """PROGRAM Documented
VAR
    A : BOOL;
    B : BOOL;
END_VAR
    // A line comment mentioning CASE and ARRAY in prose.
    (* A block comment
       spanning lines. *)
    A := B;
END_PROGRAM"""

VALID_PROGRAMS = {
    "motor start/stop with interlocks": MOTOR_START,
    "level control with setpoints": LEVEL_CONTROL,
    "FOR loop with BY": COUNTER_LOOP,
    "WHILE loop": WHILE_LOOP,
    "comments naming unsupported keywords": COMMENTED,
}

# --- deliberately broken code -------------------------------------------------

BROKEN_PROGRAMS = {
    "missing END_IF": """PROGRAM P
VAR
    A : BOOL;
    B : BOOL;
END_VAR
    IF A THEN
        B := TRUE;
END_PROGRAM""",
    "missing semicolon": """PROGRAM P
VAR
    A : BOOL;
END_VAR
    A := TRUE
END_PROGRAM""",
    "unbalanced parenthesis": """PROGRAM P
VAR
    A : BOOL;
    B : BOOL;
END_VAR
    A := (B AND TRUE;
END_PROGRAM""",
    "missing END_PROGRAM": """PROGRAM P
VAR
    A : BOOL;
END_VAR
    A := TRUE;""",
    "undeclared tag": """PROGRAM P
VAR
    A : BOOL;
END_VAR
    A := Undeclared;
END_PROGRAM""",
    "typo in tag name": """PROGRAM P
VAR_INPUT
    StartButton : BOOL;
END_VAR
VAR_OUTPUT
    MotorRun : BOOL;
END_VAR
    MotorRun := StartButtton;
END_PROGRAM""",
    "boolean into a numeric tag": """PROGRAM P
VAR
    Counter : INT;
END_VAR
    Counter := TRUE;
END_PROGRAM""",
    "number into a boolean tag": """PROGRAM P
VAR
    Enabled : BOOL;
END_VAR
    Enabled := 5;
END_PROGRAM""",
    "unreachable IF branch": """PROGRAM P
VAR
    A : BOOL;
END_VAR
    IF FALSE THEN
        A := TRUE;
    END_IF;
END_PROGRAM""",
    "unreachable WHILE body": """PROGRAM P
VAR
    A : BOOL;
END_VAR
    WHILE FALSE DO
        A := TRUE;
    END_WHILE;
END_PROGRAM""",
    "declared but never used": """PROGRAM P
VAR
    Used : BOOL;
    ForgottenInterlock : BOOL;
END_VAR
    Used := TRUE;
END_PROGRAM""",
    "assignment to an undeclared output": """PROGRAM P
VAR_INPUT
    A : BOOL;
END_VAR
    MissingOutput := A;
END_PROGRAM""",
}

# --- code this checker cannot speak to ----------------------------------------

UNCHECKABLE_PROGRAMS = {
    "CASE statement": """PROGRAM P
VAR
    X : INT;
END_VAR
    CASE X OF
        1: X := 2;
    END_CASE;
END_PROGRAM""",
    "Siemens REGION": """PROGRAM P
VAR
    A : BOOL;
END_VAR
REGION Init
    A := TRUE;
END_REGION
END_PROGRAM""",
    "direct addressing": """PROGRAM P
VAR
    A AT %I0.0 : BOOL;
END_VAR
    A := TRUE;
END_PROGRAM""",
    "function block": """FUNCTION_BLOCK Motor
VAR
    A : BOOL;
END_VAR
    A := TRUE;
END_FUNCTION_BLOCK""",
    "array declaration": """PROGRAM P
VAR
    Buffer : ARRAY[1..10] OF INT;
END_VAR
    Buffer := 0;
END_PROGRAM""",
    "vendor literal syntax": """PROGRAM P
VAR
    X : INT;
END_VAR
    X := 16#FF;
END_PROGRAM""",
    "REPEAT loop": """PROGRAM P
VAR
    X : INT;
END_VAR
    REPEAT
        X := X + 1;
    UNTIL X > 10
    END_REPEAT;
END_PROGRAM""",
}


# --- half one: valid code is not false-flagged --------------------------------


@pytest.mark.parametrize("source", VALID_PROGRAMS.values(), ids=VALID_PROGRAMS.keys())
def test_valid_code_is_not_false_flagged(source: str) -> None:
    # The half that decides whether anyone keeps the validator switched on. A
    # false error on correct code teaches an engineer to stop reading the
    # output, after which the true errors go unread too.
    result = validate_plc_code(source)

    assert result.status is ValidationStatus.VALID, [f.message for f in result.findings]
    assert not [f for f in result.findings if f.severity is FindingSeverity.ERROR]
    assert result.ready


def test_a_comment_mentioning_an_unsupported_keyword_does_not_block_checking() -> None:
    # Comments are stripped before the unsupported-construct scan. Otherwise a
    # program documenting "handles the CASE where..." would be reported as
    # unverifiable, which is both wrong and baffling to whoever reads it.
    result = validate_plc_code(COMMENTED)

    assert result.status is ValidationStatus.VALID


# --- half two: every broken program is flagged --------------------------------


@pytest.mark.parametrize("source", BROKEN_PROGRAMS.values(), ids=BROKEN_PROGRAMS.keys())
def test_broken_code_is_always_flagged(source: str) -> None:
    # "100% of a test batch". Every case here must produce at least one
    # finding; a broken program that comes back with nothing to say is the
    # failure mode that puts wrong code in front of an engineer with a tick
    # next to it.
    result = validate_plc_code(source)

    assert result.findings, "broken code produced no findings at all"


@pytest.mark.parametrize(
    "source",
    [
        BROKEN_PROGRAMS["missing END_IF"],
        BROKEN_PROGRAMS["missing semicolon"],
        BROKEN_PROGRAMS["unbalanced parenthesis"],
        BROKEN_PROGRAMS["missing END_PROGRAM"],
        BROKEN_PROGRAMS["undeclared tag"],
        BROKEN_PROGRAMS["typo in tag name"],
        BROKEN_PROGRAMS["boolean into a numeric tag"],
        BROKEN_PROGRAMS["number into a boolean tag"],
        BROKEN_PROGRAMS["assignment to an undeclared output"],
    ],
    ids=[
        "missing END_IF",
        "missing semicolon",
        "unbalanced parenthesis",
        "missing END_PROGRAM",
        "undeclared tag",
        "typo in tag name",
        "boolean into a numeric tag",
        "number into a boolean tag",
        "assignment to an undeclared output",
    ],
)
def test_code_that_will_not_run_is_never_ready(source: str) -> None:
    # These are the cases that do not compile or do not mean what they say.
    # They must block `ready`, not merely be mentioned.
    result = validate_plc_code(source)

    assert result.status is ValidationStatus.INVALID
    assert not result.ready


@pytest.mark.parametrize(
    "source",
    [
        BROKEN_PROGRAMS["unreachable IF branch"],
        BROKEN_PROGRAMS["unreachable WHILE body"],
        BROKEN_PROGRAMS["declared but never used"],
    ],
    ids=["unreachable IF", "unreachable WHILE", "unused tag"],
)
def test_suspicious_but_legal_code_warns_rather_than_fails(source: str) -> None:
    # Reported, but not as errors. Unreachable code and unused tags compile,
    # and an output left unwired may be deliberate during commissioning.
    # Failing them outright would be a false alarm on legal code.
    result = validate_plc_code(source)

    assert result.findings
    assert all(f.severity is FindingSeverity.WARNING for f in result.findings)


def test_a_syntax_error_reports_where_it_is() -> None:
    # A validator that says "invalid" without saying where sends an engineer
    # hunting through a program they did not write.
    result = validate_plc_code(BROKEN_PROGRAMS["missing END_IF"])

    assert result.findings[0].code == "syntax-error"
    assert result.findings[0].line is not None


def test_a_typo_names_the_symbol_it_could_not_resolve() -> None:
    result = validate_plc_code(BROKEN_PROGRAMS["typo in tag name"])

    undeclared = [f for f in result.findings if f.code == "undeclared-tag"]
    assert "StartButtton" in undeclared[0].message


def test_an_undeclared_tag_is_not_also_reported_as_a_type_mismatch() -> None:
    # Saying the same problem twice in different words makes a report harder to
    # act on, not more thorough.
    result = validate_plc_code(BROKEN_PROGRAMS["undeclared tag"])

    assert not [f for f in result.findings if f.code == "type-mismatch"]


# --- the third answer: could not check ----------------------------------------


@pytest.mark.parametrize("source", UNCHECKABLE_PROGRAMS.values(), ids=UNCHECKABLE_PROGRAMS.keys())
def test_unsupported_constructs_report_incomplete_never_valid(source: str) -> None:
    # AI-009's stated edge case, and the property the whole design turns on:
    # "an unverifiable result is not the same as a verified-correct one."
    #
    # These programs may well be perfectly correct. This checker cannot say,
    # and saying so is the honest answer — the dishonest one is a pass.
    result = validate_plc_code(source, dialect=PlcDialect.SIEMENS_SCL)

    assert result.status is ValidationStatus.INCOMPLETE
    assert not result.ready


def test_incomplete_says_what_it_could_not_handle() -> None:
    # So the gap is addressable rather than mysterious. "Validation incomplete"
    # with no reason gives nobody anything to fix.
    result = validate_plc_code(UNCHECKABLE_PROGRAMS["CASE statement"])

    assert "CASE" in result.findings[0].message


def test_empty_source_is_incomplete_not_valid() -> None:
    # Nothing to check is not the same as checked and fine. An empty response
    # from a generator must not come back wearing a tick.
    result = validate_plc_code("   \n  ")

    assert result.status is ValidationStatus.INCOMPLETE
    assert not result.ready


def test_incomplete_is_reported_as_a_warning_not_an_error() -> None:
    # The code is not known to be wrong. Marking it as an error would report a
    # defect that has not been found.
    result = validate_plc_code(UNCHECKABLE_PROGRAMS["function block"])

    assert all(f.severity is FindingSeverity.WARNING for f in result.findings)


# --- the verdict names its source ---------------------------------------------


def test_the_result_names_what_did_the_checking() -> None:
    # So a reader can judge what the verdict is worth. "Checked" means little
    # without knowing whether a parser or a language model did it — and this
    # task exists because those are not equivalent.
    result = validate_plc_code(MOTOR_START)

    assert result.checked_by == CHECKER
    assert "lark" in result.checked_by


def test_the_result_records_which_dialect_was_assumed() -> None:
    result = validate_plc_code(MOTOR_START, dialect=PlcDialect.ROCKWELL_ST)

    assert result.dialect is PlcDialect.ROCKWELL_ST


def test_ready_is_false_whenever_an_error_was_found() -> None:
    result = validate_plc_code(BROKEN_PROGRAMS["undeclared tag"])

    assert not result.ready


def test_ready_survives_warnings_alone() -> None:
    # A warning is a note, not a blocker. Requiring a clean sheet would mean an
    # unused spare tag stops a correct program from being usable.
    result = validate_plc_code(BROKEN_PROGRAMS["declared but never used"])

    assert result.status is ValidationStatus.VALID
    assert result.ready
