# PanelPilot

An AI diagnostic and design copilot for electrical and control engineers.
Answers are grounded in crawled manufacturer documentation and standards, with
calculations performed by deterministic code rather than by the model.

> **Status:** the diagnostic path is implemented end to end — retrieval,
> the cite-or-refuse guardrail, structured generation, the streaming
> orchestration endpoint, and the web client that renders it in English,
> Arabic and Hebrew. `docker compose up` boots all five services healthy.
>
> Four backend gaps remain, listed below. Each one is a missing _endpoint_
> rather than missing logic: the code behind it exists and is tested.

---

## What works if you boot it today

`docker compose up --build -d` brings up five healthy services. The web root
serves a live chat input on an anonymous trial — no signup, no form.

**What you can actually exercise end to end:**

- **PLC code review** — `POST /api/v1/plc/review`, or the PLC view in the UI.
  A real IEC 61131-3 parser: valid code passes, a typo'd tag or a missing
  `END_IF` is flagged with a line number, and an unsupported dialect construct
  reports `incomplete` rather than a false pass. No auth, no corpus, no model
  call. This is the best thing to try first.
- **Signup, login, and the trial claim** — including carrying an anonymous
  conversation into a new account.

**What will not work yet, and why:**

- **Asking a diagnostic question.** It fails at retrieval — see known gap 4.
  The stream opens, emits `retrieving`, and closes; the UI reports an
  interrupted turn. This is a missing embedding provider, not a bug in the
  chat surface.
- **Anything corpus-backed.** The production index is empty — nothing has been
  crawled, chunked, verified, or promoted. Even with embeddings working, every
  answer would be a refusal until the corpus is populated and verified. That
  is cite-or-refuse behaving correctly, not a defect.

## Known gaps

Recorded here rather than only in the task logs because they outlive them —
anyone picking this up needs these before they need the history.

### Backend work between here and a usable product

Four things. The first three are routes whose client half is already built and
tested; the fourth is a vendor decision, and is the one that actually blocks
the product being usable at all.

_Previously listed here and now resolved: the anonymous-trial endpoint.
`POST /api/v1/auth/trial` issues a trial session, its one-time claim secret,
and an access token, so the landing page works with zero auth and signup
carries the conversation into the new account._

**1. AI-008's recogniser is wired to no route.**
`app/ai/recognition.py` is complete: a verdict, per-field confidence, and an
off-topic rejection path, with the schema refusing a fault code reported
alongside a non-fault-display verdict. But `POST /api/v1/images` only stores
the image and returns `{image_id}`, so nothing calls it. The web client
(`apps/web/src/lib/recognition.ts`) is written against the real
`FaultRecognitionResult` shape and reports today's stored-but-unread outcome
honestly; wiring the route is the only work left.

**2. There is no endpoint to list sessions.**
`GET /api/v1/diagnostics/{session_id}` fetches one session by id. Nothing
lists them, so FE-011 (conversation history sidebar) has no paginated
`GET /sessions?cursor=` to call and was not attempted — building a client for
an API that does not exist would have been worse than leaving it. This is a
genuine gap in the original task breakdown, not an oversight in the
implementation.

**3. BE-012's rate limiter is in-memory and single-worker.**
The sliding window lives in process memory, so the limit is per-worker rather
than per-deployment. Correct for one worker and wrong the moment there are
two. A Redis-backed store is the intended replacement; Redis is already in
the compose stack.

**4. Retrieval is wired end to end, and rate-limited on the free tier.**
Voyage is implemented, keyed and verified live: `embed_query` and
`embed_documents` both return 1024-dimension vectors from `voyage-3.5`, which
is exactly what `mappings.EMBEDDING_DIMENSIONS` pins — so no re-index was
needed. The key is read from `VOYAGE_API_KEY`, named after the vendor so a
second provider added later gets its own variable rather than overloading one
that could silently hold the wrong account's credential.

**The remaining limit is an account setting, not code.** Voyage's free tier
allows 3 requests per minute with no payment method attached, so a crawl of
any size will hit it. The failure surfaces correctly — `EmbeddingError`, not a
zero vector — but a real ingestion run needs billing enabled.

Two properties worth knowing before anyone swaps model or vendor:

- **A vector of the wrong width is refused, not padded.** The width is baked
  into the index mapping, so a 1536-wide model would either be rejected by
  OpenSearch at query time or — against a freshly built index — accepted and
  quietly wrong. Every vector in a batch is checked.
- **A provider outage raises rather than returning zeros.** A zero vector is a
  legal kNN input that matches arbitrary neighbours, so substituting one would
  degrade an outage into confidently wrong retrieval with citations attached.

`chunk_body` accepts a `content_vector`, passed in by the caller rather than
computed inside `app/ingestion` — an architecture rule denies that package any
import from `app.ai.retrieval`, the same shape as `extract_structure`. The
field is omitted entirely when absent rather than written as zeros.

### Blocked on source documents that are not in this repository

**AI-005, AI-006, AI-007 — the three calculation tools.** Cable sizing, VFD
selection, and panel load sizing each name a specific manufacturer
engineering guide as the source for their tables and coefficients. None is
present here. They were not attempted, and deliberately so: the numbers these
produce end up on drawings, with cable and fire safety downstream of them, and
a table written from general knowledge would be confident and uncitable — the
exact failure the cite-or-refuse rule exists to prevent. Supplying the named
guides unblocks all three.

**BE-011 and FE-010 — the panel BOM.** Both consume the calc tools above, so
both are blocked behind them. The responsive check
(`apps/web/scripts/check-responsive.mjs`) already refuses a table with neither
a scrollable container nor a stacked fallback, so the BOM table cannot merge
later without the mobile fallback FE-013 requires.

### Local development notes

**Migrations run automatically under `docker compose`, and only there.** The
`api` service overrides its command to `alembic upgrade head && exec uvicorn`.
This is deliberately not in the Dockerfile's `CMD`: the runtime stage is the
production image, and a container that migrates its own database on boot can
rewrite schema during a rolling deploy from however many replicas start at
once. Production runs the upgrade as a separate, ordered step. Without the
override a fresh volume starts at the initial revision and every auth route
500s on a missing column.

**The web container proxies `/api/*` to the API.** A Next rewrite, targeted by
`API_PROXY_TARGET` (`http://api:8000` under compose). The browser cannot
resolve a container hostname, and the client modules post to relative paths —
without the rewrite every request lands on the Next server as a 404, and the
landing page reports the trial endpoint missing when it is running fine.
Same-origin also means no CORS entry and no API address baked into the browser
bundle at build time.

### Deliberate incompletenesses in merged work

**PLC generation is not wired to a model.** `POST /api/v1/plc/generate`
refuses with an explicit message; `POST /api/v1/plc/review` is fully working
and validates code an engineer supplies. Refusing rather than stubbing was the
point: a plausible stub would make the endpoint look finished and hand a
caller a program no model wrote, wearing whatever verdict the validator gave
it.

**The PDF structure extractor cannot stitch a headerless table continuation.**
A table continued across a page break with neither a repeated header nor a
"(continued)" banner stays two blocks rather than one. Geometry was measured
as a candidate signal and rejected — it does not separate the two cases. The
consequence is a table fragment presented as a complete table, which is why it
is recorded rather than left to be discovered.

**BE-015 is partially satisfied.** Required checks, `enforce_admins`, and
no-force-push are live and correct on `main`. The required-approval count and
a staging branch are the two remaining gaps, left for a deliberate decision
rather than settled unilaterally.

---

## The two rules worth knowing before you write any code

**1. Cite or refuse.** PanelPilot answers from cited documentation or it
declines. The decision is made in `app/ai/guardrails/`, in code, before and
after the model call — never left to the model's own judgement. An answer that
cannot name its source is a defect, not a degraded result.

**2. Nothing reaches production content without a human.** Ingestion writes to
a staging index. A reviewer promotes to production through exactly one
function. There is no second path, and CI enforces it.
Read [ADR 0001](docs/adr/0001-staging-vs-production-index.md) before touching
anything under `ingestion/`.

---

## Repository layout

```
apps/
  web/            Next.js frontend            → its own deployable
  api/            FastAPI backend + AI layer  → two runtimes, one package
packages/
  shared-types/   API contract types, generated from the backend's OpenAPI schema
infra/            Deployment and infrastructure config
docs/adr/         Architecture decision records
```

### Three deployables, not three services

| Deployable     | Entrypoint             | Shape                               |
| -------------- | ---------------------- | ----------------------------------- |
| Web frontend   | `apps/web`             | Next.js, scales on traffic          |
| API runtime    | `app.main:create_app`  | HTTP request-response, sub-second   |
| Worker runtime | `app.worker.main:main` | Batch, one job per process, minutes |

The API and worker are **the same Python package deployed twice** — same
config, same domain layer, no network hop and no internal API contract between
them. They are separate deployables because a multi-minute crawl and a
sub-second request want opposite scaling policies, not because they are
separate systems.

`app/ai/` is deliberately **not** a service. Three of its four packages do no
I/O at all, so a service boundary there would buy network latency to call pure
functions — and it would turn the promotion write in ADR 0001 into a
distributed transaction, which is the one thing that system exists to prevent.
[ADR 0002](docs/adr/0002-one-package-two-runtimes.md) records the reasoning and
the triggers that would make extraction worth revisiting.

## Where does my code go?

The backend has four layers. Getting this right is the difference between a
change being a one-file edit and a three-day archaeology exercise.

### `app/core/` — how the process runs

Configuration, database sessions, logging, auth primitives, error types.
Answers "how does this process talk to the outside world", never "what does
this business do".

Everything reads config through `get_settings()`. No module anywhere else
touches `os.environ`.

**Goes here:** a new setting, a new middleware, a new error type.
**Does not:** anything that would differ between two products built on the same
stack.

### `app/api/v1/` — the HTTP surface

Route definitions only. A handler parses the request, calls **one** function
from `app/domain/`, and returns the response. That is the whole job.

A route file must not contain business logic, a database query, an OpenSearch
call, or a `try/except` that decides what an error means. Domain code raises
`app.core.errors` exceptions; handlers registered in `app/core/errors.py`
translate them to status codes.

If a route body is longer than about five lines, the logic belongs in
`domain/`. Enforced by `app/tests/test_architecture.py`.

**Goes here:** a new endpoint, a URL change, a response-model change.
**Does not:** how the answer is computed.

### `app/domain/` — what the product does

The service layer, and the only layer that knows the business rules. Owns
authorization decisions, orchestration, transactions, and the conversion
between ORM rows and Pydantic schemas.

Framework-agnostic: importing `fastapi` here fails CI. A domain function takes
plain arguments and a `Session`, and returns a schema. It can be called from a
test, a CLI, or a background job without an HTTP request existing.

**Goes here:** a rule about who may do what, a new workflow, a change to what
gets recorded.
**Does not:** the arithmetic of a calculation, or the mechanics of a search
query.

### `app/ai/` — model-facing machinery

Everything specific to retrieval-augmented generation, in four parts:

| Directory     | Owns                                       | Rule                                        |
| ------------- | ------------------------------------------ | ------------------------------------------- |
| `retrieval/`  | OpenSearch client, hybrid search, chunking | The only place that knows OpenSearch exists |
| `tools/`      | Cable sizing, VFD selection, panel BOM     | Pure functions: no I/O, no DB, no settings  |
| `prompts/`    | Prompt templates                           | One file per response type                  |
| `guardrails/` | Cite-or-refuse, confidence scoring         | Decides in code, not by asking the model    |

`ai/` is called by `domain/`, never by a route.

**`tools/` deserves special care.** These functions produce numbers that end up
on drawings. Each one is pure — same inputs, same outputs, no hidden state — so
it can be unit-tested against the manufacturer guide it came from without a
database or a network. Every function's docstring carries a `Source:` section
naming the guide, standard, and clause behind its formula. CI fails a calc tool
that lacks one. Quantities are `Decimal`, never `float`, and every field name
carries its unit (`design_current_a`, `length_m`, `ambient_temp_c`).

### `app/ingestion/` — getting documentation in

Crawler, staging pipeline, verification queue. Writes to staging and to
Postgres, and to nothing else. It cannot write production content — that is
`domain/promotion.py`, and only after human review.

### `app/worker/` — the batch runtime

The second composition root. `worker/jobs.py` is thin in exactly the way route
files are thin: open a session, call one `domain/` function, return an exit
code. CI enforces it the same way.

One job per process, then exit — retries, concurrency, and timeouts belong to
the platform's scheduler (cron, ECS task, k8s `Job`), which does them better
than we would. Jobs run as an explicit system principal that holds the
ingestion role and **not** the reviewer role, so no scheduled job can approve
its own content.

**Goes here:** a new scheduled or batch job.
**Does not:** what the job actually does — that is a `domain/` function.

### `app/models/` — the shapes

`tables/` holds SQLAlchemy models, `schemas/` holds Pydantic models, and they
are deliberately separate. See [models/README.md](apps/api/app/models/README.md).

Schema changes ship as Alembic migrations. Never edit a live schema by hand.

---

## Tests

`app/tests/` mirrors `app/` exactly:

```
app/ai/tools/cable_sizing.py  →  app/tests/ai/tools/test_cable_sizing.py
app/domain/promotion.py       →  app/tests/domain/test_promotion.py
```

No searching for a module's tests, and a missing test file is visible at a
glance. `app/tests/test_architecture.py` enforces the mirror, along with the
layering rules above — it is the reason those rules stay true six months from
now instead of becoming aspirational prose in this README.

---

## Local development

The whole stack runs in Docker. This is the shortest path to a working
environment and the one to use unless you have a reason not to.

```bash
cp .env.example .env          # .env is gitignored; the defaults work as-is
docker compose up --build
```

Compose brings up five services on one internal network:

| Service      | Image / target                        | Host port  | Purpose                        |
| ------------ | ------------------------------------- | ---------- | ------------------------------ |
| `web`        | `apps/web`, `dev`                     | **3000**   | `next dev`, hot reload         |
| `api`        | `apps/api`, `dev`                     | _internal_ | `uvicorn --reload`, hot reload |
| `postgres`   | `postgres:16-alpine`                  | _internal_ | Primary database               |
| `opensearch` | `opensearchproject/opensearch:2.17.1` | _internal_ | Retrieval index                |
| `redis`      | `redis:7-alpine`                      | _internal_ | Rate limiting, cached lookups  |

**Only `web` publishes a port.** Everything else is reachable inside the
network by service name (`http://api:8000`, `postgres:5432`, and so on). Both
app services bind-mount their source, so edits on the host reload in place —
you do not rebuild to change code, only to change dependencies.

Startup is gated on real readiness, not on "the container started": `api` waits
for Postgres, OpenSearch, and Redis to pass their own healthchecks, and `web`
waits for `api` to answer `/api/v1/health/ready` — which returns 503 until its
dependencies actually respond.

```bash
docker compose ps                       # STATUS column shows healthy vs starting
docker compose logs -f api
docker compose down                     # stop; named volumes survive
docker compose down -v                  # stop and wipe the data volumes
```

### Migrations against the compose database

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic revision --autogenerate -m "add x"
```

### Running the checks inside the container

```bash
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api mypy app
```

### Reaching the API from the host

`NEXT_PUBLIC_API_BASE_URL` is inlined into the **browser** bundle, so it has to
be an address your machine can resolve — not the internal `http://api:8000`. As
long as nothing in the frontend calls the API this does not matter. When it
does, either uncomment the `ports` block on the `api` service in
`docker-compose.yml`, or add a Next rewrite so the browser only ever talks to
`:3000`. The second keeps the API off the host network; the first is quicker.

> **The compose file is for local development only.** It runs OpenSearch with
> security disabled and carries placeholder database credentials in plain text.
> It is not a deployment artefact and must not be used as one. The images that
> ship are the `runtime` (api) and `runner` (web) targets — non-root, no build
> or dev tooling, and for web no `node_modules` at all.

## Getting started without Docker

```bash
cp .env.example .env       # fill in local values; .env is gitignored

# Backend — needs Postgres, OpenSearch, and Redis reachable at the URLs in .env
cd apps/api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:create_app --factory --reload   # API runtime

# Worker runtime — one job per invocation, in a second terminal
python -m app.worker --list
python -m app.worker crawl abb-drives

# Frontend (from the repo root)
npm install
npm run dev --workspace @panelpilot/web
```

## Checks

CI runs these on every PR and blocks merge on failure. Run them locally first.

```bash
# apps/api
ruff check . && black --check . && mypy app && pytest

# repo root
npm run lint && npm run format:check && npm run typecheck
```

`mypy` runs in strict mode and `ruff` enforces Google-style docstrings on
`domain/` and `ai/`. Both are non-negotiable in those directories; route files
and tests are exempt from argument-level docs because their names carry the
meaning.

## Conventions

- **Never commit or push directly to `main`.** All work goes on a feature
  branch and lands through a pull request with at least one approving review
  and a green `ci` check. `main` is protected on GitHub — force pushes and
  deletions are blocked, and the rule applies to admins too, so there is no
  owner bypass. A direct push is rejected with `GH006: Protected branch update
failed`.
- Keyword-only arguments for domain and AI functions (`*` in the signature).
  Positional booleans and bare ids at call sites are how the wrong argument
  gets passed silently.
- Absolute imports only (`from app.domain import promotion`). Relative imports
  are banned by ruff — they make moving a module a find-and-replace.
- Docstrings say _why_, not _what_. The signature already says what.
- New significant decision → new ADR. See [docs/adr/](docs/adr/).
