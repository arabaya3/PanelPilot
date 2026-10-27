"""M1 compatibility: random valid designs round-trip losslessly through the model.

``hypothesis`` is not a dependency of this project, so this is a seeded
generator over the v1 schema instead: every field of every ``DesignProject``
class is set or left out at random, with edge strings (RTL, emoji, quotes,
empty, at length limits) and edge Decimals (zero, negative zero, exponents,
trailing zeros, very large and very small). The seed is fixed, so a failure
is reproducible from its case number alone.

Each project is built through the strict schema, so only valid designs are
generated; the assertion is that ``to_v1(from_v1(p))`` is ``p``, leaf by leaf
with Decimals by their exact text, and gives the same canonical bytes.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

import pytest

from app.design import snapshot
from app.design.model import serialize
from app.design.model.convert_v1 import from_v1, to_v1
from app.design.model.read import read_snapshot
from app.models.schemas.design import DesignProject, DeviceKind, LoadKind, MotorStarter, Phase
from app.tests.design.model.compat_support import strict_shape

SEED = 20261027
CASES = 300

_STRINGS = (
    "",
    "A",
    "Q12",
    "مشروع تجريبي",
    "פרויקט לדוגמה",
    "mixed עברית and عربي 123",
    'quote " and \\ backslash',
    "tab\tnewline\n",
    "emoji \U0001f50c",
    "Ünïcödé café",
    "‏‎ marks",
    "x" * 200,
)

_DECIMALS = (
    "0",
    "-0",
    "0.0",
    "1",
    "1.10",
    "1.000000000000000000001",
    "0.000000001",
    "1E-12",
    "1E+3",
    "99999999999999999999.99",
    "123456789012345678901234567890",
    "-5.5",
    "400",
    "0.85",
)


class _Gen:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)

    def maybe(self, value: Any) -> Any:
        return value if self.rng.random() < 0.5 else None

    def text(self) -> str:
        if self.rng.random() < 0.3:
            return "".join(
                self.rng.choice("abcXYZ019 -_./عבש") for _ in range(self.rng.randint(0, 30))
            )
        return self.rng.choice(_STRINGS)

    def short(self, limit: int) -> str:
        return self.text()[:limit]

    def decimal(self) -> str:
        if self.rng.random() < 0.4:
            digits = self.rng.randint(0, 12)
            return str(round(Decimal(self.rng.uniform(0, 10_000)), digits))
        return self.rng.choice(_DECIMALS)

    def positive(self, high: int) -> str:
        """A Decimal in (0, high], for the bounded fields."""
        return self.rng.choice(("0.001", "1", "1.50", str(high), "12.345678")) if high > 12 else "1"

    def params(self) -> dict[str, str]:
        return {f"k{i}{self.short(5)}": self.text() for i in range(self.rng.randint(0, 3))}

    def designation(self) -> dict[str, Any] | None:
        if self.rng.random() < 0.3:
            return None
        return {
            "function": self.maybe(self.short(20)),
            "location": self.maybe(self.short(20)),
            "product": self.short(20) or "Q1",
        }

    def note(self) -> dict[str, Any]:
        return {"code": self.short(30) or "note", "params": self.params(), "text": self.text()}

    def device(self, ident: str, earlier: list[str], parts: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {"id": ident, "kind": self.rng.choice(list(DeviceKind)).value}
        optional = {
            "designation": self.designation(),
            "part_key": self.rng.choice(parts) if parts and self.rng.random() < 0.4 else None,
            "poles": self.rng.randint(1, 4),
            "rated_current_a": self.decimal(),
            "residual_current_ma": self.decimal(),
            "curve": self.short(3),
            "breaking_capacity_ka": self.decimal(),
            "description": self.text(),
            "upstream_id": self.rng.choice(earlier) if earlier else None,
        }
        for key, value in optional.items():
            if self.rng.random() < 0.6:
                out[key] = value
        return out

    def cable(self, ident: str, parts: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": ident,
            "cores": self.rng.randint(1, 61),
            "cross_section_mm2": self.decimal(),
        }
        optional = {
            "designation": self.designation(),
            "material": self.rng.choice(("Cu", "Al", self.short(5))),
            "insulation": self.rng.choice(("PVC", "XLPE", self.short(5))),
            "length_m": self.positive(10_000),
            "part_key": self.rng.choice(parts) if parts and self.rng.random() < 0.4 else None,
            "withstand_ka2s": self.decimal(),
            "parallel": self.rng.randint(1, 6),
        }
        for key, value in optional.items():
            if self.rng.random() < 0.6:
                out[key] = value
        return out

    def circuit(self, ident: str, devices: list[str], cables: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": ident,
            "description": self.text(),
            "load": self.rng.choice(list(LoadKind)).value,
            "power_kw": self.decimal(),
            "design_current_a": self.decimal(),
            "phase": self.rng.choice(list(Phase)).value,
        }
        optional = {
            "upstream_id": self.rng.choice(devices) if devices else None,
            # Order matters and repeats are allowed: kept exactly.
            "device_ids": (
                [self.rng.choice(devices) for _ in range(self.rng.randint(0, 4))] if devices else []
            ),
            "cable_id": self.rng.choice(cables) if cables else None,
            "feeds": self.maybe(self.short(40)),
            "starter": self.maybe(self.rng.choice(list(MotorStarter)).value),
            "voltage_drop_percent": self.decimal(),
            "earth_loop_ohm": self.decimal(),
        }
        for key, value in optional.items():
            if self.rng.random() < 0.6:
                out[key] = value
        return out

    def board(self, index: int, boards: list[str], parts: list[str]) -> dict[str, Any]:
        ident = f"b{index}"
        device_ids: list[str] = []
        devices = []
        for i in range(self.rng.randint(0, 8)):
            did = f"{ident}-d{i}"
            devices.append(self.device(did, device_ids, parts))
            device_ids.append(did)
        cables = [self.cable(f"{ident}-c{i}", parts) for i in range(self.rng.randint(0, 4))]
        cable_ids = [c["id"] for c in cables]
        circuits = [
            self.circuit(f"{ident}-k{i}", device_ids, cable_ids)
            for i in range(self.rng.randint(0, 5))
        ]
        out: dict[str, Any] = {"id": ident, "name": self.text()}
        supply: dict[str, Any] = {}
        for key, value in {
            "voltage_v": self.decimal(),
            "phases": self.rng.randint(1, 3),
            "frequency_hz": self.decimal(),
            "earthing": self.rng.choice(("TN-S", "TT", "IT", self.short(8))),
            "fault_level_ka": self.positive(200),
            "earth_loop_ohm": self.positive(100),
        }.items():
            if self.rng.random() < 0.5:
                supply[key] = value
        optional = {
            "function": self.maybe(self.short(20)),
            "location": self.maybe(self.short(20)),
            "supply": supply,
            "incomer_ids": self.rng.sample(device_ids, k=min(len(device_ids), 2)),
            "devices": devices,
            "cables": cables,
            "circuits": circuits,
            "notes": [self.note() for _ in range(self.rng.randint(0, 3))],
            "fed_from": self.rng.choice(boards) if boards else None,
        }
        # What refers to devices or cables is left out with them, so a board
        # that omits its lists stays valid.
        dependants = {"devices": ("incomer_ids", "circuits"), "cables": ("circuits",)}
        dropped: set[str] = set()
        for key in optional:
            if key not in dropped and self.rng.random() >= 0.8:
                dropped |= {key, *dependants.get(key, ())}
        out.update({key: value for key, value in optional.items() if key not in dropped})
        return out

    def project(self) -> DesignProject:
        parts = [
            {
                "key": f"p{i}",
                "manufacturer": self.text(),
                "type_number": self.text(),
                "order_number": self.maybe(self.text()),
                "description": self.text(),
                "source": self.maybe(self.text()),
                "width_mm": self.maybe(self.decimal()),
            }
            for i in range(self.rng.randint(0, 4))
        ]
        keys = [p["key"] for p in parts]
        boards: list[dict[str, Any]] = []
        for i in range(self.rng.randint(0, 4)):
            boards.append(self.board(i, [b["id"] for b in boards], keys))
        info: dict[str, Any] = {"name": self.text()}
        for key in ("number", "customer", "consultant", "contractor"):
            if self.rng.random() < 0.5:
                info[key] = self.text()
        info["revisions"] = [
            {
                "index": self.short(40),
                "date": self.short(40),
                "description": self.text(),
                "drawn_by": self.short(100),
                "checked_by": self.short(100),
                "approved_by": self.short(100),
            }
            for _ in range(self.rng.randint(0, 3))
        ]
        body: dict[str, Any] = {"info": info, "boards": boards, "parts": parts}
        if self.rng.random() < 0.5:
            body["profile"] = self.short(40) or "iec-default"
        return DesignProject.model_validate(body)


def _projects() -> list[DesignProject]:
    gen = _Gen(SEED)
    return [gen.project() for _ in range(CASES)]


PROJECTS = _projects()


def test_generator_exercises_the_schema() -> None:
    """The generator is not degenerate: it sets every kind and empties and fills lists."""
    kinds = {d.kind for p in PROJECTS for b in p.boards for d in b.devices}
    assert kinds == set(DeviceKind)
    assert any(not p.boards for p in PROJECTS)
    assert any(len(p.boards) >= 3 for p in PROJECTS)
    assert any(b.fed_from for p in PROJECTS for b in p.boards)
    assert any(p.info.revisions for p in PROJECTS)


@pytest.mark.parametrize("index", range(CASES))
def test_random_project_round_trips_losslessly(index: int) -> None:
    project = PROJECTS[index]
    dumped = project.model_dump(mode="json")
    model = from_v1(project)
    back = to_v1(model)
    assert back == project
    assert strict_shape(back.model_dump()) == strict_shape(project.model_dump())
    assert back.model_dump(mode="json") == dumped
    assert serialize.canonical_v1(model) == snapshot.canonical_json(dumped)
    assert serialize.sha256_v1(model) == snapshot.sha256_of(dumped)
    read = read_snapshot(dumped)
    assert read.project == project
    assert from_v1(dumped) == model
