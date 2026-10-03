"""Tests for `app/design/export_ecad.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from io import StringIO

from app.design import designations, export_ecad, motors, profile, project
from app.models.schemas.design import (
    DesignProject,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    ProjectInfo,
)


def _project() -> DesignProject:
    company = profile.default_profile()
    boards = project.design_boards(
        [
            DistributionBoardRequest(
                name="DB1",
                location="HALL",
                loads=[
                    LoadInput(description="Lights", load=LoadKind.LIGHTING, power_kw=Decimal(1)),
                    LoadInput(description="=HYPERLINK(1)", load=LoadKind.DATA, power_kw=Decimal(1)),
                ],
            )
        ],
        company,
    )
    designed = DesignProject(
        info=ProjectInfo(name="T"), boards=boards, parts=motors.parts_for(boards)
    )
    return designations.designate_project(designed, company)


def _rows(text: str, delimiter: str) -> list[list[str]]:
    assert text.startswith("﻿")
    return list(csv.reader(StringIO(text[1:]), delimiter=delimiter))


def test_eplan_device_list_has_dt_full_and_a_row_per_terminal() -> None:
    rows = _rows(export_ecad.eplan_device_list(_project()), ";")
    assert tuple(rows[0]) == export_ecad.EPLAN_COLUMNS
    body = rows[1:]
    designations = [row[0] for row in body]
    # IEC 81346 as EPLAN's own help writes DT (full), not escaped.
    assert "=DB1+HALL-Q1" in designations
    terminals = [row for row in body if row[0].endswith("-X1")]
    assert terminals
    assert all(row[1] for row in terminals)
    # Typed text is escaped so it cannot run as a formula.
    assert "'=HYPERLINK(1)" in [row[5] for row in body]
    assert all(len(row) == len(export_ecad.EPLAN_COLUMNS) for row in body)


def test_ace_component_list_is_28_columns_split_into_inst_loc_tag() -> None:
    rows = _rows(export_ecad.ace_component_list(_project()), ",")
    assert rows
    assert all(len(row) == export_ecad.ACE_COMPONENT_COLUMNS for row in rows)
    incomer = next(row for row in rows if row[0] == "Q1")
    assert (incomer[1], incomer[2]) == ("DB1", "HALL")
    # RATING1 is In.
    assert incomer[14]


def test_ace_terminal_list_is_30_columns_with_the_terminal_number() -> None:
    rows = _rows(export_ecad.ace_terminal_list(_project()), ",")
    assert rows
    assert all(len(row) == export_ecad.ACE_TERMINAL_COLUMNS for row in rows)
    assert {row[0] for row in rows} == {"X1"}
    assert [row[27] for row in rows][:3] == ["1", "2", "3"]
