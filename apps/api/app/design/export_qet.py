"""Export a design project as a QElectroTech project (.qet).

QElectroTech is free and open source, and its project file is plain XML: the
symbols a project uses are embedded in it, every element carries its
terminals, and conductors join terminals by id. So the export is an editable
schematic, not a picture: a device moved in QElectroTech drags its
conductors with it.

The symbols are this software's own, drawn to IEC 60617 single-line
conventions and embedded under ``import/panelpilot``, so the file opens on
an installation without any element collection.

Each board gets a main diagram (incomer, busbar, one feeder per residual
current group or direct circuit) and a diagram per feeder group with its
outgoing circuits. The format follows QElectroTech 0.90's own example
projects (conductors name their elements and the symbol's terminals by
uuid), and the result opens in that version.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from decimal import Decimal

from app.models.schemas.design import Board, Circuit, DesignProject, Device, DeviceKind

_NAMESPACE = uuid.UUID("6f1c6f0e-6a0b-4b8e-9a43-2a6c0f3d9e51")
_LINE = "line-style:normal;line-weight:normal;filling:none;color:black"
_DASHED = "line-style:dashed;line-weight:thin;filling:none;color:black"
_FONT = "Arial,9,-1,5,50,0,0,0,0,0,Normal"
_COLUMN_WIDTH = 90
_FIRST_X = 120
_PER_DIAGRAM = 9

#: The element names, as embedded.
BREAKER = "pp_breaker.elmt"
RCD = "pp_rcd.elmt"
LOAD = "pp_load.elmt"
SUPPLY = "pp_supply.elmt"


def _plain(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _uuid(*parts: str) -> str:
    return "{" + str(uuid.uuid5(_NAMESPACE, "/".join(parts))) + "}"


def _line(
    parent: ET.Element, x1: float, y1: float, x2: float, y2: float, dashed: bool = False
) -> None:
    ET.SubElement(
        parent,
        "line",
        x1=str(x1),
        y1=str(y1),
        x2=str(x2),
        y2=str(y2),
        end1="none",
        end2="none",
        length1="1.5",
        length2="1.5",
        style=_DASHED if dashed else _LINE,
        antialias="true",
    )


def _definition(name: str, english: str, arabic: str) -> tuple[ET.Element, ET.Element]:
    element = ET.Element("element", name=name)
    definition = ET.SubElement(
        element,
        "definition",
        type="element",
        version="0.3",
        width="30",
        height="50",
        hotspot_x="15",
        hotspot_y="25",
    )
    names = ET.SubElement(definition, "names")
    ET.SubElement(names, "name", lang="en").text = english
    ET.SubElement(names, "name", lang="ar").text = arabic
    ET.SubElement(definition, "informations").text = "PanelPilot, IEC 60617 single-line symbol"
    return element, ET.SubElement(definition, "description")


def _terminal_uuid(kind: str, end: str) -> str:
    """The definition's terminal uuid, which conductors refer to."""
    return _uuid("terminal", kind, end)


def _terminals(body: ET.Element, kind: str) -> None:
    if kind != SUPPLY:
        ET.SubElement(
            body,
            "terminal",
            x="0",
            y="-20",
            orientation="n",
            name="",
            uuid=_terminal_uuid(kind, "top"),
        )
    if kind != LOAD:
        ET.SubElement(
            body,
            "terminal",
            x="0",
            y="20",
            orientation="s",
            name="",
            uuid=_terminal_uuid(kind, "bottom"),
        )


def _symbols() -> list[ET.Element]:
    """The embedded symbols: breaker, residual current device, load, supply."""
    elements: list[ET.Element] = []

    breaker, body = _definition(BREAKER, "Circuit-breaker", "قاطع")
    _line(body, 0, -20, 0, -8)
    _line(body, -2, -10, 2, -6)
    _line(body, -2, -6, 2, -10)
    _line(body, 0, 6, -6, -7)
    _line(body, 0, 6, 0, 20)
    _terminals(body, BREAKER)
    elements.append(breaker)

    rcd, body = _definition(RCD, "Residual current circuit-breaker", "قاطع تسرّب")
    _line(body, 0, -20, 0, -8)
    _line(body, 0, 4, -6, -7)
    _line(body, 0, 4, 0, 20)
    ET.SubElement(
        body, "ellipse", x="-5", y="9", width="10", height="4", style=_LINE, antialias="true"
    )
    _line(body, -5, 11, -9, 11, dashed=True)
    _line(body, -9, 11, -9, -2, dashed=True)
    _line(body, -9, -2, -4, -2, dashed=True)
    _terminals(body, RCD)
    elements.append(rcd)

    load, body = _definition(LOAD, "Outgoing circuit", "مخرج")
    _line(body, 0, -20, 0, -3)
    ET.SubElement(body, "circle", x="-3", y="-3", diameter="6", style=_LINE, antialias="true")
    _terminals(body, LOAD)
    elements.append(load)

    supply, body = _definition(SUPPLY, "Supply", "تغذية")
    _line(body, -8, -6, 8, -6)
    _line(body, 0, -6, 0, 20)
    _terminals(body, SUPPLY)
    elements.append(supply)
    return elements


#: A connection point: the element instance's uuid and its terminal's uuid.
_End = tuple[str, str]


class _Diagram:
    """One folio being built: its elements, conductors and terminal ids."""

    def __init__(self, title: str, order: int, folio_total: int, project: DesignProject) -> None:
        self.root = ET.Element(
            "diagram",
            title=title,
            order=str(order),
            folio="%id/%total",
            cols="17",
            colsize="60",
            rows="8",
            rowsize="80",
            height="660",
            displaycols="true",
            displayrows="true",
            displayAt="bottom",
            version="0.90",
            author=project.info.name,
            date="null",
            filename="",
            plant="",
            locmach="",
            indexrev=project.info.revisions[-1].index if project.info.revisions else "",
            freezeNewElement="false",
            freezeNewConductor="false",
            auto_page_num="",
        )
        del folio_total
        ET.SubElement(self.root, "defaultconductor", type="multi", num="_")
        self.elements = ET.SubElement(self.root, "elements")
        self.conductors = ET.SubElement(self.root, "conductors")
        ET.SubElement(self.root, "inputs")
        self.next_terminal = 0

    def element(
        self, kind: str, x: float, y: float, key: str, label: str, comment: str
    ) -> tuple[_End | None, _End | None]:
        """Place an element; return its (top, bottom) connection points."""
        node = ET.SubElement(
            self.elements,
            "element",
            type=f"embed://import/panelpilot/{kind}",
            x=str(x),
            y=str(y),
            z="10",
            orientation="0",
            prefix="",
            freezeLabel="false",
            uuid=_uuid(key),
        )
        terminals = ET.SubElement(node, "terminals")
        element_uuid = node.get("uuid", "")
        top: _End | None = None
        bottom: _End | None = None
        if kind != SUPPLY:
            top = (element_uuid, _terminal_uuid(kind, "top"))
            ET.SubElement(
                terminals, "terminal", x="0", y="-20", orientation="0", id=str(self.next_terminal)
            )
            self.next_terminal += 1
        if kind != LOAD:
            bottom = (element_uuid, _terminal_uuid(kind, "bottom"))
            ET.SubElement(
                terminals, "terminal", x="0", y="20", orientation="2", id=str(self.next_terminal)
            )
            self.next_terminal += 1
        ET.SubElement(node, "inputs")
        infos = ET.SubElement(node, "elementInformations")
        ET.SubElement(infos, "elementInformation", name="label", show="1").text = label
        ET.SubElement(infos, "elementInformation", name="comment", show="1").text = comment
        texts = ET.SubElement(node, "dynamic_texts")
        for n, info in enumerate(("label", "comment")):
            text = ET.SubElement(
                texts,
                "dynamic_elmt_text",
                x="8",
                y=str(-12 + n * 12) if kind != LOAD else str(4 + n * 12),
                rotation="0",
                text_from="ElementInfo",
                Halignment="AlignLeft",
                Valignment="AlignTop",
                font=_FONT,
                text_width="-1",
                keep_visual_rotation="true",
                frame="false",
                uuid=_uuid(key, info),
            )
            ET.SubElement(text, "text").text = label if info == "label" else comment
            ET.SubElement(text, "info_name").text = info
        ET.SubElement(node, "texts_groups")
        return top, bottom

    def connect(self, first: _End | None, second: _End | None, section: str = "") -> None:
        """Join two terminals with a conductor."""
        if first is None or second is None:
            return
        ET.SubElement(
            self.conductors,
            "conductor",
            element1=first[0],
            terminal1=first[1],
            element2=second[0],
            terminal2=second[1],
            type="multi",
            num="",
            condsize="1",
            numsize="9",
            displaytext="1",
            onetextperfolio="0",
            vertirotatetext="270",
            horizrotatetext="0",
            conductor_color="",
            conductor_section=section,
            cable="",
            bus="",
            function="",
            tension_protocol="",
            formula="",
            bicolor="false",
            color2="#000000",
            text_color="#000000",
            freezeLabel="false",
            x="0",
            y="0",
        )


def _label(device: Device) -> str:
    return str(device.designation.product) if device.designation else device.id


def _rating(device: Device) -> str:
    poles = f"{device.poles}P" if device.poles else ""
    if device.kind is DeviceKind.RESIDUAL_CURRENT_DEVICE:
        return f"{_plain(device.rated_current_a)} A {_plain(device.residual_current_ma)} mA {poles}".strip()
    if device.rated_current_a is None:
        return f"not selected {poles}".strip()
    return f"{device.curve or ''}{_plain(device.rated_current_a)} {poles}".strip()


def _feeders(board: Board) -> list[tuple[str | None, list[Circuit]]]:
    feeders: list[tuple[str | None, list[Circuit]]] = []
    for circuit in board.circuits:
        if circuit.upstream_id is not None and feeders and feeders[-1][0] == circuit.upstream_id:
            feeders[-1][1].append(circuit)
        else:
            feeders.append((circuit.upstream_id, [circuit]))
    return feeders


def _main_diagram(diagram: _Diagram, board: Board) -> None:
    supply_bottom = diagram.element(
        SUPPLY, 60, 80, f"{board.id}/supply", board.name, f"{_plain(board.supply.voltage_v)} V"
    )[1]
    previous = supply_bottom
    y = 160
    for incomer_id in board.incomer_ids:
        incomer = board.device(incomer_id)
        top, bottom = diagram.element(
            BREAKER, 60, y, f"{board.id}/{incomer.id}", _label(incomer), _rating(incomer)
        )
        diagram.connect(previous, top)
        previous = bottom
        y += 80
    bus_previous = previous
    for column, (upstream, circuits) in enumerate(_feeders(board)[:_PER_DIAGRAM]):
        x = _FIRST_X + 60 + column * _COLUMN_WIDTH
        chain: list[Device] = []
        if upstream is not None:
            rcd = board.device(upstream)
            if rcd.upstream_id is not None and rcd.upstream_id not in board.incomer_ids:
                chain.append(board.device(rcd.upstream_id))
            chain.append(rcd)
        else:
            chain.append(board.device(circuits[0].device_ids[0]))
        row = 320
        first_top: _End | None = None
        last_bottom: _End | None = None
        for device in chain:
            kind = RCD if device.kind is DeviceKind.RESIDUAL_CURRENT_DEVICE else BREAKER
            top, bottom = diagram.element(
                kind, x, row, f"{board.id}/main/{device.id}", _label(device), _rating(device)
            )
            if first_top is None:
                first_top = top
            else:
                diagram.connect(last_bottom, top)
            last_bottom = bottom
            row += 80
        # The busbar: each feeder's top joined to the previous one.
        diagram.connect(bus_previous, first_top)
        bus_previous = first_top
        description = "Group" if upstream is not None else circuits[0].description
        load_top = diagram.element(
            LOAD, x, row + 10, f"{board.id}/main/out/{column}", description, ""
        )[0]
        diagram.connect(last_bottom, load_top)


def _group_diagram(
    diagram: _Diagram, board: Board, upstream: str | None, circuits: list[Circuit]
) -> None:
    if upstream is not None:
        rcd = board.device(upstream)
        source = diagram.element(
            SUPPLY, 60, 80, f"{board.id}/{upstream}/source", _label(rcd), _rating(rcd)
        )[1]
    else:
        source = diagram.element(SUPPLY, 60, 80, f"{board.id}/busbar/source", "Busbar", board.name)[
            1
        ]
    previous = source
    for column, circuit in enumerate(circuits):
        x = _FIRST_X + column * _COLUMN_WIDTH
        breaker = board.device(circuit.device_ids[0])
        top, bottom = diagram.element(
            BREAKER,
            x,
            200,
            f"{board.id}/{breaker.id}",
            _label(breaker),
            f"{_rating(breaker)} {circuit.phase.value}",
        )
        diagram.connect(previous, top)
        previous = top
        cable = board.cable(circuit.cable_id) if circuit.cable_id else None
        section = (
            f"{cable.cores}G{_plain(cable.cross_section_mm2)} {cable.material}" if cable else ""
        )
        load_top = diagram.element(
            LOAD,
            x,
            330,
            f"{board.id}/{circuit.id}/load",
            circuit.description,
            f"{_plain(circuit.power_kw)} kW",
        )[0]
        diagram.connect(bottom, load_top, section)


def export_qet(project: DesignProject) -> bytes:
    """Write a project as a QElectroTech project file.

    Args:
        project: The project, designated.

    Returns:
        The ``.qet`` file's bytes (UTF-8 XML).
    """
    root = ET.Element("project", title=project.info.name, version="0.90")
    properties = ET.SubElement(root, "properties")
    ET.SubElement(properties, "property", name="savedfilename", show="1").text = project.info.name
    new = ET.SubElement(root, "newdiagrams")
    ET.SubElement(
        new,
        "border",
        cols="17",
        colsize="60",
        rows="8",
        rowsize="80",
        displaycols="true",
        displayrows="true",
    )
    ET.SubElement(
        new,
        "inset",
        folio="%id/%total",
        displayAt="bottom",
        title="",
        author="",
        date="null",
        filename="",
        plant="",
        locmach="",
        indexrev="",
        version="",
        auto_page_num="",
    )
    ET.SubElement(
        new, "conductors", type="multi", num="_", condsize="1", numsize="9", displaytext="1"
    )

    plans: list[tuple[str, Board, str | None, list[Circuit] | None]] = []
    for board in project.boards:
        plans.append((f"{board.name} - Main power", board, None, None))
        for upstream, circuits in _feeders(board):
            for start in range(0, len(circuits), _PER_DIAGRAM):
                plans.append(
                    (
                        f"{board.name} - Distribution",
                        board,
                        upstream,
                        circuits[start : start + _PER_DIAGRAM],
                    )
                )
    for order, (title, plan_board, upstream, chunk) in enumerate(plans, start=1):
        diagram = _Diagram(title, order, len(plans), project)
        if chunk is None:
            _main_diagram(diagram, plan_board)
        else:
            _group_diagram(diagram, plan_board, upstream, chunk)
        root.append(diagram.root)

    collection = ET.SubElement(root, "collection")
    imported = ET.SubElement(collection, "category", name="import")
    ET.SubElement(ET.SubElement(imported, "names"), "name", lang="en").text = "Imported elements"
    ours = ET.SubElement(imported, "category", name="panelpilot")
    ET.SubElement(ET.SubElement(ours, "names"), "name", lang="en").text = "PanelPilot"
    for element in _symbols():
        ours.append(element)
    ET.indent(root, space="    ")
    body: bytes = ET.tostring(root, encoding="utf-8")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body
