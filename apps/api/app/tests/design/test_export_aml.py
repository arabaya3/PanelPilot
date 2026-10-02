"""Tests for `app/design/export_aml.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from app.design import export_aml
from app.models.schemas.design import DesignProject

NS = {"c": "http://www.dke.de/CAEX"}


def _root(project: DesignProject) -> ET.Element:
    return ET.fromstring(export_aml.export_aml(project))


def test_a_caex_3_document(hall_project: DesignProject) -> None:
    root = _root(hall_project)
    assert root.tag == "{http://www.dke.de/CAEX}CAEXFile"
    assert root.get("SchemaVersion") == "3.0"
    assert root.find("c:SourceDocumentInformation", NS) is not None


def test_each_device_and_cable_is_an_element_with_attributes(hall_project: DesignProject) -> None:
    root = _root(hall_project)
    board = root.find("c:InstanceHierarchy/c:InternalElement", NS)
    assert board is not None
    assert board.get("Name") == "DBG-HALL"
    children = board.findall("c:InternalElement", NS)
    design = hall_project.boards[0]
    assert len(children) == len(design.devices) + len(design.cables)

    def attributes(element: ET.Element) -> dict[str, str]:
        return {
            a.get("Name", ""): a.findtext("c:Value", "", NS)
            for a in element.findall("c:Attribute", NS)
        }

    incomer = attributes(children[0])
    assert incomer["ReferenceDesignation"] == "=DBG-HALL+HALL-Q1"
    assert incomer["Manufacturer"] == "ETEK"
    rated = children[0].find("c:Attribute[@Name='RatedCurrent']", NS)
    assert rated is not None
    assert rated.get("Unit") == "A"


def test_links_join_existing_interfaces(hall_project: DesignProject) -> None:
    root = _root(hall_project)
    interfaces = {
        f"{element.get('ID')}:{interface.get('Name')}"
        for element in root.iter("{http://www.dke.de/CAEX}InternalElement")
        for interface in element.findall("c:ExternalInterface", NS)
    }
    links = list(root.iter("{http://www.dke.de/CAEX}InternalLink"))
    design = hall_project.boards[0]
    upstream = sum(1 for d in design.devices if d.upstream_id)
    assert len(links) == upstream + len(design.cables)
    for link in links:
        assert link.get("RefPartnerSideA") in interfaces
        assert link.get("RefPartnerSideB") in interfaces


def test_ids_are_unique_and_stable(hall_project: DesignProject) -> None:
    root = _root(hall_project)
    ids = [e.get("ID") for e in root.iter() if e.get("ID")]
    assert len(ids) == len(set(ids))
    assert export_aml.export_aml(hall_project) == export_aml.export_aml(hall_project)
