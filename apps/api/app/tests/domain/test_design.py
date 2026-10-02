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
    DesignProject,
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
                power_stated=False,
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
    assert result.assumptions[0].code == "text"
    assert result.assumptions[0].text == "No diversity applied."
    assert result.assumptions[1].text == "Sockets 1: 5 x 150 W = 0.75 kW. typical"


def test_a_design_is_priced_and_exported_as_a_quotation() -> None:
    from app.models.schemas.design import PriceListEntry, PricingSettings, QuotationRequest

    designed = design.design_board(session=None, user=USER, request=_request())  # type: ignore[arg-type]
    pricing = PricingSettings(
        price_list=[PriceListEntry(key="circuit_breaker:1P:C16", unit_price=Decimal(4))],
        labour_per_circuit=Decimal(5),
    )
    priced = design.price_design(
        user=USER, request=QuotationRequest(project=designed.project, pricing=pricing)
    )
    assert not priced.complete
    assert priced.labour == Decimal(20)
    exported = design.export_design(
        session=None,  # type: ignore[arg-type]
        user=USER,
        request=DesignExportRequest(
            project=designed.project, format=ExportFormat.QUOTATION_PDF, pricing=pricing
        ),
    )
    assert exported.content.startswith(b"%PDF")
    assert exported.filename.endswith(".quotation.pdf")
    with pytest.raises(ValidationError, match="pricing settings"):
        design.export_design(
            session=None,  # type: ignore[arg-type]
            user=USER,
            request=DesignExportRequest(
                project=designed.project, format=ExportFormat.QUOTATION_CSV
            ),
        )


def test_a_price_list_is_imported() -> None:
    entries = design.import_price_list(user=USER, data=b"Key,Price\nX,2\n")
    assert entries[0].key == "X"


def _controlled_project() -> DesignProject:
    request = BoardDesignRequest(
        info=ProjectInfo(name="Pocket"),
        board=DistributionBoardRequest(
            name="DBG-HALL",
            loads=[
                LoadInput(
                    description="Lights",
                    load=LoadKind.LIGHTING,
                    power_kw=Decimal(1),
                    controlled=True,
                )
            ],
        ),
    )
    return design.design_board(session=None, user=USER, request=request).project  # type: ignore[arg-type]


def test_a_plc_program_is_written_checked_and_exported() -> None:
    from app.models.schemas.design import PlcProgramRequest
    from app.models.schemas.plc import ValidationStatus

    project = _controlled_project()
    result = design.write_plc_program(
        user=USER,
        request=PlcProgramRequest(project=project),
    )
    assert result.validation.status is ValidationStatus.VALID
    assert [p.direction for p in result.io].count("output") == 1
    source = design.export_design(
        session=None,  # type: ignore[arg-type]
        user=USER,
        request=DesignExportRequest(project=project, format=ExportFormat.PLC_ST),
    )
    assert source.filename == "Pocket.st"
    assert b"END_PROGRAM" in source.content
    io_list = design.export_design(
        session=None,  # type: ignore[arg-type]
        user=USER,
        request=DesignExportRequest(project=project, format=ExportFormat.PLC_IO_CSV),
    )
    assert io_list.filename == "Pocket.io.csv"


def test_a_plc_program_needs_a_controlled_circuit() -> None:
    from app.models.schemas.design import PlcProgramRequest

    project = design.design_board(session=None, user=USER, request=_request()).project  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="PLC-switched"):
        design.write_plc_program(user=USER, request=PlcProgramRequest(project=project))


def test_a_project_of_boards_is_designed_and_designated_per_board() -> None:
    from app.models.schemas.design import ProjectDesignRequest

    def board(name: str, fed_from: str | None = None) -> DistributionBoardRequest:
        return DistributionBoardRequest(
            name=name,
            fed_from=fed_from,
            loads=[LoadInput(description="Lights", load=LoadKind.LIGHTING, power_kw=Decimal(1))],
        )

    response = design.design_project(
        session=None,  # type: ignore[arg-type]
        user=USER,
        request=ProjectDesignRequest(
            info=ProjectInfo(name="Tower"), boards=[board("MDB"), board("DB-1", "MDB")]
        ),
    )
    main, sub = response.project.boards
    assert [c.feeds for c in main.circuits].count("DB-1") == 1
    assert main.devices[0].designation.function == "MDB"  # type: ignore[union-attr]
    assert sub.devices[0].designation.function == "DB-1"  # type: ignore[union-attr]


def test_a_motor_project_carries_its_articles_and_prices_by_them() -> None:
    from app.models.schemas.design import (
        PriceListEntry,
        PricingSettings,
        ProjectDesignRequest,
        QuotationRequest,
    )

    response = design.design_project(
        session=None,  # type: ignore[arg-type]
        user=USER,
        request=ProjectDesignRequest(
            info=ProjectInfo(name="Plant"),
            boards=[
                DistributionBoardRequest(
                    name="MCC",
                    loads=[
                        LoadInput(
                            description="Pump",
                            load=LoadKind.MOTOR,
                            power_kw=Decimal("7.5"),
                            phases=3,
                            starter="dol",
                        )
                    ],
                )
            ],
        ),
    )
    keys = {part.key for part in response.project.parts}
    assert {"ABB/T2S160 MA 20", "ABB/A30", "ABB/TA25DU19"} <= keys
    priced = design.price_design(
        user=USER,
        request=QuotationRequest(
            project=response.project,
            pricing=PricingSettings(price_list=[PriceListEntry(key="A30", unit_price=Decimal(40))]),
        ),
    )
    line = next(line for line in priced.lines if line.key == "A30")
    assert line.total == Decimal("40.00")
    assert line.description == "ABB A30"


def test_markups_are_placed_on_the_project_they_review() -> None:
    import io

    from reportlab.pdfgen.canvas import Canvas

    from app.design import pages, profile
    from app.design.sheet import SHEET_HEIGHT, SHEET_WIDTH, Text
    from app.models.schemas.design import ProjectDesignRequest

    request = ProjectDesignRequest.model_validate(
        {
            "info": {"name": "Tower"},
            "boards": [
                {
                    "name": "MDB",
                    "loads": [{"description": "Lights", "load": "lighting", "power_kw": "1"}],
                }
            ],
        }
    )
    designed = design.design_project(session=None, user=USER, request=request)  # type: ignore[arg-type]
    sheets = pages.build_drawing_set(designed.project, profile.default_profile())
    count = len(sheets)
    # The lights' breaker on the distribution page, where a reviewer would comment on it.
    breaker = next(
        item for item in sheets[3].items if isinstance(item, Text) and item.text == "-Q3"
    )
    point = 72 / 25.4
    at = (breaker.x * point, (SHEET_HEIGHT - breaker.y) * point)
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(SHEET_WIDTH * 72 / 25.4, SHEET_HEIGHT * 72 / 25.4))
    for page in range(count):
        if page == 2:
            canvas.textAnnotation("Check this breaker", Rect=(300, 500, 320, 520))
        if page == 3:
            canvas.textAnnotation("cable length 30 m", Rect=(at[0], at[1] - 10, at[0] + 10, at[1]))
        canvas.showPage()
    canvas.save()

    report = design.read_markups(
        user=USER, data=buffer.getvalue(), project_json=designed.project.model_dump_json()
    )
    assert report.matched
    mark, length = report.markups
    assert (mark.page, mark.board, mark.text) == (3, "MDB", "Check this breaker")
    assert mark.near
    # A comment asking for nothing the schedule holds suggests nothing; one
    # giving a length on the circuit's breaker suggests that length.
    assert mark.suggestion is None
    assert length.suggestion is not None
    assert (length.suggestion.circuit, length.suggestion.load_index) == ("Lights", 0)
    assert (length.suggestion.field, length.suggestion.value) == ("length_m", "30")

    alone = design.read_markups(user=USER, data=buffer.getvalue())
    assert not alone.matched
    with pytest.raises(ValidationError):
        design.read_markups(user=USER, data=b"x" * (design.MAX_MARKUP_BYTES + 1))
    with pytest.raises(ValidationError):
        design.read_markups(user=USER, data=buffer.getvalue(), project_json="{}")
