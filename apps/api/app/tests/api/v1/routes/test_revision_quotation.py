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


#: Two price lists for the same design; neither is reviewed or approved.
LOW_PRICES: dict[str, Any] = {"currency": "JOD", "labour_per_circuit": "10"}
HIGH_PRICES: dict[str, Any] = {
    "currency": "JOD",
    "labour_per_circuit": "95",
    "markup_percent": "20",
}


def _estimate(client: TestClient, project_id: str, pricing: dict[str, Any]) -> str:
    response = revision_export(client, project_id, 1, "quotation_pdf", pricing=pricing)
    assert response.status_code == 200, response.text[:500]
    return "\n".join(pdf_pages(response.content))


def test_an_approved_design_priced_twice_is_two_provisional_estimates(
    client: TestClient,
) -> None:
    """Prices sent with the export are never presented as approved.

    The same approved revision priced with two price lists gives two files
    that differ in their totals and price-list hash, name the same design
    and design hash, and both say they are provisional estimates whose
    prices no approval covers; neither carries REVIEW APPROVED or the
    approver's name.
    """
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    low, high = _estimate(client, saved["id"], LOW_PRICES), _estimate(
        client, saved["id"], HIGH_PRICES
    )
    assert low != high
    for body in (low, high):
        pages = body.split("\f") if "\f" in body else [body]
        assert all("PROVISIONAL ESTIMATE" in page for page in pages)
        assert "REVIEW APPROVED" not in body
        assert "approved by" not in body.lower()
        assert "Prices and rates are as supplied with this export" in body
        assert "design review approved (covers the design only)" in body
        assert "revision 01" in body
    # The stamp wraps; read its identity as one run of words.
    low_id, high_id = (
        " ".join(body.split()).split("Estimate for ", 1)[1].split("not reviewed or approved")[0]
        for body in (low, high)
    )
    # The same design, other prices: only the price-list hash differs.
    assert low_id.split("Prices supplied")[0] == high_id.split("Prices supplied")[0]
    assert low_id != high_id


def test_an_unapproved_design_estimate_says_so(client: TestClient) -> None:
    saved = save(client)
    body = _estimate(client, saved["id"], LOW_PRICES)
    assert "PROVISIONAL ESTIMATE" in body
    assert "design not review approved" in body
    assert "REVIEW APPROVED" not in body


def test_an_estimate_cannot_be_made_to_look_approved(
    client: TestClient, caller: _Caller, tenants: tuple[str, str]
) -> None:
    """Nothing the client sends puts an approval on an estimate.

    Pricing fields that are not prices are refused; the currency is a code,
    so it cannot carry a label; a member who cannot approve gets the same
    provisional estimate as one who can.
    """
    saved = save(client)
    assert approve(client, saved["id"], 1).status_code == 200
    for forged in (
        {"currency": "JOD", "label": "REVIEW APPROVED"},
        {"currency": "REVIEW APPROVED"},
        {"currency": "JOD", "approval_note": "prices approved"},
    ):
        response = revision_export(client, saved["id"], 1, "quotation_pdf", pricing=forged)
        assert response.status_code == 422, (forged, response.status_code)
    caller.user = _user(tenants[0], email="member-without-approval@example.com")
    body = _estimate(client, saved["id"], LOW_PRICES)
    assert "PROVISIONAL ESTIMATE" in body
    assert "REVIEW APPROVED" not in body


def test_a_draft_estimate_is_provisional_too(client: TestClient) -> None:
    from app.tests.api.v1.routes.test_export_integrity import designed_project, draft_export

    response = draft_export(client, designed_project(client), "quotation_pdf")
    assert response.status_code == 200, response.text[:500]
    body = "\n".join(pdf_pages(response.content))
    assert "PROVISIONAL ESTIMATE" in body
    assert "an unsaved draft" in body
    assert "REVIEW APPROVED" not in body
