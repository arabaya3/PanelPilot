"""Run ``worker.crontab`` locally -- a development stand-in for a scheduler.

``docker compose`` has no cron, so the worker jobs never ran there: a crawl
queued through the API waited forever and no reviewer was ever assigned a
batch. This reads the same crontab production hands to its platform scheduler
and starts each job when it is due, as its own process.

It is not the production scheduler, and not a loop inside the worker (both of
which ``infra/README.md`` rules out): each job still runs once and exits, and a
job whose previous run has not finished is skipped rather than stacked.

    python -m app.worker.schedule worker.crontab
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: Inclusive bounds of each cron field, in order.
_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),
)


class CrontabError(ValueError):
    """A crontab line this scheduler cannot read."""


@dataclass(frozen=True)
class Entry:
    """One scheduled job.

    Attributes:
        minutes: Minutes it runs at.
        hours: Hours it runs at.
        days: Days of the month it runs on.
        months: Months it runs in.
        weekdays: Days of the week, 0 = Sunday.
        day_restricted: Whether the day-of-month field was anything but ``*``.
        weekday_restricted: Whether the day-of-week field was anything but ``*``.
        args: The ``panelpilot-worker`` arguments.
    """

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_restricted: bool
    weekday_restricted: bool
    args: tuple[str, ...] = field(default=())

    def due(self, moment: datetime) -> bool:
        """Report whether the job runs in the minute ``moment`` falls in.

        Args:
            moment: A UTC time.

        Returns:
            ``True`` if every field matches. Day of month and day of week
            follow cron's rule: when both are restricted, either may match.
        """
        weekday = (moment.weekday() + 1) % 7  # Python's Monday=0 to cron's Sunday=0
        if moment.minute not in self.minutes or moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        day_ok = moment.day in self.days
        weekday_ok = weekday in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return day_ok or weekday_ok
        return day_ok and weekday_ok


def parse_crontab(text: str) -> list[Entry]:
    """Read every job line of a crontab.

    Args:
        text: The file's contents.

    Returns:
        One entry per non-blank, non-comment line.

    Raises:
        CrontabError: On a line with too few fields or a value it cannot read,
            naming the line. A schedule that silently skips a line it did not
            understand is a job that silently never runs.
    """
    entries: list[Entry] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = shlex.split(line)
        if len(parts) < 6:
            raise CrontabError(f"line {number}: expected five time fields and a job: {raw!r}")
        try:
            values = [
                _parse_field(part, low, high)
                for part, (_, low, high) in zip(parts[:5], _FIELDS, strict=True)
            ]
        except CrontabError as exc:
            raise CrontabError(f"line {number}: {exc}") from exc
        minutes, hours, days, months, weekdays = values
        entries.append(
            Entry(
                minutes=minutes,
                hours=hours,
                days=days,
                months=months,
                # 7 is Sunday as well as 0.
                weekdays=frozenset(0 if d == 7 else d for d in weekdays),
                day_restricted=parts[2] != "*",
                weekday_restricted=parts[4] != "*",
                args=tuple(parts[5:]),
            )
        )
    return entries


def _parse_field(text: str, low: int, high: int) -> frozenset[int]:
    """Expand one cron field into the values it matches.

    Args:
        text: The field, e.g. ``*/5``, ``1-5`` or ``0,30``.
        low: Smallest allowed value.
        high: Largest allowed value.

    Returns:
        The matched values.

    Raises:
        CrontabError: If the field is not ``*``, ``*/n``, a number, a range or
            a comma list of those, or names a value out of range.
    """
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if part != "*" or not step_text.isdigit() or int(step_text) == 0:
                raise CrontabError(f"unsupported step {text!r}")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            first, _, last = part.partition("-")
            if not (first.isdigit() and last.isdigit()):
                raise CrontabError(f"unreadable range {text!r}")
            start, end = int(first), int(last)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise CrontabError(f"unreadable field {text!r}")
        if start < low or end > high or start > end:
            raise CrontabError(f"{text!r} is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


def run(
    entries: Sequence[Entry],
    *,
    start: Callable[[Sequence[str]], subprocess.Popen[bytes]],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    ticks: int | None = None,
) -> None:
    """Start each job in every minute it is due, until stopped.

    Args:
        entries: The schedule.
        start: Starts one job's process; injected by tests.
        now: The clock; injected by tests.
        sleep: Waits; injected by tests.
        ticks: Minutes to run for, for tests; forever when ``None``.

    A job still running from its last start is skipped, not started twice:
    two ``crawl-queue`` runs racing for the same queued crawl is the kind of
    overlap a real scheduler is configured to prevent.
    """
    running: dict[int, subprocess.Popen[bytes]] = {}
    count = 0
    while ticks is None or count < ticks:
        moment = now().replace(second=0, microsecond=0)
        for index, entry in enumerate(entries):
            if not entry.due(moment):
                continue
            previous = running.get(index)
            if previous is not None and previous.poll() is None:
                print(f"{moment:%H:%M} skip {' '.join(entry.args)}: still running", flush=True)
                continue
            print(f"{moment:%H:%M} start {' '.join(entry.args)}", flush=True)
            running[index] = start(entry.args)
        count += 1
        if ticks is not None and count >= ticks:
            return
        # To the top of the next minute, so a slow tick cannot drift past one.
        following = moment + timedelta(minutes=1)
        sleep(max(0.0, (following - now()).total_seconds()))


def _start(args: Sequence[str]) -> subprocess.Popen[bytes]:
    """Start one worker job as its own process.

    Args:
        args: The ``panelpilot-worker`` arguments.

    Returns:
        The running process; its output goes to this one's.
    """
    return subprocess.Popen([sys.executable, "-m", "app.worker", *args])


def main(argv: Sequence[str] | None = None) -> int:
    """Run the schedule in a crontab file until interrupted.

    Args:
        argv: ``[crontab_path]``; defaults to ``sys.argv[1:]``.

    Returns:
        ``2`` on a missing or unreadable crontab; otherwise it does not return.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m app.worker.schedule <crontab>", file=sys.stderr)
        return 2
    try:
        entries = parse_crontab(Path(args[0]).read_text(encoding="utf-8"))
    except (OSError, CrontabError) as exc:
        print(f"schedule: {exc}", file=sys.stderr)
        return 2
    print(f"schedule: {len(entries)} jobs from {args[0]}", flush=True)
    run(entries, start=_start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
