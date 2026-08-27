"""Tests for `app/ai/plc/generation.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The writers are injected stubs rather than model calls. That is not only for
speed: the property under test is that generation *cannot* return code without
a verdict on it, and stubbing the writer lets a test hand generation
deliberately broken output and check what comes back. A real model would give
correct code most of the time, which is the case that proves least.
"""

from __future__ import annotations

import pytest

from app.ai.plc.generation import (
    GenerationError,
    generate_plc_code,
    render_rungs_as_st,
)
from app.models.schemas.plc import (
    LadderBlock,
    LadderBranch,
    LadderContact,
    LadderRung,
    PlcDialect,
    PlcGenerationRequest,
    PlcLanguage,
    ValidationStatus,
)

GOOD_ST = """PROGRAM MotorStart
VAR_INPUT
    StartButton : BOOL;
END_VAR
VAR_OUTPUT
    MotorRun : BOOL;
END_VAR
    IF StartButton THEN
        MotorRun := TRUE;
    END_IF;
END_PROGRAM"""

BROKEN_ST = """PROGRAM Broken
VAR
    A : BOOL;
END_VAR
    A := Undeclared;
END_PROGRAM"""


def _request(**overrides: object) -> PlcGenerationRequest:
    """Build a generation request.

    Args:
        **overrides: Fields to replace.

    Returns:
        The request.
    """
    fields: dict[str, object] = {
        "description": "start a motor when the start button is pressed",
        "dialect": PlcDialect.IEC_61131_3,
        "language": PlcLanguage.STRUCTURED_TEXT,
    }
    fields.update(overrides)
    return PlcGenerationRequest(**fields)


def _rungs() -> list[LadderRung]:
    """Build a two-rung ladder.

    Returns:
        The rungs.
    """
    return [
        LadderRung(
            comment="Run while start is held and stop is clear",
            elements=[
                LadderContact(tag="StartButton", kind="no"),
                LadderContact(tag="StopButton", kind="nc"),
            ],
            output=LadderContact(tag="MotorRun", kind="coil"),
        ),
        LadderRung(
            comment="Lamp follows the motor",
            elements=[LadderContact(tag="MotorRun", kind="no")],
            output=LadderContact(tag="RunLamp", kind="coil"),
        ),
    ]


# --- generation always carries a verdict --------------------------------------


def test_generated_code_comes_back_validated() -> None:
    result = generate_plc_code(_request(), write_source=lambda _: GOOD_ST)

    assert result.source == GOOD_ST
    assert result.validation.status is ValidationStatus.VALID
    assert result.validation.ready


def test_broken_generated_code_is_not_marked_ready() -> None:
    # The whole point of the task. A model producing plausible-looking code
    # with a typo'd tag must not have that code come back wearing a tick.
    result = generate_plc_code(_request(), write_source=lambda _: BROKEN_ST)

    assert result.validation.status is ValidationStatus.INVALID
    assert not result.validation.ready


def test_generation_cannot_return_without_a_verdict() -> None:
    # Structural, not a convention someone must remember: the result model
    # requires a validation, so no code path returns generated code with none.
    result = generate_plc_code(_request(), write_source=lambda _: GOOD_ST)

    assert result.validation is not None
    assert result.validation.checked_by


def test_the_validator_is_not_the_writer() -> None:
    # AI-009's central instruction: something other than the model that wrote
    # the code decides whether it is sound. Here the writer emits code it would
    # presumably vouch for, and the parser disagrees.
    saw: list[str] = []

    def writer(_: PlcGenerationRequest) -> str:
        saw.append("wrote")
        return BROKEN_ST

    result = generate_plc_code(_request(), write_source=writer)

    assert saw == ["wrote"]
    assert not result.validation.ready


def test_the_requested_dialect_reaches_the_validator() -> None:
    seen: list[PlcDialect] = []

    def spy(source: str, dialect: PlcDialect) -> object:
        del source
        seen.append(dialect)
        from app.ai.plc.validation import validate_plc_code

        return validate_plc_code(GOOD_ST, dialect=dialect)

    generate_plc_code(
        _request(dialect=PlcDialect.SIEMENS_SCL),
        write_source=lambda _: GOOD_ST,
        validate=spy,  # type: ignore[arg-type]
    )

    assert seen == [PlcDialect.SIEMENS_SCL]


def test_a_structured_text_request_without_a_writer_is_refused() -> None:
    # Refused rather than returning empty output that would then validate as
    # INCOMPLETE and look like a dialect problem.
    with pytest.raises(GenerationError, match="source writer"):
        generate_plc_code(_request())


# --- ladder -------------------------------------------------------------------


def test_ladder_comes_back_as_rungs_not_text() -> None:
    # FE-009 draws these. A diagram reconstructed by parsing prose can silently
    # disagree with what the generator meant.
    result = generate_plc_code(
        _request(language=PlcLanguage.LADDER),
        write_ladder=lambda _: _rungs(),
    )

    assert result.source is None
    assert len(result.rungs) == 2
    assert result.rungs[0].output.tag == "MotorRun"


def test_ladder_is_validated_too() -> None:
    # Ladder gets the same parser-backed check as ST, via its ST equivalent,
    # rather than a weaker check or none at all.
    result = generate_plc_code(
        _request(language=PlcLanguage.LADDER),
        write_ladder=lambda _: _rungs(),
    )

    assert result.validation.status is ValidationStatus.VALID
    assert result.validation.ready


def test_a_ladder_request_without_a_writer_is_refused() -> None:
    with pytest.raises(GenerationError, match="ladder writer"):
        generate_plc_code(_request(language=PlcLanguage.LADDER))


def test_a_normally_closed_contact_becomes_a_negation() -> None:
    # What an NC contact actually does. Rendering it as a plain read would
    # invert the logic while still parsing, which is the quiet kind of wrong.
    source = render_rungs_as_st(_rungs(), program_name="Test")

    assert "NOT StopButton" in source
    assert "StartButton AND NOT StopButton" in source


def test_every_rung_tag_is_declared_in_the_rendering() -> None:
    # Otherwise the ST equivalent would fail validation for undeclared tags and
    # report a defect in the renderer as a defect in the ladder.
    source = render_rungs_as_st(_rungs(), program_name="Test")

    for tag in ("StartButton", "StopButton", "MotorRun", "RunLamp"):
        assert f"{tag} : BOOL;" in source


def test_a_tag_used_twice_is_declared_once() -> None:
    # MotorRun is a coil on one rung and a contact on the next. Declaring it
    # twice would not compile.
    source = render_rungs_as_st(_rungs(), program_name="Test")

    assert source.count("MotorRun : BOOL;") == 1


def test_the_rung_comment_survives_into_the_rendering() -> None:
    source = render_rungs_as_st(_rungs(), program_name="Test")

    assert "(* Run while start is held and stop is clear *)" in source


def test_a_rung_output_that_is_not_a_coil_is_refused() -> None:
    # A rung drives a coil. An output contact is a malformed rung, and
    # rendering it anyway would produce ST that validates while meaning
    # something the ladder never said.
    rungs = [
        LadderRung(
            comment="malformed",
            elements=[LadderContact(tag="A", kind="no")],
            output=LadderContact(tag="B", kind="no"),
        )
    ]

    with pytest.raises(GenerationError, match="not a coil"):
        render_rungs_as_st(rungs, program_name="Test")


def test_an_unknown_contact_kind_is_refused() -> None:
    rungs = [
        LadderRung(
            comment="malformed",
            elements=[LadderContact(tag="A", kind="sometimes")],
            output=LadderContact(tag="B", kind="coil"),
        )
    ]

    with pytest.raises(GenerationError, match="unknown kind"):
        render_rungs_as_st(rungs, program_name="Test")


def test_a_rung_with_no_inputs_drives_its_coil_unconditionally() -> None:
    # An always-on coil is legal ladder — an enable bit, a permanently
    # energised lamp. It must render as something that parses rather than an
    # empty condition.
    rungs = [
        LadderRung(
            comment="always on",
            elements=[],
            output=LadderContact(tag="Enable", kind="coil"),
        )
    ]

    source = render_rungs_as_st(rungs, program_name="Test")

    assert "Enable := TRUE;" in source


# --- branches and blocks ------------------------------------------------------


def _seal_in() -> list[LadderRung]:
    """Build the most common rung in the trade: a start/stop seal-in.

    Returns:
        One rung.
    """
    return [
        LadderRung(
            comment="Seal in around the start button",
            elements=[
                LadderBranch(
                    paths=[
                        [LadderContact(tag="StartButton", kind="no")],
                        [LadderContact(tag="MotorRun", kind="no")],
                    ]
                ),
                LadderContact(tag="StopButton", kind="nc"),
            ],
            output=LadderContact(tag="MotorRun", kind="coil"),
        )
    ]


def test_a_parallel_branch_becomes_a_disjunction() -> None:
    # Series is AND, parallel is OR. A renderer that flattened a branch into a
    # series would turn a seal-in — the circuit that latches a motor on — into
    # one that only runs while the button is held.
    source = render_rungs_as_st(_seal_in(), program_name="Seal")

    assert "(StartButton OR MotorRun)" in source


def test_a_branch_is_parenthesised_against_the_series_that_follows() -> None:
    # Without the parentheses this reads `StartButton OR (MotorRun AND NOT
    # StopButton)`, which latches on and never stops — the exact failure the
    # stop button exists to prevent.
    source = render_rungs_as_st(_seal_in(), program_name="Seal")

    assert "(StartButton OR MotorRun) AND NOT StopButton" in source


def test_a_seal_in_rung_validates() -> None:
    # End to end: the most common real rung renders to ST that the parser
    # accepts. A branch that produced unparseable output would report the
    # ladder as invalid when the fault was in the renderer.
    from app.ai.plc.validation import validate_plc_code

    result = validate_plc_code(render_rungs_as_st(_seal_in(), program_name="Seal"))

    assert result.status is ValidationStatus.VALID


def test_a_function_block_is_read_as_its_output_tag() -> None:
    # A timer is state over time and no expression captures it. What the rung
    # downstream actually does is read the block's output, which is what this
    # renders — and the docstring says plainly that the block's own correctness
    # is not checked, rather than implying it is.
    rungs = [
        LadderRung(
            comment="Confirm at speed after a delay",
            elements=[
                LadderContact(tag="MotorRun", kind="no"),
                LadderBlock(kind="TON", tag="StartDelay", parameters={"PT": "T#5s"}),
            ],
            output=LadderContact(tag="AtSpeed", kind="coil"),
        )
    ]

    source = render_rungs_as_st(rungs, program_name="Delay")

    assert "AtSpeed := MotorRun AND StartDelay;" in source
    assert "StartDelay : BOOL;" in source


def test_tags_inside_a_branch_are_declared() -> None:
    # The walk has to descend into branches. Missing them would leave the tags
    # undeclared and the ST would fail validation for a defect in this
    # renderer rather than in the ladder.
    source = render_rungs_as_st(_seal_in(), program_name="Seal")

    for tag in ("StartButton", "MotorRun", "StopButton"):
        assert f"{tag} : BOOL;" in source


def test_a_nested_branch_still_renders() -> None:
    # Branches within branches are legal and do occur — two alternative
    # permissives, one of which is itself a pair of alternatives.
    rungs = [
        LadderRung(
            comment="nested",
            elements=[
                LadderBranch(
                    paths=[
                        [
                            LadderBranch(
                                paths=[
                                    [LadderContact(tag="A", kind="no")],
                                    [LadderContact(tag="B", kind="no")],
                                ]
                            )
                        ],
                        [LadderContact(tag="C", kind="no")],
                    ]
                )
            ],
            output=LadderContact(tag="Out", kind="coil"),
        )
    ]

    source = render_rungs_as_st(rungs, program_name="Nested")

    assert "((A OR B) OR C)" in source
