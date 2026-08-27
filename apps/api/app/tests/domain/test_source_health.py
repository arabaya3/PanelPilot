"""Tests for source health monitoring.

The acceptance criterion is a pair, and the pair is the point: breaking a
crawler alerts within one missed cycle, and a healthy-but-unchanged source
alerts never. Either half alone is easy and useless — a monitor that alerts on
everything and one that alerts on nothing are equally ignorable.

The second half is the one that decides whether anyone reads this. Most days a
manufacturer publishes nothing, so a monitor keying on "did the crawl find
anything" would fire constantly and be muted within a week, after which its
silence means nothing either.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import DateTime, TypeDecorator, create_engine
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Session, sessionmaker

from app.domain.source_health import (
    FAILURE_THRESHOLD,
    STALE_AFTER,
    check_sources,
    record_health,
)
from app.models.tables.base import Base
from app.models.tables.escalation import SourceHealthRow

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class _UTCDateTime(TypeDecorator[datetime]):
    """A ``DateTime`` that reads back UTC-aware, the way Postgres does.

    SQLite has no timezone-aware storage. It accepts an aware datetime,
    silently drops the offset, and hands back a naive one, so a comparison
    like ``moment - row.last_success_at`` raises ``TypeError``. Postgres —
    what actually runs in production — honours ``DateTime(timezone=True)``
    and returns an aware value.

    Left uncorrected, the stand-in database is not a simplification of the
    real one but a differently-typed one, and that gap has to be paid for
    somewhere: either by pushing timezone defensiveness into the domain layer
    to satisfy a database the product never uses, or by writing these tests
    against naive datetimes and giving up the ability to catch a genuine
    comparison bug. Correcting the stand-in is much the cheaper of the two.
    """

    impl = DateTime
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Reattach UTC to a value SQLite stored without an offset.

        Args:
            value: The datetime as the driver returned it.
            dialect: The active dialect. Unused.

        Returns:
            The same instant, timezone-aware, or ``None``.
        """
        del dialect
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)


@pytest.fixture(name="session")
def _session() -> Iterator[Session]:
    """An in-memory database holding the real ``source_health`` table.

    SQLite rather than a stub: `record_health` upserts on a unique column and
    reads back what it wrote, and a fake session that appends to a list would
    exercise neither.

    Only this one table is created, rather than the whole of
    ``Base.metadata``. The metadata carries tables whose foreign keys point at
    models this module never imports, and ``create_all`` resolves every key
    across the whole collection — so creating everything fails on a table
    irrelevant to source health.
    """
    table = Base.metadata.tables[SourceHealthRow.__tablename__]
    for column in table.columns:
        if isinstance(column.type, DateTime):
            column.type = _UTCDateTime(timezone=True)

    engine = create_engine("sqlite://")
    table.create(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


# --- recording every run -----------------------------------------------------


def test_a_successful_run_is_recorded(session: Session) -> None:
    row = record_health(
        session=session, source_id="abb", success=True, documents_indexed=3, now=NOW
    )

    assert row.last_success_at == NOW
    assert row.last_checked_at == NOW
    assert row.consecutive_failures == 0
    assert row.last_error is None


def test_a_failed_run_is_recorded_with_its_error(session: Session) -> None:
    # Recorded, not raised. A run that records nothing is indistinguishable
    # from a run that never happened — which is the failure this module
    # exists to prevent.
    row = record_health(
        session=session, source_id="abb", success=False, error="503 from portal", now=NOW
    )

    assert row.last_checked_at == NOW
    assert row.last_success_at is None
    assert row.consecutive_failures == 1
    assert row.last_error == "503 from portal"


def test_a_success_after_failures_clears_the_count(session: Session) -> None:
    # Reset rather than decrement: a source that works now is healthy, and
    # carrying the count forward would keep an alert alive after the problem
    # is gone.
    for _ in range(3):
        record_health(session=session, source_id="abb", success=False, error="boom", now=NOW)
    row = record_health(session=session, source_id="abb", success=True, now=NOW)

    assert row.consecutive_failures == 0
    assert row.last_error is None


def test_consecutive_failures_accumulate(session: Session) -> None:
    for _ in range(3):
        row = record_health(session=session, source_id="abb", success=False, error="boom", now=NOW)

    assert row.consecutive_failures == 3


def test_each_source_is_tracked_separately(session: Session) -> None:
    record_health(session=session, source_id="abb", success=False, error="boom", now=NOW)
    record_health(session=session, source_id="siemens", success=True, now=NOW)

    alerts = check_sources(session=session, now=NOW)
    assert [a.source_id for a in alerts] == []  # one failure is below threshold


# --- the acceptance criterion ------------------------------------------------


def test_a_broken_crawler_alerts_once_it_crosses_the_threshold(session: Session) -> None:
    # "Deliberately breaking one crawler job triggers an alert." Simulated the
    # way it actually happens: the crawl raises, the caller records the
    # failure.
    for _ in range(FAILURE_THRESHOLD):
        record_health(
            session=session,
            source_id="abb",
            success=False,
            error="AttributeError: no listing table",
            now=NOW,
        )

    alerts = check_sources(session=session, now=NOW)

    assert len(alerts) == 1
    assert alerts[0].source_id == "abb"
    assert alerts[0].reason == "failing"
    assert "no listing table" in alerts[0].detail


def test_a_healthy_but_unchanged_source_never_alerts(session: Session) -> None:
    # The other half, and the one that decides whether anyone reads this
    # monitor. A manufacturer publishing nothing new is the normal case on
    # most days; alerting on it would train the team to mute the alert, after
    # which its silence means nothing either.
    record_health(session=session, source_id="abb", success=True, documents_indexed=0, now=NOW)

    assert check_sources(session=session, now=NOW) == []


def test_a_single_failure_does_not_alert(session: Session) -> None:
    # A portal returning 503 for ten minutes is an ordinary event. Alerting on
    # the first one is the alert fatigue the task names explicitly.
    record_health(session=session, source_id="abb", success=False, error="503", now=NOW)

    assert check_sources(session=session, now=NOW) == []


def test_a_transient_failure_that_recovers_never_alerts(session: Session) -> None:
    # The full retry story: fail, then succeed on the next cycle. Nothing
    # should ever have paged.
    record_health(session=session, source_id="abb", success=False, error="503", now=NOW)
    assert check_sources(session=session, now=NOW) == []

    record_health(session=session, source_id="abb", success=True, now=NOW + timedelta(hours=1))
    assert check_sources(session=session, now=NOW + timedelta(hours=1)) == []


# --- staleness, which is a different failure ---------------------------------


def test_a_source_whose_last_success_is_old_alerts(session: Session) -> None:
    # The case a failure count cannot catch: the scheduled job stopped running
    # at all, so nothing is failing because nothing is happening.
    record_health(session=session, source_id="abb", success=True, now=NOW)

    later = NOW + STALE_AFTER + timedelta(hours=1)
    alerts = check_sources(session=session, now=later)

    assert len(alerts) == 1
    assert alerts[0].reason == "stale"


def test_a_recent_success_is_not_stale(session: Session) -> None:
    record_health(session=session, source_id="abb", success=True, now=NOW)

    assert check_sources(session=session, now=NOW + STALE_AFTER - timedelta(hours=1)) == []


def test_a_source_that_never_succeeded_is_reported_as_such(session: Session) -> None:
    # Not "stale". Never worked and stopped working are different problems
    # with different fixes, and collapsing them sends someone hunting a
    # regression that never existed.
    record_health(session=session, source_id="abb", success=False, error="404", now=NOW)

    alerts = check_sources(session=session, now=NOW)
    assert alerts == []  # one failure, below threshold

    record_health(session=session, source_id="abb", success=False, error="404", now=NOW)
    alerts = check_sources(session=session, now=NOW)

    assert alerts[0].reason == "failing"


def test_a_source_with_no_runs_at_all_reports_never_succeeded(session: Session) -> None:
    # A row can exist without a success if a run recorded zero failures and
    # zero successes — for instance a run that was cancelled.
    session.add(SourceHealthRow(source_id="abb", consecutive_failures=0, documents_indexed=0))
    session.flush()

    alerts = check_sources(session=session, now=NOW)

    assert len(alerts) == 1
    assert alerts[0].reason == "never-succeeded"


def test_a_brand_new_source_whose_first_run_failed_waits_before_alerting(
    session: Session,
) -> None:
    # Never-succeeded and just-failed-once are both true of a source added this
    # morning whose first crawl hit a 503. Alerting on it is the same fatigue
    # the failure threshold exists to prevent, arriving by a different door: a
    # retry is due on the next cycle and will most likely clear it.
    record_health(session=session, source_id="abb", success=False, error="503", now=NOW)

    assert check_sources(session=session, now=NOW) == []


def test_a_source_that_never_ran_at_all_alerts_without_waiting(session: Session) -> None:
    # The exception to that patience, and the reason the two branches cannot be
    # collapsed. Patience is only owed where a retry is actually coming; a
    # source that has never been checked has nothing scheduled, so waiting
    # changes nothing and the silence would last until someone happened to
    # look. This is the configured-but-never-scheduled source — nothing is
    # failing because nothing is happening.
    session.add(SourceHealthRow(source_id="abb", consecutive_failures=0, documents_indexed=0))
    session.flush()

    alerts = check_sources(session=session, now=NOW)

    assert [a.reason for a in alerts] == ["never-succeeded"]


def test_a_source_that_has_never_succeeded_alerts_once_given_time(session: Session) -> None:
    # The other end of that patience: a source checked repeatedly over days
    # that has still never produced a success is no longer a transient blip,
    # even if the failure count was reset along the way.
    record_health(session=session, source_id="abb", success=False, error="404", now=NOW)

    later = NOW + STALE_AFTER + timedelta(hours=1)
    alerts = check_sources(session=session, now=later)

    assert [a.reason for a in alerts] == ["never-succeeded"]


def test_a_failing_source_is_reported_as_failing_not_stale(session: Session) -> None:
    # Both conditions are true at once here. The failure is the more useful
    # report, because `last_error` names a cause; "stale" sends someone
    # looking for why a job did not run, when it ran and threw.
    record_health(session=session, source_id="abb", success=True, now=NOW)
    later = NOW + STALE_AFTER + timedelta(days=1)
    for _ in range(FAILURE_THRESHOLD):
        record_health(session=session, source_id="abb", success=False, error="boom", now=later)

    alerts = check_sources(session=session, now=later)

    assert alerts[0].reason == "failing"


def test_a_flapping_source_is_caught_by_staleness(session: Session) -> None:
    # The case neither rule catches alone, and the reason both exist. A source
    # alternating failure and success never accumulates two consecutive
    # failures, so the threshold never fires; but if none of those successes
    # is recent the staleness rule still catches it. Without the second rule
    # this source would degrade indefinitely in silence.
    moment = NOW
    for _ in range(6):
        record_health(session=session, source_id="abb", success=False, error="503", now=moment)
        moment += timedelta(hours=6)

    # A lone success partway through, resetting the failure count.
    record_health(session=session, source_id="abb", success=True, now=NOW + timedelta(hours=6))

    later = NOW + STALE_AFTER + timedelta(days=1)
    alerts = check_sources(session=session, now=later)

    assert [a.reason for a in alerts] == ["stale"]


# --- reporting ---------------------------------------------------------------


def test_alerts_come_back_in_a_stable_order(session: Session) -> None:
    # So a page reads the same way twice and a diff between two checks means
    # something.
    for source in ("siemens", "abb", "schneider"):
        for _ in range(FAILURE_THRESHOLD):
            record_health(session=session, source_id=source, success=False, error="boom", now=NOW)

    alerts = check_sources(session=session, now=NOW)

    assert [a.source_id for a in alerts] == ["abb", "schneider", "siemens"]


def test_documents_indexed_is_recorded_but_does_not_decide_health(session: Session) -> None:
    # An operator wants the number; the monitor must not key on it. A
    # successful crawl of an unchanged library indexes nothing.
    row = record_health(
        session=session, source_id="abb", success=True, documents_indexed=0, now=NOW
    )

    assert row.documents_indexed == 0
    assert check_sources(session=session, now=NOW) == []
