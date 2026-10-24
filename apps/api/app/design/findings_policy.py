"""How severe each design note is: the policy that turns notes into findings.

Every note a design makes (``app.design.notes``) is classified here, once,
with where the classification comes from:

* a **cited rule**, where the note itself reports that a check against a
  sourced rule failed (or holds) and names the rule: the source is that rule;
* ``"policy"``, where the note names no rule, or where the severity is this
  service's judgement of what the note means for issuing the design (a device
  "left unselected" cannot be built, whatever rule would have selected it).

No threshold is set here. Each note already carries the outcome of the
design's own sourced check; this table only says how much that outcome stands
in the way. A code this table does not classify becomes a ``warning`` that
needs an engineer's confirmation (:func:`classify`), never ``info``, so a new
note cannot pass unread.

Blocking means the design breaks a sourced rule, or leaves out a device it
needs: such a revision cannot be approved. Warning means an engineer must
confirm something the design assumed or did not check. Info is said for the
record.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas.design import FindingSeverity

_IEC_411_4 = "IEC 60364-4-41 §411.4.4/§411.4.5"
_IEC_411_5 = "IEC 60364-4-41 §411.5"
_ABB_2_4 = "ABB Electrical installation handbook Vol. 2, §2.4"
_DROP_LIMIT = "company max_voltage_drop_percent (default IEC 60364-5-52 Annex G, Table G.52.1)"
_START_LIMIT = "company max_starting_voltage_drop_percent"


@dataclass(frozen=True)
class Classification:
    """How one note code is treated.

    Attributes:
        severity: Blocking, warning or info.
        requires_engineering_confirmation: Whether an engineer must confirm it.
        source: The rule it comes from, or ``"policy"``.
    """

    severity: FindingSeverity
    requires_engineering_confirmation: bool
    source: str


def _blocking(source: str) -> Classification:
    return Classification(FindingSeverity.BLOCKING, False, source)


def _confirm(source: str = "policy") -> Classification:
    return Classification(FindingSeverity.WARNING, True, source)


def _info(source: str = "policy") -> Classification:
    return Classification(FindingSeverity.INFO, False, source)


#: Every ``app.design.notes`` code a board's design makes, classified.
POLICY: dict[str, Classification] = {
    # A sourced check failed at every cable size held.
    "short_circuit_min_exceeded": _blocking(_ABB_2_4),
    "short_circuit_min_magnetic_exceeded": _blocking(_ABB_2_4),
    "earth_fault_exceeded": _blocking(_IEC_411_4),
    "earth_fault_magnetic_exceeded": _blocking(_IEC_411_4),
    "earth_fault_tt_no_rcd": _blocking(_IEC_411_5),
    "voltage_drop_exceeded": _blocking(_DROP_LIMIT),
    "starting_drop_exceeded": _blocking(_START_LIMIT),
    # A device the board needs is left unselected, or unrated for the fault:
    # what is drawn cannot be built as drawn.
    "mccb_needed": _blocking("policy"),
    "incomer_mccb": _blocking("policy"),
    "isolator_unselected": _blocking("policy"),
    "contactor_unselected": _blocking("policy"),
    "breaking_capacity_beyond": _blocking("policy"),
    # A motor whose starter table gives no overload relay: nothing protects
    # its cable on overload, which the starter design relies on.
    "no_overload_row": _blocking("policy"),
    # Not assured, or not checked: an engineer decides.
    "discrimination_group_not_met": _confirm(),
    "discrimination_incomer_not_met": _confirm(),
    "phase_imbalance": _confirm("company max_phase_imbalance_percent"),
    "voltage_drop_unchecked": _confirm(),
    "voltage_drop_untabulated": _confirm(),
    "starting_drop_unchecked": _confirm(),
    "earth_fault_no_ze": _confirm(),
    "earth_fault_unchecked": _confirm(),
    "no_fault_level": _confirm(),
    "short_circuit_withstand": _confirm(),
    "drive_fuse_fault_level": _confirm(),
    # Assumed rather than given: an engineer confirms the assumption.
    "rules_unconfirmed": _confirm(),
    "default_power_factor": _confirm(),
    "motor_current_table": _confirm(),
    "motor_current_formula": _confirm(),
    "contactor_ac1": _confirm(),
    "demand_factors": _confirm(),
    "above_company_rating": _confirm(),
    "fault_level_inherited": _confirm(),
    "enclosure_unknown_widths": _confirm(),
    "enclosure_not_modular": _confirm(),
    "enclosure_beyond": _confirm(),
    # Said for the record: how the design was made, and what it enlarged or
    # raised to hold a rule.
    "mccb_selected": _info(),
    "mccb_selected_adjustable": _info(),
    "enclosure_selected": _info(),
    "spare_ways": _info(),
    "discrimination": _info(),
    "discrimination_group_raised": _info(),
    "discrimination_incomer_raised": _info(),
    "discrimination_feeder_raised": _info(),
    "incomer_isolator": _info(),
    "breaking_capacity": _info(),
    "fault_level_calculated": _info("IEC 60909"),
    "earth_fault_basis": _info(_IEC_411_4),
    "earth_fault_upsized": _info(_IEC_411_4),
    "earth_fault_motor_basis": _info("IEC 60947-2 §8.3.3.1.2"),
    "earth_loop_calculated": _info(),
    "short_circuit_min_upsized": _info(_ABB_2_4),
    "parallel_cables": _info("ABB Electrical installation handbook Vol. 2, Table 5 note 4"),
    "cable_conditions": _info(),
    "market_rules": _info(),
    "voltage_drop_upsized": _info(_DROP_LIMIT),
    "starting_drop_upsized": _info(_START_LIMIT),
    "voltage_drop_upstream": _info(),
    "fed_from": _info(),
    "starter_table": _info(),
    "star_delta_interlock": _info(),
    "drive_selected": _info(),
}

#: How a code this policy does not classify is treated.
UNCLASSIFIED = Classification(FindingSeverity.WARNING, True, "policy")


def classify(code: str) -> Classification:
    """The classification of a note code; unclassified codes need confirmation.

    Args:
        code: A ``DesignNote`` code.

    Returns:
        Its classification, or :data:`UNCLASSIFIED`.
    """
    return POLICY.get(code, UNCLASSIFIED)
