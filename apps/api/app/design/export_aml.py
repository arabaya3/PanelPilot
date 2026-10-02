"""Export a design project as AutomationML (IEC 62714, CAEX 3.0).

AutomationML is the one neutral format several ECAD tools import (EPLAN,
E3.series, WSCAD's TIA Portal interface). What they import from it is the
device hierarchy and connections, not drawn pages, so that is what this
writes: each board as an internal element, each device and cable inside it
with its designation, ratings and part as attributes, and each "fed from"
relation as an internal link between the two devices' power interfaces.

No role class library beyond AutomationML's base one is referenced, and no
tool's application recommendation (AR APC is about PLC configuration) is
claimed: a tool maps these elements onto its own parts on import.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from decimal import Decimal

from app.models.schemas.design import Board, Cable, DesignProject, Device

_NAMESPACE = uuid.UUID("0b9e3f43-6c9a-4c2f-9b1e-6d2f8e7a5c10")
_CAEX = "http://www.dke.de/CAEX"
_BASE_ROLE = "AutomationMLBaseRoleClassLib/AutomationMLBaseRole"
_BASE_INTERFACE = "AutomationMLInterfaceClassLib/AutomationMLBaseInterface"


def _id(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "/".join(parts)))


def _plain(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _attribute(
    parent: ET.Element, name: str, value: str, unit: str = "", data_type: str = "xs:string"
) -> None:
    if not value:
        return
    attribute = ET.SubElement(parent, "Attribute", Name=name, AttributeDataType=data_type)
    if unit:
        attribute.set("Unit", unit)
    ET.SubElement(attribute, "Value").text = value


def _interface(element: ET.Element, element_id: str, name: str) -> str:
    interface_id = _id(element_id, name)
    ET.SubElement(
        element,
        "ExternalInterface",
        Name=name,
        ID=interface_id,
        RefBaseClassPath=_BASE_INTERFACE,
    )
    return interface_id


def _device(project: DesignProject, board: Board, device: Device, parent: ET.Element) -> ET.Element:
    element_id = _id(board.id, device.id)
    element = ET.SubElement(
        parent,
        "InternalElement",
        Name=device.designation.product if device.designation else device.id,
        ID=element_id,
    )
    _attribute(
        element, "ReferenceDesignation", str(device.designation) if device.designation else ""
    )
    _attribute(element, "Kind", device.kind.value)
    _attribute(element, "FunctionText", device.description)
    _attribute(element, "Poles", str(device.poles or ""), data_type="xs:int")
    _attribute(element, "RatedCurrent", _plain(device.rated_current_a), "A", "xs:decimal")
    _attribute(element, "TrippingCurve", device.curve or "")
    _attribute(
        element, "RatedResidualCurrent", _plain(device.residual_current_ma), "mA", "xs:decimal"
    )
    _attribute(element, "BreakingCapacity", _plain(device.breaking_capacity_ka), "kA", "xs:decimal")
    if device.part_key:
        part = project.part(device.part_key)
        _attribute(element, "Manufacturer", part.manufacturer)
        _attribute(element, "TypeNumber", part.type_number)
        _attribute(element, "OrderNumber", part.order_number or "")
    _interface(element, element_id, "PowerIn")
    _interface(element, element_id, "PowerOut")
    ET.SubElement(element, "RoleRequirements", RefBaseRoleClassPath=_BASE_ROLE)
    return element


def _cable(board: Board, cable: Cable, parent: ET.Element) -> None:
    element_id = _id(board.id, cable.id)
    element = ET.SubElement(
        parent,
        "InternalElement",
        Name=cable.designation.product if cable.designation else cable.id,
        ID=element_id,
    )
    _attribute(element, "ReferenceDesignation", str(cable.designation) if cable.designation else "")
    _attribute(element, "Kind", "cable")
    _attribute(element, "Cores", str(cable.cores), data_type="xs:int")
    _attribute(element, "CrossSection", _plain(cable.cross_section_mm2), "mm2", "xs:decimal")
    _attribute(element, "Material", cable.material)
    _attribute(element, "Insulation", cable.insulation)
    _attribute(element, "Length", _plain(cable.length_m), "m", "xs:decimal")
    _interface(element, element_id, "PowerIn")
    ET.SubElement(element, "RoleRequirements", RefBaseRoleClassPath=_BASE_ROLE)


def export_aml(project: DesignProject) -> bytes:
    """Write a project as an AutomationML (CAEX 3.0) document.

    Args:
        project: The project, designated.

    Returns:
        The ``.aml`` file's bytes (UTF-8 XML).
    """
    root = ET.Element(
        "CAEXFile",
        {
            "SchemaVersion": "3.0",
            "FileName": f"{project.info.name}.aml",
            "xmlns": _CAEX,
        },
    )
    ET.SubElement(
        root,
        "SourceDocumentInformation",
        {
            "OriginName": "PanelPilot",
            "OriginID": _id("origin"),
            "OriginVersion": "1",
            "OriginProjectTitle": project.info.name,
            "OriginProjectID": project.info.number or project.info.name,
        },
    )
    hierarchy = ET.SubElement(
        root, "InstanceHierarchy", Name=project.info.name, ID=_id("hierarchy", project.info.name)
    )
    for board in project.boards:
        board_id = _id("board", board.id)
        board_element = ET.SubElement(hierarchy, "InternalElement", Name=board.name, ID=board_id)
        _attribute(board_element, "Kind", "board")
        _attribute(board_element, "Voltage", _plain(board.supply.voltage_v), "V", "xs:decimal")
        _attribute(board_element, "Phases", str(board.supply.phases), data_type="xs:int")
        _attribute(
            board_element, "Frequency", _plain(board.supply.frequency_hz), "Hz", "xs:decimal"
        )
        _attribute(board_element, "Earthing", board.supply.earthing)
        _attribute(
            board_element, "FaultLevel", _plain(board.supply.fault_level_ka), "kA", "xs:decimal"
        )
        for device in board.devices:
            _device(project, board, device, board_element)
        for cable in board.cables:
            _cable(board, cable, board_element)
        links: list[tuple[str, str, str]] = []
        for device in board.devices:
            if device.upstream_id is not None:
                links.append(
                    (
                        f"{device.upstream_id}->{device.id}",
                        f"{_id(board.id, device.upstream_id)}:PowerOut",
                        f"{_id(board.id, device.id)}:PowerIn",
                    )
                )
        for circuit in board.circuits:
            if circuit.cable_id and circuit.device_ids:
                links.append(
                    (
                        f"{circuit.device_ids[-1]}->{circuit.cable_id}",
                        f"{_id(board.id, circuit.device_ids[-1])}:PowerOut",
                        f"{_id(board.id, circuit.cable_id)}:PowerIn",
                    )
                )
        for name, side_a, side_b in links:
            ET.SubElement(
                board_element,
                "InternalLink",
                Name=name,
                RefPartnerSideA=side_a,
                RefPartnerSideB=side_b,
            )
        ET.SubElement(board_element, "RoleRequirements", RefBaseRoleClassPath=_BASE_ROLE)
    ET.indent(root, space="  ")
    body: bytes = ET.tostring(root, encoding="utf-8")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body
