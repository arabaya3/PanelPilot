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

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.models.schemas.calculations import ConductorMaterial, InstallationMethod
from app.models.schemas.plc import PlcValidationResult


class DesignNote(BaseModel):
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


class Designation(BaseModel):
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


class Part(BaseModel):
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


class Device(BaseModel):
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
    rated_current_a: Decimal | None = None
    residual_current_ma: Decimal | None = None
    curve: str | None = None
    breaking_capacity_ka: Decimal | None = None
    description: str = ""
    upstream_id: str | None = None


class Cable(BaseModel):
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
            (kA)²s, for its insulation and material.
    """

    id: str
    designation: Designation | None = None
    cores: int = Field(ge=1)
    cross_section_mm2: Decimal
    material: str = "Cu"
    insulation: str = "PVC"
    length_m: Decimal | None = None
    part_key: str | None = None
    withstand_ka2s: Decimal | None = None


class Circuit(BaseModel):
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
    power_kw: Decimal
    design_current_a: Decimal
    phase: Phase
    upstream_id: str | None = None
    device_ids: list[str] = Field(default_factory=list)
    cable_id: str | None = None
    feeds: str | None = None
    starter: MotorStarter | None = None
    voltage_drop_percent: Decimal | None = None
    earth_loop_ohm: Decimal | None = None


class Supply(BaseModel):
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


class Board(BaseModel):
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


class Revision(BaseModel):
    """One issue of the drawing set.

    Attributes:
        index: "01", "A", as the company numbers them.
        date: ISO date.
        description: What changed.
        drawn_by: Initials or name.
        checked_by: Initials or name.
        approved_by: Initials or name.
    """

    index: str
    date: str
    description: str = ""
    drawn_by: str = ""
    checked_by: str = ""
    approved_by: str = ""


class ProjectInfo(BaseModel):
    """What the title block prints.

    Attributes:
        name: Project name.
        number: The company's job number.
        customer: Owner or customer.
        consultant: Consultant, where there is one.
        contractor: Contractor, where there is one.
        revisions: Issues of the drawing set, oldest first.
    """

    name: str
    number: str = ""
    customer: str = ""
    consultant: str = ""
    contractor: str = ""
    revisions: list[Revision] = Field(default_factory=list)


class DesignProject(BaseModel):
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


class CircuitRule(BaseModel):
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


class CompanyProfile(BaseModel):
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
            what is sourced (ABB S200 miniature breakers, 17.5 mm a pole).
        usable_rail_mm: The usable rail length per row of the company's
            enclosure; a row longer than this continues on the next.
        demand_factors: The share of each kind of load's design current
            taken as running at once, for rating incomers and feeders; a
            kind not listed counts in full. Empty assumes no diversity.
        rules_confirmed_by: Who confirmed the design rules. Empty while they
            are this software's defaults, which the drawing then says.
    """

    key: str
    name: str
    language: str = "en"
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
        default_factory=lambda: {DeviceKind.CIRCUIT_BREAKER: Decimal("17.5")}
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


class LoadInput(BaseModel):
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

    description: str = Field(min_length=1)
    load: LoadKind
    power_kw: Decimal = Field(gt=0)
    phases: int = Field(default=1)
    power_factor: Decimal | None = Field(default=None, gt=0, le=1)
    controlled: bool = False
    feeds: str | None = None
    starter: MotorStarter | None = None
    length_m: Decimal | None = Field(default=None, gt=0, le=10000)

    @model_validator(mode="after")
    def _one_or_three(self) -> LoadInput:
        if self.phases not in (1, 3):
            raise ValueError("phases must be 1 or 3")
        return self


class InstallationConditions(BaseModel):
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


class DistributionBoardRequest(BaseModel):
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
    location: str | None = None
    supply: Supply = Field(default_factory=Supply)
    loads: list[LoadInput] = Field(min_length=1, max_length=500)
    conditions: InstallationConditions = Field(default_factory=InstallationConditions)
    fed_from: str | None = None
    feeder_length_m: Decimal | None = Field(default=None, gt=0, le=10000)


class BoardDesignRequest(BaseModel):
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


class ProjectDesignRequest(BaseModel):
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
    QUOTATION_PDF = "quotation_pdf"
    QUOTATION_CSV = "quotation_csv"
    PLC_ST = "plc_st"
    PLC_IO_CSV = "plc_io_csv"
    JSON = "json"


class DesignExportRequest(BaseModel):
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


class ScheduleSuggestionRequest(BaseModel):
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


class PriceListEntry(BaseModel):
    """One price from a company's price list.

    Attributes:
        key: What it prices: a manufacturer's order number or type number,
            or a rating key ("circuit_breaker:1P:C16",
            "residual_current_device:4P:40A:30mA", "cable:3G2.5:Cu:PVC" per
            metre) for a device whose article is not chosen yet.
        description: The supplier's description, for the quotation.
        unit_price: Price of one, or of one metre for a cable.
    """

    key: str = Field(min_length=1, max_length=120)
    description: str = ""
    unit_price: Decimal = Field(ge=0)


class PricingSettings(BaseModel):
    """How a company prices a board.

    Attributes:
        currency: Printed beside every amount.
        price_list: The company's prices.
        cable_length_m: The length priced for an outgoing cable whose own
            length is not given; ``None`` leaves such cables unpriced.
        labour_per_circuit: Wiring and testing, per outgoing circuit.
        labour_per_board: Assembly and testing, per board.
        enclosure_price: The enclosure, busbars and accessories, per board.
        markup_percent: Added to materials and labour.
        vat_percent: Added last.
    """

    currency: str = Field(default="JOD", min_length=1, max_length=8)
    price_list: list[PriceListEntry] = Field(default_factory=list)
    cable_length_m: Decimal | None = Field(default=None, gt=0)
    labour_per_circuit: Decimal = Field(default=Decimal(0), ge=0)
    labour_per_board: Decimal = Field(default=Decimal(0), ge=0)
    enclosure_price: Decimal = Field(default=Decimal(0), ge=0)
    markup_percent: Decimal = Field(default=Decimal(0), ge=0, le=500)
    vat_percent: Decimal = Field(default=Decimal(0), ge=0, le=100)


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


class QuotationRequest(BaseModel):
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


class PlcProgramRequest(BaseModel):
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


class SaveProjectRequest(BaseModel):
    """A new project to save, as its first revision.

    Attributes:
        name: What the project is listed as.
        request: The project as entered.
        note: What this revision is, for the revision list.
    """

    name: str = Field(min_length=1, max_length=200)
    request: ProjectDesignRequest
    note: str = Field(default="", max_length=500)


class ReviseProjectRequest(BaseModel):
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
        author: Who saved it.
        created_at: When, ISO 8601.
        approved_by: The engineer who approved it, as the title block
            prints them; ``None`` while it is not approved.
        approved_at: When, ISO 8601.
    """

    number: int
    note: str
    author: str
    created_at: str
    approved_by: str | None = None
    approved_at: str | None = None


class ApproveRevisionRequest(BaseModel):
    """An engineer's approval of one revision.

    Attributes:
        approver: Their name as the title block is to print it.
    """

    approver: str = Field(min_length=1, max_length=100)


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
    """

    projects: list[ProjectSummary]
    next_cursor: str | None = None


class SavedProject(BaseModel):
    """A saved project, its revisions, and one revision's request.

    Attributes:
        id: Its identifier.
        name: Its name.
        revisions: Every revision, oldest first.
        revision: The number of the revision ``request`` is.
        request: That revision as entered.
    """

    id: str
    name: str
    revisions: list[RevisionSummary]
    revision: int
    request: ProjectDesignRequest


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
    """

    page: int
    kind: str
    author: str
    text: str
    sheet: str
    board: str
    near: str


class MarkupReport(BaseModel):
    """The marks on a reviewed drawing set.

    Attributes:
        markups: Every mark with text, in page order.
        matched: Whether the PDF's pages are this project's drawing set, so
            each mark could be placed on its board and label.
    """

    markups: list[MarkupItem]
    matched: bool
