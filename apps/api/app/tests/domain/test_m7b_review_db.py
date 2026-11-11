"""Adversarial review of M7b (configuration preservation). SYNTHETIC data only.

Each test here failed against ``6629eb6`` when written: it records a defect.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _app,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    pytestmark,
)
from app.tests.domain.test_m7b_configuration_db import (  # noqa: F401  (fixtures)
    V2,
    Switch,
    _configured,
    _count,
    _request,
    _revise,
    _saved_by_another_member,
    _switch,
)


def test_a_design_refusal_under_a_carried_configuration_is_not_a_reference_conflict(
    client: TestClient, engine: Engine, switch: Switch
) -> None:
    """A request the design refuses is not a carried reference gone.

    Without a configuration such a save is stored (with a blocking finding),
    so no entered work is lost. With one carried, it is answered with 409
    ``configuration_conflict`` naming an empty reference of kind
    ``configuration``: the contract's conflict is for a reference that no
    longer resolves, and the web's recovery ("edit or remove the binding")
    points at nothing — the user cannot tell the request is what failed.
    """
    switch(V2)
    project_id = _configured(client, V2)
    before = _count(client, project_id)
    refused = _request(V2)
    refused["boards"][1]["loads"][0]["power_kw"] = "5000"  # no tabulated section: design refused
    response = _revise(client, project_id, refused)
    body = response.json()
    if response.status_code == 409 and body.get("code") == "configuration_conflict":
        params = body.get("params") or {}
        assert params.get("ref"), f"conflict names no reference: {params}"
    else:
        assert response.status_code in (201, 422), response.text[:500]
    assert _count(client, project_id) >= before
