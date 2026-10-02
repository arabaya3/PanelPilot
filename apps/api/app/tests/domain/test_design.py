"""Tests for `app/domain/design.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationError
from app.domain import design
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.design import (
    BoardDesignRequest,
    DesignExportRequest,
    DistributionBoardRequest,
    ExportFormat,
    LoadInput,
    LoadKind,
    ProjectInfo,
)

USER = CurrentUser(id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER}))


def _request(profile: dict[str, object] | None = None) -> BoardDesignRequest:
    return BoardDesignRequest(
        info=ProjectInfo(name="Pocket Project / Hall"),
        board=DistributionBoardRequest(
            name="DBG-HALL",
            loads=[
                LoadInput(description=f"S{i}", load=LoadKind.SOCKET, power_kw=Decimal(1))
                for i in range(4)
            ],
        ),
        profile=profile,
    )


def test_a_board_is_designed_and_designated() -> None:
    response = design.design_board(session=None, user=USER, request=_request())  # type: ignore[arg-type]
    board = response.project.boards[0]
    assert all(d.designation is not None for d in board.devices)
    assert response.profile.key == "iec-default"


def test_a_company_profile_is_applied() -> None:
    response = design.design_board(
        session=None,  # type: ignore[arg-type]
        user=USER,
        request=_request({"key": "acme", "letters": {"circuit_breaker": "QF"}}),
    )
    assert response.project.profile == "acme"
    assert response.project.boards[0].devices[0].designation.product == "QF1"  # type: ignore[union-attr]


def test_a_bad_profile_is_refused() -> None:
    with pytest.raises(ValidationError, match="unknown profile settings"):
        design.design_board(
            session=None, user=USER, request=_request({"key": "x", "nope": 1})  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("fmt", "media", "start", "suffix"),
    [
        (ExportFormat.PDF, "application/pdf", b"%PDF", ".pdf"),
        (ExportFormat.DXF, "application/dxf", b"0\nSECTION", ".dxf"),
        (ExportFormat.QET, "application/xml", b"<?xml", ".qet"),
        (ExportFormat.AML, "application/xml", b"<?xml", ".aml"),
        (ExportFormat.DEVICES_CSV, "text/csv; charset=utf-8", "﻿Board".encode(), ".devices.csv"),
        (ExportFormat.PARTS_CSV, "text/csv; charset=utf-8", "﻿Quantity".encode(), ".parts.csv"),
        (ExportFormat.CABLES_CSV, "text/csv; charset=utf-8", "﻿Board".encode(), ".cables.csv"),
        (ExportFormat.CIRCUITS_CSV, "text/csv; charset=utf-8", "﻿Board".encode(), ".circuits.csv"),
        (ExportFormat.JSON, "application/json", b"{", ".json"),
    ],
)
def test_every_format_exports(fmt: ExportFormat, media: str, start: bytes, suffix: str) -> None:
    designed = design.design_board(session=None, user=USER, request=_request())  # type: ignore[arg-type]
    exported = design.export_design(
        session=None,  # type: ignore[arg-type]
        user=USER,
        request=DesignExportRequest(project=designed.project, format=fmt),
    )
    assert exported.media_type == media
    assert exported.content.startswith(start)
    assert exported.filename == f"Pocket-Project-Hall{suffix}"


def test_an_edited_project_is_renumbered_on_export() -> None:
    designed = design.design_board(session=None, user=USER, request=_request())  # type: ignore[arg-type]
    board = designed.project.boards[0]
    # An engineer drops the first circuit before exporting.
    edited = designed.project.model_copy(
        update={"boards": [board.model_copy(update={"circuits": board.circuits[1:]})]}
    )
    exported = design.export_design(
        session=None,  # type: ignore[arg-type]
        user=USER,
        request=DesignExportRequest(project=edited, format=ExportFormat.JSON),
    )
    assert b'"product": "Q1"' in exported.content


def test_a_schedule_file_is_imported() -> None:
    result = design.import_load_schedule(
        user=USER, data=b"Description,kW,Phase\nSockets hall,1.5,1\nLights,0.6,1\n"
    )
    assert [load.description for load in result.loads] == ["Sockets hall", "Lights"]
    assert result.rows_read == 2
    assert design.MAX_SCHEDULE_BYTES == 5 * 1024 * 1024


def test_a_suggestion_is_split_under_the_company_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai import schedule_writer
    from app.models.schemas.design import (
        ScheduleSuggestionOutput,
        ScheduleSuggestionRequest,
        SuggestedPoints,
    )

    drafted = ScheduleSuggestionOutput(
        items=[
            SuggestedPoints(
                description="Sockets",
                load=LoadKind.SOCKET,
                quantity=10,
                unit_power_kw=Decimal("0.15"),
                three_phase=False,
                assumption="typical",
            )
        ],
        assumptions=["No diversity applied."],
    )

    def write(request: ScheduleSuggestionRequest) -> ScheduleSuggestionOutput:
        del request
        return drafted

    monkeypatch.setattr(schedule_writer, "write_schedule", write)
    result = design.suggest_load_schedule(
        user=USER,
        request=ScheduleSuggestionRequest(
            description="hall", profile={"key": "acme", "max_points_per_circuit": {"socket": 5}}
        ),
    )
    assert [load.description for load in result.loads] == ["Sockets 1", "Sockets 2"]
    assert result.assumptions[0] == "No diversity applied."
    assert result.assumptions[1] == "Sockets 1: 5 x 150 W = 0.75 kW. typical"
