"""Generate the M1 compatibility fixtures from the real server design pipeline.

Each fixture is what a Phase 1 save stores for a request: the columns
``app.domain.design_projects._validate`` returns (snapshot, its SHA-256,
profile hash, rules and catalogue versions, findings), next to the request
that produced it. Nothing here is hand-written: every snapshot comes from
``build_design`` through ``_validate``. Two fixtures are *augmented*: a
pipeline design with devices or values the pipeline never emits today (every
``DeviceKind``, Decimal edge values), added through the strict
``DesignProject`` schema and hashed exactly as ``_validate`` hashes.

Every name is synthetic. No customer data.

Run from ``apps/api``::

    python -m app.tests.design.model.fixtures.generate

The outputs (``*.json`` beside this file) are committed; the tests read them,
so a pipeline change shows up as a diff here rather than silently.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.design import markets, snapshot
from app.domain.design_projects import _validate
from app.models.schemas.design import (
    Cable,
    Designation,
    DesignProject,
    Device,
    DeviceKind,
    Part,
    ProjectDesignRequest,
)
from app.tests.design.model.compat_support import bounds_of, valid_edges

HERE = Path(__file__).parent

#: A string at ``ProjectInfo``'s 200-character limit.
LONG_NAME = ("Synthetic-" * 20)[:200]

Request = dict[str, Any]


def _load(description: str, load: str, kw: str, **extra: Any) -> dict[str, Any]:
    return {"description": description, "load": load, "power_kw": kw, **extra}


def _board(name: str, loads: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"name": name, "loads": loads, **extra}


def _request(name: str, boards: list[dict[str, Any]], **info: Any) -> Request:
    return {"info": {"name": name, **info}, "boards": boards}


RESIDENTIAL: Request = _request(
    "Synthetic Villa",
    [
        _board(
            "DB-V",
            [
                _load("Lighting ground", "lighting", "0.8", length_m="15"),
                _load("Sockets kitchen", "socket", "2.0", length_m="12"),
                _load("Water heater", "water_heater", "3.0"),
            ],
            supply={"voltage_v": "230", "phases": 1, "fault_level_ka": "6"},
        )
    ],
    number="SYN-001",
)

MULTI_BOARD: Request = _request(
    "Synthetic Tower",
    [
        _board(
            "MDB",
            [_load("Plant lighting", "lighting", "1.2")],
            supply={"fault_level_ka": "25", "earth_loop_ohm": "0.2"},
        ),
        _board(
            "SMDB-1",
            [_load("Floor 1 sockets", "socket", "3.0"), _load("Floor 1 lighting", "lighting", "1")],
            fed_from="MDB",
            feeder_length_m="40",
        ),
        _board(
            "SMDB-2",
            [_load("Floor 2 sockets", "socket", "2.5")],
            fed_from="MDB",
        ),
        _board("DB-2A", [_load("Office lighting", "lighting", "0.6")], fed_from="SMDB-2"),
    ],
    number="SYN-002",
)

MOTORS: Request = _request(
    "Synthetic Pump Room",
    [
        _board(
            "MCC-1",
            [
                _load("Pump DOL", "motor", "5.5", phases=3, starter="dol", length_m="20"),
                _load("Fan star-delta", "motor", "22", phases=3, starter="star_delta"),
                _load("Compressor drive", "motor", "15", phases=3, starter="drive"),
            ],
            supply={"fault_level_ka": "15"},
        )
    ],
)

PLC: Request = _request(
    "Synthetic PLC Skid",
    [
        _board(
            "CP-1",
            [
                _load("Dosing pump", "motor", "4", phases=3, starter="dol", controlled=True),
                _load("Mixer", "motor", "7.5", phases=3, starter="drive", controlled=True),
                _load("Panel lighting", "lighting", "0.2", controlled=True),
            ],
            supply={"fault_level_ka": "10"},
        )
    ],
)

ARABIC: Request = _request(
    "مشروع تجريبي – لوحة توزيع",
    [_board("DB-AR", [_load("إنارة الطابق الأرضي", "lighting", "1.0")])],
    customer="عميل تجريبي",
)

HEBREW: Request = _request(
    "פרויקט לדוגמה – לוח חשמל",
    [_board("DB-HE", [_load("תאורה קומה א", "lighting", "0.9")])],
    consultant="יועץ לדוגמה",
)

MAX_LENGTH: Request = _request(
    LONG_NAME,
    [
        _board(
            "B" * 40,
            [_load(LONG_NAME, "socket", "1.5", feeds="F" * 40)],
            location="L" * 200,
        )
    ],
    number=LONG_NAME,
    customer=LONG_NAME,
    consultant=LONG_NAME,
    contractor=LONG_NAME,
)

DECIMAL_INPUTS: Request = _request(
    "Synthetic Decimals",
    [
        _board(
            "DB-DEC",
            [
                _load("Tiny load", "lighting", "0.001"),
                _load("Many decimals", "socket", "1.123456789012345678"),
                _load("Big motor", "motor", "90", phases=3, starter="drive"),
            ],
            supply={"fault_level_ka": "50"},
        )
    ],
)

OPTIONAL_ABSENT: Request = _request(
    "Synthetic Minimal", [_board("DB-MIN", [_load("L", "lighting", "0.1")])]
)

OPTIONAL_PRESENT: Request = _request(
    "Synthetic Full",
    [
        _board(
            "DB-FULL",
            [
                _load(
                    "Fully specified",
                    "motor",
                    "3",
                    phases=3,
                    power_factor="0.85",
                    controlled=True,
                    feeds="M-01",
                    starter="dol",
                    length_m="35",
                )
            ],
            location="Plant room",
            supply={
                "voltage_v": "400",
                "phases": 3,
                "frequency_hz": "50",
                "earthing": "TN-C-S",
                "fault_level_ka": "20",
                "earth_loop_ohm": "0.35",
            },
            conditions={
                "installation_method": "C",
                "ambient_temp_c": "40",
                "grouped_circuits": 3,
                "insulation_rating_c": 90,
            },
        )
    ],
    number="SYN-FULL",
    customer="Synthetic Customer",
    consultant="Synthetic Consultant",
    contractor="Synthetic Contractor",
)

#: A TT board whose motor has no RCD: a blocking ``earth_fault_tt_no_rcd``.
FINDINGS: Request = _request(
    "Synthetic Findings",
    [
        _board(
            "DB-FIND",
            [
                _load("Far lighting", "lighting", "2", length_m="900"),
                _load("Pump", "motor", "11", phases=3, starter="dol"),
            ],
            supply={"fault_level_ka": "40", "earthing": "TT", "earth_loop_ohm": "20"},
        )
    ],
)

#: A request the design refuses: saved with no snapshot and a blocking finding.
REFUSED: Request = _request(
    "Synthetic Refused",
    [_board("DB-R", [_load("L", "lighting", "1")], fed_from="NOWHERE")],
)


def _market(code: str) -> Request:
    request = _request(
        f"Synthetic {code} market",
        [
            _board(
                f"DB-{code}",
                [_load("Lighting", "lighting", "1"), _load("Sockets", "socket", "2")],
            )
        ],
    )
    request["profile"] = {"key": f"syn-{code.lower()}", "name": f"Synthetic {code}", "market": code}
    return request


def _stored(request: Request) -> dict[str, Any]:
    return _validate(ProjectDesignRequest.model_validate(request))


def _rehash(project: DesignProject, base: dict[str, Any]) -> dict[str, Any]:
    """Store a schema-built project exactly as ``_validate`` stores one."""
    dumped = project.model_dump(mode="json")
    return {**base, "design_snapshot": dumped, "snapshot_sha256": snapshot.sha256_of(dumped)}


def _every_kind(base: dict[str, Any]) -> dict[str, Any]:
    project = DesignProject.model_validate(base["design_snapshot"])
    board = project.boards[0]
    present = {device.kind for device in board.devices}
    extra = [
        Device(
            id=f"x-{kind.value}",
            kind=kind,
            designation=Designation(function="F1", location="L1", product=f"X{i}"),
            description=f"Synthetic {kind.value}",
        )
        for i, kind in enumerate(DeviceKind, start=1)
        if kind not in present
    ]
    board.devices.extend(extra)
    return _rehash(project, base)


#: Exotic Decimal texts, kept on a field the schema leaves unbounded.
UNBOUNDED_EDGES = ("0", "-0", "0.000000001", "1E-12", "99999999999999999999.99", "1.10", "1E+3")

#: The bounded Decimal fields whose edges the fixture holds, by model.
DEVICE_FIELDS = ("rated_current_a", "residual_current_ma", "breaking_capacity_ka")
CABLE_FIELDS = ("cross_section_mm2", "withstand_ka2s", "length_m")


def _decimal_edges(base: dict[str, Any]) -> dict[str, Any]:
    """Every bounded field at (and one step inside) each bound its schema metadata declares.

    The bounds are read from the schema (``compat_support.bounds_of``), so the
    fixture follows the schema when a bound changes. Exotic texts go on
    ``Part.width_mm``, which is unbounded.
    """
    project = DesignProject.model_validate(base["design_snapshot"])
    board = project.boards[0]
    for name in DEVICE_FIELDS:
        for i, value in enumerate(valid_edges(bounds_of(Device, name))):
            board.devices.append(
                Device(id=f"dec-{name}-{i}", kind=DeviceKind.OTHER, **{name: value})
            )
    for name in CABLE_FIELDS:
        for i, value in enumerate(valid_edges(bounds_of(Cable, name))):
            board.cables.append(
                Cable(
                    id=f"dec-{name}-{i}",
                    cores=1,
                    **{"cross_section_mm2": Decimal("1.5"), name: value},
                )
            )
    for i, text in enumerate(UNBOUNDED_EDGES):
        project.parts.append(
            Part(
                key=f"dec-part-{i}",
                manufacturer="Synthetic",
                type_number=f"T{i}",
                description="Decimal edge",
                width_mm=Decimal(text),
            )
        )
    return _rehash(DesignProject.model_validate(project.model_dump()), base)


def _empty_lists(base: dict[str, Any]) -> dict[str, Any]:
    """A designed project emptied of every list: the schema's floor."""
    project = DesignProject.model_validate(base["design_snapshot"])
    project.parts = []
    for board in project.boards:
        board.devices, board.cables, board.circuits, board.notes = [], [], [], []
        board.incomer_ids = []
    return _rehash(project, base)


CASES: dict[str, tuple[Request, Callable[[dict[str, Any]], dict[str, Any]] | None]] = {
    "residential": (RESIDENTIAL, None),
    "multi_board": (MULTI_BOARD, None),
    "motors": (MOTORS, None),
    "plc": (PLC, None),
    "arabic": (ARABIC, None),
    "hebrew": (HEBREW, None),
    "max_length": (MAX_LENGTH, None),
    "decimal_inputs": (DECIMAL_INPUTS, None),
    "optional_absent": (OPTIONAL_ABSENT, None),
    "optional_present": (OPTIONAL_PRESENT, None),
    "findings": (FINDINGS, None),
    "refused": (REFUSED, None),
    "every_device_kind": (RESIDENTIAL, _every_kind),
    "decimal_edges": (RESIDENTIAL, _decimal_edges),
    "empty_lists": (RESIDENTIAL, _empty_lists),
    **{f"market_{code.lower()}": (_market(code), None) for code in markets.MARKETS},
}


def generate() -> list[Path]:
    """Write every fixture; return the paths written."""
    written: list[Path] = []
    for name, (request, augment) in CASES.items():
        stored = _stored(request)
        if augment is not None:
            stored = augment(stored)
        out = HERE / f"{name}.json"
        body = {
            "case": name,
            "source": "augmented" if augment is not None else "pipeline",
            "request": request,
            "stored": stored,
        }
        out.write_text(
            json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        written.append(out)
    return written


if __name__ == "__main__":
    for path in generate():
        print(path.name)
