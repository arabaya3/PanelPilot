"""Tests for `app/design/calc_report.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pdfplumber

from app.design import calc_report, designations, motors, profile, project
from app.models.schemas.design import (
    DesignProject,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    ProjectInfo,
    Revision,
    Supply,
)


def _project(*revisions: Revision) -> DesignProject:
    company = profile.default_profile()
    boards = project.design_boards(
        [
            DistributionBoardRequest(
                name="MDB",
                supply=Supply(fault_level_ka=Decimal(25), earth_loop_ohm=Decimal("0.35")),
                loads=[
                    LoadInput(
                        description="Server room",
                        load=LoadKind.DATA,
                        power_kw=Decimal(3),
                        length_m=Decimal(45),
                    )
                ],
            )
        ],
        company,
    )
    designed = DesignProject(
        info=ProjectInfo(name="Tower", revisions=list(revisions)),
        boards=boards,
        parts=motors.parts_for(boards),
    )
    return designations.designate_project(designed, company)


def _text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def test_render_calculations_pdf_lists_every_circuit_and_its_figures() -> None:
    approved = Revision(index="01", date="2026-10-02", approved_by="A. Rabaya")
    data = calc_report.render_calculations_pdf(_project(approved), profile.default_profile())
    assert data.startswith(b"%PDF")
    text = _text(data)
    assert "Calculations report: Tower" in text
    assert "Approved by A. Rabaya" in text
    assert "Board MDB" in text
    assert "Server room" in text
    # The breaker's C16 and the most Zs it allows at U0 231 V.
    assert "C16" in text
    assert "1.372" in text
    assert "IEC 60364-4-41" in text


def test_an_unapproved_report_says_so() -> None:
    text = _text(calc_report.render_calculations_pdf(_project(), profile.default_profile()))
    assert "NOT APPROVED" in text
