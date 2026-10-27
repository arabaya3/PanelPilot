"""Convert between schema ``pp-design/1`` (``DesignProject``) and the domain model.

Both directions are pure and lossless: ``to_v1(from_v1(p)) == p`` for every
valid ``DesignProject``, and ``from_v1(to_v1(m)) == m`` for every model that
is valid v1. Every v1 field is mapped by name; a field added to the v1 schema
without being mapped here fails :func:`unmapped_v1_fields`'s check (and its
test) rather than being dropped.

A dict is read with the v1 schema's own validation, which refuses unknown
fields (``extra="forbid"``) at every level: nothing is ever silently ignored.
"""

from __future__ import annotations

from dataclasses import fields
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from app.design.model import domain
from app.models.schemas.design import (
    Board,
    Cable,
    Circuit,
    Designation,
    DesignNote,
    DesignProject,
    Device,
    Part,
    ProjectInfo,
    Revision,
    Supply,
)

#: Each v1 class and the domain class mirroring it, field for field.
V1_TO_DOMAIN: tuple[tuple[type[BaseModel], type[Any]], ...] = (
    (DesignNote, domain.Note),
    (Designation, domain.Designation),
    (Part, domain.Part),
    (Device, domain.Device),
    (Cable, domain.Cable),
    (Circuit, domain.Circuit),
    (Supply, domain.Supply),
    (Board, domain.Board),
    (Revision, domain.Revision),
    (ProjectInfo, domain.ProjectInfo),
    (DesignProject, domain.DesignModel),
)


def unmapped_v1_fields() -> dict[str, set[str]]:
    """The fields on which a v1 class and its domain mirror differ; empty when lossless.

    Returns:
        By v1 class name, the field names present on one side only.
    """
    gaps: dict[str, set[str]] = {}
    for v1, mirror in V1_TO_DOMAIN:
        difference = set(v1.model_fields) ^ {f.name for f in fields(mirror)}
        if difference:
            gaps[v1.__name__] = difference
    return gaps


def from_v1(project: DesignProject | dict[str, Any]) -> domain.DesignModel:
    """The domain model of a v1 design.

    Args:
        project: A ``DesignProject``, or its JSON form as stored.

    Returns:
        The same design as plain values.

    Raises:
        pydantic.ValidationError: If a dict is not a valid v1 design (an
            unknown field included).
    """
    if not isinstance(project, DesignProject):
        project = DesignProject.model_validate(project)
    info = project.info
    return domain.DesignModel(
        info=domain.ProjectInfo(
            name=info.name,
            number=info.number,
            customer=info.customer,
            consultant=info.consultant,
            contractor=info.contractor,
            revisions=tuple(
                domain.Revision(
                    index=r.index,
                    date=r.date,
                    description=r.description,
                    drawn_by=r.drawn_by,
                    checked_by=r.checked_by,
                    approved_by=r.approved_by,
                )
                for r in info.revisions
            ),
        ),
        profile=project.profile,
        boards=tuple(_board(b) for b in project.boards),
        parts=tuple(
            domain.Part(
                key=p.key,
                manufacturer=p.manufacturer,
                type_number=p.type_number,
                order_number=p.order_number,
                description=p.description,
                source=p.source,
                width_mm=p.width_mm,
            )
            for p in project.parts
        ),
    )


def _designation(value: Designation | None) -> domain.Designation | None:
    if value is None:
        return None
    return domain.Designation(
        function=value.function, location=value.location, product=value.product
    )


def _board(board: Board) -> domain.Board:
    supply = board.supply
    return domain.Board(
        id=board.id,
        name=board.name,
        function=board.function,
        location=board.location,
        supply=domain.Supply(
            voltage_v=supply.voltage_v,
            phases=supply.phases,
            frequency_hz=supply.frequency_hz,
            earthing=supply.earthing,
            fault_level_ka=supply.fault_level_ka,
            earth_loop_ohm=supply.earth_loop_ohm,
        ),
        incomer_ids=tuple(board.incomer_ids),
        devices=tuple(
            domain.Device(
                id=d.id,
                kind=d.kind.value,
                designation=_designation(d.designation),
                part_key=d.part_key,
                poles=d.poles,
                rated_current_a=d.rated_current_a,
                residual_current_ma=d.residual_current_ma,
                curve=d.curve,
                breaking_capacity_ka=d.breaking_capacity_ka,
                description=d.description,
                upstream_id=d.upstream_id,
            )
            for d in board.devices
        ),
        cables=tuple(
            domain.Cable(
                id=c.id,
                designation=_designation(c.designation),
                cores=c.cores,
                cross_section_mm2=c.cross_section_mm2,
                material=c.material,
                insulation=c.insulation,
                length_m=c.length_m,
                part_key=c.part_key,
                withstand_ka2s=c.withstand_ka2s,
                parallel=c.parallel,
            )
            for c in board.cables
        ),
        circuits=tuple(
            domain.Circuit(
                id=c.id,
                description=c.description,
                load=c.load.value,
                power_kw=c.power_kw,
                design_current_a=c.design_current_a,
                phase=c.phase.value,
                upstream_id=c.upstream_id,
                device_ids=tuple(c.device_ids),
                cable_id=c.cable_id,
                feeds=c.feeds,
                starter=c.starter.value if c.starter is not None else None,
                voltage_drop_percent=c.voltage_drop_percent,
                earth_loop_ohm=c.earth_loop_ohm,
            )
            for c in board.circuits
        ),
        notes=tuple(
            domain.Note(code=n.code, params=MappingProxyType(dict(n.params)), text=n.text)
            for n in board.notes
        ),
        fed_from=board.fed_from,
    )


def to_v1_dict(model: domain.DesignModel) -> dict[str, Any]:
    """The v1 field values of a domain model, ``Decimal``s kept as ``Decimal``.

    Not yet validated against v1; :func:`to_v1` validates it.

    Args:
        model: The design.

    Returns:
        A dict in the shape of ``DesignProject``, every field present.
    """
    plain = _plain(model)
    if not isinstance(plain, dict):
        raise TypeError("a design model converts to a dict")
    return plain


def to_v1(model: domain.DesignModel) -> DesignProject:
    """The v1 ``DesignProject`` of a domain model.

    Args:
        model: The design.

    Returns:
        The validated v1 design.

    Raises:
        pydantic.ValidationError: If the model is not valid v1 (an unknown
            kind, an id that does not resolve).
    """
    return DesignProject.model_validate(to_v1_dict(model))


def _plain(value: Any) -> Any:
    """Dataclasses to dicts by field name, tuples to lists, mappings to dicts."""
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, MappingProxyType | dict):
        return {key: _plain(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    return value
