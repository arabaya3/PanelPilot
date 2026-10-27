"""The design model as plain values: every field a saved design holds, nothing more.

This is schema ``pp-design/1`` (today's ``DesignProject``) expressed as frozen
dataclasses, free of any framework, so later milestones can grow the model
(terminals, nets, rules) without the persistence or transport types leaking
into it. It mirrors the v1 content field for field, under the same names, so
``convert_v1`` can map one onto the other losslessly; nothing here is a
placeholder for content that does not exist yet.

Conventions:

* Quantities are ``Decimal``, never ``float``.
* Ordered collections are tuples, in the order the design gives them.
* Mappings (a note's or a finding's text parameters) are read-only views.
* Kinds (a device's, a load's, a phase, a starter) are their v1 string values.
  They are data, not Python enums, so a future component-kind registry can
  add a kind without a code change here; ``convert_v1`` checks them against
  the v1 schema on the way back.

Invariants that span objects (unique ids, references that resolve) are the
v1 schema's and are checked when a model is converted back to v1; this module
only holds values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Note:
    """Something the design tells its reviewer (v1 ``DesignNote``)."""

    code: str
    params: Mapping[str, str]
    text: str


@dataclass(frozen=True)
class Designation:
    """An IEC 81346-1 reference designation, its aspects without prefixes."""

    function: str | None
    location: str | None
    product: str


@dataclass(frozen=True)
class Part:
    """An orderable article."""

    key: str
    manufacturer: str
    type_number: str
    order_number: str | None
    description: str
    source: str | None
    width_mm: Decimal | None


@dataclass(frozen=True)
class Device:
    """A device on a board; ``kind`` is a v1 ``DeviceKind`` value."""

    id: str
    kind: str
    designation: Designation | None
    part_key: str | None
    poles: int | None
    rated_current_a: Decimal | None
    residual_current_ma: Decimal | None
    curve: str | None
    breaking_capacity_ka: Decimal | None
    description: str
    upstream_id: str | None


@dataclass(frozen=True)
class Cable:
    """An outgoing cable."""

    id: str
    designation: Designation | None
    cores: int
    cross_section_mm2: Decimal
    material: str
    insulation: str
    length_m: Decimal | None
    part_key: str | None
    withstand_ka2s: Decimal | None
    parallel: int


@dataclass(frozen=True)
class Circuit:
    """An outgoing circuit; ``load``, ``phase`` and ``starter`` are v1 enum values."""

    id: str
    description: str
    load: str
    power_kw: Decimal
    design_current_a: Decimal
    phase: str
    upstream_id: str | None
    device_ids: tuple[str, ...]
    cable_id: str | None
    feeds: str | None
    starter: str | None
    voltage_drop_percent: Decimal | None
    earth_loop_ohm: Decimal | None


@dataclass(frozen=True)
class Supply:
    """The supply a board is connected to."""

    voltage_v: Decimal
    phases: int
    frequency_hz: Decimal
    earthing: str
    fault_level_ka: Decimal | None
    earth_loop_ohm: Decimal | None


@dataclass(frozen=True)
class Board:
    """A distribution board: an assembly of devices, cables and circuits."""

    id: str
    name: str
    function: str | None
    location: str | None
    supply: Supply
    incomer_ids: tuple[str, ...]
    devices: tuple[Device, ...]
    cables: tuple[Cable, ...]
    circuits: tuple[Circuit, ...]
    notes: tuple[Note, ...]
    fed_from: str | None


@dataclass(frozen=True)
class Revision:
    """A row of the title block's revision list."""

    index: str
    date: str
    description: str
    drawn_by: str
    checked_by: str
    approved_by: str


@dataclass(frozen=True)
class ProjectInfo:
    """The title block."""

    name: str
    number: str
    customer: str
    consultant: str
    contractor: str
    revisions: tuple[Revision, ...]


@dataclass(frozen=True)
class DesignModel:
    """A whole design: the boards of one project and the parts they use."""

    info: ProjectInfo
    profile: str
    boards: tuple[Board, ...]
    parts: tuple[Part, ...]
