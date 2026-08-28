"""Tests for `app/worker/main.py`.

Mirrors the module 1:1.

The entry point's whole job is turning a command line into an exit code, and
the exit code is the only thing a scheduler ever reads. The case worth pinning
is the quiet one: a worker invoked with a missing or misspelled job name must
not look like a run that succeeded, because a cron entry that silently does
nothing every night is indistinguishable from one that works until somebody
checks the corpus.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.core.config import Environment, Settings
from app.worker.main import main


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the entry point a valid configuration.

    `main` loads settings before it looks at argv and exits the process if they
    are absent, so without this every test here would fail on configuration
    rather than on the behaviour it is checking.
    """
    monkeypatch.setattr(
        "app.worker.main.load_settings_or_exit",
        lambda: Settings(
            environment=Environment.DEV,
            database_url="postgresql+psycopg://test:test@localhost:5432/test",
            opensearch_url="http://localhost:9200",
            anthropic_api_key="test-key",
            jwt_secret="test-secret",
            redis_url="redis://localhost:6379/0",
        ),
    )


def test_listing_jobs_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list"]) == 0
    assert "crawl" in capsys.readouterr().out


def test_no_arguments_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """Prints the jobs, but exits non-zero.

    Helpful and honest at once: the operator sees what they could have run, and
    the scheduler still sees a failure rather than a success.
    """
    assert main([]) == 2


def test_an_unknown_job_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["no-such-job"]) == 2


def test_an_unknown_job_names_what_exists(capsys: pytest.CaptureFixture[str]) -> None:
    main(["crwal"])

    assert "crawl" in capsys.readouterr().err


def _record(seen: list[list[str]]) -> Callable[[list[str]], int]:
    """A handler that records its arguments and succeeds."""

    def handler(args: list[str]) -> int:
        seen.append(args)
        return 0

    return handler


def test_the_job_receives_its_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything after the job name belongs to the job."""
    seen: list[list[str]] = []
    from app.worker import jobs as jobs_module
    from app.worker.jobs import JobSpec

    monkeypatch.setitem(
        jobs_module.REGISTRY,
        "crawl",
        JobSpec("crawl", "test", _record(seen)),
    )

    assert main(["crawl", "abb", "https://a.example/1"]) == 0
    assert seen == [["abb", "https://a.example/1"]]


def test_the_jobs_exit_code_is_the_processs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not swallowed and not remapped: a failed crawl must fail the process."""
    from app.worker import jobs as jobs_module
    from app.worker.jobs import JobSpec

    monkeypatch.setitem(jobs_module.REGISTRY, "crawl", JobSpec("crawl", "test", lambda _args: 1))

    assert main(["crawl"]) == 1
