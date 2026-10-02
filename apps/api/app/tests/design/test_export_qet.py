"""Tests for `app/design/export_qet.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The file was opened in QElectroTech 0.9 while this exporter was written;
these tests hold the structure that made it open with its conductors
attached: every conductor names an element on its own folio and a terminal
that element's embedded symbol defines.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from app.design import export_qet
from app.models.schemas.design import DesignProject


def _root(project: DesignProject) -> ET.Element:
    data = export_qet.export_qet(project)
    assert data.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    return ET.fromstring(data)


def test_one_main_folio_and_one_per_feeder_group(hall_project: DesignProject) -> None:
    root = _root(hall_project)
    assert root.tag == "project"
    assert root.get("version") == "0.90"
    titles = [d.get("title") for d in root.findall("diagram")]
    assert titles[0] == "DBG-HALL - Main power"
    groups = {c.upstream_id for c in hall_project.boards[0].circuits}
    assert len(titles) == 1 + len(groups)
    assert [d.get("order") for d in root.findall("diagram")] == [
        str(n) for n in range(1, len(titles) + 1)
    ]


def test_every_conductor_joins_terminals_that_exist(hall_project: DesignProject) -> None:
    root = _root(hall_project)
    definitions = {
        element.get("name"): {t.get("uuid") for t in element.iter("terminal")}
        for element in root.find("collection").iter("element")  # type: ignore[union-attr]
    }
    assert set(definitions) == {
        export_qet.BREAKER,
        export_qet.RCD,
        export_qet.CONTACTOR,
        export_qet.LOAD,
        export_qet.SUPPLY,
        export_qet.FUSE,
        export_qet.OVERLOAD,
        export_qet.DRIVE,
    }
    conductors = 0
    for diagram in root.findall("diagram"):
        placed = {
            e.get("uuid"): e.get("type", "").rsplit("/", 1)[-1] for e in diagram.iter("element")
        }
        for conductor in diagram.iter("conductor"):
            conductors += 1
            for side in ("1", "2"):
                kind = placed[conductor.get(f"element{side}")]
                assert conductor.get(f"terminal{side}") in definitions[kind]
    assert conductors > 0


def test_devices_carry_their_designation_and_rating(hall_project: DesignProject) -> None:
    root = _root(hall_project)
    labels = {info.text for info in root.iter("elementInformation") if info.get("name") == "label"}
    for device in hall_project.boards[0].devices:
        assert device.designation is not None
        assert device.designation.product in labels
    comments = {
        info.text for info in root.iter("elementInformation") if info.get("name") == "comment"
    }
    assert any(c and c.startswith("C16 1P") for c in comments)
    assert any(c and "30 mA" in c for c in comments)


def test_export_is_deterministic(hall_project: DesignProject) -> None:
    assert export_qet.export_qet(hall_project) == export_qet.export_qet(hall_project)


def test_motor_devices_use_their_own_symbols() -> None:
    from decimal import Decimal

    from app.design import designations, motors, profile, project
    from app.models.schemas.design import (
        DistributionBoardRequest,
        LoadInput,
        LoadKind,
        ProjectInfo,
    )

    loads = [
        LoadInput(
            description="Pump",
            load=LoadKind.MOTOR,
            power_kw=Decimal("7.5"),
            phases=3,
            starter="dol",
        ),
        LoadInput(
            description="Conveyor",
            load=LoadKind.MOTOR,
            power_kw=Decimal(15),
            phases=3,
            starter="drive",
        ),
    ]
    boards = project.design_boards(
        [DistributionBoardRequest(name="MCC", loads=loads)], profile.default_profile()
    )
    designed = designations.designate_project(
        DesignProject(
            info=ProjectInfo(name="Plant"), boards=boards, parts=motors.parts_for(boards)
        ),
        profile.default_profile(),
    )
    root = _root(designed)
    used = {e.get("type", "").rsplit("/", 1)[-1] for e in root.iter("element") if e.get("type")}
    assert {export_qet.FUSE, export_qet.OVERLOAD, export_qet.DRIVE} <= used
    texts = {t.text for t in root.iter("text") if t.text} | {
        i.text for i in root.iter("elementInformation") if i.text
    }
    assert any(text and "ACS880-01-032A-3" in text for text in texts)
