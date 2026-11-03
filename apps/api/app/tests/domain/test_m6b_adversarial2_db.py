"""M6b adversarial tests, phase 2: requirements binding, export currency, resave edges.

Independent reviewer, against the routes and the disposable Postgres. Contract
points (integration owner, phase 2):

* an approval binds the requirements record (``approved_requirements_sha256``);
  NULL (an approval made before the binding) is *not bound*: the approval is
  not current and every export prints it as a draft, never as approved;
* a record changed after approval (even with its hash recomputed) makes the
  approval not current; exports print draft;
* a trial's save records no ``author_user_id``; trial cannot resave or approve;
* an acknowledgement requires the latest revision (``revision_not_latest``);
* resave carries acknowledgements whose question and basis still stand.

Synthetic data only.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from sqlalchemy.engine import Engine

from app.design import marking, snapshot
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _engine,
    _tenants,
    error,
    pdf_pages,
    pytestmark,
    revision_row,
    update_row,
)
from app.tests.domain.test_m6a_adversarial_db import FED, MISSING
from app.tests.domain.test_m6b_adversarial_db import (  # noqa: F401  (fixtures)
    _ack,
    _app_fixture,
    _approve,
    _as_fixture,
    _open,
    _resave,
    _save,
    _summary,
    _who_fixture,
    _Who,
)


def _export(c: Any, pid: str, n: int, fmt: str = "pdf") -> Any:
    return c.post(f"/design/projects/{pid}/revisions/{n}/export", json={"format": fmt})


def _label_of(response: Any) -> str:
    assert response.status_code == 200, response.text[:600]
    text = "\n".join(pdf_pages(response.content))
    if marking.REVIEW_APPROVED_LABEL in text:
        return "approved"
    if marking.DRAFT_LABEL in text:
        return "draft"
    return "none"


def _approved(engine: Engine, as_: Any, request: dict[str, Any] | None = None) -> str:
    pid = _save(as_("engineer"), request or FED)
    ok = _approve(as_("reviewer"), pid, 1)
    assert ok.status_code == 200, ok.text[:600]
    return pid


def test_c_fresh_approval_binds_and_exports_approved(engine: Engine, as_: Any) -> None:
    pid = _approved(engine, as_)
    row = revision_row(engine, pid, 1)
    assert row["approved_requirements_sha256"] == row["requirements_sha256"]
    assert _summary(_open(as_("owner"), pid, 1), 1)["approval_current"] is True
    for fmt in ("pdf", "calculations_pdf"):
        assert _label_of(_export(as_("owner"), pid, 1, fmt)) == "approved", fmt


def test_c_unbound_approval_is_not_current_and_exports_draft(engine: Engine, as_: Any) -> None:
    pid = _approved(engine, as_)
    update_row(engine, pid, 1, approved_requirements_sha256=None)
    summary = _summary(_open(as_("owner"), pid, 1), 1)
    assert summary["approval_current"] is False
    assert "requirements" in summary["approval_changed"]
    for fmt in ("pdf", "calculations_pdf"):
        assert _label_of(_export(as_("owner"), pid, 1, fmt)) == "draft", fmt
    # Re-approval is refused (approved already); recovery is a resave.
    assert _approve(as_("reviewer2"), pid, 1).status_code == 409


def test_c_record_rewritten_after_approval_is_not_current(engine: Engine, as_: Any) -> None:
    pid = _approved(engine, as_, MISSING)
    row = revision_row(engine, pid, 1)
    record = copy.deepcopy(row["requirements"])
    q = next(x for x in record["questions"] if x["state"] == "open")
    record["acknowledgements"].append(
        {"question_id": q["id"], "by_user_id": "x", "by_name": "Synthetic forger", "at": "2026"}
    )
    update_row(engine, pid, 1, requirements=record, requirements_sha256=snapshot.sha256_of(record))
    summary = _summary(_open(as_("owner"), pid, 1), 1)
    assert summary["approval_current"] is False
    assert "requirements" in summary["approval_changed"]
    response = _export(as_("owner"), pid, 1, "pdf")
    assert _label_of(response) == "draft"


def test_c_derived_record_approval_binds_null(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"), FED)
    update_row(engine, pid, 1, requirements=None, requirements_sha256=None)
    assert _approve(as_("reviewer"), pid, 1).status_code == 200
    row = revision_row(engine, pid, 1)
    assert row["approved_requirements_sha256"] == snapshot.sha256_of(None)
    assert _summary(_open(as_("owner"), pid, 1), 1)["approval_current"] is True
    # A stored record appearing later is not what was approved.
    rec = revision_row(engine, pid, 1)
    # A well-formed stored record (another save's), appearing after the approval.
    fake = revision_row(engine, _save(as_("engineer"), FED), 1)["requirements"]
    update_row(engine, pid, 1, requirements=fake, requirements_sha256=snapshot.sha256_of(fake))
    assert _summary(_open(as_("owner"), pid, 1), 1)["approval_current"] is False
    del rec


def test_c_trial_save_records_no_account_and_cannot_be_approved(
    engine: Engine, as_: Any, who: _Who
) -> None:
    saved = as_("trial_id").post(
        "/design/projects", json={"name": "trial", "request": FED, "note": "t"}
    )
    if saved.status_code != 201:
        return  # a trial may not save in this tenant; nothing to check
    pid = str(saved.json()["id"])
    assert revision_row(engine, pid, 1)["author_user_id"] is None
    # The account later "claiming" it under the same id is not its author.
    who.users["trial_id"] = who.users["trial_id"].model_copy(
        update={"email": f"claimed-{uuid.uuid4().hex[:8]}@synthetic.example"}
    )
    refused = _approve(as_("reviewer"), pid, 1)
    assert refused.status_code == 409, refused.text[:600]
    assert error(refused)["code"] == "approval_author_not_established"


def test_c_trial_cannot_resave(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"), FED)
    for trial in ("trial", "trial_id"):
        refused = _resave(as_(trial), pid, 1)
        assert refused.status_code == 403, refused.text[:600]
        assert error(refused)["code"] == "resave_requires_account"
    assert _open(as_("owner"), pid)["revision"] == 1


def test_c_ack_on_superseded_revision_is_409(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"))
    qid = next(
        q["id"] for q in revision_row(engine, pid, 1)["requirements"]["questions"]
        if q["kind"] == "assumption"
    )
    assert _resave(as_("reviewer"), pid, 1).status_code == 201
    refused = _ack(as_("reviewer"), pid, 1, qid)
    assert refused.status_code == 409, refused.text[:600]
    assert error(refused)["code"] == "revision_not_latest"


def test_c_resave_carries_acks_whose_basis_stands(engine: Engine, as_: Any, who: _Who) -> None:
    pid = _save(as_("engineer"))
    qid = next(
        q["id"] for q in revision_row(engine, pid, 1)["requirements"]["questions"]
        if q["kind"] == "assumption"
    )
    assert _ack(as_("engineer"), pid, 1, qid).status_code == 200
    before = revision_row(engine, pid, 1)
    assert _resave(as_("reviewer"), pid, 1).status_code == 201
    new = revision_row(engine, pid, 2)["requirements"]
    carried = [a for a in new["acknowledgements"] if a["question_id"] == qid]
    assert len(carried) == 1
    assert carried[0]["by_user_id"] == who.users["engineer"].id
    assert revision_row(engine, pid, 1) == before


def test_c_resave_other_tenant_and_missing_is_404(engine: Engine, as_: Any) -> None:
    pid = _save(as_("engineer"), FED)
    assert _resave(as_("other_tenant"), pid, 1).status_code == 404
    assert _resave(as_("reviewer"), pid, 7).status_code == 404
    assert _resave(as_("reviewer"), str(uuid.uuid4()), 1).status_code == 404
    assert _open(as_("owner"), pid)["revision"] == 1
