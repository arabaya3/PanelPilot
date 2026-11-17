"""Preview the current unsaved load schedule and configuration together (SYNTHETIC data).

Drives the real routes against the disposable Postgres (``TEST_DATABASE_URL``)
with the synthetic two-board example of M7a (MB-1 feeds SB-1). Loads, names
and markings are illustrative only, not engineering values.

Contract ``docs/architecture/preview-current-inputs.md``: ``configuration/validate``
takes the editor's current ``request`` beside the configuration and designs and
composes both exactly as a save does, saving nothing. Circuit, cable and device
ids are positional, so a reference whose id now names another load is refused
(``reference_moved``) on preview and save alike until the user chooses again or
confirms it for the load now there.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    approve,
    pytestmark,
    revision_row,
)
from app.tests.design.model import m7a_support
from app.tests.domain import review_support
from app.tests.domain.test_cable_core_identity_db import _cabled
from app.tests.domain.test_m7b_configuration_db import (  # noqa: F401  (fixtures)
    V3,
    Switch,
    _count,
    _new_project,
    _opened,
    _revise,
    _switch,
)
from app.tests.domain.test_m9a_guided_patterns_db import _configuration
from app.tests.domain.test_m9b1_binding_preview_db import _project, _rows


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default deployment: the cross-board cable-core write gate off."""
    monkeypatch.setenv("DESIGN_CABLE_CORE_CROSS_BOARD", "false")


def _preview(
    client: TestClient,
    project_id: str,
    configuration: dict[str, Any],
    request: dict[str, Any] | None = None,
    confirmed: list[dict[str, Any]] | None = None,
) -> Any:
    body: dict[str, Any] = {"configuration": configuration}
    if request is not None:
        body["request"] = request
    if confirmed is not None:
        body["references_confirmed"] = confirmed
    return client.post(f"/design/projects/{project_id}/configuration/validate", json=body)


def _save(
    client: TestClient,
    project_id: str,
    request: dict[str, Any],
    configuration: dict[str, Any] | None,
    confirmed: list[dict[str, Any]] | None = None,
) -> Any:
    extra: dict[str, Any] = {"configuration": configuration}
    if confirmed is not None:
        extra["references_confirmed"] = confirmed
    return _revise(client, project_id, request, **extra)


def _with_load(request: dict[str, Any], board: str, load: dict[str, Any], at: int) -> None:
    loads = next(b for b in request["boards"] if b["name"] == board)["loads"]
    loads.insert(at, load)


HEATER = {"description": "Heater", "load": "socket", "power_kw": "1.5"}


def _bound_on_c1() -> dict[str, Any]:
    """bind-1 on MB-1 naming circuit c1 (Lighting), with a wire detail."""
    config = _configuration(bound=True)
    config["patterns"][0]["nets"][0]["connections"] = [
        {"from": 0, "to": 1, "via": "wire", "core": None, "label": None},
        {"from": 1, "to": 2, "via": "wire", "core": None, "label": None},
    ]
    config["bindings"][0]["circuit"] = "c1"
    config["bindings"][0]["wires"] = {"n1/0": {"number": {"value": "W7", "source": "user"}}}
    return config


def _saved_bound(client: TestClient, switch: Switch) -> str:
    project_id = _project(client, switch)
    saved = _save(client, project_id, m7a_support.request(), _bound_on_c1())
    assert saved.status_code == 201, saved.text[:600]
    return project_id


# --------------------------------------------------------------------------- #
# The preview of the current inputs
# --------------------------------------------------------------------------- #


def test_without_a_request_the_preview_is_as_before(client: TestClient, switch: Switch) -> None:
    project_id = _project(client, switch)
    response = _preview(client, project_id, _configuration())
    assert response.status_code == 200, response.text[:600]
    body = response.json()
    assert body["from_current_request"] is False
    assert body["based_on_revision"] == 2
    assert {b["id"] for b in body["boards"]} == {"MB-1", "SB-1"}


def test_an_unsaved_sub_board_load_is_designed_offered_and_composed_as_a_save_would(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _project(client, switch)
    before = revision_row(engine, project_id, 2)
    request = m7a_support.request()
    _with_load(request, "SB-1", HEATER, 1)
    # bind-1 on MB-1 with c1-cable; its reuse on SB-1 uses the new load's cable c2-cable.
    config = _cabled("c1-cable", "c2-cable")
    # The choices come from the server's design of the current inputs.
    choices = _preview(client, project_id, _configuration(), request).json()["boards"]
    sb1 = next(b for b in choices if b["id"] == "SB-1")
    assert [c["description"] for c in sb1["circuits"]] == ["Sockets", "Heater"]
    assert [c["id"] for c in sb1["cables"]] == ["c1-cable", "c2-cable"]
    # Against the saved schedule, SB-1 has no c2-cable: refused, as before.
    old = _preview(client, project_id, config)
    assert old.status_code == 422, old.text[:400]
    assert old.json()["params"]["refusal"] == "unknown_reference"
    # Against the current schedule, it composes.
    shown = _preview(client, project_id, config, request)
    assert shown.status_code == 200, shown.text[:600]
    assert shown.json()["from_current_request"] is True
    # Nothing saved by either preview.
    assert _count(client, project_id) == 2
    after = revision_row(engine, project_id, 2)
    for column in ("requirements", "requirements_sha256", "design_snapshot", "snapshot_sha256"):
        assert after[column] == before[column], column
    # Saving the same inputs once composes exactly what the preview showed.
    saved = _save(client, project_id, request, config)
    assert saved.status_code == 201, saved.text[:600]
    assert _rows(_opened(client, project_id, 3)["model_v2"]) == _rows(shown.json()["model"])


def _zero_power(request: dict[str, Any]) -> None:
    request["boards"][0]["loads"][0]["power_kw"] = "0"


def _unknown_supply(request: dict[str, Any]) -> None:
    request["boards"][1]["fed_from"] = "NO-SUCH-BOARD"


@pytest.mark.parametrize("mutate", [_zero_power, _unknown_supply])
def test_an_invalid_current_request_is_refused_by_preview_and_save_alike(
    client: TestClient, switch: Switch, mutate: Any
) -> None:
    project_id = _project(client, switch)
    request = m7a_support.request()
    mutate(request)
    preview = _preview(client, project_id, _configuration(), request)
    save = _save(client, project_id, request, _configuration())
    assert preview.status_code == save.status_code, (preview.text[:300], save.text[:300])
    assert preview.status_code in (400, 409, 422)
    assert preview.json().get("code") == save.json().get("code")
    assert _count(client, project_id) == 2


def test_the_preview_alters_no_approval_and_another_tenant_gets_404(
    client: TestClient, switch: Switch, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _project(client, switch)
    assert approve(client, project_id, 2).status_code == 200
    request = m7a_support.request()
    _with_load(request, "SB-1", HEATER, 1)
    assert _preview(client, project_id, _configuration(), request).status_code == 200
    assert _opened(client, project_id, 2)["approval_current"] is True
    assert _count(client, project_id) == 2
    owner = caller.user
    caller.user = _user(tenants[1], email="intruder-preview@example.com")
    assert _preview(client, project_id, _configuration(), request).status_code == 404
    caller.user = owner


# --------------------------------------------------------------------------- #
# References that moved with the load schedule
# --------------------------------------------------------------------------- #

CONFIRM_C1 = [{"binding": "bind-1", "field": "circuit", "key": None, "id": "c1"}]


def _refusal(response: Any) -> dict[str, Any]:
    assert response.status_code == 422, response.text[:400]
    params: dict[str, Any] = response.json()["params"]
    return params


def test_inserting_a_load_before_a_bound_circuit_is_refused_until_confirmed(
    client: TestClient, switch: Switch
) -> None:
    project_id = _saved_bound(client, switch)
    request = m7a_support.request()
    _with_load(request, "MB-1", HEATER, 0)  # c1 is now Heater; Lighting is c2
    config = _bound_on_c1()
    for response in (
        _preview(client, project_id, config, request),
        _save(client, project_id, request, config),
    ):
        params = _refusal(response)
        assert params["refusal"] == "reference_moved", params
        assert (params["binding"], params["field"], params["ref"]) == ("bind-1", "circuit", "c1")
        assert (params["before"], params["after"]) == ("Lighting", "Heater")
    assert _count(client, project_id) == 3
    # Choosing the circuit the load moved to is not a move.
    rebound = copy.deepcopy(config)
    rebound["bindings"][0]["circuit"] = "c2"
    assert _preview(client, project_id, rebound, request).status_code == 200
    # Or the user keeps c1 for the load now there: explicit, sent with the request.
    assert _preview(client, project_id, config, request, CONFIRM_C1).status_code == 200
    saved = _save(client, project_id, request, config, CONFIRM_C1)
    assert saved.status_code == 201, saved.text[:600]
    # Wire details are kept as they were, on the same binding.
    stored = _opened(client, project_id, 4)["configuration"]["bindings"][0]
    assert stored["wires"] == {"n1/0": {"number": {"value": "W7", "source": "user"}}}
    assert stored["circuit"] == "c1"


def test_a_carried_configuration_is_refused_too_never_silently_re_attached(
    client: TestClient, switch: Switch
) -> None:
    project_id = _saved_bound(client, switch)
    request = m7a_support.request()
    _with_load(request, "MB-1", HEATER, 0)
    params = _refusal(_revise(client, project_id, request))  # no configuration: carried
    assert params["refusal"] == "reference_moved", params
    assert _count(client, project_id) == 3


def test_deleting_and_reordering_loads_are_refused_and_appending_is_not(
    client: TestClient, switch: Switch
) -> None:
    project_id = _saved_bound(client, switch)
    config = _bound_on_c1()
    # Appending after the bound load keeps c1 Lighting.
    appended = m7a_support.request()
    _with_load(appended, "MB-1", HEATER, 1)
    assert _preview(client, project_id, config, appended).status_code == 200
    # Removing Lighting and adding another first load: c1 is reused by a different load.
    reused = m7a_support.request()
    reused["boards"][0]["loads"] = [HEATER]
    assert _refusal(_preview(client, project_id, config, reused))["after"] == "Heater"
    # Reordering two loads moves c1 to the other.
    reordered = m7a_support.request()
    _with_load(reordered, "MB-1", HEATER, 1)
    loads = reordered["boards"][0]["loads"]
    loads[0], loads[1] = loads[1], loads[0]
    assert _refusal(_preview(client, project_id, config, reordered))["before"] == "Lighting"


def test_a_confirmation_is_not_stored_and_applies_only_to_its_reference(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _saved_bound(client, switch)
    request = m7a_support.request()
    _with_load(request, "MB-1", HEATER, 0)
    config = _bound_on_c1()
    wrong = [{"binding": "bind-1", "field": "circuit", "key": None, "id": "c9"}]
    assert _refusal(_preview(client, project_id, config, request, wrong))["ref"] == "c1"
    saved = _save(client, project_id, request, config, CONFIRM_C1)
    assert saved.status_code == 201, saved.text[:600]
    record = revision_row(engine, project_id, 4)["requirements"]
    assert "references_confirmed" not in str(record)


# --------------------------------------------------------------------------- #
# The preview runs Save's own checks of the current request
# --------------------------------------------------------------------------- #

LINK = {
    "fed_board": "SB-1",
    "from_end": {"label": "SYN outgoing way 3"},
    "to_end": {"label": "SYN incomer"},
    "attributes": {"cores": 4},
}


@pytest.mark.parametrize("fed_board", ["SB-1", "NO-SUCH-BOARD"])
def test_feeder_links_of_the_current_request_are_checked_as_a_save_checks_them(
    client: TestClient, switch: Switch, fed_board: str
) -> None:
    project_id = _project(client, switch)
    request = m7a_support.request([{**LINK, "fed_board": fed_board}])
    preview = _preview(client, project_id, _configuration(), request)
    if fed_board == "SB-1":
        assert preview.status_code == 200, preview.text[:600]
        assert _count(client, project_id) == 2
        assert _save(client, project_id, request, _configuration()).status_code == 201
        return
    save = _save(client, project_id, request, _configuration())
    assert preview.status_code == save.status_code == 422, (preview.text[:300], save.text[:300])
    assert preview.json()["code"] == save.json()["code"]
    assert preview.json().get("params") == save.json().get("params")
    assert _count(client, project_id) == 2


def _renamed(request: dict[str, Any], name: str) -> None:
    request["boards"][0]["name"] = name
    request["boards"][1]["fed_from"] = name


def test_a_renamed_board_does_not_hide_a_moved_reference(
    client: TestClient, switch: Switch
) -> None:
    project_id = _saved_bound(client, switch)
    config = _bound_on_c1()
    config["bindings"][0]["board"] = "MB-9"
    for strip in config.get("strips", []):
        if strip["board"] == "MB-1":
            strip["board"] = "MB-9"
    for item in config.get("components", []):
        if item["board"] == "MB-1":
            item["board"] = "MB-9"
    renamed = m7a_support.request()
    _renamed(renamed, "MB-9")
    # The rename alone moves nothing.
    assert _preview(client, project_id, config, renamed).status_code == 200
    # Renamed and a load inserted before the bound one: refused.
    _with_load(renamed, "MB-9", HEATER, 0)
    params = _refusal(_preview(client, project_id, config, renamed))
    assert (params["refusal"], params["before"], params["after"]) == (
        "reference_moved",
        "Lighting",
        "Heater",
    )
