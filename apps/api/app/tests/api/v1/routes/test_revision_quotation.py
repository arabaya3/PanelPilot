"""A saved revision's quotation: priced from its snapshot, for its tenant only.

The fixtures and helpers are the Phase 1 adversarial suite's
(``test_export_integrity``), so these run against the same disposable
Postgres and refuse any other database.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.tests.api.v1.routes.test_export_integrity import (
    DATABASE_URL,
    REQUEST,
    _app,
    _Caller,
    _caller,
    _client,
    _engine,
    _tenants,
    _user,
    approve,
    error,
    pdf_pages,
    revision_export,
    save,
)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs Postgres: revisions and tenancy are the database's",
)

# Re-exported so pytest finds them in this module.
__all__ = ["_app", "_caller", "_client", "_engine", "_tenants"]


def test_revision_quotation_is_marked_and_from_the_snapshot(client: TestClient) -> None:
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    response = revision_export(client, saved["id"], 1, "quotation_pdf")
    assert response.status_code == 200, response.text[:500]
    for page in pdf_pages(response.content):
        assert "NOT FOR EXECUTION" in page


def test_revision_quotation_refuses_a_client_project(client: TestClient) -> None:
    saved = save(client)
    response = revision_export(
        client, saved["id"], 1, "quotation_pdf", project={"info": {"name": "x"}, "boards": []}
    )
    assert response.status_code == 422
    assert error(response)["detail"][0]["loc"] == ["body", "project"]


def test_revision_pricing_cannot_add_cable_metres(client: TestClient) -> None:
    """A default cable length is the client's, so it never adds metres to a revision."""
    request: dict[str, Any] = copy.deepcopy(REQUEST)
    for load in request["boards"][0]["loads"]:
        load.pop("length_m", None)
    saved = save(client, request)
    response = revision_export(
        client,
        saved["id"],
        1,
        "quotation_pdf",
        pricing={"currency": "JOD", "cable_length_m": "999"},
    )
    assert response.status_code == 200, response.text[:500]
    body = "\n".join(pdf_pages(response.content))
    # Every cable stays unpriced for want of a length, none at 999 m.
    assert "(no length)" in body


@pytest.mark.parametrize(
    "pricing",
    [
        {"currency": "JOD", "labour_per_circuit": "-10"},
        {"currency": "JOD", "price_list": [{"key": "k", "unit_price": "NaN"}]},
        {"currency": "JOD", "vat_percent": "1000"},
        {"currency": "JOD", "approved_by": "FORGED"},
    ],
)
def test_revision_quotation_refuses_bad_pricing(
    client: TestClient, pricing: dict[str, Any]
) -> None:
    saved = save(client)
    response = revision_export(client, saved["id"], 1, "quotation_pdf", pricing=pricing)
    assert response.status_code == 422, response.text[:500]


def test_another_tenants_revision_quotation_is_404(
    client: TestClient, caller: _Caller, tenants: tuple[str, str]
) -> None:
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    caller.user = _user(tenants[1], email="intruder@example.com")
    response = revision_export(client, saved["id"], 1, "quotation_pdf")
    assert response.status_code == 404
    assert error(response)["code"] in ("revision_not_found", "project_not_found")


def test_a_revision_quotation_names_each_line_by_the_snapshot_not_the_price_list(
    client: TestClient,
) -> None:
    """A price list's description cannot rename a device on a revision's quotation."""
    from app.design import quotation
    from app.domain import design as design_domain
    from app.models.schemas.design import ProjectDesignRequest

    designed, _ = design_domain.build_design(ProjectDesignRequest.model_validate(REQUEST))
    keys = {quotation.device_key(d) for b in designed.boards for d in b.devices}
    keys |= {p.order_number for p in designed.parts if p.order_number}
    keys |= {p.type_number for p in designed.parts if p.type_number}
    saved = save(client)
    response = revision_export(
        client,
        saved["id"],
        1,
        "quotation_pdf",
        pricing={
            "currency": "JOD",
            "price_list": [
                {"key": key, "description": "FORGED 400 A MCCB", "unit_price": "1"}
                for key in sorted(keys)
            ],
        },
    )
    assert response.status_code == 200, response.text[:500]
    body = "\n".join(pdf_pages(response.content))
    assert "FORGED" not in body
