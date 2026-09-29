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
        return CrawlJobResponse(id="job-1", status=status)

    from app.domain import ingestion as ingestion_domain

    monkeypatch.setattr(ingestion_domain, "create_crawl_job", fake_create)

    # A stand-in with `close`, because the handler wraps the session in
    # `closing()` -- which is the behaviour under test: a job that leaked a
    # connection per run would exhaust the pool overnight.
    class _Session:
        closed = False

        def close(self) -> None:
            _Session.closed = True

    monkeypatch.setattr("app.core.db.get_session", lambda: iter([_Session()]))
    seen["session_class"] = _Session
    return seen


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


def test_a_source_alone_crawls_its_curated_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """No seed URL: the domain falls back to the source's known document list."""
    seen = _patch_crawl(monkeypatch, CrawlJobStatus.SUCCEEDED)

    assert jobs.run_crawl(["abb"]) == 0
    assert seen["request"].seed_urls == []


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


# --- operator commands: roles and assignment ----------------------------------


class _RecordingSession:
    """A session stand-in recording commits and closure."""

    def __init__(self) -> None:
        self.committed = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def _patch_session(monkeypatch: pytest.MonkeyPatch) -> _RecordingSession:
    session = _RecordingSession()
    monkeypatch.setattr("app.core.db.get_session", lambda: iter([session]))
    return session


def test_grant_role_grants_commits_and_closes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.domain import auth as auth_domain

    session = _patch_session(monkeypatch)
    calls: list[tuple[str, Role]] = []

    def fake_grant(*, session: Any, email: str, role: Role) -> frozenset[Role]:
        calls.append((email, role))
        return frozenset({Role.ENGINEER, role})

    monkeypatch.setattr(auth_domain, "grant_role", fake_grant)

    code = jobs.run_grant_role(["lead@example.com", "reviewer"])

    assert code == 0
    assert calls == [("lead@example.com", Role.REVIEWER)]
    assert session.committed
    assert session.closed
    assert "engineer, reviewer" in capsys.readouterr().out


def test_revoke_role_revokes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain import auth as auth_domain

    _patch_session(monkeypatch)
    calls: list[Role] = []

    def fake_revoke(*, session: Any, email: str, role: Role) -> frozenset[Role]:
        calls.append(role)
        return frozenset({Role.ENGINEER})

    monkeypatch.setattr(auth_domain, "revoke_role", fake_revoke)

    assert jobs.run_revoke_role(["lead@example.com", "reviewer"]) == 0
    assert calls == [Role.REVIEWER]


@pytest.mark.parametrize("args", [[], ["only@example.com"], ["a@b.c", "reviewer", "extra"]])
def test_a_role_command_with_the_wrong_arguments_is_a_usage_error(args: list[str]) -> None:
    assert jobs.run_grant_role(args) == 2


def test_an_unknown_role_is_a_usage_error_naming_the_real_ones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert jobs.run_grant_role(["a@b.c", "superuser"]) == 2
    assert "reviewer" in capsys.readouterr().err


def test_granting_to_a_missing_account_fails_without_committing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain import auth as auth_domain

    session = _patch_session(monkeypatch)

    def fake_grant(**_: Any) -> frozenset[Role]:
        raise NotFoundError("no account with email 'x@y.z'")

    monkeypatch.setattr(auth_domain, "grant_role", fake_grant)

    assert jobs.run_grant_role(["x@y.z", "reviewer"]) == 1
    assert not session.committed
    assert session.closed


def test_assignment_commits_and_reports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uuid

    from app.domain import verification_queue as queue_domain

    session = _patch_session(monkeypatch)
    monkeypatch.setattr(
        queue_domain,
        "assign_to_reviewers",
        lambda **_: {uuid.UUID(int=1): 3, uuid.UUID(int=2): 2},
    )

    assert jobs.run_assign_verification([]) == 0
    assert session.committed
    assert "assigned 5 items across 2 reviewers" in capsys.readouterr().out


def test_assignment_with_no_reviewers_says_how_to_fix_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.domain import verification_queue as queue_domain

    session = _patch_session(monkeypatch)

    def fake_assign(**_: Any) -> dict[Any, int]:
        raise queue_domain.QueueError("cannot assign a batch with no verifiers")

    monkeypatch.setattr(queue_domain, "assign_to_reviewers", fake_assign)

    assert jobs.run_assign_verification([]) == 1
    assert not session.committed
    assert "grant-role" in capsys.readouterr().err


# --- corpus maintenance ---------------------------------------------------------


def test_reindex_passes_the_source_and_reports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.domain import staging_maintenance

    _patch_session(monkeypatch)
    seen: list[str | None] = []

    def fake_reembed(*, session: Any, source_id: str | None) -> int:
        seen.append(source_id)
        return 7

    monkeypatch.setattr(staging_maintenance, "reembed_staging", fake_reembed)

    assert jobs.run_reindex_staging(["abb"]) == 0
    assert seen == ["abb"]
    assert "re-embedded 7" in capsys.readouterr().out


def test_reindex_without_a_source_covers_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain import staging_maintenance

    _patch_session(monkeypatch)
    seen: list[str | None] = []

    def fake_reembed(*, session: Any, source_id: str | None) -> int:
        seen.append(source_id)
        return 0

    monkeypatch.setattr(staging_maintenance, "reembed_staging", fake_reembed)

    assert jobs.run_reindex_staging([]) == 0
    assert seen == [None]


def test_a_provider_failure_exits_non_zero_and_says_rerun_is_safe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.ai.retrieval.embedding import EmbeddingError
    from app.domain import staging_maintenance

    _patch_session(monkeypatch)

    def fake_reembed(**_: Any) -> int:
        raise EmbeddingError("provider down")

    monkeypatch.setattr(staging_maintenance, "reembed_staging", fake_reembed)

    assert jobs.run_reindex_staging([]) == 1
    assert "idempotent" in capsys.readouterr().err


def test_reindex_with_too_many_arguments_is_a_usage_error() -> None:
    assert jobs.run_reindex_staging(["a", "b"]) == 2


def test_a_current_production_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain import staging_maintenance

    _patch_session(monkeypatch)
    monkeypatch.setattr(staging_maintenance, "find_superseded", lambda **_: [])

    assert jobs.run_expire_stale_sources([]) == 0


def test_stale_content_has_its_own_exit_code_and_is_listed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Distinct from a failed job, so a scheduler can page a reviewer instead."""
    from app.domain import staging_maintenance

    _patch_session(monkeypatch)
    monkeypatch.setattr(
        staging_maintenance,
        "find_superseded",
        lambda **_: [
            staging_maintenance.SupersededChunk(
                chunk_id="doc#0001-a",
                source_url="https://m.invalid/x.pdf",
                live_hash="1",
                latest_hash="2",
            )
        ],
    )

    assert jobs.run_expire_stale_sources([]) == jobs.EXIT_STALE_CONTENT_FOUND
    assert "doc#0001-a" in capsys.readouterr().out
