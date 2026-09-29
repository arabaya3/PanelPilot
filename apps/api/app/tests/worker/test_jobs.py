"""Tests for `app/worker/jobs.py`.

Mirrors the module 1:1 — if you add a job there, add its test here.

Job handlers are thin by contract, so what is worth testing is the contract
itself: the exit code a scheduler reads, and the identity unattended work acts
as. The second one carries the weight. `system_actor` is the principal every
scheduled crawl stages content under, and if it ever held the reviewer role a
nightly job could approve the content it had just fetched — which is the
entire human gate ADR 0001 exists to impose, removed by a one-word change that
nothing else would notice.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import NotFoundError
from app.models.schemas.auth import Role
from app.models.schemas.ingestion import CrawlJobResponse, CrawlJobStatus
from app.worker import jobs

# --- the system actor, which is a security boundary --------------------------


def test_the_system_actor_holds_the_ingestion_role() -> None:
    assert jobs.system_actor().has_role(Role.INGESTION)


def test_the_system_actor_does_not_hold_the_reviewer_role() -> None:
    """The four-eyes rule, at the identity level.

    A scheduled job that could review would let the same principal both stage
    and promote, which is the one thing the staging boundary is for.
    """
    assert not jobs.system_actor().has_role(Role.REVIEWER)


def test_the_system_actor_holds_no_role_beyond_ingestion() -> None:
    """Pinned as an exact set rather than a pair of negatives.

    A future role added to this actor "just to make something work" would
    otherwise pass both checks above.
    """
    assert jobs.system_actor().roles == frozenset({Role.INGESTION})


def test_the_system_actor_is_stable_across_calls() -> None:
    """Staged content names an ingester of record.

    Staged content names an ingester of record. An id that changed per run
    would make the audit trail unjoinable and could let a later run promote an
    earlier run's content.
    """
    assert jobs.system_actor().id == jobs.system_actor().id


# --- the job registry ---------------------------------------------------------


def test_a_registered_job_is_found() -> None:
    assert jobs.get_job("crawl").name == "crawl"


def test_an_unknown_job_is_refused() -> None:
    with pytest.raises(NotFoundError):
        jobs.get_job("no-such-job")


def test_the_error_names_the_jobs_that_do_exist() -> None:
    """A typo at 3am should be self-correcting rather than a lookup."""
    with pytest.raises(NotFoundError, match="crawl"):
        jobs.get_job("crwal")


# --- run_crawl's exit codes, which are what a scheduler reads ----------------


def _patch_crawl(monkeypatch: pytest.MonkeyPatch, status: CrawlJobStatus) -> dict[str, Any]:
    """Replace the domain call and the session, capturing what was passed."""
    seen: dict[str, Any] = {}

    def fake_create(*, session: Any, user: Any, request: Any) -> CrawlJobResponse:
        seen["user"] = user
        seen["request"] = request
        return CrawlJobResponse(id="job-1", status=CrawlJobStatus.QUEUED)

    def fake_run(*, session: Any, job_id: str) -> CrawlJobResponse:
        # The command runs the job it just queued, not whatever is next.
        seen["ran"] = job_id
        return CrawlJobResponse(id=job_id, status=status)

    from app.domain import ingestion as ingestion_domain

    monkeypatch.setattr(ingestion_domain, "create_crawl_job", fake_create)
    monkeypatch.setattr(ingestion_domain, "run_crawl_job", fake_run)

    # A stand-in with `close`, because the handler wraps the session in
    # `closing()` -- which is the behaviour under test: a job that leaked a
    # connection per run would exhaust the pool overnight.
    class _Session:
        closed = False

        def __init__(self) -> None:
            # Where the job declares it spans tenants (ADR 0003).
            self.info: dict[str, object] = {}

        def commit(self) -> None:
            # The queued row must be committed before it is run.
            seen["committed_before_run"] = "ran" not in seen

        def close(self) -> None:
            _Session.closed = True

    monkeypatch.setattr("app.core.db.get_session", lambda: iter([_Session()]))
    seen["session_class"] = _Session
    return seen


def test_the_crawl_command_runs_the_job_it_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_crawl(monkeypatch, CrawlJobStatus.SUCCEEDED)

    jobs.run_crawl(["abb", "https://library.abb.com/x"])

    assert seen["ran"] == "job-1"
    assert seen["committed_before_run"]


def test_a_successful_crawl_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_crawl(monkeypatch, CrawlJobStatus.SUCCEEDED)

    assert jobs.run_crawl(["abb", "https://library.abb.com/x"]) == 0


def test_a_failed_crawl_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recorded failure is still a failure.

    Exiting 0 would leave the scheduler silent about a source that has stopped
    returning documents — the exact condition BE-006's staleness alerting
    exists to surface.
    """
    _patch_crawl(monkeypatch, CrawlJobStatus.FAILED)

    assert jobs.run_crawl(["abb", "https://library.abb.com/x"]) != 0


def test_missing_arguments_are_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not a crash, and not a success."""
    assert jobs.run_crawl([]) == 2
    assert jobs.run_crawl(["abb"]) == 2


def test_every_seed_url_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """A crawl told to start from three entry points must not silently use.

    A crawl told to start from three entry points must not silently use
    one.
    """
    seen = _patch_crawl(monkeypatch, CrawlJobStatus.SUCCEEDED)
    jobs.run_crawl(["abb", "https://a.example/1", "https://a.example/2"])

    assert seen["request"].seed_urls == ["https://a.example/1", "https://a.example/2"]


def test_the_crawl_runs_as_the_system_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not as an anonymous or empty principal.

    Not as an anonymous or empty principal: staged content has to name who
    brought it in.
    """
    seen = _patch_crawl(monkeypatch, CrawlJobStatus.SUCCEEDED)
    jobs.run_crawl(["abb", "https://a.example/1"])

    assert seen["user"].id == jobs.system_actor().id
    assert not seen["user"].has_role(Role.REVIEWER)


def test_the_session_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job that leaked a connection per run would exhaust the pool."""
    seen = _patch_crawl(monkeypatch, CrawlJobStatus.SUCCEEDED)
    jobs.run_crawl(["abb", "https://a.example/1"])

    assert seen["session_class"].closed is True


# --- grant-role / revoke-role --------------------------------------------------


def _patch_roles(monkeypatch: pytest.MonkeyPatch, *, changed: bool = True) -> dict[str, Any]:
    """Replace the role domain calls and the session, capturing what was passed."""
    seen: dict[str, Any] = {"calls": []}

    def _fake(verb: str) -> Any:
        def change(*, session: Any, email: str, role: Any) -> bool:
            # The exemption must be in force while the account is looked up.
            seen["calls"].append((verb, email, role, dict(session.info)))
            return changed

        return change

    from app.domain import roles as roles_domain

    monkeypatch.setattr(roles_domain, "grant_role", _fake("grant"))
    monkeypatch.setattr(roles_domain, "revoke_role", _fake("revoke"))

    class _Session:
        closed = False
        committed = False

        def __init__(self) -> None:
            self.info: dict[str, object] = {}

        def commit(self) -> None:
            _Session.committed = True

        def close(self) -> None:
            _Session.closed = True

    monkeypatch.setattr("app.core.db.get_session", lambda: iter([_Session()]))
    seen["session_class"] = _Session
    return seen


def test_grant_role_grants_commits_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_roles(monkeypatch)

    assert jobs.run_grant_role(["a@example.com", "reviewer"]) == 0

    verb, email, role, info = seen["calls"][0]
    assert (verb, email, role) == ("grant", "a@example.com", Role.REVIEWER)
    assert info, "the account was looked up without declaring it spans tenants"
    assert seen["session_class"].committed
    assert seen["session_class"].closed


def test_revoke_role_revokes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_roles(monkeypatch)

    assert jobs.run_revoke_role(["a@example.com", "ingestion"]) == 0
    assert seen["calls"][0][:3] == ("revoke", "a@example.com", Role.INGESTION)


@pytest.mark.parametrize(
    "args", [[], ["a@example.com"], ["a@example.com", "reviewer", "extra"], ["a@x", "owner"]]
)
def test_bad_role_arguments_are_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    """Including a role name that does not exist: nothing is touched."""
    seen = _patch_roles(monkeypatch)

    assert jobs.run_grant_role(args) == 2
    assert seen["calls"] == []


def test_the_role_jobs_are_registered() -> None:
    assert jobs.get_job("grant-role").handler is jobs.run_grant_role
    assert jobs.get_job("revoke-role").handler is jobs.run_revoke_role


# --- crawl-queue ---------------------------------------------------------------


def _patch_queue(
    monkeypatch: pytest.MonkeyPatch, result: CrawlJobResponse | None
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_next(*, session: Any) -> CrawlJobResponse | None:
        seen["info"] = dict(session.info)
        return result

    from app.domain import ingestion as ingestion_domain

    monkeypatch.setattr(ingestion_domain, "run_next_crawl_job", fake_next)

    class _Session:
        closed = False

        def __init__(self) -> None:
            self.info: dict[str, object] = {}

        def close(self) -> None:
            _Session.closed = True

    monkeypatch.setattr("app.core.db.get_session", lambda: iter([_Session()]))
    seen["session_class"] = _Session
    return seen


def test_an_empty_queue_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scheduled every few minutes, it will usually find nothing."""
    _patch_queue(monkeypatch, None)

    assert jobs.run_crawl_queue([]) == 0


def test_a_queued_crawl_that_fails_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_queue(monkeypatch, CrawlJobResponse(id="j", status=CrawlJobStatus.FAILED, error="x"))

    assert jobs.run_crawl_queue([]) == 1


def test_a_queued_crawl_runs_cross_tenant_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_queue(monkeypatch, CrawlJobResponse(id="j", status=CrawlJobStatus.SUCCEEDED))

    assert jobs.run_crawl_queue([]) == 0
    assert seen["info"], "the job ran without declaring it spans tenants"
    assert seen["session_class"].closed


# --- assign-review-batches -----------------------------------------------------


def _patch_assignment(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    from app.domain import verification_queue as queue_domain

    def fake_assign(*, session: Any) -> Any:
        seen["info"] = dict(session.info)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(queue_domain, "assign_to_reviewers", fake_assign)

    class _Session:
        committed = False

        def __init__(self) -> None:
            self.info: dict[str, object] = {}

        def commit(self) -> None:
            _Session.committed = True

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.core.db.get_session", lambda: iter([_Session()]))
    seen["session_class"] = _Session
    return seen


def test_assignment_commits_and_spans_tenants(monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid as _uuid

    seen = _patch_assignment(monkeypatch, {_uuid.uuid4(): 3, _uuid.uuid4(): 2})

    assert jobs.run_assign_review_batches([]) == 0
    assert seen["session_class"].committed
    assert seen["info"], "reviewers were looked up without declaring it spans tenants"


def test_no_reviewers_is_a_failure_the_scheduler_sees(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain.verification_queue import QueueError

    seen = _patch_assignment(monkeypatch, QueueError("nobody holds the reviewer role"))

    assert jobs.run_assign_review_batches([]) == 1
    assert not seen["session_class"].committed


# --- calibrate-relevance ------------------------------------------------------


def _eval_set(tmp_path: Any, answerable: int, off_topic: int) -> str:
    import json

    entries = [
        {
            "id": f"a{i}",
            "query": f"question {i}",
            "category": "straightforward",
            "expected_answer_summary": "An answer.",
            "required_phrases": ["answer"],
            "expected_citation": {"document_id": "doc"},
        }
        for i in range(answerable)
    ] + [
        {
            "id": f"o{i}",
            "query": "torque spec for a Corolla head bolt",
            "category": "out_of_scope",
            "expected_answer_summary": "Not in the corpus.",
        }
        for i in range(off_topic)
    ]
    path = tmp_path / "eval.json"
    path.write_text(json.dumps(entries))
    return str(path)


def _patch_search(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Answerable questions match at 0.7, off-topic ones at 0.1."""
    from app.ai.retrieval import hybrid_search
    from app.models.schemas.retrieval_config import RetrievalConfig
    from app.models.schemas.search import Citation, RetrievedPassage

    configs: list[Any] = []

    def fake_search(query: str, brand: Any, model: Any, *, config: Any) -> list[RetrievedPassage]:
        configs.append(config)
        similarity = 0.1 if "Corolla" in query else 0.7
        return [
            RetrievedPassage(
                id="p",
                text="t",
                score=1.0,
                similarity=similarity,
                citation=Citation(document_id="doc", document_title="Doc", manufacturer="ABB"),
            )
        ]

    monkeypatch.setattr(hybrid_search, "search", fake_search)
    monkeypatch.setattr(
        hybrid_search,
        "retrieval_config_from_settings",
        lambda: RetrievalConfig(min_similarity=0.9),
    )
    return configs


def test_calibration_prints_the_setting_to_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    configs = _patch_search(monkeypatch)

    assert jobs.run_calibrate_relevance([_eval_set(tmp_path, 20, 5)]) == 0

    assert "RETRIEVAL_MIN_SIMILARITY=0.7" in capsys.readouterr().out
    # Measured with no floor, or a configured one would hide what it measures.
    assert configs
    assert all(config.min_similarity is None for config in configs)


def test_calibration_without_enough_data_says_why_and_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_search(monkeypatch)

    assert jobs.run_calibrate_relevance([_eval_set(tmp_path, 3, 1)]) == 1

    assert "no recommendation" in capsys.readouterr().out


@pytest.mark.parametrize("args", [[], ["a.json", "b.json"]])
def test_calibration_needs_exactly_one_eval_set(args: list[str]) -> None:
    assert jobs.run_calibrate_relevance(args) == 2


def test_an_unreadable_eval_set_is_a_usage_error(tmp_path: Any) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('[{"id": "x"}]')

    assert jobs.run_calibrate_relevance([str(tmp_path / "missing.json")]) == 2
    assert jobs.run_calibrate_relevance([str(bad)]) == 2


# --- operator mistakes and unbuilt jobs --------------------------------------


def test_granting_to_an_unknown_email_is_a_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.domain import roles as roles_domain

    class _Session:
        def __init__(self) -> None:
            self.info: dict[str, object] = {}

        def commit(self) -> None:
            raise AssertionError("nothing to commit after a refusal")

        def close(self) -> None:
            pass

    def unknown(**_kwargs: object) -> bool:
        raise NotFoundError("no account with that email")

    monkeypatch.setattr("app.core.db.get_session", lambda: iter([_Session()]))
    monkeypatch.setattr(roles_domain, "grant_role", unknown)

    assert jobs.run_grant_role(["nobody@example.com", "reviewer"]) == 1
    assert "no account with that email" in capsys.readouterr().err


@pytest.mark.parametrize("job", ["reindex-staging", "expire-stale-sources"])
def test_an_unbuilt_job_says_so_and_exits_distinctly(
    job: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """It raised NotImplementedError: a traceback in the scheduler's log."""
    assert jobs.get_job(job).handler([]) == jobs.NOT_BUILT
    assert "not built yet" in capsys.readouterr().err
    assert jobs.get_job(job).description.startswith("(not built yet)")
