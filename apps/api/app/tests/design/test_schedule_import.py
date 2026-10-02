"""Tests for `app/design/schedule_import.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from app.core.errors import ValidationError
from app.design import schedule_import
from app.models.schemas.design import LoadKind


def _xlsx(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Project: Pocket", None, None])  # a title row above the header
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _pdf(rows: list[list[str]], *, ruled: bool = True) -> bytes:
    buffer = io.BytesIO()
    table = Table(rows)
    if ruled:
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, (0, 0, 0))]))
    SimpleDocTemplate(buffer, pagesize=A4).build([table])
    return buffer.getvalue()


def test_an_excel_schedule_is_read() -> None:
    data = _xlsx(
        [
            ["No.", "Circuit Description", "Load Type", "Load (kW)", "Phase", "PF"],
            [1, "Sockets hall east", "Socket", 1.5, "1", 0.9],
            [2, "Lighting zone 1", "", 0.6, "L1", None],
            [3, "AC unit 1", "A/C", 4, "3", 0.85],
            [4, "Spare", "", None, "", None],
            [None, None, None, None, None, None],
            ["", "Total", "", 6.1, "", ""],
        ]
    )
    result = schedule_import.import_schedule(data)
    assert [(load.description, load.load, load.power_kw, load.phases) for load in result.loads] == [
        ("Sockets hall east", LoadKind.SOCKET, Decimal("1.5"), 1),
        ("Lighting zone 1", LoadKind.LIGHTING, Decimal("0.6"), 1),
        ("AC unit 1", LoadKind.AIR_CONDITIONING, Decimal(4), 3),
    ]
    assert result.loads[2].power_factor == Decimal("0.85")
    assert result.rows_read == 4
    # The light's type was inferred, and the spare left out: both said.
    codes = {(w.code, w.params.get("load")) for w in result.warnings}
    assert ("import_kind_inferred", "Lighting zone 1") in codes
    assert ("import_spare", "Spare") in codes


def test_an_arabic_csv_in_watts() -> None:
    text = "\n".join(
        [
            "الوصف,النوع,القدرة (W),الفاز",
            "إنارة الممر,,400,1",
            "مكيف المكتب,تكييف,3500,ثلاثي",
            "مضخة المياه,,750,3",
            "شي غريب,,100,1",
        ]
    )
    result = schedule_import.import_schedule(text.encode("utf-8-sig"))
    kinds = [(load.load, load.power_kw, load.phases) for load in result.loads]
    assert kinds == [
        (LoadKind.LIGHTING, Decimal("0.4"), 1),
        (LoadKind.AIR_CONDITIONING, Decimal("3.5"), 3),
        (LoadKind.MOTOR, Decimal("0.75"), 3),
        (LoadKind.OTHER, Decimal("0.1"), 1),
    ]
    assert any("not recognised" in w.text for w in result.warnings)


def test_a_pdf_table_is_read() -> None:
    data = _pdf(
        [
            ["Circuit", "Description", "kW", "Phases"],
            ["C1", "Exhaust fan", "0.37", "1"],
            ["C2", "Water heater", "3", "1"],
        ]
    )
    result = schedule_import.import_schedule(data)
    # "Description" is preferred over the "Circuit" column of numbers.
    assert [(load.description, load.load) for load in result.loads] == [
        ("Exhaust fan", LoadKind.FAN),
        ("Water heater", LoadKind.WATER_HEATER),
    ]
    assert [load.power_kw for load in result.loads] == [Decimal("0.37"), Decimal(3)]


def test_a_pdf_table_without_rules_is_read_from_its_alignment() -> None:
    data = _pdf(
        [
            ["Circuit", "Description", "Load (kW)", "Phases"],
            ["C1", "Sockets east", "1.5", "1"],
            ["C2", "AC unit", "4", "3"],
        ],
        ruled=False,
    )
    result = schedule_import.import_schedule(data)
    assert [(load.description, load.power_kw, load.phases) for load in result.loads] == [
        ("Sockets east", Decimal("1.5"), 1),
        ("AC unit", Decimal(4), 3),
    ]


def test_kva_only_is_read_and_said() -> None:
    data = b"Description,kVA\nPump,5.5\n"
    result = schedule_import.import_schedule(data)
    assert result.loads[0].power_kw == Decimal("5.5")
    assert any("only kVA" in w.text for w in result.warnings)


def test_rows_it_cannot_read_are_reported() -> None:
    data = b"Description,kW,PF,Phase\n,2,,1\nSockets,abc,,1\nLights,1,1.4,x\n"
    result = schedule_import.import_schedule(data)
    assert len(result.loads) == 1
    joined = " ".join(w.text for w in result.warnings)
    assert "Row 2: no description" in joined
    assert "Row 3 (Sockets): no power given" in joined
    assert "power factor 1.4 is out of range" in joined
    assert "phases 'x' not read" in joined


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "empty"),
        (b"a" * (schedule_import.MAX_SCHEDULE_BYTES + 1), "limit"),
        (b"\xd0\xcf\x11\xe0rest", "old Excel"),
        (b"PK\x03\x04broken", "damaged archive"),
        (b"Name,Colour\nx,red\n", "no header row"),
        (b"Description,kW\nSpare,\n", "no load could be read"),
    ],
)
def test_unreadable_files_are_refused(data: bytes, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        schedule_import.import_schedule(data)


def test_a_pdf_without_a_table_is_refused() -> None:
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer).build([])
    with pytest.raises(ValidationError):
        schedule_import.import_schedule(buffer.getvalue() or b"%PDF-1.4")


def test_read_rows_handles_semicolons() -> None:
    assert schedule_import.read_rows(b"a;b\n1;2\n") == [["a", "b"], ["1", "2"]]


def test_only_the_first_rows_and_columns_are_read() -> None:
    wide = ",".join(f"c{i}" for i in range(100))
    data = ("Description,kW\n" + "Lights,1\n" * (schedule_import.MAX_ROWS + 50) + wide).encode()
    rows = schedule_import.read_rows(data)
    assert len(rows) == schedule_import.MAX_ROWS
    workbook_rows: list[list[object]] = [
        ["Description", "kW"],
        *[["Lights", 1] for _ in range(schedule_import.MAX_ROWS + 10)],
    ]
    assert len(schedule_import.read_rows(_xlsx(workbook_rows))) == schedule_import.MAX_ROWS
