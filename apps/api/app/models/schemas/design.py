"""The design project: one tool-neutral description of a panel design.

Every output -- the drawing set, the parts and terminal lists, and each ECAD
tool's import file -- is generated from this one model. Nothing here knows
about pages, coordinates or any tool's file format: a model that did would
tie every company to one tool's way of drawing.

Structure follows IEC 81346-1 reference designations, which EPLAN, E3.series
and AutoCAD Electrical all understand: a function aspect (``=``), a location
aspect (``+``) and a product aspect (``-``). A device's product designation
("Q12") is assigned from the company profile, never typed into a design, so
the same design can be issued under any company's conventions.

Every physical quantity carries its unit in the field name and is a
``Decimal``, as in the calculation schemas, because these numbers end up on
drawings.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SerializationInfo,
    SerializeAsAny,
    SerializerFunctionWrapHandler,
    StringConstraints,
    field_serializer,
    model_validator,
)

from app.models.schemas.auth import Permission
from app.models.schemas.calculations import ConductorMaterial, InstallationMethod
from app.models.schemas.plc import PlcValidationResult


class StrictModel(BaseModel):
    """A model that refuses fields it does not define.

    Used for everything a client sends a design in: a field this service does
    not know (a ``standby_supply``, a load's ``colour``) is a 422 naming it,
    never silently dropped, so a design is never taken to cover what it
    ignored. Free-form company settings (``profile``) stay a plain mapping:
    ``app.design.profile.load_profile`` refuses unknown settings itself, at
    every level, since ``CompanyProfile`` and ``CircuitRule`` are strict too.
    """

    model_config = ConfigDict(extra="forbid")


# Bounds on free text a client sends in a design, so an oversized field is a
# 422 rather than a page-long title block. They match the limits a saved
# project already has: its name (200), a revision note (500), an approver (100).
_NAME_TEXT = 200
_NOTE_TEXT = 500
_PERSON_TEXT = 100
#: An approval's optional note (the ``approval_note`` column).
_APPROVAL_NOTE_TEXT = 200
_SHORT_TEXT = 40
#: A title block lists at most every revision a project holds
#: (``app.domain.design_projects.MAX_REVISIONS``).
_MAX_TITLE_REVISIONS = 200


#: Bounds on the engineering figures a sent project carries, far above any
#: real panel's and above every value the design's tables give (the largest
#: MCCB held is 800 A and 200 kA; the largest tabulated section 630 mm²). A figure
#: past them is a 422, never a 500 from the arithmetic or a megabyte finding.
MAX_RATED_CURRENT_A = Decimal(10000)
MAX_SECTION_MM2 = Decimal(1000)
MAX_BREAKING_CAPACITY_KA = Decimal(200)
MAX_RESIDUAL_CURRENT_MA = Decimal(3000)
#: k²S² of a 1000 mm² cable at the largest k tabulated is about 2·10⁴ (kA)²s.
MAX_WITHSTAND_KA2S = Decimal(1000000)
#: Digits a computed figure may carry: the decimal context's 28, with room.
_COMPUTED_DIGITS = 40


class DesignNote(StrictModel):
    """Something the design tells its reviewer.

    Attributes:
        code: Stable identifier, which the page renders in the reader's
            language (``app.design.notes``).
        params: The values the note names, as text.
        text: The note in English, for the drawing set and as a fallback.
    """

    code: str
    params: dict[str, str] = Field(default_factory=dict)
    text: str


class FindingSeverity(StrEnum):
    """How much a finding stands in the way of issuing a design."""

    #: The design breaks a sourced rule or is incomplete: it cannot be approved.
    BLOCKING = "blocking"
    #: An engineer must look at it before relying on the design.
    WARNING = "warning"
    #: Said for the record; nothing to decide.
    INFO = "info"


class DesignFinding(StrictModel):
    """One thing a check of a design found, with where to fix it.

    Attributes:
        code: Stable identifier: a ``DesignNote`` code, or an integrity
            check's own (``app.design.integrity``).
        severity: From ``app.design.findings_policy``.
        board_id: The board it is on, where it is on one.
        circuit_id: The circuit, where it is about one.
        device_id: The device, where it is about one.
        values: The values it names, as text.
        message: In English; the page renders ``code`` in the reader's language.
        requires_engineering_confirmation: Whether an engineer must confirm
            it before the design is relied on.
        source: The rule it comes from, or ``"policy"`` where the severity is
            this service's classification rather than a cited rule's.
    """

    code: str
    severity: FindingSeverity
    board_id: str | None = None
    circuit_id: str | None = None
    device_id: str | None = None
    values: dict[str, str] = Field(default_factory=dict)
    message: str
    requires_engineering_confirmation: bool = False
    source: str


class MotorStarter(StrEnum):
    """How a motor is started."""

    DIRECT_ON_LINE = "dol"
    STAR_DELTA = "star_delta"
    DRIVE = "drive"


class DeviceKind(StrEnum):
    """What a device does, which decides its designation letter."""

    CIRCUIT_BREAKER = "circuit_breaker"
    RESIDUAL_CURRENT_DEVICE = "residual_current_device"
    SWITCH_DISCONNECTOR = "switch_disconnector"
    CONTACTOR = "contactor"
    OVERLOAD_RELAY = "overload_relay"
    FUSE = "fuse"
    SURGE_PROTECTOR = "surge_protector"
    DRIVE = "drive"
    MOTOR = "motor"
    RELAY = "relay"
    BUS_ACTUATOR = "bus_actuator"
    POWER_SUPPLY = "power_supply"
    METER = "meter"
    INDICATOR_LAMP = "indicator_lamp"
    TERMINAL_STRIP = "terminal_strip"
    CABLE = "cable"
    BUSBAR = "busbar"
    OTHER = "other"


class LoadKind(StrEnum):
    """What a circuit feeds.

    Company rules (breaker size, residual current sensitivity) are set per
    kind in the profile.
    """

    LIGHTING = "lighting"
    SOCKET = "socket"
    AIR_CONDITIONING = "air_conditioning"
    WATER_HEATER = "water_heater"
    MOTOR = "motor"
    FAN = "fan"
    KITCHEN = "kitchen"
    LIFT = "lift"
    SUB_BOARD = "sub_board"
    CONTROL = "control"
    DATA = "data"
    SPARE = "spare"
    OTHER = "other"


class Phase(StrEnum):
    """A line conductor, or all three."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    THREE_PHASE = "L1L2L3"


class Designation(StrictModel):
    """An IEC 81346-1 reference designation.

    Attributes:
        function: The function aspect, without its ``=`` prefix ("DB1").
        location: The location aspect, without its ``+`` prefix ("HALL").
        product: The product aspect, without its ``-`` prefix ("Q12").
    """

    function: str | None = None
    location: str | None = None
    product: str

    def __str__(self) -> str:
        """Print the designation as IEC 81346-1 writes it: ``=DB1+HALL-Q12``."""
        parts = []
        if self.function:
            parts.append(f"={self.function}")
        if self.location:
            parts.append(f"+{self.location}")
        parts.append(f"-{self.product}")
        return "".join(parts)


class Part(StrictModel):
    """An orderable article.

    Attributes:
        key: Unique within the project; how devices refer to the part.
        manufacturer: As the manufacturer names itself.
        type_number: The manufacturer's type designation.
        order_number: The number an order is placed with, where it differs.
        description: What it is, in the catalogue's words.
        source: Where the data came from (catalogue, page), so a reviewer
            can check it.
        width_mm: Mounting width, for layout. ``None`` when not known.
    """

    key: str
    manufacturer: str
    type_number: str
    order_number: str | None = None
    description: str
    source: str | None = None
    width_mm: Decimal | None = None


class Device(StrictModel):
    """One device in a board.

    Attributes:
        id: Stable identifier within the project; connections refer to it.
        kind: What the device does.
        designation: Assigned from the company profile; ``None`` until then.
        part_key: The part it is built from; ``None`` for a device whose part
            has not been selected yet (it is listed as such, never guessed).
        poles: Number of poles, where it has any.
        rated_current_a: In, for switching and protective devices.
        residual_current_ma: IΔn, for a residual current device.
        curve: Tripping characteristic of a circuit-breaker ("B", "C", "D").
        breaking_capacity_ka: Rated short-circuit breaking capacity.
        description: A short function text for the drawing.
        upstream_id: The device that feeds this one (a group breaker feeding
            a residual current device, say); ``None`` for the busbar.
    """

    id: str
    kind: DeviceKind
    designation: Designation | None = None
    part_key: str | None = None
    poles: int | None = Field(default=None, ge=1, le=4)
    rated_current_a: Decimal | None = Field(
        default=None,
        gt=0,
        le=MAX_RATED_CURRENT_A,
        max_digits=8,
        decimal_places=3,
        allow_inf_nan=False,
    )
    residual_current_ma: Decimal | None = Field(
        default=None,
        gt=0,
        le=MAX_RESIDUAL_CURRENT_MA,
        max_digits=5,
        decimal_places=1,
        allow_inf_nan=False,
    )
    curve: str | None = None
    breaking_capacity_ka: Decimal | None = Field(
        default=None,
        gt=0,
        le=MAX_BREAKING_CAPACITY_KA,
        max_digits=5,
        decimal_places=2,
        allow_inf_nan=False,
    )
    description: str = ""
    upstream_id: str | None = None


class Cable(StrictModel):
    """An outgoing cable.

    Attributes:
        id: Stable identifier within the project.
        designation: Assigned from the company profile.
        cores: Number of cores, protective conductor included.
        cross_section_mm2: Of each line conductor.
        material: "Cu" or "Al".
        insulation: "PVC" or "XLPE".
        length_m: Where known.
        part_key: The cable type, where selected.
        withstand_ka2s: The let-through energy it withstands, ``k²S²``, in
            (kA)²s, for its insulation and material; of one run, where it
            runs in parallel.
        parallel: How many identical cables run in parallel, each with every
            core; 1 for a single cable.
    """

    id: str
    designation: Designation | None = None
    cores: int = Field(ge=1)
    cross_section_mm2: Decimal = Field(
        gt=0, le=MAX_SECTION_MM2, max_digits=6, decimal_places=2, allow_inf_nan=False
    )
    material: str = "Cu"
    insulation: str = "PVC"
    # Bounded as a load's length is (it is copied from one): a sent project's
    # cable length multiplies a per-metre price in the quotation.
    length_m: Decimal | None = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    part_key: str | None = None
    withstand_ka2s: Decimal | None = Field(
        default=None,
        gt=0,
        le=MAX_WITHSTAND_KA2S,
        max_digits=10,
        decimal_places=3,
        allow_inf_nan=False,
    )
    parallel: int = Field(default=1, ge=1, le=6)

    @property
    def size(self) -> str:
        """Its cores and section as a cable list writes them: "4G240", or "2x4G240" for two runs."""
        section = format(self.cross_section_mm2.normalize(), "f")
        runs = f"{self.parallel}x" if self.parallel > 1 else ""
        return f"{runs}{self.cores}G{section}"


class Circuit(StrictModel):
    """An outgoing circuit.

    A load, the devices that switch and protect it, and the cable feeding it.

    Attributes:
        id: Stable identifier within the board.
        description: What it feeds, as the load schedule names it.
        load: The kind of load.
        power_kw: Installed power.
        design_current_a: Ib.
        phase: The line conductor(s) it is connected to.
        upstream_id: The device this circuit is fed from (a residual current
            device, say); ``None`` when it hangs off the busbar directly.
        device_ids: The circuit's own devices, in order from the busbar.
        cable_id: Its outgoing cable, where it has one.
        feeds: The board it supplies, for a feeder to a sub-board.
        starter: How the motor it feeds is started, for a motor circuit.
        voltage_drop_percent: The drop along its own cable, where its length
            is known; the board's notes add what feeds the board.
        earth_loop_ohm: ``Zs`` at its far end, where its length and the
            board's ``Ze`` are known and its breaker disconnects by it.
    """

    id: str
    description: str
    load: LoadKind
    # Computed figures carry up to the decimal context's 28 digits; the bound
    # on digits keeps a sent exponent (1e-999999) from printing a megabyte.
    power_kw: Decimal = Field(ge=0, max_digits=_COMPUTED_DIGITS, allow_inf_nan=False)
    design_current_a: Decimal = Field(ge=0, max_digits=_COMPUTED_DIGITS, allow_inf_nan=False)
    phase: Phase
    upstream_id: str | None = None
    device_ids: list[str] = Field(default_factory=list)
    cable_id: str | None = None
    feeds: str | None = None
    starter: MotorStarter | None = None
    voltage_drop_percent: Decimal | None = Field(
        default=None, ge=0, max_digits=_COMPUTED_DIGITS, allow_inf_nan=False
    )
    earth_loop_ohm: Decimal | None = Field(
        default=None, ge=0, max_digits=_COMPUTED_DIGITS, allow_inf_nan=False
    )

    @model_validator(mode="after")
    def _starter_only_for_a_motor(self) -> Circuit:
        # A starter decides which device the integrity check reads as the
        # cable's overload protection (a drive's is not checked at all), so
        # one on a circuit that feeds no motor is refused, not trusted.
        if self.starter is not None and self.load is not LoadKind.MOTOR:
            raise ValueError(f"circuit {self.id}: a starter is only for a motor circuit")
        return self


class Supply(StrictModel):
    """The board's incoming supply.

    Attributes:
        voltage_v: Line-to-line voltage.
        phases: 1 or 3.
        frequency_hz: 50 or 60.
        earthing: The system earthing ("TN-S", "TN-C-S", "TT").
        fault_level_ka: Prospective short-circuit current at the board.
        earth_loop_ohm: ``Ze``, the earth fault loop impedance outside the
            board, as measured or as the supplier declares it. Given, each
            circuit is checked for disconnection on an earth fault.
    """

    voltage_v: Decimal = Decimal(400)
    phases: int = Field(default=3, ge=1, le=3)
    frequency_hz: Decimal = Decimal(50)
    earthing: str = "TN-S"
    fault_level_ka: Decimal | None = Field(default=None, gt=0, le=200)
    earth_loop_ohm: Decimal | None = Field(default=None, gt=0, le=100)


class Board(StrictModel):
    """One panel or distribution board.

    Attributes:
        id: Stable identifier within the project.
        name: As the project names it ("DBG-HALL").
        function: Its IEC 81346 function aspect; defaults to the name.
        location: Its IEC 81346 location aspect, where given.
        supply: The incoming supply.
        incomer_ids: The incoming devices, in order from the supply.
        devices: Every device in the board.
        cables: Every outgoing cable.
        circuits: The outgoing circuits, in the order they are drawn.
        notes: What the design could not settle, for the reviewer.
        fed_from: The board that supplies this one, within the project.
    """

    id: str
    name: str
    function: str | None = None
    location: str | None = None
    supply: Supply = Field(default_factory=Supply)
    incomer_ids: list[str] = Field(default_factory=list)
    devices: list[Device] = Field(default_factory=list)
    cables: list[Cable] = Field(default_factory=list)
    circuits: list[Circuit] = Field(default_factory=list)
    notes: list[DesignNote] = Field(default_factory=list)
    fed_from: str | None = None

    @model_validator(mode="after")
    def _references_resolve(self) -> Board:
        device_ids = {d.id for d in self.devices}
        if len(device_ids) != len(self.devices):
            raise ValueError(f"board {self.id}: device ids are not unique")
        cable_ids = {c.id for c in self.cables}
        if len(cable_ids) != len(self.cables):
            raise ValueError(f"board {self.id}: cable ids are not unique")
        circuit_ids = [c.id for c in self.circuits]
        if len(set(circuit_ids)) != len(circuit_ids):
            raise ValueError(f"board {self.id}: circuit ids are not unique")
        for device in self.devices:
            if device.upstream_id is not None and device.upstream_id not in device_ids:
                raise ValueError(f"device {device.id}: {device.upstream_id} is not a device")
        for incomer in self.incomer_ids:
            if incomer not in device_ids:
                raise ValueError(f"board {self.id}: incomer {incomer} is not a device")
        for circuit in self.circuits:
            for ref in [*circuit.device_ids, circuit.upstream_id]:
                if ref is not None and ref not in device_ids:
                    raise ValueError(f"circuit {circuit.id}: {ref} is not a device")
            if circuit.cable_id is not None and circuit.cable_id not in cable_ids:
                raise ValueError(f"circuit {circuit.id}: {circuit.cable_id} is not a cable")
        return self

    def device(self, device_id: str) -> Device:
        """Return a device by id.

        Raises:
            KeyError: If the board has no such device.
        """
        for device in self.devices:
            if device.id == device_id:
                return device
        raise KeyError(device_id)

    def cable(self, cable_id: str) -> Cable:
        """Return a cable by id.

        Raises:
            KeyError: If the board has no such cable.
        """
        for cable in self.cables:
            if cable.id == cable_id:
                return cable
        raise KeyError(cable_id)


class Revision(StrictModel):
    """One issue of the drawing set.

    Attributes:
        index: "01", "A", as the company numbers them.
        date: ISO date.
        description: What changed.
        drawn_by: Initials or name.
        checked_by: Initials or name.
        approved_by: Initials or name. On a saved revision, the approving
            account's display name (or "a team member"), set by the server.
        approval_note: The note added to that approval, set by the server;
            never who approved.
    """

    index: str = Field(max_length=_SHORT_TEXT)
    date: str = Field(max_length=_SHORT_TEXT)
    description: str = Field(default="", max_length=_NOTE_TEXT)
    drawn_by: str = Field(default="", max_length=_PERSON_TEXT)
    checked_by: str = Field(default="", max_length=_PERSON_TEXT)
    approved_by: str = Field(default="", max_length=_PERSON_TEXT)
    approval_note: str = Field(default="", max_length=_APPROVAL_NOTE_TEXT)


class ProjectInfo(StrictModel):
    """What the title block prints.

    Attributes:
        name: Project name.
        number: The company's job number.
        customer: Owner or customer.
        consultant: Consultant, where there is one.
        contractor: Contractor, where there is one.
        revisions: Issues of the drawing set, oldest first.
    """

    name: str = Field(max_length=_NAME_TEXT)
    number: str = Field(default="", max_length=_NAME_TEXT)
    customer: str = Field(default="", max_length=_NAME_TEXT)
    consultant: str = Field(default="", max_length=_NAME_TEXT)
    contractor: str = Field(default="", max_length=_NAME_TEXT)
    revisions: list[Revision] = Field(default_factory=list, max_length=_MAX_TITLE_REVISIONS)


class DesignProject(StrictModel):
    """A whole design: what every output is generated from.

    Attributes:
        info: Title-block data.
        profile: The key of the company profile the project is issued under.
        boards: The boards, in the order they are drawn.
        parts: Every part any device or cable refers to.
    """

    info: ProjectInfo
    profile: str = "iec-default"
    boards: list[Board] = Field(default_factory=list)
    parts: list[Part] = Field(default_factory=list)

    @model_validator(mode="after")
    def _parts_resolve(self) -> DesignProject:
        keys = [p.key for p in self.parts]
        if len(set(keys)) != len(keys):
            raise ValueError("part keys are not unique")
        known = set(keys)
        for board in self.boards:
            references = [(d.id, d.part_key) for d in board.devices]
            references += [(c.id, c.part_key) for c in board.cables]
            for item_id, part_key in references:
                if part_key is not None and part_key not in known:
                    raise ValueError(f"{item_id}: part {part_key} is not in the project")
        board_ids = [b.id for b in self.boards]
        if len(set(board_ids)) != len(board_ids):
            raise ValueError("board ids are not unique")
        return self

    def part(self, key: str) -> Part:
        """Return a part by key.

        Raises:
            KeyError: If the project has no such part.
        """
        for part in self.parts:
            if part.key == key:
                return part
        raise KeyError(key)


class TitleField(StrEnum):
    """A field a title block can print."""

    PROJECT_NAME = "project_name"
    PROJECT_NUMBER = "project_number"
    BOARD_NAME = "board_name"
    CUSTOMER = "customer"
    CONSULTANT = "consultant"
    CONTRACTOR = "contractor"
    PAGE_TITLE = "page_title"
    PAGE_NUMBER = "page_number"
    REVISION = "revision"
    DRAWN_BY = "drawn_by"
    CHECKED_BY = "checked_by"
    APPROVED_BY = "approved_by"
    DATE = "date"
    COMPANY = "company"


class PageKind(StrEnum):
    """A kind of page in a drawing set."""

    TITLE = "title"
    SAFETY = "safety"
    CONTENTS = "contents"
    LAYOUT = "layout"
    SINGLE_LINE = "single_line"
    DISTRIBUTION = "distribution"
    NOTES = "notes"
    TERMINALS = "terminals"
    CABLES = "cables"
    PARTS = "parts"


class WireNumbering(StrEnum):
    """How wires are numbered."""

    POTENTIAL = "potential"
    SEQUENTIAL = "sequential"
    SOURCE_TARGET = "source_target"


class CircuitRule(StrictModel):
    """A company's rule for one kind of load.

    Attributes:
        breaker_a: The circuit-breaker rating used for this kind of load,
            where the company fixes one rather than sizing from the load.
        curve: The tripping characteristic.
        residual_current_ma: The residual current device sensitivity this
            kind of load is grouped under; ``None`` for none.
        cable_mm2: The minimum cable cross-section for this kind of load.
    """

    breaker_a: Decimal | None = None
    curve: str = "C"
    residual_current_ma: Decimal | None = None
    cable_mm2: Decimal | None = None


class CompanyProfile(StrictModel):
    """How one company issues a design.

    Naming, numbering, title block, page order, brands and design rules. A
    project is designed once; issuing it under another profile changes only
    what this model governs.

    Attributes:
        key: Unique identifier.
        name: The company's name, as the title block prints it.
        language: Drawing language ("en", "ar").
        letters: The product-aspect letter for each kind of device.
        start_number: The first number of each letter's sequence.
        title_fields: The title block's fields, in order.
        page_order: The drawing set's pages, in order.
        wire_numbering: How wires are numbered.
        preferred_manufacturers: For each device kind, the manufacturers to
            choose from, most preferred first.
        circuit_rules: The company's rule for each kind of load.
        max_circuits_per_rcd: How many outgoing circuits one residual current
            device may protect.
        max_points_per_circuit: How many points of a kind share a final
            circuit; a kind not listed gets a circuit per point.
        max_kw_per_circuit: The most power a final circuit of a kind may
            carry, which can split points further than the count does.
        spare_ways_percent: Spare outgoing ways to leave, as a share of the
            circuits.
        max_phase_imbalance_percent: The largest difference between the most
            and least loaded line conductors, as a share of the most loaded.
        discrimination_ratio: How many times the largest breaker after it each
            breaker is rated at least, for overload discrimination.
        max_voltage_drop_percent: The largest voltage drop from the origin of
            the installation to a load, by kind of load; a kind not listed
            takes ``default_max_voltage_drop_percent``. The defaults are IEC
            60364-5-52 Annex G (Table G.52.1) for a public LV supply.
        default_max_voltage_drop_percent: The limit for any other load.
        max_starting_voltage_drop_percent: The largest drop from the origin
            to a motor's terminals while it starts.
        rail_widths_mm: The DIN-rail width of one pole of each kind of
            device, from the datasheets of the ranges the company fits. A
            kind not given has no width on the layout; the default holds only
            what is sourced: ABB S200 miniature breakers and F200 RCCBs,
            17.5 mm a pole (35 mm 2P, 70 mm 4P;
            ABB's 2020 residual current device catalogue, A_ELSB_Cat 2020_2 p. 8,
            gives 72 mm for the 4P 125 A).
        usable_rail_mm: The usable rail length per row of the company's
            enclosure; a row longer than this continues on the next.
        demand_factors: The share of each kind of load's design current
            taken as running at once, for rating incomers and feeders; a
            kind not listed counts in full. Empty assumes no diversity.
        market: The country the company designs for (ISO 3166-1 alpha-2,
            ``app.design.markets``); its code's stated rules apply under the
            company's own. Empty for IEC 60364 as the default applies it.
        rules_confirmed_by: Who confirmed the design rules. Empty while they
            are this software's defaults, which the drawing then says.
    """

    key: str
    name: str
    language: str = "en"
    market: str = ""
    letters: dict[DeviceKind, str]
    start_number: int = Field(default=1, ge=0)
    title_fields: list[TitleField]
    page_order: list[PageKind]
    wire_numbering: WireNumbering = WireNumbering.POTENTIAL
    preferred_manufacturers: dict[DeviceKind, list[str]] = Field(default_factory=dict)
    circuit_rules: dict[LoadKind, CircuitRule] = Field(default_factory=dict)
    max_circuits_per_rcd: int = Field(default=6, ge=1)
    max_points_per_circuit: dict[LoadKind, int] = Field(
        default_factory=lambda: {LoadKind.SOCKET: 8, LoadKind.LIGHTING: 15}
    )
    max_kw_per_circuit: dict[LoadKind, Decimal] = Field(
        default_factory=lambda: {LoadKind.SOCKET: Decimal(2), LoadKind.LIGHTING: Decimal("1.5")}
    )
    spare_ways_percent: Decimal = Decimal(20)
    max_phase_imbalance_percent: Decimal = Decimal(10)
    discrimination_ratio: Decimal = Field(default=Decimal("1.6"), ge=1, le=10)
    max_voltage_drop_percent: dict[LoadKind, Decimal] = Field(
        default_factory=lambda: {LoadKind.LIGHTING: Decimal(3)}
    )
    default_max_voltage_drop_percent: Decimal = Decimal(5)
    max_starting_voltage_drop_percent: Decimal = Decimal(15)
    rail_widths_mm: dict[DeviceKind, Decimal] = Field(
        default_factory=lambda: {
            DeviceKind.CIRCUIT_BREAKER: Decimal("17.5"),
            DeviceKind.RESIDUAL_CURRENT_DEVICE: Decimal("17.5"),
        }
    )
    usable_rail_mm: Decimal | None = Field(default=None, gt=0, le=5000)
    demand_factors: dict[LoadKind, Annotated[Decimal, Field(gt=0, le=1)]] = Field(
        default_factory=dict
    )
    rules_confirmed_by: str = ""

    @model_validator(mode="after")
    def _every_kind_has_a_letter(self) -> CompanyProfile:
        missing = [kind for kind in DeviceKind if kind not in self.letters]
        if missing:
            raise ValueError(f"profile {self.key}: no letter for {missing}")
        return self


class LoadInput(StrictModel):
    """One line of a distribution board's load schedule.

    Attributes:
        description: What it feeds ("Sockets - hall east").
        load: The kind of load, which picks the company's rule for it.
        power_kw: Installed active power.
        phases: 1 or 3.
        power_factor: cosφ. ``None`` assumes 0.9, the value the handbook's
            load-current table is drawn up for, and the board says so.
        controlled: Switched by a contactor the PLC drives, rather than
            live whenever its breaker is closed.
        feeds: The board this circuit feeds, for a feeder to a sub-board;
            set by the project design, not typed in.
        starter: For a three-phase motor, how it is started; its
            ``power_kw`` is then the motor's shaft power.
        length_m: The cable's route length, one way. Given, the cable is
            checked (and if need be enlarged) for voltage drop.
    """

    description: str = Field(min_length=1, max_length=_NAME_TEXT)
    load: LoadKind
    power_kw: Decimal = Field(gt=0)
    phases: int = Field(default=1)
    power_factor: Decimal | None = Field(default=None, gt=0, le=1)
    controlled: bool = False
    feeds: str | None = Field(default=None, max_length=40)
    starter: MotorStarter | None = None
    length_m: Decimal | None = Field(default=None, gt=0, le=10000)

    @model_validator(mode="after")
    def _one_or_three(self) -> LoadInput:
        if self.phases not in (1, 3):
            raise ValueError("phases must be 1 or 3")
        if self.starter is not None and self.load is not LoadKind.MOTOR:
            raise ValueError("a starter is only for a motor load")
        return self


class InstallationConditions(StrictModel):
    """How the outgoing cables are run, for their sizing.

    Attributes:
        installation_method: IEC 60364-5-52 reference method.
        ambient_temp_c: Air temperature around the cables.
        grouped_circuits: Loaded circuits run together.
        conductor_material: Copper or aluminium.
        insulation_rating_c: 70 (PVC) or 90 (XLPE/EPR).
    """

    installation_method: InstallationMethod = InstallationMethod.B1
    ambient_temp_c: Decimal = Decimal(30)
    grouped_circuits: int = Field(default=1, ge=1)
    conductor_material: ConductorMaterial = ConductorMaterial.COPPER
    insulation_rating_c: int = 70


class DistributionBoardRequest(StrictModel):
    """What a distribution board is designed from.

    Attributes:
        name: The board's name ("DBG-HALL").
        location: Its IEC 81346 location aspect, where given.
        supply: The incoming supply.
        loads: The load schedule, in the order the circuits are drawn.
        conditions: How the outgoing cables are run.
        fed_from: The board whose feeder supplies this one; ``None`` for a
            board fed from the utility or a main switchboard outside the
            project.
        feeder_length_m: The route length of the cable feeding this board
            from ``fed_from``, for that feeder's voltage drop.
    """

    name: str = Field(min_length=1, max_length=40)
    location: str | None = Field(default=None, max_length=_NAME_TEXT)
    supply: Supply = Field(default_factory=Supply)
    loads: list[LoadInput] = Field(min_length=1, max_length=500)
    conditions: InstallationConditions = Field(default_factory=InstallationConditions)
    fed_from: str | None = Field(default=None, max_length=40)
    feeder_length_m: Decimal | None = Field(default=None, gt=0, le=10000)


class BoardDesignRequest(StrictModel):
    """A distribution board to design, under a company's profile.

    Attributes:
        info: Title-block data for the project.
        board: The board's load schedule and conditions.
        profile: The company's profile settings (only what differs from the
            default); ``None`` designs under the default profile.
    """

    info: ProjectInfo
    board: DistributionBoardRequest
    profile: dict[str, Any] | None = None


class ProjectDesignRequest(StrictModel):
    """A project of one or more boards to design, under a company's profile.

    Attributes:
        info: Title-block data for the project.
        boards: Each board's schedule. A board naming another in
            ``fed_from`` gets a feeder in that board, sized from its own
            design, so sub-boards need no hand-entered load.
        profile: The company's profile settings; ``None`` for the default.
    """

    info: ProjectInfo
    boards: list[DistributionBoardRequest] = Field(min_length=1, max_length=20)
    profile: dict[str, Any] | None = None


class BoardDesignResponse(BaseModel):
    """A designed board, issued under the profile it was designed with.

    Attributes:
        project: The project, designated.
        profile: The profile applied, in full.
    """

    project: DesignProject
    profile: CompanyProfile


class ExportFormat(StrEnum):
    """A file a project can be exported as."""

    PDF = "pdf"
    DXF = "dxf"
    QET = "qet"
    AML = "aml"
    DEVICES_CSV = "devices_csv"
    PARTS_CSV = "parts_csv"
    CABLES_CSV = "cables_csv"
    CIRCUITS_CSV = "circuits_csv"
    TERMINALS_CSV = "terminals_csv"
    EPLAN_DEVICES_CSV = "eplan_devices_csv"
    ACE_COMPONENTS_CSV = "ace_components_csv"
    ACE_TERMINALS_CSV = "ace_terminals_csv"
    QUOTATION_PDF = "quotation_pdf"
    CALCULATIONS_PDF = "calculations_pdf"
    QUOTATION_CSV = "quotation_csv"
    PLC_ST = "plc_st"
    PLC_IO_CSV = "plc_io_csv"
    JSON = "json"


class DesignExportRequest(StrictModel):
    """A project to export, possibly edited since it was designed.

    Attributes:
        project: The project.
        profile: The company's profile settings; ``None`` for the default.
        format: The file to produce.
        pricing: The company's prices, for a quotation export.
    """

    project: DesignProject
    profile: dict[str, Any] | None = None
    format: ExportFormat
    pricing: PricingSettings | None = None


class ExportPurpose(StrEnum):
    """What a saved revision is exported for."""

    #: For review: labelled draft, or review-approved as a study.
    REVIEW = "review"
    #: For execution on site: refused until an execution package exists.
    EXECUTION = "execution"


class RevisionExportRequest(StrictModel):
    """A saved revision to export, from the design the server stored for it.

    Attributes:
        format: The file to produce.
        pricing: The company's prices, for a quotation export.
        purpose: Review, or execution (always refused for now).
    """

    format: ExportFormat
    pricing: PricingSettings | None = None
    purpose: ExportPurpose = ExportPurpose.REVIEW


class ExportStatus(StrEnum):
    """What a saved revision can be exported as now."""

    #: Saved with no server-built design (older, or its design was refused):
    #: save it again to validate it.
    NOT_VALIDATED = "not_validated"
    #: Its rules or company profile changed since it was saved: save it again.
    STALE = "stale"
    #: It has blocking findings: exported as a draft only, never approved.
    BLOCKED = "blocked"
    #: Validated, not approved: exported as a draft.
    DRAFT = "draft"
    #: Approved for this exact design: exported as a review-approved study.
    REVIEW_APPROVED = "review_approved"


class LoadScheduleImport(BaseModel):
    """A load schedule read from a consultant's file.

    Attributes:
        loads: The loads read, in the schedule's order.
        warnings: Every row skipped and every assumption made, by row.
        rows_read: Data rows found below the header.
    """

    loads: list[LoadInput]
    warnings: list[DesignNote]
    rows_read: int


class SuggestedPoints(BaseModel):
    """A group of like points the model read from a description.

    Attributes:
        description: What they are, in the description's language
            ("مآخذ القاعة", "Hall sockets").
        load: Their kind.
        quantity: How many points.
        unit_power_kw: The power of one point.
        three_phase: Whether one point is a three-phase load.
        power_stated: Whether the description states this power, or a rating
            it follows from (watts, kW, tons, HP). False means a typical
            figure was used.
    """

    description: str = Field(min_length=1, max_length=100)
    load: LoadKind
    quantity: int = Field(ge=1, le=500)
    unit_power_kw: Decimal = Field(gt=0, le=500)
    three_phase: bool
    power_stated: bool


class ScheduleSuggestionOutput(BaseModel):
    """What the model returns: the points, and assumptions about the whole."""

    items: list[SuggestedPoints] = Field(min_length=1, max_length=40)
    assumptions: list[str] = Field(max_length=20)


class ScheduleSuggestionRequest(StrictModel):
    """A plain description of what a board feeds.

    Attributes:
        description: "A hall with 20 sockets, 30 lights and two 2-ton ACs".
        supply_phases: The board's supply, so three-phase loads are only
            proposed where there is three-phase.
        profile: The company's settings, whose points-per-circuit rule
            splits the points into circuits.
    """

    description: str = Field(min_length=3, max_length=2000)
    supply_phases: int = Field(default=3, ge=1, le=3)
    profile: dict[str, Any] | None = None


class LoadScheduleSuggestion(BaseModel):
    """A proposed load schedule, for the engineer to check before designing.

    Attributes:
        loads: The proposed circuits.
        assumptions: Every assumption, per circuit and overall.
    """

    loads: list[LoadInput]
    assumptions: list[DesignNote]


#: The largest unit price, rate or per-board price accepted, in any currency:
#: far above any real panel item, low enough that no total overflows a
#: readable figure. Bounds, not engineering values.
MAX_UNIT_PRICE = Decimal("1000000000")
#: The longest default cable length priced, in metres.
MAX_CABLE_LENGTH_M = Decimal("10000")
#: The most price-list entries one request carries.
MAX_PRICE_LIST = 20000
#: The finest fraction a price, rate or percentage carries. Far below any
#: currency's smallest unit, and enough for a per-metre price; a figure with
#: more places is float noise or an attack on the arithmetic, not a price.
PRICE_DECIMAL_PLACES = 6
#: Digits in a bounded price: MAX_UNIT_PRICE's ten before the point, six after.
PRICE_MAX_DIGITS = 16


class PriceListEntry(StrictModel):
    """One price from a company's price list.

    Attributes:
        key: What it prices: a manufacturer's order number or type number,
            or a rating key ("circuit_breaker:1P:C16",
            "residual_current_device:4P:40A:30mA", "cable:3G2.5:Cu:PVC" per
            metre) for a device whose article is not chosen yet.
        description: The supplier's description, kept with the price. It
            never names a quotation line: a line is the designed item's.
        unit_price: Price of one, or of one metre for a cable.
    """

    key: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=300)
    unit_price: Decimal = Field(
        ge=0,
        le=MAX_UNIT_PRICE,
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        allow_inf_nan=False,
    )


class PricingSettings(StrictModel):
    """How a company prices a board.

    Attributes:
        currency: Printed beside every amount: an ISO 4217 code.
        price_list: The company's prices.
        cable_length_m: The length priced for an outgoing cable whose own
            length is not given; ``None`` leaves such cables unpriced.
        labour_per_circuit: Wiring and testing, per outgoing circuit.
        labour_per_board: Assembly and testing, per board.
        enclosure_price: The enclosure, busbars and accessories, per board.
        markup_percent: Added to materials and labour.
        vat_percent: Added last.
    """

    currency: str = Field(default="JOD", pattern=r"^[A-Z]{3}$")
    price_list: list[PriceListEntry] = Field(default_factory=list, max_length=MAX_PRICE_LIST)
    cable_length_m: Decimal | None = Field(
        default=None,
        gt=0,
        le=MAX_CABLE_LENGTH_M,
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        allow_inf_nan=False,
    )
    labour_per_circuit: Decimal = Field(
        default=Decimal(0),
        ge=0,
        le=MAX_UNIT_PRICE,
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        allow_inf_nan=False,
    )
    labour_per_board: Decimal = Field(
        default=Decimal(0),
        ge=0,
        le=MAX_UNIT_PRICE,
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        allow_inf_nan=False,
    )
    enclosure_price: Decimal = Field(
        default=Decimal(0),
        ge=0,
        le=MAX_UNIT_PRICE,
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        allow_inf_nan=False,
    )
    markup_percent: Decimal = Field(
        default=Decimal(0),
        ge=0,
        le=500,
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        allow_inf_nan=False,
    )
    vat_percent: Decimal = Field(
        default=Decimal(0),
        ge=0,
        le=100,
        max_digits=PRICE_MAX_DIGITS,
        decimal_places=PRICE_DECIMAL_PLACES,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def _keys_are_distinct(self) -> PricingSettings:
        # Keys match as ``price_key`` normalises them: two that normalise
        # alike would price a line by whichever came last, silently.
        duplicates = duplicate_price_keys(entry.key for entry in self.price_list)
        if duplicates:
            raise ValueError(f"duplicate price-list key: {', '.join(duplicates)}")
        return self


def price_key(key: str) -> str:
    """A price-list key as it is matched: case, spaces and padding ignored.

    Args:
        key: As written.

    Returns:
        The key matched by.
    """
    return key.strip().lower().replace(" ", "")


def duplicate_price_keys(keys: Iterable[str]) -> list[str]:
    """The keys, as written, that match a key written before them.

    Args:
        keys: The price list's keys, in order.

    Returns:
        Each repeated key as written, in order; empty when all are distinct.
    """
    seen: set[str] = set()
    repeated: list[str] = []
    for key in keys:
        matched = price_key(key)
        if matched in seen:
            repeated.append(key)
        seen.add(matched)
    return repeated


class QuotationLine(BaseModel):
    """One line of a quotation.

    Attributes:
        description: What it is.
        designations: The devices or cables it covers.
        quantity: How many, or metres for a cable.
        unit: "pcs" or "m".
        key: The price-list key it was matched by, or would be.
        unit_price: ``None`` when the price list has no price for it.
        total: ``None`` when unpriced.
        missing: Why it is unpriced: "price" (not in the price list) or
            "length" (a cable with no length); ``None`` when priced.
    """

    description: str
    designations: list[str]
    quantity: Decimal
    unit: str
    key: str
    unit_price: Decimal | None
    total: Decimal | None
    missing: Literal["price", "length"] | None = None


class Quotation(BaseModel):
    """A board's price, line by line, with what could not be priced.

    Attributes:
        currency: Of every amount.
        lines: Materials, then labour and enclosure.
        materials: Sum of the priced material lines.
        labour: Labour and enclosure.
        markup: On materials and labour.
        vat: On the marked-up total.
        total: What the customer pays, for what was priced.
        unpriced: The lines with no price, by key, for the price list's owner.
        complete: Whether every line was priced.
    """

    currency: str
    lines: list[QuotationLine]
    materials: Decimal
    labour: Decimal
    markup: Decimal
    vat: Decimal
    total: Decimal
    unpriced: list[str]
    complete: bool


#: What a quotation priced from a client-sent project says it is.
PROVISIONAL_ESTIMATE_LABEL = (
    "Provisional estimate: not a quotation, not for execution. "
    "Priced from an unsaved design sent by the browser; "
    "a quotation is exported from a saved revision."
)


class QuotationEstimate(Quotation):
    """A provisional estimate: a project the client sent, priced and checked.

    Nothing in it is persisted, approved or issued. The project is the
    client's, so it is re-designated and integrity-checked on the server and
    its findings are returned beside the prices: a tampered rating is a
    blocking finding here, as on a draft export. It carries no revision,
    checker or approver.

    Attributes:
        status: Always ``"provisional_estimate"``.
        label: In English; the page renders ``status`` in the reader's language.
        findings: The integrity checks and notes on the project as sent.
        blocking: Whether any finding is blocking.
    """

    status: Literal["provisional_estimate"] = "provisional_estimate"
    label: str = PROVISIONAL_ESTIMATE_LABEL
    findings: list[DesignFinding]
    blocking: bool


class QuotationRequest(StrictModel):
    """A project to price.

    Attributes:
        project: The designed project.
        profile: The company's profile settings; ``None`` for the default.
        pricing: The company's prices and rates.
    """

    project: DesignProject
    profile: dict[str, Any] | None = None
    pricing: PricingSettings


class PlcIoPoint(BaseModel):
    """One point on a control program's I/O list.

    Attributes:
        tag: The variable name in the program.
        direction: "input" or "output".
        board: The board it belongs to; empty for shared inputs.
        device: The designation of the device it drives or reports.
        description: What it is, for the wiring list.
    """

    tag: str
    direction: str
    board: str
    device: str
    description: str


class PlcProgramRequest(StrictModel):
    """A project whose PLC-switched circuits need a control program.

    Attributes:
        project: The designed project.
        profile: The company's profile settings; ``None`` for the default.
    """

    project: DesignProject
    profile: dict[str, Any] | None = None


class PlcProgramResponse(BaseModel):
    """The control program for a project, and the checker's verdict on it.

    Attributes:
        name: The program unit's name.
        source: The IEC 61131-3 Structured Text.
        io: Every input and output, shared inputs first.
        validation: The parser-based checker's verdict on ``source``.
    """

    name: str
    source: str
    io: list[PlcIoPoint]
    validation: PlcValidationResult


# The export request names the pricing settings, defined after it.
DesignExportRequest.model_rebuild()
RevisionExportRequest.model_rebuild()


class SaveProjectRequest(StrictModel):
    """A new project to save, as its first revision.

    Attributes:
        name: What the project is listed as.
        request: The project as entered.
        note: What this revision is, for the revision list.
    """

    name: str = Field(min_length=1, max_length=200)
    request: ProjectDesignRequest
    note: str = Field(default="", max_length=500)


class ReviseProjectRequest(StrictModel):
    """A new revision of a saved project.

    Attributes:
        request: The project as entered now.
        note: What changed, for the revision list.
    """

    request: ProjectDesignRequest
    note: str = Field(default="", max_length=500)


class RevisionSummary(BaseModel):
    """One revision as the revision list shows it.

    Attributes:
        number: 1 for the first save, then in order.
        note: What it is.
        author: The display name of the account that saved it, read from
            the tenant's users when the response is built; ``""`` when that
            account has no display name. Never its email.
        created_at: When, ISO 8601.
        approved_by: The approver as every export prints it: the approving
            account's display name, or "a team member" when it has none or
            is not recorded (an approval made before accounts were). Never
            a name typed at approval. ``None`` while it is not approved.
        approved_at: When, ISO 8601.
        approver_account_recorded: Whether the server recorded the
            signed-in account that approved it (from the approver's token,
            never from the request). ``False`` while it is not approved, and
            for an approval recorded before accounts were.
        approver_name: That account's display name, read from the tenant's
            users when the response is built; ``None`` when no account is
            recorded or it has no display name, which a client shows as "a
            team member". The account's email, kept in the database as the
            audit identity, is never sent.
        approval_note: The note added at approval, if any; never an identity.
        validated: Whether the server designed it from its request when it
            was saved, and holds that design; ``False`` for an older one.
        export_status: What it can be exported as now.
        approval_current: Whether its approval still counts: of the design
            it holds, under the policy that applies now (the resolved profile
            hash, the rules and catalogue versions and the findings
            classification it was granted under). ``None`` while it is not
            approved. The tenant's company settings are not part of it.
        approval_changed: What changed since the approval, when it no longer
            counts: ``snapshot``, ``profile``, ``rules_version``,
            ``catalogue_version``, ``findings_policy``.
    """

    number: int
    note: str
    author: str
    created_at: str
    approved_by: str | None = None
    approved_at: str | None = None
    approver_account_recorded: bool = False
    approver_name: str | None = None
    approval_note: str | None = None
    validated: bool = False
    export_status: ExportStatus = ExportStatus.NOT_VALIDATED
    approval_current: bool | None = None
    approval_changed: list[str] = Field(default_factory=list)


class ApproveRevisionRequest(StrictModel):
    """An engineer's approval of one revision.

    The approver is the signed-in account, never anything the request says: a
    field naming one (``approver``) is refused with 422, as any other field
    this model does not define.

    Attributes:
        note: An optional note to the approval, printed labelled "Approval
            note", never where the approver is named.
    """

    note: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=_APPROVAL_NOTE_TEXT)
    ] = ""


class ProjectSummary(BaseModel):
    """One saved project as the project list shows it.

    Attributes:
        id: Its identifier.
        name: Its name.
        revisions: How many revisions it has.
        updated_at: When its latest revision was saved, ISO 8601.
    """

    id: str
    name: str
    revisions: int
    updated_at: str


class ProjectPage(BaseModel):
    """One page of saved projects, most recently saved first.

    Attributes:
        projects: The page.
        next_cursor: Pass back for the next page; ``None`` on the last.
        permissions: What the caller may do to a project
            (``app.domain.permissions``), for the client to hide what it
            may not; the server checks again on every request.
    """

    projects: list[ProjectSummary]
    next_cursor: str | None = None
    permissions: list[Permission] = Field(default_factory=list)


class SavedProject(BaseModel):
    """A saved project, its revisions, and one revision's request.

    Attributes:
        id: Its identifier.
        name: Its name.
        revisions: Every revision, oldest first.
        revision: The number of the revision ``request`` is.
        request: That revision as entered.
        findings: What the checks found in the design the server built for
            that revision when it was saved; empty for an older revision.
        validated: Whether that revision holds a server-built design.
        snapshot_sha256: The SHA-256 of that design's canonical JSON.
        rules_version: The calculation rules it was designed under.
        approved_snapshot_sha256: The design its approval is bound to;
            ``None`` while it is not approved (or approved before approvals
            were bound to a design).
        export_status: What it can be exported as now.
        approval_current: Whether its approval still counts: of the design
            it holds, under the policy that applies now (the resolved profile
            hash, the rules and catalogue versions and the findings
            classification it was granted under). ``None`` while it is not
            approved. The tenant's company settings are not part of it.
        approval_changed: What changed since the approval, when it no longer
            counts: ``snapshot``, ``profile``, ``rules_version``,
            ``catalogue_version``, ``findings_policy``.
        execution_blockers: Why it cannot be exported for execution: the
            parts of an execution package this service does not produce yet,
            then ``approval`` and each blocking finding's code, as they apply.
        approving_as: The signed-in caller's display name, as an approval
            they made now would name them; ``None`` when they have none,
            which a client shows as "a team member". Never their email.
        permissions: What the caller may do to this project
            (``app.domain.permissions``); the server checks again on every
            request.
    """

    id: str
    name: str
    revisions: list[RevisionSummary]
    revision: int
    request: ProjectDesignRequest
    findings: list[DesignFinding] = Field(default_factory=list)
    validated: bool = False
    snapshot_sha256: str | None = None
    rules_version: str | None = None
    approved_snapshot_sha256: str | None = None
    approval_current: bool | None = None
    approval_changed: list[str] = Field(default_factory=list)
    export_status: ExportStatus = ExportStatus.NOT_VALIDATED
    execution_blockers: list[str] = Field(default_factory=list)
    approving_as: str | None = None
    permissions: list[Permission] = Field(default_factory=list)

    #: The revision's request as stored, when it no longer validates against
    #: today's bounds (an older revision's 600-character description): it is
    #: sent back as it is, for the engineer to correct and save again, rather
    #: than the revision failing to open. Set through ``as_stored``.
    _stored_request: dict[str, Any] | None = PrivateAttr(default=None)

    def as_stored(self, request: dict[str, Any]) -> SavedProject:
        """Send ``request``, JSON-shaped as stored, in place of ``request``."""
        self._stored_request = request
        return self

    @field_serializer(
        "request",
        mode="wrap",
        # The schema stays the request's; a stored request (a dict) is sent as is.
        return_type=SerializeAsAny[ProjectDesignRequest],
    )
    def _request_as_stored(
        self,
        value: ProjectDesignRequest,
        handler: SerializerFunctionWrapHandler,
        info: SerializationInfo,
    ) -> Any:
        del info
        if self._stored_request is not None:
            return self._stored_request
        return handler(value)


class MarketInfo(BaseModel):
    """A market's supply and stated rules, for a new board's defaults.

    Attributes:
        code: ISO 3166-1 alpha-2.
        name: In English.
        voltage_v: Line to line, three-phase.
        phase_voltage_v: Line to neutral.
        frequency_hz: 50 or 60.
        earthing: The usual earthing system.
        currency: ISO 4217, for a quotation.
        regulation: The code a design there answers to.
        max_voltage_drop_percent: Its stated limit for every load, if any.
        rcd_every_final_circuit: Whether it asks 30 mA on every final circuit.
        languages: Customary drawing languages, first preferred.
    """

    code: str
    name: str
    voltage_v: int
    phase_voltage_v: int
    frequency_hz: int
    earthing: str
    currency: str
    regulation: str
    max_voltage_drop_percent: Decimal | None
    rcd_every_final_circuit: bool
    languages: list[str]


class CompanySettings(BaseModel):
    """A tenant's saved company profile settings.

    Attributes:
        settings: What the company does differently from the default
            profile; ``None`` while none are saved.
        updated_by: Who saved them last.
        updated_at: When, ISO 8601.
        permissions: What the caller may do with them
            (``edit_company_settings``, or nothing: every member reads them).
    """

    settings: dict[str, Any] | None = None
    updated_by: str = ""
    updated_at: str | None = None
    permissions: list[Permission] = Field(default_factory=list)


class SaveCompanySettings(StrictModel):
    """Company settings to save.

    Attributes:
        settings: What the company does differently from the default profile.
    """

    settings: dict[str, Any]


class MarkupCandidate(BaseModel):
    """A circuit a comment on a residual current group may mean.

    Attributes:
        circuit: Its description, as the schedule names it.
        load_index: Its place in the board's schedule.
    """

    circuit: str
    load_index: int


class MarkupSuggestion(BaseModel):
    """A change to the load schedule a reviewer's comment asks for.

    Attributes:
        board: The board whose schedule changes.
        circuit: The circuit's description, as the schedule names it.
        load_index: Its place in that board's schedule; ``None`` for a change
            to the board itself (a feeder length).
        field: What changes.
        value: Its new value; ``None`` for a removal.
        candidates: For a comment on a group of several circuits, each one
            it may mean, for the engineer to pick; ``circuit`` is then empty.
    """

    board: str
    circuit: str
    load_index: int | None = None
    field: Literal[
        "remove", "power_kw", "length_m", "power_factor", "phases", "starter", "feeder_length_m"
    ]
    value: str | None = None
    candidates: list[MarkupCandidate] = Field(default_factory=list)


class MarkupItem(BaseModel):
    """One reviewer's mark read off a drawing set PDF.

    Attributes:
        page: Its page, from 1.
        kind: The PDF annotation type.
        author: Who made it, where the PDF says.
        text: What it says.
        sheet: The page's title, where the PDF is this project's drawing set.
        board: The board the page draws, likewise.
        near: The label nearest the mark, likewise.
        suggestion: The change to the schedule it asks for, where it sits on
            a circuit and says one the schedule holds; offered, never applied.
    """

    page: int
    kind: str
    author: str
    text: str
    sheet: str
    board: str
    near: str
    suggestion: MarkupSuggestion | None = None


class MarkupReport(BaseModel):
    """The marks on a reviewed drawing set.

    Attributes:
        markups: Every mark with text, in page order.
        matched: Whether the PDF's pages are this project's drawing set, so
            each mark could be placed on its board and label.
    """

    markups: list[MarkupItem]
    matched: bool
