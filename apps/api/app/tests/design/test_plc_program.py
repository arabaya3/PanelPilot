"""Tests for `app/design/plc_program.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

from app.ai.plc.validation import validate_plc_code
from app.design import designations, distribution, plc_program, profile
from app.models.schemas.design import (
    DesignProject,
    DeviceKind,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    ProjectInfo,
)
from app.models.schemas.plc import ValidationStatus


def _project(*, controlled: bool) -> DesignProject:
    loads = [
        LoadInput(
            description=f"Lighting {i} (*east*)",
            load=LoadKind.LIGHTING,
            power_kw=Decimal("0.6"),
            controlled=controlled,
        )
        for i in range(1, 3)
    ]
    loads.append(LoadInput(description="Sockets", load=LoadKind.SOCKET, power_kw=Decimal("1.5")))
    board = distribution.design_distribution_board(
        DistributionBoardRequest(name="DBG-HALL", location="HALL", loads=loads),
        profile.default_profile(),
    )
    return designations.designate_project(
        DesignProject(info=ProjectInfo(name="Pocket Project"), boards=[board]),
        profile.default_profile(),
    )


def test_no_program_without_a_controlled_circuit() -> None:
    assert plc_program.build_program(_project(controlled=False)) is None


def test_every_contactor_gets_a_coil_and_a_manual_input() -> None:
    project = _project(controlled=True)
    contactors = [d for d in project.boards[0].devices if d.kind is DeviceKind.CONTACTOR]
    program = plc_program.build_program(project)
    assert program is not None
    assert len(contactors) == 2
    outputs = [p for p in program.io if p.direction == "output"]
    inputs = [p.tag for p in program.io if p.direction == "input"]
    assert len(outputs) == 2
    assert inputs[:3] == ["EStop_OK", "Auto_Mode", "Schedule_On"]
    assert len(inputs) == 5
    for point in outputs:
        manual = point.tag.replace("K_", "Man_", 1)
        assert (
            f"{point.tag} := EStop_OK AND ({manual} OR (Auto_Mode AND Schedule_On));"
            in program.source
        )
    assert program.name == "Pocket_Project_Control"


def test_the_program_passes_the_checker() -> None:
    program = plc_program.build_program(_project(controlled=True))
    assert program is not None
    # A description holding a comment closer must not break the comment.
    assert "(*east* )" in program.source
    verdict = validate_plc_code(program.source)
    assert verdict.status is ValidationStatus.VALID, verdict.findings


def test_io_list_csv() -> None:
    program = plc_program.build_program(_project(controlled=True))
    assert program is not None
    text = plc_program.io_list_csv(program)
    assert text.startswith("﻿Tag,Direction,Type,Board,Device,Description,Address\r\n")
    assert text.count("\r\n") == 1 + len(program.io)
    assert ",output,BOOL,DBG-HALL," in text


def test_a_description_in_arabic_is_left_to_the_io_list() -> None:
    project = _project(controlled=True)
    for circuit in project.boards[0].circuits:
        circuit.description = "إنارة"
    program = plc_program.build_program(project)
    assert program is not None
    assert "?" not in program.source
    assert "=DBG-HALL" in program.source
    assert "إنارة" in plc_program.io_list_csv(program)
    assert validate_plc_code(program.source).status is ValidationStatus.VALID


def _motor_project() -> DesignProject:
    from app.design import motors
    from app.design import project as project_design
    from app.models.schemas.design import MotorStarter

    loads = [
        LoadInput(
            description=name,
            load=LoadKind.MOTOR,
            power_kw=Decimal(kw),
            phases=3,
            starter=starter,
        )
        for name, kw, starter in (
            ("Pump", "7.5", MotorStarter.DIRECT_ON_LINE),
            ("Fan", "30", MotorStarter.STAR_DELTA),
            ("Conveyor", "15", MotorStarter.DRIVE),
        )
    ]
    boards = project_design.design_boards(
        [DistributionBoardRequest(name="MCC", loads=loads)], profile.default_profile()
    )
    return designations.designate_project(
        DesignProject(
            info=ProjectInfo(name="Plant"), boards=boards, parts=motors.parts_for(boards)
        ),
        profile.default_profile(),
    )


def test_motors_get_their_starters_logic_and_the_checker_passes_it() -> None:
    program = plc_program.build_program(_motor_project())
    assert program is not None
    tags = {point.tag: point for point in program.io}
    # Direct on line: start, stop, overload in; one coil out.
    assert {"Start_MCC_Q3", "Stop_MCC_Q3", "OL_MCC_Q3", "K_MCC_Q3"} <= set(tags)
    assert tags["OL_MCC_Q3"].device.endswith("-F1")
    # Star-delta: line, star and delta coils, timed and interlocked.
    assert {"KM_MCC_Q5", "KY_MCC_Q5", "KD_MCC_Q5"} <= set(tags)
    assert "TStar_MCC_Q5(IN := Run_MCC_Q5, PT := T#6S);" in program.source
    assert "KY_MCC_Q5 := Run_MCC_Q5 AND NOT TStar_MCC_Q5.Q AND NOT KD_MCC_Q5;" in program.source
    assert "KD_MCC_Q5 := Run_MCC_Q5 AND TGap_MCC_Q5.Q AND NOT KY_MCC_Q5;" in program.source
    # Drive: run command gated by its ready signal.
    assert "Run_MCC_T1 := EStop_OK AND Stop_MCC_T1 AND Ready_MCC_T1" in program.source
    verdict = validate_plc_code(program.source)
    assert verdict.status is ValidationStatus.VALID, verdict.findings
    assert not verdict.findings


def test_every_motor_output_drops_on_emergency_stop() -> None:
    program = plc_program.build_program(_motor_project())
    assert program is not None
    for line in program.source.splitlines():
        if line.startswith(("K_", "Run_")) and ":=" in line:
            assert "EStop_OK AND" in line
