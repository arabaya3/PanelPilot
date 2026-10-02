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
    "group_discrimination": (
        "Discrimination between each group breaker and its outgoing breakers is not checked."
    ),
    "no_fault_level": (
        "No fault level given: every breaker's breaking capacity is left to be confirmed."
    ),
    "cable_conditions": (
        "Cables sized for method {method}, {ambient} °C, {grouped} grouped circuit(s), "
        "{insulation} °C insulation."
    ),
    "rules_unconfirmed": (
        "Circuit rules are this software's defaults, not confirmed by the company's engineers."
    ),
    # Projects of boards.
    "fed_from": "Fed from {board}.",
    "feeder_discrimination": (
        "Discrimination between each sub-board feeder and the incomer it supplies is not "
        "checked."
    ),
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
