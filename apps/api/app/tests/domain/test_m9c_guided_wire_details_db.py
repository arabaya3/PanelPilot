"""M9c integration: wire details entered in the guided editor (SYNTHETIC data).

Drives the real routes against the disposable Postgres (``TEST_DATABASE_URL``)
with the synthetic two-board example of M7a. Wire numbers, cross-sections and
colours are illustrative only, not engineering values.

Contract ``docs/architecture/m9c-guided-wire-details.md``: wire details are the
composer's existing ``bindings[].wires`` shape; a value entered in the editor is
``{value, source: "user"}``; the preview, the saved model and the wire schedule
agree; an edit or removal makes a new revision and leaves earlier revisions and
their exports unchanged; a value the stored v2 model cannot hold is refused
naming its binding, wire and field, with nothing saved.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.design.model import ids
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    approve,
    pdf_pages,
    pytestmark,
    revision_export,
    revision_row,
)
from app.tests.design.model import m7a_support
from app.tests.domain import review_support
from app.tests.domain.test_m7b_configuration_db import (  # noqa: F401  (fixtures)
    V3,
    Switch,
    _count,
    _new_project,
    _opened,
    _revise,
    _switch,
)
from app.tests.domain.test_m9b1_binding_preview_db import _project, _rows, _validate
from app.tests.domain.test_m9b2_guided_connections_db import _c, _edited


@pytest.fixture(autouse=True)
def _saved_by_another_member(monkeypatch: pytest.MonkeyPatch) -> None:
    review_support.author_revisions_as_another_member(monkeypatch)


def _user_value(text: str) -> dict[str, str]:
    return {"value": text, "source": "user"}


#: Net n1 fully joined by wires (so the revision has no unrealised net), net n2 by one wire.
WHOLE = [_c(0, 1, "wire"), _c(1, 3, "internal"), _c(1, 2, "wire")]
DETAILS = {
    "n1/0": {
        "number": _user_value("W7"),
        "cross_section_mm2": _user_value("1.5"),
        "colour": _user_value("BU"),
    },
    "n1/2": {"number": _user_value("W8")},
}


def _configured(wires: dict[str, Any]) -> dict[str, Any]:
    config = _edited(copy.deepcopy(WHOLE), wires=copy.deepcopy(wires))
    config["patterns"][0]["nets"][1]["connections"] = [_c(0, 1, "wire")]
    return config


def _wire(model: dict[str, Any], key: str) -> dict[str, Any]:
    role, index = key.split("/")
    wid = ids.derive("w", "bind-1", role, index)
    return next(w for w in model["wires"] if w["wire_id"] == wid)


def _schedule_text(client: TestClient, project_id: str, number: int) -> str:
    response = revision_export(client, project_id, number, "wire_schedule_pdf")
    assert response.status_code == 200, response.text[:300]
    return " ".join(" ".join(page.split()) for page in pdf_pages(response.content))


def test_details_save_reopen_and_agree_with_preview_and_wire_schedule(
    client: TestClient, switch: Switch
) -> None:
    project_id = _project(client, switch)
    config = _configured(DETAILS)
    preview = _validate(client, project_id, config)
    assert preview.status_code == 200, preview.text[:600]
    saved = _revise(client, project_id, m7a_support.request(), configuration=config)
    assert saved.status_code == 201, saved.text[:600]
    opened = _opened(client, project_id, 3)
    # Reopened exactly as entered.
    assert opened["configuration"]["bindings"][0]["wires"] == DETAILS
    stored = opened["model_v2"]
    assert _rows(stored) == _rows(preview.json()["model"])
    w7 = _wire(stored, "n1/0")
    assert w7["number"]["value"] == "W7"
    assert w7["number"]["source"] == "user"
    assert w7["cross_section_mm2"]["value"] == "1.5"
    assert w7["colour"]["value"] == "BU"
    # Nothing filled in where nothing was given.
    w8 = _wire(stored, "n1/2")
    assert w8["cross_section_mm2"]["value"] is None
    assert w8["cross_section_mm2"]["source"] == "unresolved"
    shown = _wire(preview.json()["model"], "n1/0")
    assert {k: shown[k] for k in ("number", "cross_section_mm2", "colour")} == {
        k: w7[k] for k in ("number", "cross_section_mm2", "colour")
    }
    # The wire schedule prints each given value with its source; an unset value as not set.
    text = _schedule_text(client, project_id, 3)
    assert "W7 [user]" in text
    assert "1.5 [user]" in text
    assert "BU [user]" in text
    assert "W8 [user]" in text


def test_editing_and_removing_details_leaves_earlier_revisions_and_exports_unchanged(
    client: TestClient, switch: Switch, engine: Engine
) -> None:
    project_id = _project(client, switch)
    first = _revise(client, project_id, m7a_support.request(), configuration=_configured(DETAILS))
    assert first.status_code == 201, first.text[:600]
    before = revision_row(engine, project_id, 3)
    before_text = _schedule_text(client, project_id, 3)
    # Edit W7's colour; remove W8's details entirely (an explicit removal in the editor).
    edited = copy.deepcopy(DETAILS)
    edited["n1/0"]["colour"] = _user_value("RD")
    del edited["n1/2"]
    second = _revise(client, project_id, m7a_support.request(), configuration=_configured(edited))
    assert second.status_code == 201, second.text[:600]
    latest = _opened(client, project_id, 4)["model_v2"]
    assert _wire(latest, "n1/0")["colour"]["value"] == "RD"
    assert _wire(latest, "n1/2")["number"]["value"] is None
    # Revision 3 is byte-identical and exports as before.
    after = revision_row(engine, project_id, 3)
    for column in ("requirements", "requirements_sha256", "design_snapshot", "snapshot_sha256"):
        assert after[column] == before[column], column
    assert _schedule_text(client, project_id, 3) == before_text
    assert "BU [user]" in before_text
    assert "W8 [user]" in before_text
    assert "RD [user]" in _schedule_text(client, project_id, 4)


def _bad_cross_section(config: dict[str, Any]) -> None:
    config["bindings"][0]["wires"] = {"n1/0": {"cross_section_mm2": _user_value("1,5")}}


def _zero_cross_section(config: dict[str, Any]) -> None:
    config["bindings"][0]["wires"] = {"n1/0": {"cross_section_mm2": _user_value("0")}}


def _too_long(config: dict[str, Any]) -> None:
    config["bindings"][0]["wires"] = {"n1/0": {"number": _user_value("W" * 201)}}


def _empty(config: dict[str, Any]) -> None:
    config["bindings"][0]["wires"] = {"n1/0": {"colour": _user_value("")}}


REFUSED: dict[str, tuple[Any, str]] = {
    "bad_cross_section": (_bad_cross_section, "cross_section_mm2"),
    "zero_cross_section": (_zero_cross_section, "cross_section_mm2"),
    "too_long": (_too_long, "number"),
    "empty": (_empty, "colour"),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_an_invalid_value_is_refused_naming_binding_wire_and_field_with_nothing_saved(
    client: TestClient, switch: Switch, case: str
) -> None:
    project_id = _project(client, switch)
    config = _configured({})
    mutate, field = REFUSED[case]
    mutate(config)
    for response in (
        _validate(client, project_id, config),
        _revise(client, project_id, m7a_support.request(), configuration=config),
    ):
        assert response.status_code == 422, response.text[:400]
        body = response.json()
        assert body["code"] == "configuration_refused", body
        params = body["params"]
        assert params["refusal"] == "configuration_invalid", params
        assert (params["binding"], params["wire"], params["field"]) == ("bind-1", "n1/0", field)
    assert _count(client, project_id) == 2


def test_details_are_not_inherited_by_approval_and_stay_in_their_tenant(
    client: TestClient, switch: Switch, caller: _Caller, tenants: tuple[str, str]
) -> None:
    project_id = _project(client, switch)
    assert (
        _revise(
            client, project_id, m7a_support.request(), configuration=_configured({})
        ).status_code
        == 201
    )
    # Approved by another member (the fixture authors revisions as someone else).
    assert approve(client, project_id, 3).status_code == 200
    saved = _revise(client, project_id, m7a_support.request(), configuration=_configured(DETAILS))
    assert saved.status_code == 201, saved.text[:600]
    assert _opened(client, project_id, 4)["approval_current"] is not True
    assert _opened(client, project_id, 3)["approval_current"] is True
    owner = caller.user
    caller.user = _user(tenants[1], email="intruder-m9c@example.com")
    assert client.get(f"/design/projects/{project_id}", params={"revision": 4}).status_code == 404
    assert _validate(client, project_id, _configured(DETAILS)).status_code == 404
    caller.user = owner
