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
