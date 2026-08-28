"""Worker entrypoint.

The second composition root, alongside ``app.main``. Same package, same
config, same domain layer — different runtime, because batch work and HTTP
requests have nothing in common operationally. See
docs/adr/0002-one-package-two-runtimes.md.

Usage::

    python -m app.worker crawl abb-drives
    python -m app.worker --list
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from app.core.config import load_settings_or_exit
from app.core.logging import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    """Run one background job to completion.

    Deliberately runs a single job per process rather than holding a scheduler
    loop: retries, concurrency, and timeouts belong to the platform's scheduler
    (cron, ECS task, k8s Job), which already does them better than we would.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        A process exit code — ``0`` on success, non-zero on failure.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    settings = load_settings_or_exit()
    configure_logging(log_level=settings.log_level, json_output=not settings.debug)

    from app.core.errors import NotFoundError
    from app.worker.jobs import REGISTRY, get_job

    if not args or args[0] in {"--list", "-l"}:
        for spec in REGISTRY.values():
            print(f"{spec.name:24} {spec.description}")
        # No job named is a usage error, not a successful listing: a scheduler
        # invoking the worker with a missing argument must not look like a run
        # that succeeded.
        return 0 if args else 2

    name, *rest = args
    try:
        spec = get_job(name)
    except NotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    return spec.handler(rest)


if __name__ == "__main__":
    sys.exit(main())
