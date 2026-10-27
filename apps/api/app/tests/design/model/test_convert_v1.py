"""Tests of ``app.design.model.convert_v1``: lossless v1 to domain and back."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.design.model import convert_v1
from app.domain import design as design_domain
from app.models.schemas.design import (
    DesignProject,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    MotorStarter,
    ProjectDesignRequest,
    ProjectInfo,
    Supply,
)


def tower() -> DesignProject:
    """Two boards, a feeder, a motor with a starter: most v1 fields set."""
    request = ProjectDesignRequest(
        info=ProjectInfo(name="Tower", number="P-7", customer="C"),
        boards=[
            DistributionBoardRequest(
                name="MDB",
                supply=Supply(fault_level_ka=Decimal(25), earth_loop_ohm=Decimal("0.2")),
                loads=[
                    LoadInput(description="Lighting", load=LoadKind.LIGHTING, power_kw=Decimal(2)),
                    LoadInput(
                        description="Pump",
                        load=LoadKind.MOTOR,
                        power_kw=Decimal("7.5"),
                        phases=3,
                        starter=MotorStarter.DIRECT_ON_LINE,
                        length_m=Decimal(30),
                    ),
                ],
            ),
            DistributionBoardRequest(
                name="SDB",
                fed_from="MDB",
                feeder_length_m=Decimal(40),
                loads=[
                    LoadInput(description="Sockets", load=LoadKind.SOCKET, power_kw=Decimal("3.5")),
                ],
            ),
        ],
    )
    designed, _ = design_domain.build_design(request)
    return designed


@pytest.fixture(params=["hall", "tower"])
def project(request: pytest.FixtureRequest, hall_project: DesignProject) -> DesignProject:
    return hall_project if request.param == "hall" else tower()


def test_every_v1_field_has_a_domain_mirror() -> None:
    assert convert_v1.unmapped_v1_fields() == {}


def test_round_trip_is_lossless(project: DesignProject) -> None:
    model = convert_v1.from_v1(project)
    assert convert_v1.to_v1(model) == project
    assert convert_v1.from_v1(convert_v1.to_v1(model)) == model


def test_a_dict_reads_as_the_model_does(project: DesignProject) -> None:
    assert convert_v1.from_v1(project.model_dump(mode="json")) == convert_v1.from_v1(project)


def test_the_tower_sets_a_starter_and_a_feeder() -> None:
    model = convert_v1.from_v1(tower())
    circuits = [c for b in model.boards for c in b.circuits]
    assert any(c.starter == MotorStarter.DIRECT_ON_LINE.value for c in circuits)
    assert any(b.fed_from == "MDB" for b in model.boards)


def test_to_v1_dict_keeps_decimals(hall_project: DesignProject) -> None:
    plain = convert_v1.to_v1_dict(convert_v1.from_v1(hall_project))
    assert isinstance(plain["boards"][0]["supply"]["voltage_v"], Decimal)


def test_an_unknown_field_in_a_dict_is_refused(hall_project: DesignProject) -> None:
    stored = hall_project.model_dump(mode="json")
    stored["boards"][0]["cables"][0]["colour"] = "red"
    with pytest.raises(PydanticValidationError):
        convert_v1.from_v1(stored)


def test_to_v1_refuses_an_invalid_model(hall_project: DesignProject) -> None:
    model = convert_v1.from_v1(hall_project)
    device = dataclasses.replace(model.boards[0].devices[0], kind="not_a_kind")
    board = dataclasses.replace(model.boards[0], devices=(device, *model.boards[0].devices[1:]))
    with pytest.raises(PydanticValidationError):
        convert_v1.to_v1(dataclasses.replace(model, boards=(board,)))
