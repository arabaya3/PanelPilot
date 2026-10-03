"""What a design tells its reviewer, as codes the reader's language can render.

Every note the design, the schedule import and the schedule suggestion make
is a :class:`~app.models.schemas.design.DesignNote`: a stable ``code``, the
values it names (``params``), and its English ``text``. The page renders the
code in the reader's language from its own catalogue (en/ar/he); the drawing
set, whose frame fonts carry Latin script only, prints the English text, as
drawings for site are customarily issued; and a reader with no entry for a
code still gets the English.

The templates below are the English catalogue. A code's params are the
fields its template names, so a missing or extra param is a bug a test
catches rather than a sentence with a hole in it.
"""

from __future__ import annotations

import string

from app.models.schemas.design import DesignNote

#: The English text of every code. Params in braces.
TEMPLATES: dict[str, str] = {
    # Distribution boards.
    "mccb_needed": (
        "{load}: Ib {current} A is above the largest miniature breaker held (125 A); a "
        "moulded-case breaker is needed and none is selected here. The cable is sized for Ib; "
        "check it against the breaker's In."
    ),
    "above_company_rating": (
        "{load}: Ib {current} A exceeds the company's {fixed} A for {kind}; rated {rated} A "
        "instead."
    ),
    "contactor_unselected": (
        "{load}: no modular contactor rating carries {current} A; the contactor is left "
        "unselected."
    ),
    "phase_imbalance": (
        "Phase imbalance {imbalance} % exceeds the company's {limit} %: the loads cannot be "
        "spread more evenly as given."
    ),
    "incomer_mccb": (
        "Incomer: {current} A on the most loaded conductor exceeds 125 A; a moulded-case "
        "breaker is needed and none is selected here."
    ),
    "spare_ways": "Leave {count} spare outgoing ways ({percent} %).",
    "default_power_factor": "Loads without a power factor were taken at cos phi 0.9.",
    "contactor_ac1": (
        "Contactors are rated at least their breaker's current (AC-1); confirm the utilisation "
        "category against the catalogue for motor or lamp loads."
    ),
    "no_fault_level": (
        "No fault level given: every breaker's breaking capacity is left to be confirmed."
    ),
    "discrimination": (
        "Each breaker is rated at least {ratio} x the largest breaker after it, so they "
        "discriminate on overload. On short circuit, miniature breakers discriminate only up to "
        "the upstream breaker's instantaneous trip (about 5 x In for curve C); where total "
        "selectivity is required, confirm it from the manufacturer's selectivity tables."
    ),
    "discrimination_group_raised": (
        "Group {group} breaker raised to {rated} A, {ratio} x the {after} A breaker after it, for "
        "discrimination."
    ),
    "discrimination_group_not_met": (
        "Group {group} breaker: {rated} A is less than {ratio} x the {after} A breaker after it, "
        "and no larger rating is held for its RCCB; overload discrimination is not assured."
    ),
    "discrimination_incomer_raised": (
        "Incomer raised to {rated} A, {ratio} x the {after} A breaker after it, for "
        "discrimination."
    ),
    "discrimination_incomer_not_met": (
        "Incomer: {rated} A is less than {ratio} x the {after} A breaker after it, and a larger "
        "miniature breaker is not held; overload discrimination is not assured."
    ),
    "discrimination_feeder_raised": (
        "Feeder to {board} raised to {rated} A, {ratio} x the {after} A breaker after it on "
        "{board}, for discrimination; its cable is sized for {rated} A."
    ),
    "incomer_isolator": (
        "The incomer is a switch-disconnector: {board}'s feeder breaker protects this board, so "
        "the switch is rated no lower than that breaker and does not trip; the feeder "
        "discriminates with this board's breakers."
    ),
    "isolator_unselected": (
        "Incomer: no switch-disconnector rating held carries {current} A (up to 250 A); none is "
        "selected here."
    ),
    "breaking_capacity": (
        "Breakers are rated for at least {rating} kA breaking capacity against {fault} kA "
        "prospective at the board; each RCCB's conditional short-circuit current with the "
        "breaker ahead of it must reach it too."
    ),
    "breaking_capacity_beyond": (
        "Prospective fault {fault} kA exceeds the largest breaking capacity held "
        "({largest} kA): the breakers need back-up (cascade) protection from a current-limiting "
        "device ahead of them, confirmed from the manufacturer's tables; none is rated here."
    ),
    "fault_level_calculated": (
        "Fault level {fault} kA, calculated from {board}'s {upstream} kA through {length} m of "
        "{section} mm² {material} feeder (IEC 60909, conductors at 20 °C)."
    ),
    "fault_level_inherited": (
        "Fault level {fault} kA taken from {board}, not reduced by the feeder cable; enter this "
        "board's own fault level to rate its breakers lower."
    ),
    "earth_fault_basis": (
        "Earth fault disconnection checked from Ze {ze} Ω at U0 {voltage} V: Zs x Ia <= 0.95 U0, Ia the top of the breaker's instantaneous band (5, 10, 20 In for curves B, C, D), conductors at their maximum operating temperature, protective conductor the size of the line conductors (IEC 60364-4-41 §411.4.4)."
    ),
    "earth_fault_upsized": (
        "{load}: cable enlarged from {sized} to {section} mm² so its breaker disconnects an earth fault at the far end: Zs {loop} Ω, within {limit} Ω (IEC 60364-4-41 §411.4.4)."
    ),
    "earth_fault_exceeded": (
        "{load}: Zs {loop} Ω is above the {limit} Ω at which its {rated} A curve {curve} breaker trips at once, at every cable size held; protect the circuit with a residual current device (IEC 60364-4-41 §411.4.5)."
    ),
    "earth_fault_motor_exceeded": (
        "{load}: Zs {loop} Ω is above the {limit} Ω at which its motor breaker trips at once (I3 {trip} A), at every cable size held; protect the circuit with a residual current device (IEC 60364-4-41 §411.4.5)."
    ),
    "earth_fault_motor_basis": (
        "Motor starter breakers trip on short circuit only: their Ia is the I3 their coordination table prints, raised by 20 % for the tolerance of an instantaneous release (IEC 60947-2 §8.3.3.1.2)."
    ),
    "earth_fault_tt_no_rcd": (
        "{load}: in a TT system only a residual current device disconnects an earth fault in time (IEC 60364-4-41 §411.5); put this circuit under one."
    ),
    "earth_fault_no_ze": (
        "No Ze is given for this board, so circuits not under a residual current device are not checked for disconnection on an earth fault; enter the measured or declared Ze."
    ),
    "earth_fault_unchecked": (
        "{count} circuit(s) are not checked for earth fault disconnection: no cable length, a motor starter's breaker, or a breaker not selected here."
    ),
    "earth_loop_calculated": (
        "Ze {ze} Ω, from {board}'s {upstream} Ω plus the loop of {length} m of {section} mm² {material} feeder at its maximum operating temperature."
    ),
    "short_circuit_min_upsized": (
        "{load}: cable enlarged from {sized} to {section} mm² so a short circuit at its far end trips its breaker at once: Ikmin {current} A, at least {trip} A (ABB handbook §2.4)."
    ),
    "short_circuit_min_exceeded": (
        "{load}: a short circuit at the far end draws only {current} A, below the {trip} A that trips its {rated} A curve {curve} breaker at once, at every cable size held; shorten the run or protect it with a lower curve or rating."
    ),
    "short_circuit_withstand": (
        "Each cable's withstand k²S² is in the cable list; compare it with the selected breaker's let-through I²t at {fault} kA from its maker's curve (ABB handbook §2.4)."
    ),
    "demand_factors": (
        "Incomer rated for {demand} A on the most loaded conductor after the company's demand factors; {connected} A is connected. The factors are the company's, not a standard's."
    ),
    "parallel_cables": (
        "{load}: {runs} identical cables in parallel of {section} mm², each carrying {current} A; each run counts as a circuit in its group (ABB handbook Table 5 note 4). Lay them the same length and route so they share equally."
    ),
    "cable_conditions": (
        "Cables sized for method {method}, {ambient} °C, {grouped} grouped circuit(s), "
        "{insulation} °C insulation."
    ),
    "rules_unconfirmed": (
        "Circuit rules are this software's defaults, not confirmed by the company's engineers."
    ),
    # Voltage drop.
    "voltage_drop_upsized": (
        "{load}: cable enlarged from {sized} to {section} mm² for voltage drop over {length} m: "
        "{total} % from the origin, within the {limit} % limit."
    ),
    "voltage_drop_exceeded": (
        "{load}: voltage drop {total} % from the origin ({own} % on its own cable, {upstream} % "
        "before the board) exceeds the {limit} % limit, and no tabulated section brings it "
        "within; kept at {section} mm². Shorten the run, run cables in parallel or enlarge the "
        "feeders."
    ),
    "voltage_drop_untabulated": (
        "{load}: {section} mm² is beyond the voltage-drop tables (up to 300 mm²); its drop is "
        "not checked."
    ),
    "starting_drop_upsized": (
        "{load}: cable enlarged from {sized} to {section} mm² for starting: {multiple} x Ir at "
        "cos phi 0.35 drops {total} % from the origin, within the {limit} % allowed while "
        "starting."
    ),
    "starting_drop_exceeded": (
        "{load}: while starting the drop from the origin is {total} %, above the {limit} % "
        "allowed, and no tabulated section brings it within; kept at {section} mm². Consider a "
        "soft starter or a drive."
    ),
    "starting_drop_unchecked": (
        "{load}: the drop while starting is not checked for aluminium cable (no start-up "
        "column is tabulated)."
    ),
    "voltage_drop_unchecked": (
        "{count} circuit(s) have no cable length given; their voltage drop is not checked."
    ),
    "voltage_drop_upstream": (
        "{percent} % is dropped on the feeders before this board, and taken from every "
        "circuit's limit."
    ),
    # Projects of boards.
    "fed_from": "Fed from {board}.",
    # Motors.
    "motor_current_table": (
        "{load}: Ir {current} A, the typical Ir of a {power} kW motor in {source}; check it "
        "against the nameplate."
    ),
    "motor_current_formula": (
        "{load}: Ir {current} A from {power} kW shaft power at cos phi {power_factor}, "
        "efficiency {efficiency}; check it against the nameplate."
    ),
    "starter_table": "{load}: {starter} starter, Type 2 coordination, {source}.",
    "no_overload_row": "{load}: the table gives no overload relay for this row.",
    "star_delta_interlock": (
        "{load}: star and delta contactors need a mechanical interlock; the motor is fed by "
        "six conductors (U1 V1 W1, U2 V2 W2)."
    ),
    "drive_selected": (
        "{load}: {drive} for normal duty at {ambient} °C, behind {fuse} A aR fuses ({source}). "
        "Use a screened, symmetrical motor cable, earthed 360° at both ends."
    ),
    "drive_fuse_fault_level": (
        "{load}: the fuses need at least {current} A prospective short-circuit current at the "
        "board to operate fast enough."
    ),
    # Load schedule import, one per row.
    "import_no_description": "Row {row}: no description; skipped.",
    "import_spare": "Row {row} ({load}): a spare way; left for the spare count.",
    "import_no_power": "Row {row} ({load}): no power given; skipped.",
    "import_kind_unknown": "Row {row} ({load}): load type not recognised; taken as other.",
    "import_kind_inferred": "Row {row} ({load}): taken as {kind} from its description.",
    "import_phases_unread": "Row {row} ({load}): phases '{value}' not read; taken as 1.",
    "import_pf_out_of_range": (
        "Row {row} ({load}): power factor {value} is out of range; ignored."
    ),
    "import_kva_only": (
        "Row {row} ({load}): only kVA given; taken as kW, which assumes cos phi 1."
    ),
    # Load schedule suggestion.
    "split_points": "{load}: {points} x {watts} W = {power} kW. {source}",
    # Free text already in the reader's language (the model's own assumptions).
    "text": "{text}",
}


def fields(code: str) -> set[str]:
    """The params a code's template names.

    Args:
        code: A code in :data:`TEMPLATES`.

    Returns:
        The field names.
    """
    return {name for _, name, _, _ in string.Formatter().parse(TEMPLATES[code]) if name}


def note(code: str, **params: object) -> DesignNote:
    """Make a note: its code, its params as text, and its English rendering.

    Args:
        code: A code in :data:`TEMPLATES`.
        **params: Exactly the fields the code's template names.

    Returns:
        The note.

    Raises:
        KeyError: If the code is unknown.
        ValueError: If the params are not exactly the template's fields.
    """
    expected = fields(code)
    if set(params) != expected:
        raise ValueError(f"note {code!r} takes {sorted(expected)}, got {sorted(params)}")
    text_params = {name: str(value) for name, value in params.items()}
    return DesignNote(code=code, params=text_params, text=TEMPLATES[code].format(**text_params))
