"""M6d review: attacks on the safety resolutions and the scoped approval (``panelpilot_test_*`` only).

Written by the independent reviewer from the M6d contract, before the
implementation was read. A resolution (``excluded_from_scope`` or
``false_positive``) is by a member with approve permission who neither
created the proposal nor authored the revision, with a 10-500 character
reason, who/when set by the server, on the latest unapproved revision only;
approval is refused ``approval_blocked_by_safety`` while any concern is
unresolved and needs the literal ``scope_confirmed: true``.

The coming rules were strict xfail until the integrated head landed (M6e: all pass); the route
and body are assumed in ``test_m6d_review_journeys_db.resolve``. Tests that
pass today pin what already holds. Synthetic data only.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.engine import Engine

from app.design import snapshot
from app.tests.api.v1.routes.test_export_integrity import (  # noqa: F401  (fixtures)
    _engine,
    _tenants,
    pytestmark,
    revision_row,
    update_row,
)
from app.tests.domain.test_m6b_acknowledgement_concurrency_db import (  # noqa: F401
    _project_held,
    _race,
)
from app.tests.domain.test_m6b_ai_adversarial_db import (  # noqa: F401  (fixtures)
    StubProvider,
    _app_fixture,
    _as_fixture,
    _provider_fixture,
    _save,
    _who_fixture,
)
from app.tests.domain.test_m6d_review_journeys_db import (
    STANDBY,
    _fire,
    _standby,
    approve,
    assert_never_executable,
    board,
    by_quote,
    code,
    decide_all,
    it,
    lighting,
    mention_ids,
    motor,
    pdf_text,
    propose,
    record,
    request,
    resolve,
    unsupported_ids,
)

REASON = "Supplied and wired by the fire-protection contractor."


def _concerns(
    engine: Engine, pid: str, proposal: dict[str, Any], quote: str, n: int = 1
) -> list[dict[str, str]]:
    """Every subject to resolve on the standby/fire journey: the item, then each mention."""
    item = by_quote(proposal, quote)["id"]
    return [{"item_id": item}] + [{"mention_id": m} for m in mention_ids(record(engine, pid, n))]


def _resolutions(rec: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = (rec.get("ai") or {}).get("resolutions") or []
    return found


# --------------------------------------------------------------------------- #
# What already holds: approval with no scope body succeeds today (the bypass)
# --------------------------------------------------------------------------- #


def test_the_710da3b_bypass_an_empty_body_approval_is_now_refused(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    """At 710da3b this approved with the fire-pump mention open and FP-1 placed."""
    pid, _ = _fire(as_, provider, pump_in_request=True)
    done = approve(as_("reviewer"), pid, body={})
    # Was 200 at 710da3b; 5133b60 refuses it.
    assert done.status_code in (409, 422), done.text[:300]
    assert_never_executable(as_("reviewer"), pid)


# --------------------------------------------------------------------------- #
# Forged bodies
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "extra",
    [
        {"by_user_id": "00000000-0000-0000-0000-000000000000"},
        {"by_name": "Chief Engineer"},
        {"at": "2001-01-01T00:00:00Z"},
        {"validated": True},
        {"approved": True},
    ],
    ids=["by_user_id", "by_name", "at", "validated", "approved"],
)
def test_a_forged_who_or_when_is_refused(
    engine: Engine, as_: Any, provider: StubProvider, extra: dict[str, Any]
) -> None:
    pid, proposal = _standby(as_, provider)
    before = record(engine, pid)
    subject = _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB")[0]
    refused = resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", REASON, **extra)
    assert refused.status_code == 422, refused.text[:300]
    assert record(engine, pid) == before
    # And the honest body works, recording the server's who and when.
    done = resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", REASON)
    assert done.status_code == 200, done.text[:300]
    (entry,) = _resolutions(record(engine, pid))
    assert entry["by_user_id"] != "00000000-0000-0000-0000-000000000000"
    assert not entry["at"].startswith("2001")


@pytest.mark.parametrize(
    "resolution", ["acknowledged", "clarified", "not_life_safety", "validated", "", None, 1]
)
def test_only_the_two_resolutions_exist(
    engine: Engine, as_: Any, provider: StubProvider, resolution: Any
) -> None:
    pid, proposal = _standby(as_, provider)
    subject = _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB")[0]
    refused = resolve(as_("reviewer"), pid, 1, subject, resolution, REASON)
    assert refused.status_code == 422, refused.text[:300]
    assert _resolutions(record(engine, pid)) == []
    assert (
        resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", REASON).status_code == 200
    )


@pytest.mark.parametrize("reason", ["", "too short", " " * 12, "x" * 501])
def test_a_reason_is_10_to_500_characters(
    engine: Engine, as_: Any, provider: StubProvider, reason: str
) -> None:
    pid, proposal = _standby(as_, provider)
    subject = _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB")[0]
    refused = resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", reason)
    assert refused.status_code == 422, refused.text[:300]
    assert (
        resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", "x" * 10).status_code
        == 200
    )


# --------------------------------------------------------------------------- #
# Who may resolve
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("caller", "status"),
    [
        ("engineer", 403),
        ("ingestion", 403),
        ("trial", 403),
        ("trial_id", 403),
        ("other_tenant", 404),
    ],
)
def test_who_may_not_resolve(
    engine: Engine, as_: Any, provider: StubProvider, caller: str, status: int
) -> None:
    pid, proposal = _standby(as_, provider)
    subject = _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB")[0]
    refused = resolve(as_(caller), pid, 1, subject, "excluded_from_scope", REASON)
    assert refused.status_code == status, refused.text[:300]
    assert _resolutions(record(engine, pid)) == []
    assert (
        resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", REASON).status_code == 200
    )


def test_the_proposal_creator_may_not_resolve(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    """The reviewer drafts the proposal; the engineer saves; the reviewer is refused."""
    t = STANDBY
    proposal = propose(
        as_,
        provider,
        t,
        [
            it(t, "Main board MDB", "b1", "board", name="MDB"),
            it(
                t,
                "Standby pump 5.5 kW on MDB",
                "l1",
                "load",
                board="MDB",
                description="Pump",
                kind="motor",
                power_kw=5.5,
            ),
        ],
        creator="reviewer",
    )
    decide_all(as_, proposal, {i["id"]: "accept" for i in proposal["items"]}, who="reviewer")
    mdb = board()
    mdb["loads"] = [lighting()]
    saved = _save(
        as_("reviewer"),
        request(mdb),
        {
            "intake": "ai",
            "ai": {
                "proposal_id": proposal["id"],
                "placements": [{"item_id": proposal["items"][0]["id"], "board_index": 0}],
            },
        },
    )
    assert saved.status_code == 201, saved.text[:300]
    pid = str(saved.json()["id"])
    # A second revision authored by the owner: the reviewer is now only the creator.
    revised = as_("owner").post(
        f"/design/projects/{pid}/revisions", json={"request": request(mdb), "note": "r2"}
    )
    assert revised.status_code == 201, revised.text[:300]
    subject = _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB", 2)[0]
    refused = resolve(as_("reviewer"), pid, 2, subject, "excluded_from_scope", REASON)
    assert refused.status_code == 403, refused.text[:300]
    # The owner authored revision 2: refused too.
    refused = resolve(as_("owner"), pid, 2, subject, "excluded_from_scope", REASON)
    assert refused.status_code == 403, refused.text[:300]
    assert _resolutions(record(engine, pid, 2)) == []


def test_the_revision_author_may_not_resolve(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, proposal = _standby(as_, provider)
    revised = as_("reviewer").post(
        f"/design/projects/{pid}/revisions",
        json={"request": revision_row(engine, pid, 1)["request"], "note": "r2"},
    )
    assert revised.status_code == 201, revised.text[:300]
    subject = _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB", 2)[0]
    refused = resolve(as_("reviewer"), pid, 2, subject, "excluded_from_scope", REASON)
    assert refused.status_code == 403, refused.text[:300]
    assert resolve(as_("owner"), pid, 2, subject, "excluded_from_scope", REASON).status_code == 200


# --------------------------------------------------------------------------- #
# When: latest unapproved revision only
# --------------------------------------------------------------------------- #


def test_no_resolution_after_approval_or_on_an_older_revision(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, proposal = _standby(as_, provider)
    subjects = _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB")
    for subject in subjects:
        assert (
            resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", REASON).status_code
            == 200
        )
    assert approve(as_("owner"), pid).status_code == 200
    approved = record(engine, pid)
    again = resolve(as_("reviewer"), pid, 1, subjects[0], "false_positive", REASON)
    assert again.status_code == 409, again.text[:300]
    assert record(engine, pid) == approved
    # A newer revision makes revision 1 not the latest.
    pid2, proposal2 = _standby(as_, provider)
    assert (
        as_()
        .post(
            f"/design/projects/{pid2}/revisions",
            json={"request": revision_row(engine, pid2, 1)["request"], "note": "r2"},
        )
        .status_code
        == 201
    )
    subject = _concerns(engine, pid2, proposal2, "Standby pump 5.5 kW on MDB")[0]
    stale = resolve(as_("reviewer"), pid2, 1, subject, "excluded_from_scope", REASON)
    assert stale.status_code == 409, stale.text[:300]


def test_approval_racing_the_last_resolution_is_serialised(
    engine: Engine, as_: Any, provider: StubProvider, race: Any, app: FastAPI
) -> None:
    """Approval queued before the last resolution: refused; after it: binds the full record."""
    for order in (("approve", "resolve"), ("resolve", "approve")):
        pid, proposal = _standby(as_, provider)
        subjects = _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB")
        for subject in subjects[:-1]:
            assert (
                resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", REASON).status_code
                == 200
            )
        calls: dict[str, Any] = {
            "approve": lambda c, p=pid: c.post(
                f"/design/projects/{p}/revisions/1/approval",
                json={"scope_confirmed": True},
                headers={"x-test-user": "owner"},
            ),
            "resolve": lambda c, p=pid, s=subjects[-1]: c.post(
                f"/design/projects/{p}/revisions/1/requirements/safety-resolutions",
                json={**s, "resolution": "excluded_from_scope", "reason": REASON},
                headers={"x-test-user": "reviewer"},
            ),
        }
        race.results.clear()
        race.threads.clear()
        with _project_held(engine, pid):
            for name in order:
                race.queue(name, calls[name])
        results = race.finish()
        row = revision_row(engine, pid, 1)
        if order[0] == "approve":
            assert code(results["approve"]) == "approval_blocked_by_safety"
            assert results["resolve"].status_code == 200
            assert row["approved_at"] is None
        else:
            assert results["resolve"].status_code == 200
            assert results["approve"].status_code == 200
            assert row["approved_requirements_sha256"] == snapshot.sha256_of(row["requirements"])
            assert len(_resolutions(row["requirements"])) == len(subjects)


# --------------------------------------------------------------------------- #
# The scoped approval
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [{}, {"scope_confirmed": False}, {"scope_confirmed": "true"}, {"scope_confirmed": 1}, None],
    ids=["missing", "false", "string", "one", "null"],
)
def test_approval_needs_the_literal_true(
    engine: Engine, as_: Any, provider: StubProvider, body: Any
) -> None:
    pid, proposal = _standby(as_, provider)
    for subject in _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB"):
        assert (
            resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", REASON).status_code
            == 200
        )
    payload = {"scope_confirmed": None} if body is None else body
    refused = approve(as_("owner"), pid, body=payload)
    assert refused.status_code == 422, refused.text[:300]
    assert revision_row(engine, pid, 1)["approved_at"] is None
    assert approve(as_("owner"), pid).status_code == 200


def test_tampering_the_record_after_approval_voids_it(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, proposal = _standby(as_, provider)
    for subject in _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB"):
        assert (
            resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", REASON).status_code
            == 200
        )
    assert approve(as_("owner"), pid).status_code == 200
    tampered = copy.deepcopy(record(engine, pid))
    tampered["ai"]["resolutions"][0]["reason"] = "Engineer validated: fire pump designed."
    update_row(
        engine, pid, 1, requirements=tampered, requirements_sha256=snapshot.sha256_of(tampered)
    )
    opened = as_("owner").get(f"/design/projects/{pid}").json()
    assert opened["approval_current"] is False
    text = pdf_text(as_("owner"), pid)
    assert "REVIEW APPROVED" not in text
    # The draft still prints the record as it now is, edited reason included:
    # a DB edit is visible only as the lost approval (approval_current False).
    assert_never_executable(as_("owner"), pid)


def test_dropping_the_concern_from_the_record_does_not_unblock_approval(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _fire(as_, provider)
    tampered = copy.deepcopy(record(engine, pid))
    tampered["open_items"] = [o for o in tampered["open_items"] if not o["code"].startswith("ai_")]
    update_row(
        engine, pid, 1, requirements=tampered, requirements_sha256=snapshot.sha256_of(tampered)
    )
    refused = approve(as_("reviewer"), pid)
    assert refused.status_code == 409, refused.text[:300]
    assert code(refused) == "approval_blocked_by_safety"


# --------------------------------------------------------------------------- #
# Dodges
# --------------------------------------------------------------------------- #


def test_a_generic_acknowledgement_cannot_answer_a_safety_concern(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, proposal = _fire(as_, provider)
    rec = record(engine, pid)
    pump = by_quote(proposal, "Fire pump FP-1 15 kW on MDB")["id"]
    for target in [pump, *mention_ids(rec)]:
        for who in ("reviewer", "owner"):
            done = as_(who).post(
                f"/design/projects/{pid}/revisions/1/questions/{target}",
                json={"action": "acknowledge"},
            )
            assert done.status_code in (404, 409, 422), (target, done.text[:300])
    # Acknowledge every M6a question there is: the safety concerns stay open.
    for q in rec.get("questions", []):
        if q.get("state") == "open":
            as_("reviewer").post(
                f"/design/projects/{pid}/revisions/1/questions/{q['id']}",
                json={"action": "acknowledge"},
            )
    latest = record(engine, pid, max(1, len(_revisions(engine, pid))))
    assert "ai_life_safety_mentioned" in {o["code"] for o in latest["open_items"]}
    assert pump in unsupported_ids(latest)


def _revisions(engine: Engine, pid: str) -> list[int]:
    from sqlalchemy import text

    with engine.connect() as conn:
        return [
            int(r[0])
            for r in conn.execute(
                text(
                    "SELECT r.number FROM design_project_revisions r "
                    "WHERE r.project_id = CAST(:p AS uuid) ORDER BY r.number"
                ),
                {"p": pid},
            )
        ]


def test_an_acknowledged_safety_question_still_blocks_approval(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, _ = _fire(as_, provider)
    for q in record(engine, pid).get("questions", []):
        as_("reviewer").post(
            f"/design/projects/{pid}/revisions/1/questions/{q['id']}",
            json={"action": "acknowledge"},
        )
    n = _revisions(engine, pid)[-1]
    refused = approve(as_("owner"), pid, n)
    assert code(refused) == "approval_blocked_by_safety", refused.text[:300]


@pytest.mark.parametrize("how", ["revise", "resave"])
def test_re_saving_carries_the_concerns(
    engine: Engine, as_: Any, provider: StubProvider, how: str
) -> None:
    pid, _ = _fire(as_, provider)
    first = record(engine, pid)
    if how == "revise":
        done = as_("reviewer").post(
            f"/design/projects/{pid}/revisions",
            json={"request": revision_row(engine, pid, 1)["request"], "note": "r2"},
        )
    else:
        done = as_("reviewer").post(f"/design/projects/{pid}/revisions/1/resave")
    assert done.status_code == 201, done.text[:300]
    second = record(engine, pid, 2)
    assert mention_ids(second) == mention_ids(first)
    assert unsupported_ids(second) == unsupported_ids(first)
    assert_never_executable(as_("reviewer"), pid, 2)


@pytest.mark.parametrize("how", ["revise", "resave"])
def test_re_saving_does_not_dodge_the_block(
    engine: Engine, as_: Any, provider: StubProvider, how: str
) -> None:
    pid, _ = _fire(as_, provider)
    if how == "revise":
        as_("reviewer").post(
            f"/design/projects/{pid}/revisions",
            json={"request": revision_row(engine, pid, 1)["request"], "note": "r2"},
        )
    else:
        as_("reviewer").post(f"/design/projects/{pid}/revisions/1/resave")
    refused = approve(as_("owner"), pid, 2)
    assert code(refused) == "approval_blocked_by_safety", refused.text[:300]


def test_a_new_proposal_omitting_the_fire_pump_does_not_clear_fp1(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    """Rev 1 flags the fire pump; rev 2 applies a fresh proposal without it, FP-1 still loaded."""
    pid, _ = _fire(as_, provider)
    t = "Main board MDB, fault level 10 kA. Office lighting 1 kW on MDB."
    fresh = propose(
        as_,
        provider,
        t,
        [
            it(t, "Main board MDB", "b1", "board", name="MDB"),
            it(
                t,
                "Office lighting 1 kW on MDB",
                "l1",
                "load",
                board="MDB",
                description="Office lighting",
                kind="lighting",
                power_kw=1,
            ),
        ],
    )
    assert fresh["safety_mentions"] == []
    ids = [i["id"] for i in fresh["items"]]
    decide_all(as_, fresh, dict.fromkeys(ids, "accept"))
    mdb = board()
    mdb["loads"] = [lighting(), motor("FP-1", "15")]
    revised = as_().post(
        f"/design/projects/{pid}/revisions",
        json={
            "request": request(mdb),
            "note": "r2 without the pump in the text",
            "requirements_input": {
                "intake": "ai",
                "ai": {
                    "proposal_id": fresh["id"],
                    "placements": [
                        {"item_id": ids[0], "board_index": 0},
                        {"item_id": ids[1], "board_index": 0, "load_index": 0},
                    ],
                },
            },
        },
    )
    assert revised.status_code == 201, revised.text[:300]
    refused = approve(as_("reviewer"), pid, 2)
    assert code(refused) == "approval_blocked_by_safety", refused.text[:300]
    assert_never_executable(as_("reviewer"), pid, 2)


# --------------------------------------------------------------------------- #
# A test run must own its disposable database
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("state", ["foreign_marker", "data_no_marker", "other_comment"])
def test_a_run_on_a_disposable_database_it_does_not_own_is_refused_before_collection(
    engine: Engine, state: str
) -> None:
    """Another owner's marker, data with no marker, or a foreign comment: refused, untouched."""
    import os
    import subprocess
    import sys

    from sqlalchemy import create_engine, text

    name = "panelpilot_test_m6d_rev_unowned"
    admin = create_engine(
        engine.url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {name}"))
        if state == "foreign_marker":
            conn.execute(
                text(
                    f"COMMENT ON DATABASE {name} IS 'panelpilot-test-owner:wt-someoneelse path=/x'"
                )
            )
        elif state == "other_comment":
            conn.execute(text(f"COMMENT ON DATABASE {name} IS 'keep me'"))
    url = engine.url.set(database=name).render_as_string(hide_password=False)
    target = create_engine(url)
    try:
        with target.begin() as conn:
            conn.execute(text("CREATE TABLE users (id int)"))
            if state == "data_no_marker":
                conn.execute(text("INSERT INTO users VALUES (1)"))
        env = {**os.environ, "TEST_DATABASE_URL": url}
        env.pop("PANELPILOT_TEST_DB_OWNER", None)
        ran = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--co",
                "app/tests/domain/test_m6a_requirements_db.py",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert ran.returncode != 0, ran.stdout[-400:]
        assert "owner" in (ran.stdout + ran.stderr).lower()
        with admin.connect() as conn:
            comment: str = conn.execute(
                text(
                    "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
                    "WHERE datname = :n"
                ),
                {"n": name},
            ).scalar_one()
        expected = {"foreign_marker": "wt-someoneelse", "other_comment": "keep me"}
        assert (comment or "") == "" if state == "data_no_marker" else expected[state] in comment
        with target.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM users")).scalar_one() == (
                1 if state == "data_no_marker" else 0
            )
    finally:
        target.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


def test_a_manual_project_with_a_fire_pump_load_is_not_approved(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    mdb = board()
    mdb["loads"] = [lighting(), motor("Fire pump FP-1", "15")]
    saved = as_().post(
        "/design/projects", json={"name": "Manual", "request": request(mdb), "note": "r1"}
    )
    assert saved.status_code == 201, saved.text[:300]
    refused = approve(as_("reviewer"), str(saved.json()["id"]))
    assert refused.status_code == 409, refused.text[:300]


# --------------------------------------------------------------------------- #
# Integrated-head probes (5133b60)
# --------------------------------------------------------------------------- #


def _road_mention_project(as_: Any, provider: StubProvider, engine: Engine) -> tuple[str, str]:
    """A possibly-safety mention only ("Fire Station Road"), rev 2 authored by the reviewer."""
    from app.tests.domain.test_m6d_review_journeys_db import _road

    pid, _ = _road(as_, provider)
    revised = as_("reviewer").post(
        f"/design/projects/{pid}/revisions",
        json={"request": revision_row(engine, pid, 1)["request"], "note": "r2"},
    )
    assert revised.status_code == 201, revised.text[:300]
    (mid,) = mention_ids(record(engine, pid, 2))
    return pid, mid


def test_the_revision_author_may_not_use_the_new_route(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, mid = _road_mention_project(as_, provider, engine)
    refused = resolve(as_("reviewer"), pid, 2, {"mention_id": mid}, "false_positive", REASON)
    assert refused.status_code == 403, refused.text[:300]


def test_the_revision_author_may_not_use_the_deprecated_route_either(
    engine: Engine, as_: Any, provider: StubProvider
) -> None:
    pid, mid = _road_mention_project(as_, provider, engine)
    done = as_("reviewer").post(
        f"/design/projects/{pid}/revisions/2/requirements/safety-clarifications",
        json={"mention_id": mid, "statement": "not_life_safety", "reason": "A street name."},
    )
    approved = approve(as_("owner"), pid, 2)
    # Observed at 5133b60: 200, then approval 200.
    assert done.status_code == 403 or approved.status_code == 409, (
        done.text[:200],
        approved.text[:200],
    )


@pytest.mark.parametrize("intake", ["manual", "guided"])
def test_re_saving_with_another_intake_does_not_dodge_the_block(
    engine: Engine, as_: Any, provider: StubProvider, intake: str
) -> None:
    pid, _ = _fire(as_, provider, pump_in_request=True)
    revised = as_().post(
        f"/design/projects/{pid}/revisions",
        json={
            "request": revision_row(engine, pid, 1)["request"],
            "note": "r2",
            "requirements_input": {"intake": intake},
        },
    )
    if revised.status_code != 201:
        assert revised.status_code in (409, 422), revised.text[:300]
        return
    refused = approve(as_("reviewer"), pid, 2)
    assert code(refused) == "approval_blocked_by_safety", refused.text[:300]


@pytest.mark.parametrize(
    "fmt",
    [
        "pdf",
        "calculations_pdf",
        "quotation_pdf",
        "json",
    ],
)
def test_every_review_export_states_the_scope_and_the_exclusions(
    engine: Engine, as_: Any, provider: StubProvider, fmt: str
) -> None:
    import json as _json

    from app.tests.api.v1.routes.test_export_integrity import PRICING, text_of

    pid, proposal = _standby(as_, provider)
    for subject in _concerns(engine, pid, proposal, "Standby pump 5.5 kW on MDB"):
        assert (
            resolve(as_("reviewer"), pid, 1, subject, "excluded_from_scope", REASON).status_code
            == 200
        )
    for approved in (False, True):
        if approved:
            assert approve(as_("owner"), pid).status_code == 200
        body: dict[str, Any] = {"format": fmt}
        if fmt.startswith("quotation"):
            body["pricing"] = PRICING
        done = as_("owner").post(f"/design/projects/{pid}/revisions/1/export", json=body)
        assert done.status_code == 200, (fmt, done.text[:300])
        if fmt == "json":
            block = _json.loads(done.content)["approval"]
            assert block["engineer_validated"] is False
            assert block["scope_confirmed"] is approved
            assert block["excluded_from_scope"], block
            continue
        words = text_of(fmt, done.content)
        assert "[SCOPE] It does not cover" in words, fmt
        assert "[EXCLUDED FROM SCOPE]" in words, fmt
        assert ("approver confirmed this scope" in words) is approved, fmt
