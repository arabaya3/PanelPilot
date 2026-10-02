# PanelPilot

An AI diagnostic and design copilot for electrical and control engineers.
Answers are grounded in crawled manufacturer documentation and standards, with
calculations performed by deterministic code rather than by the model.

> **Status:** the diagnostic path is implemented end to end — retrieval,
> the cite-or-refuse guardrail, structured generation, the streaming
> orchestration endpoint, and the web client that renders it in English,
> Arabic and Hebrew. `docker compose up` boots all five services healthy.
>
> The corpus is not in the repository: an OpenAI account with billing
> enabled embeds it, and "Building the corpus from scratch" below rebuilds it.

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
- **Fault-code photo recognition** — `POST /api/v1/images`, or the camera
  button in the chat. The photo is stored and read by the vision model; a
  confident reading pre-fills the message, and anything less asks the engineer
  to confirm. Needs a real key for the configured provider (`OPENAI_API_KEY`
  by default, or `ANTHROPIC_API_KEY` with `LLM_PROVIDER=anthropic`); without
  one the upload still succeeds and the UI asks for the code to be typed.

**What will not work yet, and why:**

- **Asking a diagnostic question.** It answers with a refusal — see known
  gap 3. The stream runs to completion (`retrieving` → `refused` → `result`)
  and the UI renders the refusal, which is correct: the corpus is empty, so
  cite-or-refuse has nothing to cite. This is a rate-limited embedding
  account and an unpopulated index, not a bug in the chat surface.
- **Anything corpus-backed.** The production index is empty — nothing has been
  crawled, chunked, verified, or promoted. Even with embeddings working, every
  answer would be a refusal until the corpus is populated and verified. That
  is cite-or-refuse behaving correctly, not a defect.

## Known gaps

Recorded here rather than only in the task logs because they outlive them —
anyone picking this up needs these before they need the history.

### Backend work between here and a usable product

Nothing in code. A usable deployment needs an OpenAI account with billing
enabled (embeddings and answers), and a corpus built with it.

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

**Model provider.** OpenAI by default (`LLM_PROVIDER=openai`,
`OPENAI_MODEL=gpt-4o-mini`, embeddings from `text-embedding-3-small` at 1024
dimensions). Claude and Voyage remain available: `LLM_PROVIDER=anthropic` and
`EMBEDDING_PROVIDER=voyage`. Every generation path speaks one request shape and
`app/ai/openai_transport.py` translates it for OpenAI, so the guardrails and
parsers are the same under either. Switching the embedding provider on an
existing corpus is a re-index (`python -m app.worker reindex-staging`).

**Retrieval is wired end to end.** Both embedding providers return
1024-dimension vectors, which is what `mappings.EMBEDDING_DIMENSIONS` pins.
Each provider's key has its own variable (`OPENAI_API_KEY`, `VOYAGE_API_KEY`),
so a second account's credential never lands in the first one's slot. A free
tier is too rate-limited to embed a corpus: Voyage's allows 3 requests a
minute without a payment method. The failure surfaces as `EmbeddingError`,
never as a zero vector.

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

Cable sizing (AI-005) is implemented from ABB's _Electrical installation
handbook_ Vol. 2 (1SDC010001D0204), which republishes the IEC 60364-5-52
tables and prints two worked sizing examples the tests check against. It
covers copper and aluminium, PVC and XLPE/EPR, methods A1-C, E and F;
everything else is refused, not guessed. Voltage drop is read from
Schneider's Fig. G28 for copper and from the handbook's own §2.2.2 tables for
aluminium (cos φ 1, 0.9, 0.85, 0.8, 0.75).

VFD selection (AI-006) is implemented from the ABB ACS880-01 hardware
manual (3AUA0000078093): the IEC ratings of the -3 types on 380-415 V, the
-5 types on 415-500 V (rated at 500 V), and the -7 types on 525-600 V (the
manual's UL ratings at 575 V, taking ILd as the normal-duty rating) and on
660-690 V (IEC, rated at 690 V); the temperature and altitude deratings; and
the motor current from ABB's Technical guide No. 7. Other ranges and
voltages, 600-660 V among them, are refused for the ACS880-01.

The same selection runs on other manufacturers' ranges, each transcribed from
its own manual in the corpus with the page of every table and derating rule
(`app/ai/tools/drive_ranges.py`, listed by `GET /calculations/drive-ranges`):
Danfoss FC 302 and FC 51, Delta C2000 Plus, MS300, ME300 and VFD-EL-W, Fuji
FRENIC-Mini (C2), Hitachi WJ200, Invertek Optidrive E3 and P2, Lenze i550,
LS Electric S100 and iG5A, Mitsubishi FR-A800 and FR-E800, Omron MX2, Rockwell PowerFlex
523/525, Schneider ATV12, ATV320, ATV630 and ATV650, SEW MOVITRAC LTE-B and
Siemens SINAMICS V20, and Yaskawa GA500, GA800 and V1000. Where a manual's derating differs by enclosure the most
conservative is held, and where it is a curve rather than a rate the range is
offered only where it carries full current. The FRENIC-Ace, Unidrive M700,
CFW500 and 8400 StateLine manuals in the corpus print no rating
table (each refers to another document), so those series are not offered.
The panel BOM takes its drives from the first preferred vendor with a range
for the supply; input fuses are listed only for the ACS880-01.

The panel BOM (AI-007, BE-011) builds on both: a drive for each
variable-speed load from the range chosen, an outgoing copper or aluminium
cable for each load, the enclosure, and the heat balance from Rittal's
_Enclosure and process cooling_ (IEC 60890 effective area, k = 5.5 W/m²K). A
load given a start type (direct on line, star-delta, or heavy-duty direct on
line) also gets its circuit-breaker, contactors and overload relay from the
ABB _Electrical installation handbook_ Vol. 2 coordination tables (Type 2,
50 kA) at 400, 440, 500 or 690 V; the 690 V rows the handbook marks Type 1
only are left out, a relay fed through a current transformer is listed with
it, and a larger row whose relay cannot be set down to the motor's current is
refused rather than offered. A supply more than 5 % from those voltages, or a
fault level above 50 kA, is refused. Each ACS880-01 drive gets its ultrarapid
(aR) input fuses from its hardware manual, with the minimum prospective
short-circuit current they need. Every other load gets a curve C miniature
circuit-breaker chosen by the handbook's overload rule Ib ≤ In ≤ Iz against
its cable, from the rated currents of its Table 2.3 (3-125 A); its breaking
capacity is left to be confirmed against the panel's fault level. Each copper
outgoing cable gets its Siemens 8WH1 through-type terminals (Catalog LV 10,
10/2022), chosen to clamp the cable and carry its current. Terminals for
aluminium cables have no sourced selection table and are listed as not
included.

### Panel design projects

`app/design` and `app/models/schemas/design.py` hold one tool-neutral model of
a panel design, from which every output is generated: the drawing set, the
parts, terminal and cable lists, and each ECAD tool's import file. No ECAD
tool reads another's project, and the one neutral standard several import
(AutomationML AR APC) carries devices and PLC I/O but no schematic pages, so
the model is the common ground and each tool gets an exporter of its own.

Devices are named with IEC 81346-1 reference designations (`=DB1+HALL-Q12`),
assigned from a **company profile** rather than typed into a design, so one
design can be issued under any company's letters and numbering, title block,
page order, preferred brands and design rules. A company states only what it
does differently; everything else is the default profile's. The default
profile's circuit rules are this software's defaults, not any engineer's, and
stay marked unconfirmed until a company names who confirmed its own.

A **distribution board** is designed from its load schedule
(`app/design/distribution.py`):

- Each load's Ib is P / (k Ur cos φ) (ABB handbook Annex B).
- Its breaker is the company's fixed rating for that kind of load, or the
  smallest curve C rating at or above Ib. Above 125 A, the largest
  miniature breaker held, the breaker is left unselected as a moulded-case
  breaker, the cable is sized for Ib, and the board says so. The same holds
  for the incomer.
- Its cable is sized to carry In as installed, so Ib ≤ In ≤ Iz holds by
  construction.
- Loads the company puts under a residual current device are grouped by
  sensitivity, up to the company's limit per device. Each group is fed
  through a group breaker, since an RCCB has no overcurrent protection of its
  own (handbook §5.7).
- Single-phase loads are spread over L1–L3 largest first, and the imbalance
  is checked against the company's limit.

What the design assumed is listed on the board: the power factor, the fault
level, the cable conditions, and whether the rules were confirmed. Every
note, import warning and suggestion note is a code with its values
(`app/design/notes.py`). The page renders it in the reader's language
(en/ar/he), and a test keeps the catalogues in step with the codes. The
drawing set prints the English text, since its frame fonts are Latin-only.
A refusal the engineer can meet works the same way: a load the tables do not
cover, a board graph that loops, an unreadable file. It carries a `code` and
`params` beside its English `detail` (`PanelPilotError(code=..., params=...)`,
and `.about(subject)` to name the load or board), and the page says it from
`design.errors`. The design routes share a per-address budget of 240 requests
in 5 minutes (`DESIGN_POLICY`). That is generous for a site of engineers
re-exporting drawings, and holds a script to under one drawing set a second. Devices
carry ratings, not articles, until a catalogue is chosen.

The **drawing set** (`app/design/pages.py`, `render_pdf.py`) is laid out once
as plain geometry on an A3 frame. The frame has columns 0–9 and rows A–F, as
EPLAN uses, so a cross-reference such as "/4.3" names a page and a column.
Every renderer draws that same geometry. The set holds:

- a title page, including the revisions and whether the design rules were
  confirmed;
- the contents;
- per board, single-line pages: the incomer, the busbar, and each group
  breaker with its residual current device;
- distribution pages, with each circuit's breaker, phase and cable;
- the design notes;
- the cable list and the parts list.

The profile decides which of these pages appear and in what order, and what
the title block shows. Symbols follow IEC 60617 single-line conventions.
Drawing text is in English for now.

**Exports** from the same model:

- **CSV lists**: devices, parts, cables and the circuit schedule. These are
  what EPLAN, AutoCAD Electrical, SEE Electrical, WSCAD and PC|SCHEMATIC
  import from a spreadsheet, and Excel opens directly. A cell starting with
  `=`, `+`, `-` or `@` (an IEC 81346 designation such as `=DB1-Q3`) is written
  with a leading apostrophe. Excel then shows it as text instead of `#NAME?`,
  and a description can never run as a formula.
- **DXF (R12)**: the drawing set's geometry, for any CAD tool. It is a
  drawing only, with no device data.
- **QElectroTech (`.qet`)**: an editable schematic, with its symbols
  embedded. It was checked by opening it in QElectroTech 0.9.
- **AutomationML (CAEX 3.0)**: the device hierarchy, ratings and power
  links. These are the parts of AutomationML that EPLAN and E3.series
  import.

There are no EPLAN or AutoCAD Electrical project exporters yet: their
formats are undocumented and need testing against a real installation.

The **`/design` page** takes a board's load schedule and, optionally, the
company's profile settings as JSON. It shows each circuit with its breaker,
RCD and cable, lists what the design assumed, and downloads every export.
The API behind it is `POST /api/v1/design/distribution-board` and
`POST /api/v1/design/export`. The export takes the project back from the
caller, so an engineer's edits are what gets exported, renumbered under the
profile.

A consultant's **load schedule file** (Excel, CSV or PDF table, English or
Arabic headers) can be imported instead of typed
(`app/design/schedule_import.py`). The header row is found by what its cells
say. Each load type comes from its type column, or failing that from its
description, and a type inferred that way is reported. Every row skipped
(totals, spares, no power) and every assumption made is listed by row
number, so the engineer checks exactly what the import decided. A file is
read up to 5 MiB: the first 5,000 rows, 60 columns and 20 sheets, or 50 PDF
pages. A PDF table drawn without rules is read from its text alignment. The
design routes accept request bodies up to 6 MiB
(`MAX_DESIGN_REQUEST_BODY_BYTES`); everything else keeps the 64 KiB default.

A board can also be **described in words** ("a hall with 20 sockets, 30
LED lights and two 2-ton split units"). This happens in two stages
(`app/ai/schedule_writer.py`, `app/design/schedule_split.py`):

1. **The model only reads the points:** how many, of what kind, and the
   power of one. It marks a power as typical when the description gives
   none, and answers in the description's language, which is detected in
   code and named to it.
2. **Code splits the points into circuits** under the company's rules
   (`max_points_per_circuit`, `max_kw_per_circuit`). The same description
   therefore always gives the same circuits. Small appliances are kept
   single-phase whatever the model says.

In live tests, a model asked to do the grouping itself put 20 sockets on one
circuit and answered English in Arabic, so it is not asked to. Every
circuit's arithmetic is listed ("7 x 150 W = 1.05 kW. typical") for the
engineer to check, and each suggestion is charged to the tenant's monthly
model allowance.

A three-phase **motor** load can name its starter (`app/design/motors.py`):

- **Direct on line or star-delta:** gets a Type 2 coordinated set from the
  coordination tables of ABB's _Electrical installation handbook_ Vol. 2,
  §3.3: a moulded-case breaker, the contactor(s) and a thermal overload
  relay. In star-delta the relay sits in the phase windings, so it is set
  to Ir/√3, and the motor is fed by six conductors.
- **Drive:** gets the smallest ACS880-01 for normal duty at the board's
  ambient, behind the aR fuses its manual lists.
- **Motor current:** Ir comes from the nameplate power factor where one is
  given. Otherwise it is the table's typical Ir for that power, or
  P / (√3 U η cos φ) at assumed values. Either way the board says which.
- **Articles:** each article is a project part (`ABB/A30`), so the drawings,
  the parts list and the quotation show it before a company catalogue is
  loaded. The drawings use a fuse, overload relay, drive and Y/D block
  symbol.
- **PLC program:** the motor gets start/stop latching in hand and the
  schedule in auto. A star-delta gets a TON star period and a 100 ms gap
  before delta, each contactor interlocked against the other. A drive's
  run command is gated by its ready signal. The PLC checker now reads
  function-block calls (`T1(IN := x, PT := T#5S)`), instance outputs
  (`T1.Q`) and duration literals, so these programs are checked in full.

A project can hold several boards (`POST /design/project`,
`app/design/project.py`). A sub-board names the board that feeds it, and
boards are designed leaves first:

- **Feeder sizing:** each feeder is sized from its sub-board as designed. Its
  Ib is the sub-board's most loaded line conductor, the same current that
  board's incomer is rated for, so no feeder load is typed in by hand.
- **Checks:** names, the feeding graph (an unknown board, a board feeding
  itself, a loop) and supply compatibility are checked first, and each
  refusal names the board.
- **Drawings:** the feeder and the sub-board cross-reference each other by
  page and column ("to DB-1 /4.0", "from MDB -Q5 /6.0").
- **Discrimination:** a sub-board's incomer is a switch-disconnector,
  protected by its feeder breaker and rated no lower than it. A breaker there
  would only add one more level for the ratio to multiply through. The
  feeder is rated at least the company's ratio times the largest breaker
  that switch feeds, and its cable is sized for that rating.

**Discrimination** follows the company's ratio (`discrimination_ratio`,
default 1.6). Every breaker is rated at least that many times the largest
breaker after it:

- **Group breakers:** raised as far as their RCCB's ratings allow.
- **The incomer:** raised as far as the miniature-breaker range allows.
- **Feeders:** raised to discriminate with the sub-board's breakers.

Anything that cannot be raised is named on the board. On short circuit,
miniature breakers discriminate only up to the upstream one's instantaneous
trip (about 5 × In for curve C), and the board says to confirm total
selectivity from the manufacturer's tables.

**Breaking capacity** is set from the fault level (`distribution.breaking_capacity`):

- **Rating:** every breaker gets the smallest standard breaking capacity at or
  above the board's prospective fault current (6, 10, 15, 25, 36 or 50 kA).
  A motor starter keeps its coordinated breaker, which the tables hold to
  50 kA.
- **Beyond 50 kA:** no capacity is rated, and the board says back-up
  (cascade) protection is needed from the manufacturer's tables.
- **Sub-boards:** a sub-board with no fault level of its own takes its
  supply's, unreduced by the feeder. That is a safe figure, and the engineer
  can replace it with a calculated one.

**Voltage drop** is checked for every circuit whose cable length is given,
and for every feeder whose length is given (`app/design/voltage_drop.py`):

- **Limit:** the limit runs from the origin to the load. It is 3 % for
  lighting and 5 % for anything else (IEC 60364-5-52 Annex G), and the
  company profile can change it per kind of load.
- **Tables:** the drop is read from Schneider's Fig. G28 for copper and ABB's
  §2.2.2 tables for aluminium. Each is taken at the worst power factor the
  tables hold, with no interpolation. A star-delta motor is read as three
  loops at Ir/√3 across the line voltage.
- **Enlarging:** a cable that drops too much is stepped up to the smallest
  tabulated section that keeps it within the limit, and the board names it.
  If no section is enough, the board says so.
- **Adding up:** a sub-board's circuits get their limit less what its feeders
  dropped. Each feeder is held to the strictest load anywhere below it. The
  drop shows in the result table and in the cable list.

A designed board can be **priced** from the company's price list (Excel
or CSV) and rates (`app/design/quotation.py`):

- **Matching:** each device is matched by its order number or type number.
  Until a catalogue is chosen it is matched by its rating key
  (`circuit_breaker:1P:C16`, `residual_current_device:4P:40A:30mA`). Cables
  are priced per metre (`cable:3G2.5:Cu:PVC`) at their own length or the
  company's default length.
- **Rates:** labour per circuit and per board, the enclosure, markup and VAT
  are added in that order.
- **No guessing:** a line with no price is never priced at a guess. It stays
  on the quotation without an amount, the quotation is marked incomplete,
  and the PDF says so above its totals.
- **Output:** the quotation exports as PDF or CSV.

A load marked **PLC switched** gets a contactor after its breaker, rated no
lower than the breaker (AC-1), and the board gets a **control program**
(`app/design/plc_program.py`):

- **I/O:** one coil output per contactor and one manual-on input per circuit.
  Three inputs are shared: `EStop_OK` (normally closed), `Auto_Mode` and
  `Schedule_On`.
- **Logic:** each coil is
  `K := EStop_OK AND (Manual_On OR (Auto_Mode AND Schedule_On))`, so an open
  emergency stop drops every contactor.
- **Check:** the program is plain IEC 61131-3 Structured Text with no direct
  addresses. It is run through the same parser-based checker as generated
  PLC code, and the page shows that checker's verdict.
- **Output:** the program exports as `.st`, and the I/O list as CSV with an
  empty address column for the engineer to fill in. The contactor also
  appears in the drawing set and the QElectroTech file.

### Local development notes

**Migrations run automatically under `docker compose`, and only there.** The
`api` service overrides its command to `alembic upgrade head && exec uvicorn`.
This is deliberately not in the Dockerfile's `CMD`: the runtime stage is the
production image, and a container that migrates its own database on boot can
rewrite schema during a rolling deploy from however many replicas start at
once. Production runs the upgrade as a separate, ordered step. Without the
override a fresh volume starts at the initial revision and every auth route
500s on a missing column.

**The web container proxies `/api/v1/*` to the API.** A route handler
(`apps/web/src/app/api/[...path]/route.ts`) that reads `API_PROXY_TARGET`
(`http://api:8000` under compose) on every request. The browser cannot resolve
a container hostname, and the client modules post to relative paths — without
the proxy every request lands on the Next server as a 404, and the landing page
reports the trial endpoint missing when it is running fine. Same-origin also
means no CORS entry and no API address baked into the browser bundle. It is a
route handler rather than a Next rewrite because a rewrite's target is fixed at
build time, which sent the production image's API traffic to its own
`localhost:8000`.

### Deliberate incompletenesses in merged work

**PLC generation returns code with its verdict, never code alone.**
`POST /api/v1/plc/generate` asks the model for the program and runs the same
validator as `POST /api/v1/plc/review` on it before answering. A validator
that fails returns an explicit `INCOMPLETE` verdict rather than none, so
generated code is never handed over looking checked when it was not.

**A headerless table continuation is stitched only on matching evidence.**
A table continued across a page break with neither a repeated header nor a
"(continued)" banner is joined when nothing comes between the two halves,
their column widths agree to 1.5 pt, and the last row before the break has
the same shape (empty, number, code or text per cell) as the first row after
it. Text beside empty cells is not joined: that is a framed page layout, not
data. Checked on the PowerFlex 520, WJ200, MS300, FC 302 and ATV630 manuals.
A continuation that fails any of these stays two blocks, so a fragment can
still be presented as a complete table; a new table opening on the same grid
with a data row would be wrongly joined.

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

**Tenant isolation lives here too, not in each query.** `core/tenancy.py`
filters every ORM query on a tenant-scoped table to the tenant the session is
bound to, and a session bound to none cannot query one at all. Requests are
bound in `resolve_caller`. Code that genuinely spans tenants says why with
`cross_tenant(...)`, in the modules the architecture test allows
([ADR 0003](docs/adr/0003-tenant-isolation-needs-one-enforcement-point.md)).

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
| `api`        | `apps/api`, `dev`                     | 8000 (lo)  | `uvicorn --reload`, hot reload |
| `postgres`   | `postgres:16-alpine`                  | _internal_ | Primary database               |
| `opensearch` | `opensearchproject/opensearch:2.17.1` | _internal_ | Retrieval index                |
| `redis`      | `redis:7-alpine`                      | _internal_ | Rate limiting, cached lookups  |

**Only `web` publishes a port to the network.** `api` is published on
loopback only, for tools on your own machine. Everything else is reachable
inside the network by service name (`http://api:8000`, `postgres:5432`, and so on). Both
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

The browser never needs to: it talks only to `:3000`, and the web server
proxies `/api/v1/*` to the API over the compose network. For curl or an OpenAPI
client on your own machine, the API is published on loopback only, at
`http://127.0.0.1:8000`.

> **The compose file is for local development only.** It runs OpenSearch with
> security disabled and carries placeholder database credentials in plain text.
> It is not a deployment artefact and must not be used as one. The images that
> ship are the `runtime` (api) and `runner` (web) targets — non-root, no build
> or dev tooling, and for web no `node_modules` at all.

## Building the corpus from scratch

A fresh checkout has empty indices: answers come only from manuals that were
crawled, checked and promoted. The corpus is not in this repository; these
steps rebuild it. Expect a few hours, mostly the crawler's politeness delays
and PDF extraction, and well under a dollar of OpenAI embeddings for the
~60,000 passages. Every command runs inside the `api` container.

**1. Configure and boot.** In `.env`, set `OPENAI_API_KEY` (embeddings and
answers) on an account with billing enabled; the free tier's rate limit
cannot embed a corpus. Then `docker compose up --build -d`; the `api` service
creates both indices with their vector mapping before it starts. Outside
compose, run `python -m app.ai.retrieval.bootstrap` first: compose's OpenSearch
refuses writes to an index that does not exist, but another cluster may create
one from the first document, with no vector mapping.

**2. Make yourself a reviewer.** Sign up at <http://localhost:3000>, then:

```bash
docker compose exec api python -m app.worker grant-role you@example.com reviewer
```

**3. Crawl the sources that allow it.** Each has curated manual URLs
(`app/ingestion/known_documents.py`), so no seed is needed:

```bash
for source in siemens abb danfoss yaskawa rockwell mitsubishi weg omron \
              lselectric inovance hitachi fuji nidec sew; do
  docker compose exec api python -m app.worker crawl "$source"
done
```

**4. Stage the manuals supplied by hand.** Schneider, Delta, Invertek, Lenze,
Phoenix Contact, Weidmüller and B&R refuse the crawler, so their PDFs are
downloaded in a browser. Each folder's `sources.csv` names every file and
where it came from; put the PDFs next to it under `apps/api/data/<source>/`
(they are gitignored), then:

```bash
for source in schneider delta invertek lenze phoenixcontact weidmueller br; do
  docker compose exec api python -m app.worker ingest-files "$source" "data/$source"
done
```

Everything is now in **staging**, queued for review. Nothing is live yet.

**5. Check against the PDFs and promote.** `review-staged` reads the page
of the original PDF that each pending passage cites, and clears it as you
only when its citation is complete, it is not a contents list or an index,
and at least 90% of its words are on that page or the two after it:

```bash
docker compose exec api python -m app.worker review-staged you@example.com --dry-run
docker compose exec api python -m app.worker review-staged you@example.com
```

The dry run prints each manual's verdicts and clears nothing. The real run
clears through the same path as the review console: four-eyes check, audit
row and production write, committed one passage at a time, so running it
again after an interruption picks up where it stopped.
Manuals supplied by hand are read from `data/`; crawled ones are fetched
again through the crawler into `data/_fetched/`, and are kept only if they
are byte-for-byte the file that was staged (`--no-fetch` skips this). The
command is never scheduled, because it clears in a person's name.

**6. Read what is left.** Passages that fail the check (weak or no match on
their page, navigation) stay pending, so a person decides on them in the
review console. The corpus answers questions without them.

## Getting started without Docker

```bash
cp .env.example .env       # fill in local values; .env is gitignored

# Backend — needs Postgres, OpenSearch, and Redis reachable at the URLs in .env
cd apps/api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install --require-hashes -r requirements-dev.lock   # the exact versions CI runs
pip install --no-deps -e .   # or both at once, as CI does: scripts/install-locked.sh
alembic upgrade head
uvicorn app.main:create_app --factory --reload   # API runtime

# Worker runtime — one job per invocation, in a second terminal
python -m app.worker --list
python -m app.worker crawl abb https://library.abb.com/...   # queue and run one now
python -m app.worker crawl-queue   # run the oldest crawl queued via POST /ingestion/crawl-jobs
python -m app.worker assign-review-batches   # daily: hand staged chunks to reviewers
python -m app.worker calibrate-relevance eval.json   # recommend RETRIEVAL_MIN_SIMILARITY
python -m app.worker expire-stale-sources   # weekly: flag live documents changed upstream (exit 1 = review needed)
python -m app.worker reindex-staging [abb]   # after an embedding model change: re-embed staging in place
python -m app.worker review-staged you@example.com [--dry-run]   # check pending passages against their PDFs; clear the grounded ones as you

# `POST /api/v1/ingestion/crawl-jobs` only queues (202); schedule `crawl-queue`
# every few minutes to run what it queued. Poll GET .../crawl-jobs/{id}.

# Roles: every account is an engineer; reviewer, ingestion and admin are
# granted by an operator. Read from the database on every request, so a grant
# or revocation applies to the account's next request, not when its token expires.
python -m app.worker grant-role engineer@example.com reviewer
python -m app.worker revoke-role engineer@example.com reviewer

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

Python dependencies are locked. After editing `dependencies` or the `dev`
extra in `apps/api/pyproject.toml`, run `scripts/lock-deps.sh` from
`apps/api` and commit both `requirements*.lock` files; CI's `lock drift` job
fails otherwise. It keeps every other pin where it is; `--upgrade` (or
`--upgrade-package <name>`) moves them. It needs `uv` at the version CI pins
(`UV_VERSION` in `.github/workflows/ci.yml`).

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
