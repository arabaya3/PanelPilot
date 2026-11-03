"""M6a adversarial tests, no database: server-owned feeders and duplicate designations.

Written against ``docs/architecture/m6a-guided-intake.md`` independently of the
implementation. They drive the stateless ``POST /design/project`` route (which
persists nothing and reads no session) and the findings module directly.

Synthetic data only.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.routes import design as design_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.design import distribution, integrity
from app.domain import design as design_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.design import ProjectDesignRequest

SUPPLY = {"voltage_v": 400, "phases": 3, "fault_level_ka": 10, "earth_loop_ohm": "0.35"}


def _board(name: str, fed_from: str | None = None, kw: str = "3") -> dict[str, Any]:
    board: dict[str, Any] = {
        "name": name,
        "supply": dict(SUPPLY) if fed_from is None else {"voltage_v": 400, "phases": 3},
        "loads": [
            {"description": f"{name} sockets", "load": "socket", "power_kw": kw, "length_m": 15},
            {"description": f"{name} lights", "load": "lighting", "power_kw": "1", "length_m": 15},
        ],
    }
    if fed_from is not None:
        board["fed_from"] = fed_from
        board["feeder_length_m"] = 25
    return board


def _request(*boards: dict[str, Any]) -> dict[str, Any]:
    return {"info": {"name": "M6a ADV", "number": "M6A-ADV"}, "boards": list(boards)}


@pytest.fixture(name="client")
def _client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(design_route.router, prefix="/design")
    user = CurrentUser(
        id=str(uuid.uuid4()),
        email="m6a-adv@synthetic.example",
        tenant_id=str(uuid.uuid4()),
        roles=frozenset({Role.ENGINEER}),
    )
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[deps.enforce_design_rate_limit] = lambda: None
    install_exception_handlers(app)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _refused(response: Any, code: str) -> dict[str, Any]:
    assert response.status_code == 422, (response.status_code, response.text[:600])
    body: dict[str, Any] = response.json()
    assert body.get("code") == code, body
    return body


# --------------------------------------------------------------------------- #
# 1. Server-owned feeders
# --------------------------------------------------------------------------- #


def test_a_client_supplied_feeds_is_refused_naming_board_and_row(client: TestClient) -> None:
    main = _board("MDB")
    main["loads"].append(
        {
            "description": "Feeder typed",
            "load": "sub_board",
            "power_kw": "50",
            "phases": 3,
            "feeds": "DB2",
        }
    )
    body = _refused(
        client.post("/design/project", json=_request(main, _board("DB2", "MDB"))),
        "feeder_server_owned",
    )
    assert body["params"] == {"board": "MDB", "load_index": 2}


def test_feeds_on_an_ordinary_load_is_refused_too(client: TestClient) -> None:
    """``feeds`` is the server's alone, whatever kind of load it is put on."""
    main = _board("MDB")
    main["loads"][0]["feeds"] = "DB2"
    body = _refused(
        client.post("/design/project", json=_request(main, _board("DB2", "MDB"))),
        "feeder_server_owned",
    )
    assert body["params"] == {"board": "MDB", "load_index": 0}


def test_a_sub_board_load_alongside_fed_from_is_refused(client: TestClient) -> None:
    """The duplicate-feeder bug: a typed sub_board row plus ``fed_from`` drew two feeders."""
    main = _board("MDB")
    main["loads"].append(
        {"description": "Feeder to DB2 (typed)", "load": "sub_board", "power_kw": "50", "phases": 3}
    )
    body = _refused(
        client.post("/design/project", json=_request(main, _board("DB2", "MDB"))),
        "sub_board_load_server_owned",
    )
    assert body["params"] == {"board": "MDB", "load_index": 2}
    # The message tells the user what to do instead.
    assert "fed from" in body["detail"].lower()


def test_a_sub_board_load_without_any_fed_from_is_refused(client: TestClient) -> None:
    """Refused for new designs, not only when it duplicates a ``fed_from``."""
    main = _board("MDB")
    main["loads"].append(
        {"description": "Spare sub-board", "load": "sub_board", "power_kw": "20", "phases": 3}
    )
    _refused(client.post("/design/project", json=_request(main)), "sub_board_load_server_owned")


def test_two_boards_claiming_the_same_child_are_refused(client: TestClient) -> None:
    """A second board claiming DB2 through ``feeds`` cannot sneak in a second feeder."""
    other = _board("MDB2")
    other["loads"].append(
        {
            "description": "Feeder claim",
            "load": "sub_board",
            "power_kw": "30",
            "phases": 3,
            "feeds": "DB2",
        }
    )
    response = client.post(
        "/design/project", json=_request(_board("MDB"), other, _board("DB2", "MDB"))
    )
    assert response.status_code == 422, response.text[:600]
    assert response.json()["code"] in {"feeder_server_owned", "sub_board_load_server_owned"}


def test_a_sub_boards_feeder_appears_exactly_once_sized_from_its_own_design(
    client: TestClient,
) -> None:
    response = client.post(
        "/design/project",
        json=_request(_board("MDB"), _board("DB2", "MDB", kw="6"), _board("DB3", "DB2", kw="2")),
    )
    assert response.status_code == 200, response.text[:600]
    boards = {b["name"]: b for b in response.json()["project"]["boards"]}
    for parent, child in (("MDB", "DB2"), ("DB2", "DB3")):
        feeders = [c for c in boards[parent]["circuits"] if c.get("feeds") is not None]
        assert [c["feeds"] for c in feeders] == [child], feeders
        # No other circuit anywhere in the project feeds that child.
        everywhere = [c for b in boards.values() for c in b["circuits"] if c.get("feeds") == child]
        assert len(everywhere) == 1
    request = ProjectDesignRequest.model_validate(
        _request(_board("MDB"), _board("DB2", "MDB", kw="6"), _board("DB3", "DB2", kw="2"))
    )
    designed, _ = design_domain.build_design(request)
    by_name = {b.name: b for b in designed.boards}
    feeder = next(c for c in by_name["MDB"].circuits if c.feeds == "DB2")
    sub_current = max(distribution.phase_currents(by_name["DB2"].circuits).values())
    assert sub_current <= feeder.design_current_a <= sub_current + Decimal("0.05")


@pytest.mark.parametrize(
    ("boards", "code", "board"),
    [
        ([_board("MDB"), _board("DB2", "NOPE")], "board_supply_unknown", "DB2"),
        ([_board("MDB"), _board("DB2", "DB2")], "board_feeds_itself", "DB2"),
        (
            [_board("MDB"), _board("DB2", "DB3"), _board("DB3", "DB2")],
            "boards_loop",
            None,
        ),
        ([_board("MDB"), _board("MDB")], "board_name_twice", "MDB"),
    ],
)
def test_inconsistent_feeding_is_refused_clearly(
    client: TestClient, boards: list[dict[str, Any]], code: str, board: str | None
) -> None:
    body = _refused(client.post("/design/project", json=_request(*copy.deepcopy(boards))), code)
    if board is not None:
        assert body["params"]["board"] == board
    else:
        assert body["params"]["board"] in {"DB2", "DB3"}


# --------------------------------------------------------------------------- #
# 4. Duplicate designations (v1)
# --------------------------------------------------------------------------- #


def test_a_duplicate_designation_within_a_board_is_a_blocking_finding() -> None:
    request = ProjectDesignRequest.model_validate(_request(_board("MDB")))
    designed, _ = design_domain.build_design(request)
    board = designed.boards[0]
    breakers = [d for d in board.devices if d.designation is not None]
    assert len(breakers) >= 2
    breakers[1].designation = breakers[0].designation.model_copy()
    found = [f for f in integrity.project_findings(designed) if f.code == "designation_duplicate"]
    assert found, "a duplicated printed designation raised no designation_duplicate"
    assert {f.severity.value for f in found} == {"blocking"}
    assert integrity.blocking(found) == found


def test_the_same_designation_on_two_boards_is_not_a_duplicate() -> None:
    """Within a board: MDB-Q1 and DB2-Q1 print differently and are not duplicates."""
    request = ProjectDesignRequest.model_validate(_request(_board("MDB"), _board("DB2", "MDB")))
    designed, _ = design_domain.build_design(request)
    codes = {f.code for f in integrity.project_findings(designed)}
    assert "designation_duplicate" not in codes
