"""Tests for `app/design/export_lists.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import csv
from io import StringIO

import pytest

from app.design import export_lists
from app.models.schemas.design import DesignProject


def _rows(text: str) -> list[dict[str, str]]:
    assert text.startswith("﻿"), "Excel needs the byte-order mark to read UTF-8"
    return list(csv.DictReader(StringIO(text.removeprefix("﻿"))))


def test_device_list(hall_project: DesignProject) -> None:
    rows = _rows(export_lists.device_list(hall_project))
    board = hall_project.boards[0]
    assert len(rows) == len(board.devices)
    incomer = rows[0]
    # Written as text: a spreadsheet would read a leading = as a formula.
    assert incomer["Designation"] == "'=DBG-HALL+HALL-Q1"
    assert incomer["Manufacturer"] == "ETEK"
    assert incomer["Breaking capacity kA"] == "10"
    rcd = next(r for r in rows if r["Kind"] == "residual_current_device")
    assert rcd["Residual current mA"] == "30"
    assert rcd["Fed from"].startswith("'=DBG-HALL+HALL-Q")


def test_parts_list_counts_identical_devices(hall_project: DesignProject) -> None:
    rows = _rows(export_lists.parts_list(hall_project))
    total = sum(int(r["Quantity"]) for r in rows)
    assert total == len(hall_project.boards[0].devices)
    sockets = next(r for r in rows if r["Rated current A"] == "16" and r["Poles"] == "1")
    assert int(sockets["Quantity"]) == len(sockets["Designations"].split())


def test_cable_list_and_unicode(hall_project: DesignProject) -> None:
    rows = _rows(export_lists.cable_list(hall_project))
    assert len(rows) == len(hall_project.boards[0].cables)
    cafe = next(r for r in rows if r["To"] == "Café, east wall")
    assert cafe["Cores"] == "3"
    assert cafe["From"].startswith("'=DBG-HALL+HALL-Q")


def test_circuit_schedule(hall_project: DesignProject) -> None:
    rows = _rows(export_lists.circuit_schedule(hall_project))
    assert [r["Circuit"] for r in rows] == [c.description for c in hall_project.boards[0].circuits]
    assert {r["Phase"] for r in rows} <= {"L1", "L2", "L3", "L1L2L3"}
    assert rows[0]["Cable"].startswith("3G")


@pytest.mark.parametrize(
    ("value", "written"),
    [
        ("=DB1-Q3", "'=DB1-Q3"),
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+1", "'+1"),
        ("-5", "'-5"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tx", "'\tx"),
        ("Sockets", "Sockets"),
        ("", ""),
    ],
)
def test_text_cell(value: str, written: str) -> None:
    assert export_lists.text_cell(value) == written


def test_write_csv() -> None:
    text = export_lists.write_csv(["A", "B"], [["=x", "y"], ["z"]])
    assert text == "﻿A,B\r\n'=x,y\r\nz\r\n"
