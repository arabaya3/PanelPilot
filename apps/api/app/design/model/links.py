"""Feeder links between boards (M7a, ``pp-design/3``): entry, derivation, findings, rows.

One link per server-owned feeder: ``fed_from`` stays the source of truth, the
server derives the link, and the user only fills in what they know of it
(``feeder_links`` in the request): each end's modelled terminal or free-text
label, and the cable/conductor attributes. A designer value is never copied
into a link; an attribute is the user's or unset, and an unset one prints
"not given".

Nothing here changes a calculation or decides completeness. The findings
say what is not set (``link_endpoint_unresolved``, ``link_attribute_unset``)
and where a user value differs from what the feeder calculation used
(``link_value_differs_from_calculation``), showing both without saying
which is right.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.errors import ValidationError
from app.design.model import codec_v3, entities
from app.models.schemas.design import (
    FeederLinkEndInput,
    FeederLinkInput,
    FeederLinkTerminal,
    ProjectDesignRequest,
)

#: Printed for an end with no modelled terminal.
TERMINAL_NOT_SET = "terminal NOT SET"

#: Printed after a user's label: it is their text, not a modelled terminal.
LABEL_NOTE = "label given by user — not a modelled terminal"

#: Printed for an attribute the user did not give.
NOT_GIVEN = "not given"

#: Each finding code with its severity (warnings all; none blocks approval).
SEVERITIES: Mapping[str, str] = {
    "link_endpoint_unresolved": "warning",
    "link_attribute_unset": "warning",
    "link_value_differs_from_calculation": "warning",
}

#: Every link finding code.
CODES: tuple[str, ...] = tuple(SEVERITIES)

#: The link codes that block execution (execution export stays refused anyway).
EXECUTION_BLOCKING = frozenset({"link_endpoint_unresolved", "link_attribute_unset"})

#: The link codes an engineer confirms (missing information).
CONFIRM = frozenset({"link_endpoint_unresolved", "link_attribute_unset"})

#: Printed names of the attributes.
ATTRIBUTE_NAMES: Mapping[str, str] = {
    "cores": "cores",
    "cross_section_mm2": "cross-section mm²",
    "material": "material",
    "insulation": "insulation",
    "length_m": "length m",
    "number": "number",
    "colour": "colour",
}


# --------------------------------------------------------------------------- #
# Entry: checks on the request alone
# --------------------------------------------------------------------------- #


def check_request(request: ProjectDesignRequest) -> None:
    """Refuse feeder links that name no feeder of the request, or one twice.

    Raises:
        ValidationError: ``link_board_unknown`` (no such board),
            ``link_not_a_feeder`` (the board has no ``fed_from``) or
            ``link_duplicate`` (a board's link given twice); each names the
            board and the link's index.
    """
    boards = {board.name: board for board in request.boards}
    seen: set[str] = set()
    for index, entry in enumerate(request.feeder_links or []):
        params = {"board": entry.fed_board, "link_index": index}
        board = boards.get(entry.fed_board)
        if board is None:
            raise ValidationError(
                f"feeder link {index + 1}: {entry.fed_board} is not a board of the project",
                code="link_board_unknown",
                params=params,
            )
        if board.fed_from is None:
            raise ValidationError(
                f"feeder link {index + 1}: {entry.fed_board} is not fed from another board "
                'of the project; a link records a feeder, set its "Fed from" first',
                code="link_not_a_feeder",
                params=params,
            )
        if entry.fed_board in seen:
            raise ValidationError(
                f"feeder link {index + 1}: the link to {entry.fed_board} is given twice",
                code="link_duplicate",
                params=params,
            )
        seen.add(entry.fed_board)


# --------------------------------------------------------------------------- #
# Derivation: one link per server-owned feeder
# --------------------------------------------------------------------------- #


def _terminal(
    model: entities.DesignModelV2,
    given: FeederLinkTerminal | None,
    board: str,
    role: str,
    entry: FeederLinkInput,
    index: int,
) -> entities.TerminalRef | None:
    if given is None:
        return None
    ref = (
        entities.TerminalRef(component=given.component, terminal=given.terminal)
        if given.component is not None
        else entities.TerminalRef(point=given.point, side=given.side)
    )
    on = codec_v3.terminal_board(model, ref)
    params = {"board": entry.fed_board, "link_index": index, "end": role, "end_board": board}
    if on is None:
        raise ValidationError(
            f"feeder link {index + 1}, {role} end: the terminal is not a terminal of the "
            "saved configuration",
            code="link_terminal_unknown",
            params=params,
        )
    if on != board:
        raise ValidationError(
            f"feeder link {index + 1}, {role} end: the terminal is on {on}, not on {board}",
            code="link_end_off_board",
            params={**params, "terminal_board": on},
        )
    return ref


def _attributes(entry: FeederLinkInput | None) -> tuple[tuple[str, entities.LinkAttribute], ...]:
    given = entry.attributes if entry is not None else None
    out: list[tuple[str, entities.LinkAttribute]] = []
    for name in entities.LINK_ATTRIBUTES:
        value: Any = getattr(given, name) if given is not None else None
        if value is None:
            out.append((name, entities.LINK_UNSET))
            continue
        if isinstance(value, Decimal):
            value = Decimal(codec_v3.canonical_decimal(value))
        out.append((name, entities.LinkAttribute(value=value, source="user")))
    return tuple(out)


def _entered(entry: FeederLinkInput | None) -> bool:
    if entry is None:
        return False
    ends = [end for end in (entry.from_end, entry.to_end) if end is not None]
    attrs = entry.attributes
    return any(end.terminal is not None or end.label is not None for end in ends) or (
        attrs is not None and any(getattr(attrs, n) is not None for n in entities.LINK_ATTRIBUTES)
    )


def derive(
    model: entities.DesignModelV2,
    entered: Sequence[FeederLinkInput] | None,
    board_refs: Mapping[str, str],
    *,
    entered_by_user_id: str | None,
    entered_at: str | None,
) -> list[entities.InterBoardLink]:
    """The links of a design: one per server-owned feeder, with what the user entered.

    Args:
        model: The v2 model the revision is saved with.
        entered: The request's ``feeder_links`` (already passed :func:`check_request`).
        board_refs: Each board's requirements-record ref, by board name.
        entered_by_user_id: The saving account (``None`` for a trial).
        entered_at: When it was saved (ISO 8601).

    Returns:
        The links, sorted by id. A link holding a value entered by the user
        records who and when; one holding none records neither.

    Raises:
        ValidationError: ``link_terminal_unknown`` for a terminal the saved
            configuration does not hold, ``link_end_off_board`` for one on
            another board than its end's.
    """
    names = {board.id: board.name for board in model.design.boards}
    by_board = {entry.fed_board: (index, entry) for index, entry in enumerate(entered or [])}
    links: list[entities.InterBoardLink] = []
    for feeder in codec_v3.derived_feeders(model):
        index, entry = by_board.get(names[feeder.fed_board], (-1, None))
        ends: list[entities.LinkEnd] = []
        for role, board, end in (
            ("from", feeder.supply_board, entry.from_end if entry else None),
            ("to", feeder.fed_board, entry.to_end if entry else None),
        ):
            given: FeederLinkEndInput | None = end
            terminal = (
                _terminal(model, given.terminal, board, role, entry, index)
                if given is not None and entry is not None
                else None
            )
            ends.append(
                entities.LinkEnd(
                    role=role,
                    board=board,
                    terminal=terminal,
                    label=given.label if given is not None else None,
                )
            )
        cable = codec_v3.feeder_cable(model, feeder)
        user = _entered(entry)
        links.append(
            entities.InterBoardLink(
                id=codec_v3.link_id(feeder.supply_board, feeder.fed_board),
                origin="derived",
                feeder=feeder,
                ends=(ends[0], ends[1]),
                carrier=(
                    entities.LinkCarrier(kind="cable", cable_id=cable.id)
                    if cable is not None
                    else entities.LinkCarrier(kind="unknown", cable_id=None)
                ),
                attributes=_attributes(entry),
                provenance=entities.LinkProvenance(
                    requirement_ref=codec_v3.requirement_ref(
                        board_refs.get(names[feeder.fed_board], names[feeder.fed_board])
                    ),
                    entered_by_user_id=entered_by_user_id if user else None,
                    entered_at=entered_at if user else None,
                ),
            )
        )
    return sorted(links, key=lambda link: link.id)


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LinkFinding:
    """One link finding: shaped like a structural finding (code, severity, message, location)."""

    code: str
    severity: str
    message: str
    location: Mapping[str, str]

    @property
    def blocks_execution(self) -> bool:
        """Whether it is an execution blocker."""
        return self.code in EXECUTION_BLOCKING


def _plain(value: Any) -> str:
    if isinstance(value, Decimal):
        return codec_v3.canonical_decimal(value)
    return str(value)


def _same(name: str, user: Any, calculated: Any) -> bool:
    if name in ("cross_section_mm2", "length_m"):
        return Decimal(str(user)) == Decimal(str(calculated))
    if name == "cores":
        return int(user) == int(calculated)
    return " ".join(str(user).split()).casefold() == " ".join(str(calculated).split()).casefold()


def calculation_values(
    model: entities.DesignModelV2, link: entities.InterBoardLink
) -> dict[str, Any]:
    """What the feeder calculation used, by link attribute; only those it holds.

    The feeder cable's cores, cross-section, material and insulation, and its
    length (the request's ``feeder_length_m``) where given.
    """
    cable = codec_v3.feeder_cable(model, link.feeder)
    if cable is None:
        return {}
    out: dict[str, Any] = {
        "cores": cable.cores,
        "cross_section_mm2": cable.cross_section_mm2,
        "material": cable.material,
        "insulation": cable.insulation,
    }
    if cable.length_m is not None:
        out["length_m"] = cable.length_m
    return out


def differences(
    model: entities.DesignModelV2, link: entities.InterBoardLink
) -> list[tuple[str, str, str]]:
    """``(attribute, user value, calculation value)`` wherever the two differ."""
    used = calculation_values(model, link)
    out: list[tuple[str, str, str]] = []
    for name, attribute in link.attributes:
        if attribute.source != "user" or name not in used:
            continue
        if not _same(name, attribute.value, used[name]):
            out.append((name, _plain(attribute.value), _plain(used[name])))
    return out


def _pair(link: entities.InterBoardLink) -> str:
    return f"{link.feeder.supply_board} to {link.feeder.fed_board}"


def findings(
    model: entities.DesignModelV2, links: Sequence[entities.InterBoardLink]
) -> list[LinkFinding]:
    """Every link finding, in a stable order (link id, then code and location)."""
    out: list[LinkFinding] = []
    for link in links:
        base = {
            "board_id": link.feeder.supply_board,
            "link": link.id,
            "fed_board": link.feeder.fed_board,
        }
        for end in link.ends:
            if end.terminal is None:
                out.append(
                    LinkFinding(
                        code="link_endpoint_unresolved",
                        severity=SEVERITIES["link_endpoint_unresolved"],
                        message=(
                            f"Link {_pair(link)}: the {end.role} end on {end.board} has no "
                            "modelled terminal (terminal NOT SET)"
                            + ("; the user's label is not a terminal" if end.label else "")
                        ),
                        location={**base, "end": end.role, "end_board": end.board},
                    )
                )
        unset = [name for name, attribute in link.attributes if attribute.source == "unset"]
        if unset:
            out.append(
                LinkFinding(
                    code="link_attribute_unset",
                    severity=SEVERITIES["link_attribute_unset"],
                    message=(
                        f"Link {_pair(link)}: not given: "
                        + ", ".join(ATTRIBUTE_NAMES[name] for name in unset)
                    ),
                    location={**base, "attributes": ",".join(unset)},
                )
            )
        for name, user, calculated in differences(model, link):
            out.append(
                LinkFinding(
                    code="link_value_differs_from_calculation",
                    severity=SEVERITIES["link_value_differs_from_calculation"],
                    message=(
                        f"Link {_pair(link)}: {ATTRIBUTE_NAMES[name]} entered by the user is "
                        f"{user}; the feeder calculation used {calculated}. The two values "
                        "differ; the calculation is not changed by the link."
                    ),
                    location={
                        **base,
                        "attribute": name,
                        "user_value": user,
                        "user_source": "user",
                        "calculation_value": calculated,
                        "calculation_source": "feeder calculation",
                    },
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Rows: what the wire schedule and the connection drawing print
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EndRow:
    """One end as printed."""

    role: str
    board: str
    terminal: str
    label: str | None
    resolved: bool
    key: tuple[str, str, str] | None


@dataclass(frozen=True)
class LinkRow:
    """One link as the wire schedule and the connection drawing print it, from one source."""

    link_id: str
    supply_board: str
    fed_board: str
    circuit_id: str
    carrier: str
    ends: tuple[EndRow, EndRow]
    attributes: tuple[tuple[str, str], ...]
    differences: tuple[tuple[str, str, str], ...]


def terminal_text(model: entities.DesignModelV2, ref: entities.TerminalRef) -> str:
    """A modelled terminal as the schedules name it: ``<owner>:<marking>``."""
    if ref.component is not None:
        component = next((c for c in model.components if c.id == ref.component), None)
        if component is None:
            return f"{ref.component}:{ref.terminal}"
        template = model.template(component.template_ref)
        terminal = template.terminal(ref.terminal or "") if template is not None else None
        owner = component.designation.product if component.designation else component.id
        return f"{owner}:{terminal.marking if terminal is not None else ref.terminal}"
    for strip in model.strips:
        for point in strip.points:
            if point.id == ref.point:
                owner = strip.designation.product if strip.designation else strip.id
                return f"{owner}:{point.marking} ({ref.side})"
    return f"{ref.point} ({ref.side})"


def label_text(label: str) -> str:
    """A user's end label as printed: quoted, and said not to be a terminal."""
    return f'"{label}" ({LABEL_NOTE})'


def attribute_text(attribute: entities.LinkAttribute) -> str:
    """An attribute as printed: ``"<value> [user]"`` or ``"not given"``."""
    if attribute.source == "unset" or attribute.value is None:
        return NOT_GIVEN
    return f"{_plain(attribute.value)} [user]"


def rows(model: entities.DesignModelV2, links: Sequence[entities.InterBoardLink]) -> list[LinkRow]:
    """The printed rows of the links, sorted by link id."""
    out: list[LinkRow] = []
    for link in sorted(links, key=lambda x: x.id):
        ends = tuple(
            EndRow(
                role=end.role,
                board=end.board,
                terminal=(
                    terminal_text(model, end.terminal)
                    if end.terminal is not None
                    else TERMINAL_NOT_SET
                ),
                label=label_text(end.label) if end.label is not None else None,
                resolved=end.terminal is not None,
                key=end.terminal.key if end.terminal is not None else None,
            )
            for end in link.ends
        )
        out.append(
            LinkRow(
                link_id=link.id,
                supply_board=link.feeder.supply_board,
                fed_board=link.feeder.fed_board,
                circuit_id=link.feeder.circuit_id,
                carrier=(
                    f"feeder cable {link.carrier.cable_id}"
                    if link.carrier.cable_id is not None
                    else "carrier unknown"
                ),
                ends=(ends[0], ends[1]),
                attributes=tuple(
                    (name, attribute_text(attribute)) for name, attribute in link.attributes
                ),
                differences=tuple(differences(model, link)),
            )
        )
    return out


def difference_text(name: str, user: str, calculated: str) -> str:
    """One discrepancy as printed, both values with their sources."""
    return (
        f"{ATTRIBUTE_NAMES[name]}: {user} [user] / {calculated} [feeder calculation] — "
        "the values differ; the calculation is unchanged"
    )
