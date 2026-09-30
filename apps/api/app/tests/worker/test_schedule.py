"""Tests for `app/worker/schedule.py`, and for the crontab it reads."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.worker import jobs
from app.worker.schedule import CrontabError, main, parse_crontab, run

CRONTAB = Path(__file__).resolve().parents[3] / "worker.crontab"


def _one(line: str):  # type: ignore[no-untyped-def]
    (entry,) = parse_crontab(line)
    return entry


# --- the real schedule --------------------------------------------------------


def test_the_real_crontab_parses_and_names_only_registered_jobs() -> None:
    """A typo in a job name would be a job that silently never runs."""
    entries = parse_crontab(CRONTAB.read_text(encoding="utf-8"))

    assert [e.args[0] for e in entries] == [
        "crawl-queue",
        "assign-review-batches",
        "expire-stale-sources",
        "crawl",
        "crawl",
    ]
    for entry in entries:
        assert jobs.get_job(entry.args[0]).name == entry.args[0]


def test_queued_crawls_run_every_five_minutes() -> None:
    entry = parse_crontab(CRONTAB.read_text(encoding="utf-8"))[0]
    runs = [
        datetime(2026, 9, 30, 8, 0, tzinfo=UTC) + timedelta(minutes=m)
        for m in range(60)
        if entry.due(datetime(2026, 9, 30, 8, 0, tzinfo=UTC) + timedelta(minutes=m))
    ]
    assert [r.minute for r in runs] == list(range(0, 60, 5))


def test_every_scheduled_crawl_names_a_source_it_can_crawl_unseeded() -> None:
    """`crawl <source>` with no seed works only for a source with curated URLs."""
    from app.ingestion.known_documents import urls_for

    for entry in parse_crontab(CRONTAB.read_text(encoding="utf-8")):
        if entry.args[0] == "crawl" and len(entry.args) == 2:
            assert urls_for(entry.args[1]), f"{entry.args[1]} has no curated documents"


def test_stale_sources_are_checked_weekly_on_monday() -> None:
    entry = parse_crontab(CRONTAB.read_text(encoding="utf-8"))[2]
    week = [datetime(2026, 9, 27, 3, 30, tzinfo=UTC) + timedelta(days=d) for d in range(7)]
    # 2026-09-28 is a Monday.
    assert [d.date().isoformat() for d in week if entry.due(d)] == ["2026-09-28"]


# --- the cron subset ----------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("*/15 * * * * job", {0, 15, 30, 45}),
        ("5 * * * * job", {5}),
        ("0,30 * * * * job", {0, 30}),
        ("10-12 * * * * job", {10, 11, 12}),
    ],
)
def test_minute_fields(field: str, expected: set[int]) -> None:
    assert _one(field).minutes == expected


def test_sunday_is_both_zero_and_seven() -> None:
    assert _one("0 0 * * 7 job").weekdays == {0}


def test_day_of_month_and_weekday_follow_crons_either_rule() -> None:
    # Restricted both ways: the 1st of the month OR any Monday.
    entry = _one("0 0 1 * 1 job")
    assert entry.due(datetime(2026, 10, 1, tzinfo=UTC))  # a Thursday, the 1st
    assert entry.due(datetime(2026, 10, 5, tzinfo=UTC))  # a Monday
    assert not entry.due(datetime(2026, 10, 6, tzinfo=UTC))


def test_arguments_are_kept_with_quoting() -> None:
    assert _one("0 2 * * 0 crawl abb 'https://x/a b'").args == ("crawl", "abb", "https://x/a b")


@pytest.mark.parametrize(
    "line",
    [
        "* * * * crawl-queue",  # four fields
        "60 * * * * job",  # out of range
        "*/0 * * * * job",  # a zero step
        "5/2 * * * * job",  # a step on a number
        "a * * * * job",
    ],
)
def test_an_unreadable_line_is_an_error_naming_it(line: str) -> None:
    with pytest.raises(CrontabError, match="line 2"):
        parse_crontab(f"# comment\n{line}\n")


# --- running it ---------------------------------------------------------------


class _Process:
    def __init__(self, finished: bool) -> None:
        self.finished = finished

    def poll(self) -> int | None:
        return 0 if self.finished else None


def test_each_due_job_starts_as_its_own_process_once_a_minute() -> None:
    started: list[tuple[str, ...]] = []
    clock = [datetime(2026, 9, 30, 6, 0, 20, tzinfo=UTC)]

    def start(args: Sequence[str]) -> _Process:
        started.append(tuple(args))
        return _Process(finished=True)

    def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds)

    run(
        parse_crontab("*/5 * * * * crawl-queue\n0 6 * * * assign-review-batches\n"),
        start=start,  # type: ignore[arg-type]
        now=lambda: clock[0],
        sleep=sleep,
        ticks=6,
    )

    assert started == [
        ("crawl-queue",),
        ("assign-review-batches",),
        ("crawl-queue",),  # 06:05
    ]


def test_a_job_still_running_is_not_started_again() -> None:
    started: list[tuple[str, ...]] = []
    clock = [datetime(2026, 9, 30, 6, 0, tzinfo=UTC)]

    def start(args: Sequence[str]) -> _Process:
        started.append(tuple(args))
        return _Process(finished=False)

    run(
        parse_crontab("* * * * * crawl-queue\n"),
        start=start,  # type: ignore[arg-type]
        now=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + timedelta(seconds=s)),
        ticks=3,
    )

    assert started == [("crawl-queue",)]


def test_main_refuses_a_missing_or_broken_crontab(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([]) == 2
    assert main([str(tmp_path / "missing")]) == 2
    broken = tmp_path / "broken"
    broken.write_text("61 * * * * job\n", encoding="utf-8")
    assert main([str(broken)]) == 2
    assert "line 1" in capsys.readouterr().err
