"""Read a consultant's load schedule into the rows a board is designed from.

Load schedules arrive as Excel workbooks, CSV exports, or PDF tables, with
headers in English or Arabic and no two consultants laying them out alike.
This reads the table, finds the header row by what its cells say, maps each
column it recognises, and turns every data row it can into a load.

Nothing is guessed silently. A row this cannot read (no power, a power it
cannot parse, a load type it cannot place) is reported by its row number
and reason, and a load whose type was inferred from its description rather
than stated is reported as such, so the engineer checks exactly what the
import assumed.
"""

from __future__ import annotations

import csv
import io
import itertools
import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pdfplumber

from app.core.errors import ValidationError
from app.design.notes import note
from app.models.schemas.design import DesignNote, LoadInput, LoadKind

#: The largest schedule accepted, in bytes.
MAX_SCHEDULE_BYTES = 5 * 1024 * 1024

#: Rows, columns and sheets read from a spreadsheet. A schedule or a price
#: list is a few hundred rows; the bound keeps a 5 MB file that expands to
#: millions of cells (an .xlsx is a zip) from costing more than one that
#: does not.
MAX_ROWS = 5000
_MAX_COLUMNS = 60
_MAX_SHEETS = 20

#: Pages of a PDF searched for the schedule: a schedule is a few pages, and
#: table detection is costly enough that a 500-page upload is refused work.
_MAX_PDF_PAGES = 50

#: How many rows from the top are searched for the header.
_HEADER_SEARCH_ROWS = 30


def _norm(text: str) -> str:
    """Lower-case, strip Arabic diacritics and tatweel, collapse spaces."""
    text = re.sub("[\u064b-\u065f\u0640]", "", text)
    for variant, plain in (
        ("\u0623", "\u0627"),
        ("\u0625", "\u0627"),
        ("\u0622", "\u0627"),
        ("\u0629", "\u0647"),
    ):
        text = text.replace(variant, plain)
    return re.sub(r"\s+", " ", text).strip().lower()


#: Header words for each column, normalised. Matched as whole words.
_HEADERS: dict[str, tuple[str, ...]] = {
    "description": (
        "description",
        "circuit description",
        "circuit",
        "load description",
        "item",
        "name",
        "الوصف",
        "البيان",
        "الدائره",
        "وصف الدائره",
        "وصف الحمل",
    ),
    "type": ("type", "load type", "category", "النوع", "نوع الحمل"),
    "kw": ("kw", "power", "load", "rated power", "kw/ph", "القدره", "الحمل", "القدره kw"),
    "w": ("w", "watt", "watts", "واط"),
    "kva": ("kva",),
    "phases": ("phase", "phases", "ph", "no. of phases", "الفاز", "الفازات", "الطور"),
    "pf": ("pf", "p.f.", "cos", "cos phi", "cosφ", "power factor", "معامل القدره"),
}

#: Load types by keyword, normalised; checked in order, first match wins.
_KINDS: tuple[tuple[LoadKind, tuple[str, ...]], ...] = (
    (LoadKind.SPARE, ("spare", "احتياط", "احتياطي")),
    (LoadKind.LIGHTING, ("light", "lighting", "lights", "lamp", "انار", "اناره", "اضاءه", "اناره")),
    (
        LoadKind.SOCKET,
        (
            "socket",
            "sockets",
            "s.o",
            "outlet",
            "receptacle",
            "power point",
            "مخرج",
            "ماخذ",
            "مآخذ",
            "بريز",
            "افياش",
        ),
    ),
    (
        LoadKind.AIR_CONDITIONING,
        ("a/c", "ac", "air cond", "air-cond", "split", "hvac", "fcu", "مكيف", "تكييف", "مكيفات"),
    ),
    (LoadKind.WATER_HEATER, ("heater", "boiler", "geyser", "سخان", "بويلر")),
    (LoadKind.FAN, ("fan", "exhaust", "extract", "شفاط", "مروحه", "مراوح")),
    (LoadKind.LIFT, ("lift", "elevator", "مصعد")),
    (LoadKind.MOTOR, ("motor", "pump", "compressor", "مضخه", "محرك", "ضاغط")),
    (LoadKind.KITCHEN, ("kitchen", "oven", "cooker", "hob", "مطبخ", "فرن")),
    (LoadKind.SUB_BOARD, ("sub board", "sub-board", "smdb", "sdb", "db-", "panel", "لوحه")),
    (LoadKind.DATA, ("data", "server", "rack", "it ", "ups", "network", "cctv", "شبكه", "سيرفر")),
    (LoadKind.CONTROL, ("control", "bms", "fire alarm", "تحكم", "انذار")),
)

_TOTAL = ("total", "sum", "grand total", "المجموع", "الاجمالي", "اجمالي")


@dataclass
class ScheduleImport:
    """What was read from a schedule.

    Attributes:
        loads: The loads, in the order the schedule lists them.
        warnings: What the import skipped or assumed, row by row.
        rows_read: Data rows below the header, blank rows excluded.
    """

    loads: list[LoadInput] = field(default_factory=list)
    warnings: list[DesignNote] = field(default_factory=list)
    rows_read: int = 0


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _rows_from_xlsx(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a zoo of types for a bad file
        raise ValidationError(
            f"the workbook could not be read: {exc}", code="workbook_unreadable"
        ) from exc
    try:
        # The first sheet that has a recognisable header; the first sheet
        # otherwise, so the error names what was found there.
        sheets = [
            [
                [_cell(v) for v in row]
                for row in sheet.iter_rows(max_row=MAX_ROWS, max_col=_MAX_COLUMNS, values_only=True)
            ]
            for sheet in workbook.worksheets[:_MAX_SHEETS]
        ]
    finally:
        workbook.close()
    for rows in sheets:
        if _find_header(rows) is not None:
            return rows
    return sheets[0] if sheets else []


def _rows_from_csv(data: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[c.strip() for c in row[:_MAX_COLUMNS]] for row in itertools.islice(reader, MAX_ROWS)]


#: Column and row edges taken from the text's alignment, for a table drawn
#: without rules (a schedule printed from Word or a plain report).
_UNRULED = {"vertical_strategy": "text", "horizontal_strategy": "text"}


def _rows_from_pdf(data: bytes) -> list[list[str]]:
    rows: list[list[str]] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:_MAX_PDF_PAGES]:
                tables = page.extract_tables() or page.extract_tables(_UNRULED)
                for table in tables:
                    for row in table:
                        cells = [_cell(c).replace("\n", " ") for c in row]
                        if any(cells):
                            rows.append(cells)
    except Exception as exc:  # pdfminer raises many types for a bad file
        raise ValidationError(f"the PDF could not be read: {exc}", code="pdf_unreadable") from exc
    if not rows:
        raise ValidationError(
            "no table was found in the PDF; export the schedule from Excel, "
            "or check that the PDF holds text rather than a scanned image",
            code="pdf_no_table",
        )
    return rows


def _matches(cell: str, words: tuple[str, ...]) -> bool:
    text = _norm(cell)
    if not text:
        return False
    for word in words:
        if text == word or re.search(rf"(^|[\s(/\[]){re.escape(word)}($|[\s)/\]:])", text):
            return True
    return False


#: Description headers that name the description outright, preferred over
#: ones ("Circuit", "Item") that often head a column of circuit numbers.
_STRONG_DESCRIPTION = (
    "description",
    "circuit description",
    "load description",
    "الوصف",
    "البيان",
    "وصف الدائره",
    "وصف الحمل",
)


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    """The header row's index and the column of each field it names."""
    for index, row in enumerate(rows[:_HEADER_SEARCH_ROWS]):
        columns: dict[str, int] = {}
        for column, cell in enumerate(row):
            for name in ("kva", "pf", "phases", "type", "description", "w", "kw"):
                if name not in columns and _matches(cell, _HEADERS[name]):
                    # A kW column named "Load (kW)" is kW, whatever else it says.
                    if name == "w" and "kw" in _norm(cell):
                        continue
                    columns[name] = column
                    break
        # A header saying "description" outright beats "Circuit" or "Item".
        for column, cell in enumerate(row):
            if _matches(cell, _STRONG_DESCRIPTION) and not _matches(cell, _HEADERS["type"]):
                columns["description"] = column
                break
        power = any(k in columns for k in ("kw", "w", "kva"))
        if "description" in columns and power:
            return index, columns
    return None


def _number(text: str) -> Decimal | None:
    cleaned = text.replace(",", ".").replace("\u066b", ".")
    cleaned = cleaned.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    try:
        return Decimal(match.group())
    except InvalidOperation:
        return None


def _kind(text: str) -> LoadKind | None:
    normalised = f" {_norm(text)} "
    for kind, words in _KINDS:
        for word in words:
            if word in normalised:
                return kind
    return None


def _phases(text: str) -> int | None:
    normalised = _norm(text)
    if not normalised:
        return None
    if re.search(r"(^|\D)3(\D|$)|three|ثلاث|tp|tpn|l1\s*l2\s*l3|rst", normalised):
        return 3
    if re.search(r"(^|\D)1(\D|$)|single|احادي|sp|spn|^l[123]$|^[rstyb]$", normalised):
        return 1
    return None


def read_rows(data: bytes) -> list[list[str]]:
    """Read a schedule file's cells, whatever its format.

    Args:
        data: The file's bytes: an Excel workbook, a CSV file or a PDF.

    Returns:
        Its rows, each a list of cell texts.

    Raises:
        ValidationError: If the file is empty, too large, or cannot be read.
    """
    if not data:
        raise ValidationError("the file is empty", code="file_empty")
    if len(data) > MAX_SCHEDULE_BYTES:
        raise ValidationError(
            f"the file is {len(data) // 1024} KB; the limit is {MAX_SCHEDULE_BYTES // 1024} KB",
            code="file_too_large",
            params={"size": len(data) // 1024, "limit": MAX_SCHEDULE_BYTES // 1024},
        )
    if data.startswith(b"%PDF"):
        return _rows_from_pdf(data)
    if data.startswith(b"PK"):
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile as exc:
            raise ValidationError("the file is a damaged archive", code="archive_damaged") from exc
        if any(name.startswith("xl/") for name in names):
            return _rows_from_xlsx(data)
        raise ValidationError(
            "the archive is not an Excel workbook (.xlsx)", code="archive_not_excel"
        )
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValidationError(
            "old Excel files (.xls) are not read; save it as .xlsx or CSV", code="old_excel"
        )
    return _rows_from_csv(data)


def import_schedule(data: bytes) -> ScheduleImport:
    """Read a load schedule into loads, reporting every row it could not read.

    Args:
        data: The schedule file's bytes (.xlsx, .csv or .pdf).

    Returns:
        The loads read, and what was skipped or assumed.

    Raises:
        ValidationError: If the file cannot be read, or has no header naming
            both a description and a power column.
    """
    rows = read_rows(data)
    found = _find_header(rows)
    if found is None:
        raise ValidationError(
            "no header row names both a description and a power (kW or W) column "
            "in the first 30 rows",
            code="schedule_no_header",
        )
    header_index, columns = found
    result = ScheduleImport()

    def get(row: list[str], name: str) -> str:
        column = columns.get(name)
        return row[column] if column is not None and column < len(row) else ""

    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(cell.strip() for cell in row):
            continue
        description = get(row, "description").strip()
        if any(_matches(cell, _TOTAL) for cell in row[:3]):
            continue
        result.rows_read += 1
        if not description:
            result.warnings.append(note("import_no_description", row=offset))
            continue
        if "kw" in columns:
            power = _number(get(row, "kw"))
            scale = Decimal(1)
        elif "w" in columns:
            power = _number(get(row, "w"))
            scale = Decimal("0.001")
        else:
            power = _number(get(row, "kva"))
            scale = Decimal(1)
        type_text = get(row, "type")
        kind = _kind(type_text) if type_text else None
        inferred = False
        if kind is None:
            kind = _kind(description)
            inferred = kind is not None
        if kind is LoadKind.SPARE:
            result.warnings.append(note("import_spare", row=offset, load=description))
            continue
        if power is None or power <= 0:
            result.warnings.append(note("import_no_power", row=offset, load=description))
            continue
        power = (power * scale).normalize()
        if kind is None:
            kind = LoadKind.OTHER
            result.warnings.append(note("import_kind_unknown", row=offset, load=description))
        elif inferred:
            result.warnings.append(
                note("import_kind_inferred", row=offset, load=description, kind=kind.value)
            )
        phases = _phases(get(row, "phases")) or 1
        if "phases" in columns and _phases(get(row, "phases")) is None:
            result.warnings.append(
                note(
                    "import_phases_unread",
                    row=offset,
                    load=description,
                    value=get(row, "phases"),
                )
            )
        power_factor = _number(get(row, "pf")) if "pf" in columns else None
        if power_factor is not None and not 0 < power_factor <= 1:
            result.warnings.append(
                note("import_pf_out_of_range", row=offset, load=description, value=power_factor)
            )
            power_factor = None
        if "kw" not in columns and "w" not in columns:
            result.warnings.append(note("import_kva_only", row=offset, load=description))
        result.loads.append(
            LoadInput(
                description=description[:120],
                load=kind,
                power_kw=power,
                phases=phases,
                power_factor=power_factor,
            )
        )
    if not result.loads:
        raise ValidationError(
            "no load could be read below the header; "
            + (result.warnings[0].text if result.warnings else ""),
            code="schedule_no_load",
        )
    return result
