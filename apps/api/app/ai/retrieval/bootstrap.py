"""Create the OpenSearch indices and search pipelines this API needs.

Run once before the API serves traffic, the same way `alembic upgrade head`
prepares Postgres. Both indices and every blend pipeline are created here;
`ensure_index` is idempotent, so running it again is a no-op.

**Why this is a separate step rather than a startup hook.** Reaching
OpenSearch during app construction would make the API fail to boot whenever
the cluster is slow to come up, and a container that cannot start is harder to
diagnose than one that starts and reports a dependency as unhealthy —
`/health/ready` already answers that question honestly. Keeping the setup out
of the request path also means no query can trigger it, which matters because
a lazily-created index would be created from whichever mapping the first
caller happened to reach, at a moment nobody chose.

**Why it is needed at all.** The hybrid query names a search pipeline per
query type. OpenSearch answers a request naming an unregistered pipeline with
`illegal_argument_exception: Pipeline ... is not defined` — a hard 400, not a
silent fallback. Before this existed, `ensure_index` had no caller anywhere in
production code, so a fresh cluster had neither indices nor pipelines and every
diagnosis failed at retrieval with an error that read like a corrupt query.
"""

from __future__ import annotations

import sys

import structlog

logger = structlog.get_logger(__name__)


def bootstrap_opensearch() -> list[str]:
    """Create both indices and every search pipeline.

    Returns:
        The concrete index names created or already present.

    Raises:
        Exception: Whatever the OpenSearch client raises. Deliberately not
            caught: this runs before the API serves traffic, and a cluster
            that cannot be prepared is a reason to stop rather than to start
            and fail every query later with a less obvious error.
    """
    from app.ai.retrieval.client import IndexTarget, ensure_index

    names = [ensure_index(target) for target in IndexTarget]
    logger.info("opensearch.bootstrapped", indices=names)
    return names


def main() -> int:
    """Entry point for `python -m app.ai.retrieval.bootstrap`.

    Returns:
        0 on success, 1 if the cluster could not be prepared.

    Prints rather than only logging, because this runs as a container command
    where the failure needs to be visible in `docker compose logs` without a
    structured-log reader.
    """
    try:
        names = bootstrap_opensearch()
    except Exception as exc:
        print(f"opensearch bootstrap failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"opensearch ready: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
