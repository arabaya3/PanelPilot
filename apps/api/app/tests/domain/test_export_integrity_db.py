"""Adversarial, database-backed races on approval and revision export (Phase 1).

Two or more real sessions against the disposable Postgres, through the real
routes, at the same time. The invariants:

- an approval is recorded once, bound to the snapshot it approved;
- no export carries an approval for a snapshot it did not render;
- concurrent revisions get distinct numbers, each with its own snapshot.

A missing ``TEST_DATABASE_URL`` fails the run (see the route test's ``engine``
fixture); a skip here would hide exactly the lock these tests exist for.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    APPROVER,
    REQUEST,
    _app,
    _caller,
    _client,
    _engine,
    _tenants,
    approve,
    canonical_sha256,
    pdf_pages,
    revise,
    revision_export,
    revision_row,
    save,
)

THREADS = 8


def _race(app: FastAPI, calls: list[Callable[[TestClient], Any]]) -> list[Any]:
    """Run each call on its own client, released together."""
    barrier = threading.Barrier(len(calls))

    def run(call: Callable[[TestClient], Any]) -> Any:
        with TestClient(app, raise_server_exceptions=False) as own:
            barrier.wait()
            return call(own)

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run, calls))


def test_concurrent_approvals_record_one_bound_approval(
    app: FastAPI, client: TestClient, engine: Engine
) -> None:
    saved = save(client)
    pid = saved["id"]

    def approver(i: int) -> Callable[[TestClient], Any]:
        return lambda c: approve(c, pid, 1, approver=f"Racer {i}")

    results = _race(app, [approver(i) for i in range(THREADS)])
    statuses = [r.status_code for r in results]
    assert statuses.count(200) == 1, statuses
    assert 500 not in statuses, [r.text for r in results if r.status_code == 500]
    for r in results:
        if r.status_code != 200:
            assert r.json()["code"] == "revision_already_approved", r.text
    row = revision_row(engine, pid, 1)
    winner = next(r for r in results if r.status_code == 200)
    assert row["approved_by"] in json.dumps(winner.json())
    assert row["approved_snapshot_sha256"] == row["snapshot_sha256"]
    assert row["snapshot_sha256"] == canonical_sha256(row["design_snapshot"])


def test_exports_racing_an_approval_never_mislabel(
    app: FastAPI, client: TestClient, engine: Engine
) -> None:
    saved = save(client)
    pid = saved["id"]
    snapshot = revision_row(engine, pid, 1)["snapshot_sha256"]
    calls: list[Callable[[TestClient], Any]] = [
        lambda c: revision_export(c, pid, 1, "pdf") for _ in range(THREADS - 1)
    ]
    calls.insert(THREADS // 2, lambda c: approve(c, pid, 1))
    results = _race(app, calls)
    assert all(r.status_code == 200 for r in results), [r.status_code for r in results]
    for r in results:
        if r.headers["content-type"] != "application/pdf":
            continue
        body = "\n".join(pdf_pages(r.content))
        assert snapshot[:8] in body, "export does not name the snapshot it rendered"
        if "REVIEW APPROVED" in body:
            assert APPROVER in body
        else:
            assert APPROVER not in body
            assert "NOT FOR EXECUTION" in body


def test_unprotected_exports_racing_an_approval_are_all_refused(
    app: FastAPI, client: TestClient
) -> None:
    """No approval state, mid-race or after, lets a format with a strippable mark out."""
    saved = save(client)
    pid = saved["id"]
    calls: list[Callable[[TestClient], Any]] = [
        lambda c: revision_export(c, pid, 1, "circuits_csv") for _ in range(THREADS - 1)
    ]
    calls.insert(THREADS // 2, lambda c: approve(c, pid, 1))
    results = _race(app, calls)
    refused = [r for r in results if r.url.path.endswith("/export")]
    assert len(refused) == THREADS - 1
    for r in refused:
        assert r.status_code == 409, r.text[:300]
        assert "content-disposition" not in r.headers
        assert r.json()["code"] == "export_format_unprotected"


def test_concurrent_revisions_get_distinct_numbers_and_snapshots(
    app: FastAPI, client: TestClient, engine: Engine
) -> None:
    saved = save(client)
    pid = saved["id"]
    results = _race(app, [lambda c: revise(c, pid) for _ in range(4)])
    statuses = [r.status_code for r in results]
    assert 500 not in statuses, [r.text[:300] for r in results if r.status_code == 500]
    created = sorted(r.json()["revision"] for r in results if r.status_code == 201)
    assert len(created) == len(set(created)), created
    # A refused racer must say why (a conflict), not fail silently.
    for r in results:
        if r.status_code != 201:
            assert r.status_code == 409, r.text
            assert r.json().get("code"), r.text
    for number in [1, *created]:
        row = revision_row(engine, pid, number)
        assert row["snapshot_sha256"] == canonical_sha256(row["design_snapshot"])
        assert row["approved_snapshot_sha256"] is None


def test_revision_during_approved_export_keeps_the_old_binding(
    app: FastAPI, client: TestClient, engine: Engine
) -> None:
    saved = save(client)
    pid = saved["id"]
    assert approve(client, pid, 1).status_code == 200
    first = revision_row(engine, pid, 1)
    changed = json.loads(json.dumps(REQUEST))
    changed["boards"][0]["loads"][0]["power_kw"] = "0.4"
    calls: list[Callable[[TestClient], Any]] = [
        lambda c: revision_export(c, pid, 1, "calculations_pdf") for _ in range(THREADS - 1)
    ]
    calls.append(lambda c: revise(c, pid, changed))
    results = _race(app, calls)
    for r in results[:-1]:
        assert r.status_code == 200, r.text[:300]
        body = "\n".join(pdf_pages(r.content))
        assert "REVIEW APPROVED" in body
        assert first["snapshot_sha256"][:8] in body
    assert results[-1].status_code == 201
    second = revision_row(engine, pid, results[-1].json()["revision"])
    assert second["approved_snapshot_sha256"] is None
    assert revision_row(engine, pid, 1)["approved_snapshot_sha256"] == first["snapshot_sha256"]


def test_approve_while_two_revisions_are_written(
    app: FastAPI, client: TestClient, engine: Engine
) -> None:
    """Approving revision 1 while revisions are added binds revision 1's own hash."""
    saved = save(client)
    pid = saved["id"]
    results = _race(
        app, [lambda c: approve(c, pid, 1), lambda c: revise(c, pid), lambda c: revise(c, pid)]
    )
    assert results[0].status_code == 200, results[0].text
    row = revision_row(engine, pid, 1)
    assert row["approved_snapshot_sha256"] == row["snapshot_sha256"]
    for r in results[1:]:
        if r.status_code == 201:
            other = revision_row(engine, pid, r.json()["revision"])
            assert other["approved_snapshot_sha256"] is None
