# ADR 0002: One Python package, two runtimes — and AI is a layer, not a service

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** PanelPilot engineering

## Context

The obvious way to read this system's name is as three things: a frontend, a
backend, and an AI service. That reading suggests three deployables. The
question came up early enough to be worth settling in writing, because
splitting later is cheap and un-splitting later is not.

The frontend is already a separate deployable (`apps/web`) and nothing here
changes that. The real question is whether `apps/api/app/ai/` should become its
own service.

Two facts about the code decide it.

**`app/ai/` is mostly not I/O.** Of its four packages, three perform none at
all:

| Package       | Does I/O?                                              |
| ------------- | ------------------------------------------------------ |
| `tools/`      | No — pure functions, enforced by convention and review |
| `prompts/`    | No — template rendering                                |
| `guardrails/` | No — logic over already-fetched schemas                |
| `retrieval/`  | Yes — OpenSearch, and only here                        |

Making this a network service means paying serialisation and a network hop to
call functions that compute a derating factor in memory. The isolation a
service boundary would buy is already bought by
`app/tests/test_architecture.py`, which fails CI if `domain/` or `ai/` imports
the web framework, or if a route reaches past the domain layer — at zero
runtime cost.

**Splitting `ai/` out would break the invariant in [ADR 0001](0001-staging-vs-production-index.md).**
Promotion writes the production index and the `promotion_audits` row in a
single transaction, so live content can never exist without a named human
attached to it. If OpenSearch access moved behind a separate service, that
becomes a distributed write:

```
API writes audit row → calls AI service → index write succeeds → API commit fails
                                                                        ↓
                                        live content with no audit record
```

That is precisely the failure ADR 0001 exists to prevent. It is recoverable
with an outbox or saga, but that is real, permanent complexity bought to solve
a problem we do not have.

Meanwhile there _is_ a genuine operational split in the system, and it runs
along a different axis. `app/ingestion/` is batch work: minutes-long crawls,
retry semantics, no HTTP surface, and scaling driven by corpus size rather than
by user traffic. Everything else answers a request in under a second. Running
both in one process means a crawl competes with request handling for the same
workers, and the scaling knob for one is the wrong knob for the other.

## Decision

**One Python package (`apps/api/app`), deployed as two runtimes.**

1. `app.main:create_app` — the web runtime. FastAPI, request-response, scales
   on traffic.
2. `app.worker.main:main` — the batch runtime. Runs one job per process and
   exits; scheduling, retries, and timeouts are the platform's job (cron, ECS
   task, k8s `Job`), not ours.

Both import the same `app.domain`, the same `app.core` config, and the same
models. There is no network hop and no internal API contract between them.

**`app/ai/` stays a layer inside that package.** It is called by `app.domain`,
never by a route and never by the worker directly. `app/worker/jobs.py` is thin
in exactly the way route files are thin, and CI enforces both the same way.

**Background jobs act as an explicit system principal** (`jobs.system_actor`)
that holds the ingestion role and _not_ the reviewer role — so no scheduled job
can approve content, preserving the four-eyes rule from ADR 0001 under
automation.

## Consequences

**What this buys us**

- The crawler can scale, fail, and be retried independently of the API without
  either one being able to starve the other.
- The promotion transaction stays local and atomic. ADR 0001 holds as written.
- One dependency set, one test suite, one migration history. A change that
  touches domain logic and a job is one PR and one review.
- Extracting `ai/` later remains mechanical rather than archaeological,
  because the import boundaries are already asserted in CI.

**What it costs**

- Both runtimes ship the same image and the same dependencies, so the worker
  carries FastAPI it never imports. Tens of megabytes; not worth solving.
- Two deploy targets to configure instead of one.
- A slow job cannot be scaled independently _within_ the worker — the unit of
  scaling is the job process. Acceptable while jobs are per-source.

## Extraction triggers

Revisit this — with a new ADR superseding it, not an edit — when any of these
becomes true:

1. **A separate team owns the AI layer** and needs a release cadence
   independent of the API.
2. **We self-host an embedding or inference model**, making GPU-class nodes a
   requirement for a subset of the code. This is the most likely trigger.
3. **Something outside PanelPilot needs to consume the AI layer**, making the
   internal contract an external product.
4. **Retrieval latency needs independent horizontal scaling** that the web
   runtime's scaling cannot provide.

Note that trigger 2 or 3 would also force resolving the ADR 0001 transaction
problem. Budget for that explicitly; it is the expensive part of the split, not
the code movement.

## Alternatives considered

**Three services: `api`, `ai`, `web`.** Rejected for the two reasons above —
network cost to call pure functions, and a distributed write on the one path
that most needs to be atomic. Reconsider only against the triggers listed.

**Everything in one runtime, including ingestion.** Simplest, and rejected
because a multi-minute crawl and a sub-second request cannot share a process
pool without one degrading the other, and because they want opposite scaling
policies.

**A separate `apps/worker` package with its own `pyproject.toml`.** Rejected:
the worker needs the whole domain layer, so a package split would mean
publishing an internal library and versioning it against itself. Two
entrypoints in one package gives the same deployment separation with none of
the packaging overhead.
