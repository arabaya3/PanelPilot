"""Source health: telling "nothing new" apart from "the scraper is broken".

Both look identical from outside — an empty crawl result either way — and that
is the whole problem this exists to solve. A manufacturer publishing nothing
new is the *normal* case on most days; a crawler whose selectors stopped
matching after a portal redesign produces exactly the same silence, and can do
so for weeks before anyone notices the knowledge base has stopped growing.

So every crawl run records its outcome whether it succeeded or not, and the
alert fires on **staleness of the last success**, not on the emptiness of the
last result.

Two rules shape the alerting, and both are about being believed:

* A single failure does not alert. A portal returning 503 for ten minutes is
  an ordinary event, and a monitor that pages on it trains people to ignore
  it — after which the monitor is worse than none, because its silence now
  means nothing either.
* A source that has never succeeded is not "stale", it is unproven. Those are
  different problems with different fixes, and collapsing them sends someone
  looking for a regression in a source that never worked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables.escalation import SourceHealthRow

logger = structlog.get_logger(__name__)

#: Consecutive failures before a source is considered broken rather than
#: unlucky. Two, because one is noise — a 503, a DNS blip, a portal restart —
#: and a monitor that fires on noise stops being read.
FAILURE_THRESHOLD = 2

#: How long a source may go without a successful crawl before that is itself
#: the alert. Sized against the crawl schedule rather than against how often
#: manufacturers publish: the question is "did the job run and work", not "was
#: there anything to find".
STALE_AFTER = timedelta(days=2)


class SourceAlert:
    """A source needing attention, and why.

    Attributes:
        source_id: Which source.
        reason: ``failing`` when consecutive failures crossed the threshold,
            ``stale`` when the last success is too old, ``never-succeeded``
            when there has never been one.
        detail: Human-readable specifics for the log line or page.
    """

    __slots__ = ("detail", "reason", "source_id")

    def __init__(self, source_id: str, reason: str, detail: str) -> None:
        """Record one source's alert.

        Args:
            source_id: Which source.
            reason: ``failing``, ``stale`` or ``never-succeeded``.
            detail: Specifics for whoever reads this.
        """
        self.source_id = source_id
        self.reason = reason
        self.detail = detail

    def __repr__(self) -> str:
        """Return a debugging representation."""
        return f"SourceAlert({self.source_id!r}, {self.reason!r}, {self.detail!r})"

    def __eq__(self, other: object) -> bool:
        """Compare by field, so tests can assert on a constructed alert."""
        if not isinstance(other, SourceAlert):
            return NotImplemented
        return (self.source_id, self.reason, self.detail) == (
            other.source_id,
            other.reason,
            other.detail,
        )


def record_health(
    *,
    session: Session,
    source_id: str,
    success: bool,
    error: str | None = None,
    documents_indexed: int = 0,
    now: datetime | None = None,
) -> SourceHealthRow:
    """Record the outcome of one crawl run.

    Called at the end of **every** run, successful or not. A run that records
    nothing is indistinguishable from a run that never happened, which is the
    failure mode this whole module exists to prevent.

    Args:
        session: Open database session. The caller commits.
        source_id: Which source was crawled.
        success: Whether the run completed without error.
        error: The failure, when there was one.
        documents_indexed: How many documents this run staged. Recorded for
            operators, never used to decide health — a successful crawl of an
            unchanged library indexes nothing, and that is not a problem.
        now: Injected for tests.

    Returns:
        The updated health row.
    """
    moment = now or datetime.now(UTC)
    row = session.execute(
        select(SourceHealthRow).where(SourceHealthRow.source_id == source_id)
    ).scalar_one_or_none()

    if row is None:
        row = SourceHealthRow(source_id=source_id, consecutive_failures=0, documents_indexed=0)
        session.add(row)

    row.last_checked_at = moment
    row.documents_indexed = documents_indexed

    if success:
        row.last_success_at = moment
        # Reset rather than decrement. A source that works now is healthy;
        # carrying a failure count forward would keep an alert alive after the
        # problem is gone, which is its own kind of noise.
        row.consecutive_failures = 0
        row.last_error = None
        logger.info("source_health.success", source_id=source_id, documents=documents_indexed)
    else:
        row.consecutive_failures += 1
        row.last_error = error
        logger.warning(
            "source_health.failure",
            source_id=source_id,
            consecutive_failures=row.consecutive_failures,
            error=error,
        )

    return row


def check_sources(
    *,
    session: Session,
    now: datetime | None = None,
    failure_threshold: int = FAILURE_THRESHOLD,
    stale_after: timedelta = STALE_AFTER,
) -> list[SourceAlert]:
    """Report every source needing attention.

    Args:
        session: Open database session.
        now: Injected for tests.
        failure_threshold: Consecutive failures before alerting.
        stale_after: How long without a success before alerting.

    Returns:
        One alert per unhealthy source, in source order so a page reads the
        same way twice.
    """
    moment = now or datetime.now(UTC)
    rows = session.execute(select(SourceHealthRow).order_by(SourceHealthRow.source_id)).scalars()

    alerts: list[SourceAlert] = []
    for row in rows:
        alert = _assess(
            row, moment=moment, failure_threshold=failure_threshold, stale_after=stale_after
        )
        if alert is not None:
            alerts.append(alert)
            logger.warning(
                "source_health.alert",
                source_id=row.source_id,
                reason=alert.reason,
                detail=alert.detail,
            )
    return alerts


def _assess(
    row: SourceHealthRow,
    *,
    moment: datetime,
    failure_threshold: int,
    stale_after: timedelta,
) -> SourceAlert | None:
    """Decide whether one source is unhealthy.

    Args:
        row: The source's health record.
        moment: Now.
        failure_threshold: Consecutive failures before alerting.
        stale_after: How long without a success before alerting.

    Returns:
        An alert, or ``None`` when the source is healthy.
    """
    if row.consecutive_failures >= failure_threshold:
        # Reported before staleness, deliberately: a source failing right now
        # has a cause worth reading, and `last_error` names it. Reporting it
        # as merely "stale" would send someone looking for the reason a job
        # did not run, when it ran and threw.
        return SourceAlert(
            row.source_id,
            "failing",
            f"{row.consecutive_failures} consecutive failures; last error: {row.last_error}",
        )

    if row.last_success_at is None:
        # Never worked, rather than stopped working. Different problem,
        # different fix, and collapsing the two sends someone hunting a
        # regression that never existed.
        #
        # Held to the same patience as any other failure, though. A source
        # added this morning whose first run hit a 503 has never succeeded and
        # is not yet a problem — alerting on it is exactly the fatigue the
        # failure threshold exists to avoid, arriving by a different door. The
        # threshold above has already cleared a source with repeated failures,
        # so reaching here means fewer than that many, and the remaining
        # question is whether the source has been given time to prove itself.
        #
        # A source that has never been *checked* is the exception, and alerts
        # immediately. Patience is only owed where a retry is actually coming;
        # here nothing has ever run, so waiting changes nothing and the
        # silence would last until someone happened to look. That is the
        # configured-but-never-scheduled source — precisely the case where
        # nothing fails because nothing is happening.
        if row.last_checked_at is not None and moment - row.last_checked_at <= stale_after:
            return None
        return SourceAlert(
            row.source_id, "never-succeeded", "no successful crawl has ever been recorded"
        )

    age = moment - row.last_success_at
    if age > stale_after:
        return SourceAlert(
            row.source_id,
            "stale",
            f"last successful crawl was {age.days} days ago",
        )

    # Healthy — including the ordinary case of a source that ran, succeeded,
    # and found nothing new. That is what most days look like.
    return None
