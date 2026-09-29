# PanelPilot

An AI diagnostic and design copilot for electrical and control engineers.
Answers are grounded in crawled manufacturer documentation and standards, with
calculations performed by deterministic code rather than by the model.

> **Status:** every path from a crawled manual to an answered question is
> implemented and exercised locally against real Postgres, OpenSearch and
> Redis: crawl into staging, human review, publication to production,
> retrieval, the cite-or-refuse guardrail, structured generation, and the web
> client in English, Arabic and Hebrew. The single-line schematic renderer
> (PD-007/PD-008) draws panel schedules. The calculation tools remain blocked
> on source documents — see Known gaps.

---

## Run it locally, end to end

```bash
cp .env.example .env
# Set real keys in .env: VOYAGE_API_KEY (embeddings; needs billing enabled on
# the Voyage account) and ANTHROPIC_API_KEY (answers and photo reading).
docker compose up --build -d          # five services; web on :3000
```

The corpus starts empty, so every question is refused until content has been
crawled **and** reviewed. That is cite-or-refuse working, not a fault. To get
from nothing to an answered question:

```bash
# 1. An account, made a reviewer. Roles are granted only from the operator
#    CLI, never through the API, so no account can promote itself.
curl -X POST http://localhost:3000/api/v1/auth/signup \
  -H 'content-type: application/json' \
  -d '{"email": "you@example.com", "password": "a-long-password"}'
docker compose exec api python -m app.worker grant-role you@example.com reviewer

# 2. Crawl a source into staging. With no seed URL the source's curated
#    document list is used (app/ingestion/known_documents.py).
docker compose exec api python -m app.worker crawl abb

# 3. Hand the crawled chunks to reviewers (run daily in a deployment).
docker compose exec api python -m app.worker assign-verification
```

4. Open <http://localhost:3000/verification>, sign in as the reviewer, and
   check each chunk's text against its cited page. **Correct** publishes it to
   the production index, with an audit row naming you; **Incorrect** and
   **Uncertain** need a note and go to a lead, who decides them with
   `POST /api/v1/verification/escalations/{id}/resolve`.
5. Ask a question at <http://localhost:3000>. Only reviewed content is ever
   searched.

Other operator commands, all `python -m app.worker <job>` (`--list` shows them):

| Job                          | What it does                                                                                |
| ---------------------------- | ------------------------------------------------------------------------------------------- |
| `revoke-role <email> <role>` | Takes a role away. Effective immediately, not at token expiry.                              |
| `reindex-staging [source]`   | Re-embeds staging after an embedding-model change. Never touches production.                |
| `expire-stale-sources`       | Lists live chunks whose source document changed since review. Exit 3 means "review needed". |

**What else you can use straight away:**

- **Single-line schematic** — <http://localhost:3000/schematic>. Paste a panel
  schedule, get a paginated IEC-style diagram with PNG and PDF export.
  Quantities no verified tool can size (conductors, trunking, enclosure) are
  printed as "not calculated", with the blocking task, never left blank.
- **PLC code review** — `POST /api/v1/plc/review`, or the PLC view. A real
  IEC 61131-3 parser; an unsupported dialect construct reports `incomplete`
  rather than a false pass. No corpus or model call needed.
- **Fault-code photo recognition** — the camera button in the chat. A reading
  costs one free question, like a diagnosis, and only when one is delivered.
- **Signup, login, and the trial claim**, carrying an anonymous conversation
  into a new account.

## Known gaps

Recorded here rather than only in the task logs because they outlive them —
anyone picking this up needs these before they need the history.

### Backend work between here and a usable product

No code gap remains on the path from crawl to answer. What remains is content:
the corpus has to be crawled and reviewed, which needs the keys above.

_Resolved in the same pass as this note: verified content could never reach
production. A `correct` label never promoted anything, escalations could not be
decided, queue items were never assigned, `promote_chunk` refused every real
chunk id, every access token said `engineer`, no page hosted the review
console, and the worker's crawl could not queue its chunks. Each is fixed and
tested against the real services; the list is kept because each was invisible
from the code alone._

_Still open: `POST /api/v1/ingestion/promotions` calls a `promote_document`
stub and returns 500. Publication goes through the review path above instead;
that endpoint needs either implementing or removing, and was left for a
deliberate decision._

_Previously listed here and now resolved: the anonymous-trial endpoint.
`POST /api/v1/auth/trial` issues a trial session, its one-time claim secret,
and an access token, so the landing page works with zero auth and signup
carries the conversation into the new account._

_Also resolved: the sessions list. `GET /api/v1/sessions?cursor=` returns a
tenant-scoped page of conversations ordered by last activity, and FE-011's
sidebar is wired to it — selecting an entry hydrates the chat view through the
same session-fetch route the page already uses, restoring the context
indicator as well as the messages. That last part needed one schema change:
the equipment a turn was answered about is now recorded on the turn, because
it previously lived only on the live response and a replayed turn came back
with the chip blank. Re-deriving it from the stored prose would have meant
guessing a model number out of an answer, which is what the chip's neutral
state exists to prevent._

_Also resolved: AI-008's recogniser is wired. `POST /api/v1/images` stores
the photo and returns `{image_id, recognition}`, where `recognition` is the
model's verdict and per-field confidences. It is best-effort: once the bytes
are stored, a model outage or an unparseable report returns
`recognition: null` rather than failing an upload that already happened, and
the client shows that as "uploaded, please type the code"._

_And resolved: BE-012's rate limiter is shared across workers. The sliding
window lives in Redis (`RATE_LIMIT_BACKEND=redis`, the default), so the limit
is per deployment rather than per worker. It fails open — an unreachable
Redis is logged and the request allowed — because failing closed would lock
every trial user out at once, and the per-account quota still bounds spend.
`RATE_LIMIT_BACKEND=memory` keeps the old single-process store for running
without Redis._

**Retrieval is wired end to end, and rate-limited on the free tier.**
Voyage is implemented, keyed and verified live: `embed_query` and
`embed_documents` both return 1024-dimension vectors from `voyage-3.5`, which
is exactly what `mappings.EMBEDDING_DIMENSIONS` pins — so no re-index was
needed. The key is read from `VOYAGE_API_KEY`, named after the vendor so a
second provider added later gets its own variable rather than overloading one
that could silently hold the wrong account's credential.

**A real crawl needs a Voyage account with billing enabled.** The free tier
allows 3 requests per minute with no payment method attached. A limit hit
surfaces correctly — `EmbeddingError`, not a zero vector — and
`reindex-staging` is safe to re-run after one.

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
selection, and panel load sizing each need a manufacturer engineering guide
with at least ten published worked examples to verify against, exactly. The
numbers end up on drawings, with cable and fire safety downstream of them, so
a table written from general knowledge — confident and uncitable — is the
exact failure cite-or-refuse exists to prevent. Their endpoints refuse with a
501 naming the blocker rather than returning a number.

A search of openly published guides found fewer than ten each: the ABB
_Electrical installation handbook_ (2006) adds three conductor-sizing and one
derating example to EIG 2010's two; ABB hardware manuals (ACH580-01, ACS355)
give five drive-derating examples, none heavy-duty. Every one is "All rights
reserved", and ABB's _Technical guide No. 7_ forbids use without written
consent, which someone should clear before its values go into code. Most
manufacturer sites (Schneider, Siemens, Danfoss, Rittal, Legrand, Hager) were
unreachable from the build environment, so the search is incomplete rather
than negative.

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
