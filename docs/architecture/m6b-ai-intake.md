# M6b — AI-assisted requirements intake (contract)

Status: integration-owner contract, 2026-11-04. Builds on M6a
(`m6a-guided-intake.md`). Draft; not for merge or deployment.

The model reads a designer's plain-language description and **proposes**
entries for the existing guided requirements step. It designs nothing. Only
what the designer confirms reaches the request, and only the request reaches
the deterministic designer. There is no second design or control model.

## 1. What may be proposed

Only what `ProjectDesignRequest` and the `pp-req/1` control model already hold:

| Item kind      | Fields it may set                                                                                                                                                                                             |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `board`        | `name`; `fed_from` (another proposed or existing board's name)                                                                                                                                                |
| `load`         | `board` (a board name), `description`, `kind` (the load kinds the editor offers; never `sub_board`), and, **only where the text states them**: `power_kw`, `phases`, `power_factor`, `length_m`, `controlled` |
| `control_need` | `load` (a proposed load's item id), `need` (one of the seven M6a needs); implies `controlled: true` on that load                                                                                              |
| `question`     | `text`, `about` (an item id or `null`), `reason`: `missing`, `ambiguous` or `contradictory`                                                                                                                   |
| `unsupported`  | `text`, `reason`: what the schema cannot hold (a product or part number, a cable spec, a connection between boards, fire-pump or life-safety equipment, a standard edition, …)                                |

Rules:

- **No invented values.** A numeric field is set only when its source span
  states it. Each numeric value carries the span it was read from. There are
  no "typical" figures (unlike the load-schedule writer, which this does not
  reuse for values). A load with no stated power gets no power; the M6a
  deterministic questions (missing values, the 0.9 default) and the editor's
  own validation then show what is missing.
- **Every item carries `source`** = `{start, end}` character offsets into the
  stored input. The server checks the offsets lie in the input and stores the
  quoted text; for each numeric field it checks the number appears in that
  item's span. An item failing a check is not dropped silently: it moves to
  `server_rejected` with a reason, shown to the user.
- Fire-pump and life-safety equipment is always `unsupported` (out of scope).
- The model never sets approval, review, validation, acknowledgement, author,
  tenant, revision or any field not listed above. Such keys are a schema
  violation.

## 2. Provider and output handling

- Reuse `app.ai.anthropic_client.get_llm_client` and the forced-tool
  structured output helpers (`app.ai.structured_output`). A provider
  interface is injectable so tests use a deterministic stub; no stub is
  reachable from a production configuration.
- The description is sent as delimited data; the system prompt says it is
  data, not instructions. Provider output is untrusted: parsed by strict
  pydantic models (`extra="forbid"`), bounded (at most 200 items, strings at
  most 200 characters, offsets within the input).
- Input: typed text only, at most 8 000 characters (422 `ai_input_too_long`),
  non-empty (422 `ai_input_empty`). No files, attachments or customer
  documents are sent to a provider; there is no upload path in this slice.
- Errors save nothing and never touch the user's draft:
  - no provider configured / provider down: 503 `ai_unavailable`;
  - malformed output (not the tool, schema violation, bad offsets en masse):
    502 `ai_proposal_malformed`;
  - timeout: 504 `ai_timeout` (or the existing timeout code if one exists).
- Permission: a signed-in account that may save a revision (403
  `ai_requires_account` for a trial). Tenant-scoped. Reuse an existing rate
  limit if there is one; otherwise document the gap.

## 3. Proposal record `pp-ai-proposal/1`

Stored server-side in a new tenant-scoped table `design_ai_proposals`:
`id`, `tenant_id`, `created_by_user_id`, `created_at`, `input_text`,
`input_sha256`, `provider`, `model`, `prompt_version`, `items` (JSONB, each
with `id`, `kind`, `source`, `quote`, `value`), `server_rejected` (JSONB),
`decisions` (JSONB, append-only), `used_by_revision_id` (nullable),
`proposal_sha256` (canonical hash of the immutable part: input, provider,
model, prompt version, items, server_rejected). Another tenant's proposal is
404 `ai_proposal_not_found`.

Endpoints:

- `POST /design/projects/requirements/ai-proposals` — body
  `{description, request?}` (`request` is the current draft, used only so the
  model can name existing boards; never stored as confirmed). 201 with the
  proposal.
- `GET /design/projects/requirements/ai-proposals/{id}`.
- `POST /design/projects/requirements/ai-proposals/{id}/items/{item_id}/decision`
  — strict body `{"decision": "accept" | "reject"}` or
  `{"decision": "edit", "value": {...}}` (validated against the item kind's
  fields). Server sets `by_user_id`, `by_name`, `at`; a client-sent who/when
  is 422. Append-only: a later decision supersedes, history kept. 409
  `ai_proposal_used` once a revision used it.

## 4. Applying and saving

- The UI inserts an accepted or edited item into the editor; rows remember
  which item they came from. Nothing is inserted on "proposed" or "reject".
- Save sends `requirements_input = {intake: "ai", control: [...], ai:
{proposal_id, placements: [{item_id, board_index, load_index?}]}}`.
- The server, under the project lock: the proposal is the tenant's and
  unused; each placed item's latest decision is accept/edit (else 422
  `ai_item_not_confirmed`); the placed row exists. Where the row differs from
  the decided value on a field the item set, the placement records
  `changed_in_editor: [fields]` — the user may edit after accepting.
- The revision's requirements record gains `ai`: the full proposal (input
  text, provenance, items, server_rejected, proposal_sha256), the decisions
  and the placements. It is covered by `requirements_sha256`, and so by the
  approval binding. The proposal row is marked used.
- Items left undecided, and every `question`/`unsupported` item not rejected
  by the user, are open items in the record: `ai_item_unreviewed`,
  `ai_question`, `ai_unsupported`. They print as `[OPEN]` lines on review
  exports (renderer bump). A user "reject" of a question is recorded, never
  hidden.
- The designer receives only the request. The `ai` block never feeds design,
  calculations or checks. `intake: "ai"` stops being refused only with an
  `ai` block.

## 5. Compatibility and versions

- `pp-req/1` gains the optional `ai` field (additive). Records without it are
  unchanged; earlier revisions are untouched.
- Exports printing AI open items bump the affected renderers; RULES_VERSION
  is unchanged (no finding changes).
- One additive migration for `design_ai_proposals`; single Alembic head.
- Execution export and restricted formats stay blocked. Nothing here claims
  electrical accuracy or standards compliance; an accepted proposal is a
  designer's entry, not engineer validation.

## 6. As built (API, 2026-11-04)

Where this section and §1–5 differ, this section holds. Integration-owner
decisions of 2026-11-04 are marked (owner).

### 6.1 Provider and prompt

- `app/ai/intake_proposer.py`: `ProposalProvider` interface,
  `LlmProposalProvider` over `get_llm_client` with a forced tool
  `propose_requirements` (schema from strict pydantic models, objects closed),
  `PROMPT_VERSION = "m6b-intake-2026.11.04.1"` (in `app/design/ai_intake.py`).
  The route takes the provider from the dependency
  `app.domain.design_ai.get_proposal_provider`; tests override it with a stub
  that exists only under `app/tests` (`ai_intake_support.StubProvider`).
- The description is sent between `<description>` tags, unescaped so offsets
  stay true; a typed `</description>` is neutralised at the same length.
  Existing board names (from `request.boards[*].name`, nothing else of the
  draft) go in their own escaped block. The system prompt says the block is
  data, never instructions.
- Forced-tool payload (owner): `{"items": [{id, kind, source: {start, end},
value: {...}}]}`. The model sends no quote: the stored `quote` is always
  `input_text[start:end]` (owner).
- Provider/model recorded from settings (`llm_provider`, `generation_model`).
- The architecture test's guardrail exemption names `app/ai/intake_proposer.py`
  (answers nothing, cites nothing; its gate is the server check plus the
  designer's confirmation), like `schedule_writer.py`.

### 6.2 Server checks (`app/design/ai_intake.py`)

- Whole output refused, 502 `ai_proposal_malformed`, nothing stored: not an
  object `{items: [...]}`; any unknown key at top level, on an item or in a
  value (e.g. `approved`, `validated`, `author`, `tenant`, `quote`); an item
  id not `[A-Za-z0-9_.-]{1,40}`; more than 200 items; more than half the items
  with unusable spans; no tool call or a cut-off answer.
- Per item → `server_rejected` (`{id, kind, source, quote, value (bounded),
reason, detail}`), never dropped: `offsets_outside_input` (not
  `0 ≤ start < end ≤ len`), `span_too_long` (> 400 chars), `invalid_value`
  (unknown kind, a value not fitting its kind: enums, `sub_board`, strings >
  200, ranges), `value_not_in_text`, `reference_unresolved` (iterated to a
  fixpoint: a load on a refused board is refused too), `duplicate_id`.
- Numbers: each of `power_kw`, `power_factor`, `length_m`, `phases` must be a
  literal number in the item's span (decimal comma accepted; `1,200` is
  twelve hundred; Arabic-Indic digits read). A `power_kw` literal followed by
  `W`/`watts`/`HP` does not count (no unit conversion). `phases` may also be
  written "single-phase"/"three-phase". The literal's position is stored as
  `value_spans[field]`.
- Life safety (owner): a server keyword check on the span and the
  name/description (English, Arabic, Hebrew: fire pump, jockey pump,
  sprinkler, fire alarm, smoke control, emergency lighting, exit sign, life
  safety, stair pressurisation, …) turns a `board` or `load` into an
  `unsupported` item, whatever kind the model gave it. Placing it is 422
  `ai_placement_invalid`.
- Server item ids are `i<n>` by output position; references in values are
  rewritten to them.

### 6.3 Endpoints and shapes

- `POST /design/projects/requirements/ai-proposals` — body
  `AiProposalRequest {description, request?}` (`request` any object; only its
  board names are read; never stored). 201 `AiProposal`.
  Errors: 422 `ai_input_empty` / `ai_input_too_long` (`params.limit`,
  `params.length`); 403 `ai_requires_account`; 429 `rate_limited` /
  `model_budget_exceeded`; 502 `ai_proposal_malformed`; 503 `ai_unavailable`;
  504 `ai_timeout`. Error bodies are generic (no input or output echoed).
- `GET …/ai-proposals/{id}` — 200 `AiProposal`; 404 `ai_proposal_not_found`
  (unknown, malformed id, or another tenant's).
- `POST …/ai-proposals/{id}/items/{item_id}/decision` — strict
  `AiDecisionRequest`; 200 `AiProposal`. 404 `ai_item_not_found` (also a
  server-rejected item); 409 `ai_proposal_used`; 422 `ai_edit_not_valid`
  (edit of a `question`/`unsupported` item, or a value not fitting the kind),
  `ai_decisions_full` (2 000).
- `AiProposal` = `{id, schema: "pp-ai-proposal/1", created_at,
created_by_user_id, input_text, input_sha256, provider, model,
prompt_version, items, server_rejected, decisions, used,
used_by_revision_id, proposal_sha256}`. Additions to §3: each item also
  carries `value_spans`, `state` (`proposed|accepted|edited|rejected`, from
  the latest decision) and `decided_value`; `used` (bool). A decision is
  `{item_id, decision, value, by_user_id, by_name, at}` (`by_name`: display
  name, else email, else `unknown account`).
- Permission: any signed-in member of the tenant (the same as saving a
  revision; there is no separate save permission). Trials are 403.
- Rate limits (owner): the design router's per-address
  `enforce_design_rate_limit` plus the per-address `enforce_trial_rate_limit`
  on create, and the tenant's monthly model-call budget
  (`model_budget.charge_model_call`). **No per-user limit exists** in the
  codebase; none was added (gap).
- Transactions (owner): the call is charged in a short committed transaction,
  then the provider is asked outside any transaction or lock, then the
  proposal is stored in a short transaction. A failed provider call stays
  charged (the provider was asked); nothing else is stored.
- Logging (owner): `input_text`, prompts and model output are never logged;
  validation errors already strip `input`.

### 6.4 Save, preview, carry

- `requirements_input.ai = {proposal_id, placements: [{item_id, board_index,
load_index?}]}` (strict: client-sent `proposal`, `decisions`,
  `proposal_sha256`, … are 422). `intake: "ai"` without `ai`, and `ai`
  without `intake: "ai"`, are 422 (pydantic).
- Under the save's transaction the proposal row is locked `FOR UPDATE`:
  404 `ai_proposal_not_found` (another tenant's), 409 `ai_proposal_used`,
  422 `ai_item_not_confirmed`, 422 `ai_placement_invalid` (unknown item, row
  missing, a `question`/`unsupported` item, `load_index` given for a board
  item or missing for a load/control need), 422 `ai_placement_twice`.
  A `control_need` is placed on its load's row; `changed_in_editor: ["need"]`
  when the save's control needs for that row lack it.
- Record `ai` block: `{proposal: {id, schema, created_at,
created_by_user_id, input_text, input_sha256, provider, model,
prompt_version, items, server_rejected, proposal_sha256}, decisions,
placements: [{item_id, board_index, load_index, board_ref, load_ref,
changed_in_editor}], applied_in_revision, applied_at}`. Covered by
  `requirements_sha256`. The proposal gets `used_at` and
  `used_by_revision_id` (FK `SET NULL` on project delete; `used_at` keeps it
  used). A save may send the block with zero placements: every item then
  yields its open item.
- Open items (subject `{item_id}`): `ai_item_unreviewed` (an undecided
  board/load/control_need item), `ai_question` and `ai_unsupported` (not
  rejected; an accept leaves them open). Server-rejected items are not open
  items (they are shown on the proposal).
- Carry: a later revision without a new `ai` block (revise, answer, resave)
  carries the previous record's block **unchanged** (its timestamps, indices
  and `applied_in_revision` are the applying revision's; refs stay valid) and
  re-derives its open items. A new proposal replaces it; a used proposal is
  never re-applied.
- `POST /design/projects/requirements/preview` accepts the same `ai` block,
  checks it read-only (no lock, not marked used) and returns the record with
  it.

### 6.5 Value types and offsets

- `source` offsets are Unicode code points (Python `str` indices).
- Values are stored with numbers as decimal strings (the canonical hash
  takes no float) and sent by the API as JSON numbers (`power_kw`,
  `power_factor`, `length_m`; `phases` 1 or 3; `controlled` boolean); absent
  fields are omitted, never defaulted. Edits take numbers.
- `question` and `unsupported` items take `accept`/`reject` only (`edit` is
  422 `ai_edit_not_valid`).
- Additive to the UI shape: items carry `value_spans`, `state`,
  `decided_value`; rejected items carry `id`, `detail`; the proposal carries
  `schema`, `created_by_user_id`, `input_sha256`, `used`.

### 6.6 Exports and versions

- Review exports (drawing set, calculations report, v2 connection drawing
  and schedules, JSON `requirements` lines) print:
  `[OPEN] AI intake question, not answered: <text>`,
  `[OPEN] Not covered by this design (from AI intake): <text> — <reason>`,
  `[OPEN] AI intake item not reviewed (<kind>): “<quote>”` (texts cut at 120
  characters). No input text is printed otherwise.
- Renderers bumped: v1 `2026.11.03.3` → `2026.11.04.1`, v2
  `2026.11.03.v2-15` → `2026.11.04.v2-16` (new output paths; reference
  exports unchanged, fingerprints re-recorded for the version key).
  `RULES_VERSION` unchanged. Approvals recorded under the previous renderer
  versions stop counting (`renderer_version`); on this unmerged stack that
  is every approval.
- Migration `d4f8a1c3e6b2` (revises `c8e4a2d6f1b9`), single head, additive.
- New error classes: `BadGatewayError` (502), `GatewayTimeoutError` (504).

### 6.7 Review fixes (independent review of 1fe1c81)

- **F2: numbers need their own unit.** A number counts for a field only with
  a positive unit or cue next to it in the item's span: `power_kw` followed
  by `kW` / `kilowatt(s)` (or the Arabic كيلوواط / ك.و, Hebrew קילוואט /
  קו״ט); `length_m` followed by `m` / `metre(s)` / `meter(s)` (Arabic متر /
  م, Hebrew מטר / מ'); `phases` followed by `phase(s)` / `ph` / `Ø` / `φ`
  (Arabic فاز / طور, Hebrew פאז…) or written "single-phase" / "three-phase";
  `power_factor` after `pf` / `p.f.` / `cos φ` / `cos phi` / "power factor"
  (Arabic معامل القدرة, Hebrew מקדם הספק). "63 A", "16 mm2", "400 V",
  "5 MW", "10 kVA", "12 kWh", "5.5 kW" as a length and "3 kW" as phases are
  `server_rejected` with `value_not_in_text`. The reason code was kept, not
  split per unit.
- **F1: part numbers.** A `board` or `load` whose span or name/description
  holds a manufacturer part or catalogue number becomes `unsupported` with
  `reason: "product_number"`. Two conservative rules: a known brand (ABB,
  Siemens, Schneider, Legrand, Eaton, Hager, Mitsubishi, Omron,
  Allen-Bradley, Danfoss, Telemecanique, Chint, LS, GE, WEG, Wago, Phoenix,
  Rittal, Finder, Lovato, Socomec) followed by a code with letters and
  digits ("ABB S203", "Schneider iC60N"); or an unhyphenated token of
  letters and at least three digits ("S203", "3RT2015", "LC1D18") that is
  not a number with its unit ("5500W", "400V"). Decided: tags such as
  "Pump P-7", "DB-1" or "AHU2" are **not** part numbers (a hyphen, or fewer
  than three digits); "Siemens motor 3 kW" is not one either.
- **F3: life safety reads context.** The check reads every sentence the span
  stands in (sentences end at `.` `;` `!` `?` and the Arabic marks when
  followed by space, so "5.5" does not split one, or at a line break), and
  every other sentence that names one of the span's identifier tags
  (letters then digits, "P-7"), except the board names, so "P-7 is the fire
  pump" reaches "Pump P-7 5.5 kW" while a board's other loads are not drawn
  in. The word list adds hydrant, deluge, drencher, foam pump/system, fire
  main/hose/service/damper/curtain, fireman's/firemen's lift, firefighting
  lift, smoke detector/fan, evacuation, and in Arabic إطفاء / هيدرانت / دخان,
  in Hebrew כיבוי / הידרנט / עשן. **Decided:** emergency lighting (and
  emergency luminaires/exit signs) stays unsupported: it is life-safety
  equipment under its own standards, which this designer does not cover.
- **S1: bounded call.** The provider is asked with a 60 s timeout and no
  retry (`with_options(timeout=60, max_retries=0)`, also on the OpenAI
  wrapper), and the domain bounds the whole call to
  `PROVIDER_DEADLINE_S = 60` on a worker thread: past it, 504 `ai_timeout`,
  nothing stored. An abandoned call's thread ends at the client's own
  timeout.
- **S4:** any opening or closing `description` tag typed into the
  description, in any case or spacing (`</ Description >`), has its angle
  brackets replaced by single guillemets in place (same length, offsets
  unchanged).
- **S6:** only the SDKs' `APIError` types, `httpx`/`httpx2` `HTTPError`,
  `ConnectionError` and `TimeoutError` map to 503/504; anything else
  propagates as a 500 (the generic handler, no customer text in the body).
- The adversarial suite's app fixture now also overrides the per-address
  trial limit (every request of a run comes from one test address; the
  limits have their own tests), and its synthetic SDK errors use `httpx2`,
  the SDK's own HTTP package, so `mypy app` passes.

### 6.8 Open owner decisions

- **S2:** decided in code, defaults open (see §7.1): a per-account monthly
  cap and one call in flight per account, on top of the tenant's budget.
- **S3:** partly addressed (see §7): consent is recorded, unused proposals
  can be deleted by their author and are purged after a retention period.
  `input_text` copied into a saved revision's hashed record is **not**
  removable today; §7.4 is the design for a separate change.
  `created_by_user_id` is still a plain nullable id with **no** foreign key;
  deleting an account leaves the id in place (owner decision unchanged).

### 6.9 Second review round (G1–G8) and the life-safety safety net

- **Normalised reading.** Every check reads the text normalised: NFKC per
  character, format characters (zero-width spaces/joiners, bidi marks)
  dropped, every decimal digit as ASCII, and the Arabic decimal and
  thousands separators (٫ ٬) as `.` and `,`. Stored text and offsets never
  change; a position map takes a match back to the stored text
  (`value_spans` stay true). G1 ("٥٫٥ كيلوواط" is 5.5), G7 (a zero-width
  space inside "fire pump").
- **G2/G3.** A number that is one side of a fraction or ratio (`1/2`,
  `3:1`) is no value; a length followed by `/` or "per" is a rate
  ("3 m/s"), not a length.
- **G5.** Tags compare without case, hyphen or space (`P7` = `P-7` = `p 7`).
- **G4.** A sentence opening with a word referring back (It, Its, This,
  That, These, Those, They, Which, The same; Arabic هذا/هذه/ذلك/تلك/هو/هي;
  Hebrew זה/זו/זאת/הוא/היא) is read with the sentence before it, in a chain.
- **G8.** Part numbers also cover hyphenated and spaced codes with at
  least three digits and a segment mixing letters and digits (`LC1-D18`,
  `S203-C16`, `6ES7 214-1AG40`, `ACS580-01-12A6-4`); not `P-7`, `DB-1`,
  `DB-12`, `AHU2`, `Room 101A` or `230-400V`.
- **Safety net (needs no linking).** When a life-safety term appears
  anywhere in the description, the proposal says so (`life_safety_mentioned:
true`, shown as a warning beside the proposals, with no way to dismiss
  it) and a save records the record-level open item
  `ai_life_safety_mentioned` (subject `{}`; one per mention with
  `{mention_id, term}` since §6.10). No decision closes it (only a
  clarification, §6.10), and a carried block keeps it. Review exports print:
  `[OPEN] The description mentions fire-pump or life-safety equipment: out
of scope and not designed; check that no placed load is that equipment`.
  The renderer versions already bumped on this unmerged branch
  (`2026.11.04.1`, `2026.11.04.v2-16`) are not bumped again (the guard's
  reference exports are unchanged).
- **Limits.** Unit, part-number and life-safety detection are heuristics
  with known limits: other phrasings, languages, spellings and indirect
  references can still pass, and ordinary text can be caught. They narrow
  what a model can slip through; they are not the gate. The gate remains the
  user's accept and place step: nothing reaches the request unless the user
  confirms it, and the safety-net open item stays on the record for review.

### 6.10 Life-safety detection, ambiguity and clarification (M6c)

**Keyword detection can never recognise every safety requirement.** It
finds the words listed below, as listed; a description can name life-safety
equipment in ways no list holds. It narrows what a model can slip through
and puts what it finds in front of a person. It is not the gate and it
validates nothing: the gate stays the user's accept-and-place step, and
every finding stays on the record and on every review export. The UI says
so beside the clarifications ("Keyword detection cannot recognise every
safety requirement ... not engineer validation").

**Reading (`app/design/safety_terms.py`, detection only).** Text is read
through a UTS #39-style skeleton: NFKC; every dash and hyphen variant
(U+2010–2015, U+2011, U+2212, U+00AD soft hyphen, U+2043, U+FE58, U+FE63,
U+FF0D and others) as `-`; format characters (zero-width spaces/joiners,
bidi marks) and the Arabic tatweel dropped; combining marks dropped
(accents, Arabic harakat, Hebrew niqqud); Cyrillic, Greek and small-capital
Latin look-alikes folded to Latin (`fіre` with Cyrillic і is `fire`);
Arabic alef/hamza seats, alef maqsura and teh marbuta folded; lower case.
Stored text and offsets never change: each mention carries offsets into the
stored text. Compound words match with any of nothing, space, `-`, `_`,
`.` or `/` between their parts.

**Two levels.**

- _Life-safety_ (`life_safety`): fire pump/water/alarm/main/hose/
  fighting/fighter(s) lift/fireman's lift/service/damper/curtain/shutter/
  suppression/detection/extinguisher/protection/reel/riser/brigade/door
  hold-open/telephone/panel; firewater; jockey; sprinkler(s); hydrant;
  deluge; drencher; foam pump/system; wet/dry riser; smoke extract/exhaust/
  control/vent/damper/detector/fan/curtain/management/evacuation/clearance/
  shaft/alarm; emergency lighting/luminaire/exit/escape/voice/evacuation/
  lift; escape lighting/sign; exit sign; life safety; stair(case/well)
  pressurisation; evacuation; voice alarm; refuge alarm; gas suppression;
  FM-200; heat detector; manual call point; break glass. Arabic: مضخة
  (ال)حريق, إنذار (ال)حريق, مكافحة (ال)حريق, إطفاء, رشاش/مرشات, هيدرانت,
  دخان, جوكي, إخلاء, سلامة الأرواح, تضغيط, إنارة/إضاءة (ال)طوارئ, مخرج
  (ال)طوارئ. Hebrew: משאבת (ה)אש/כיבוי, מתז, ספרינקלר, גלאי עשן/אש/חום,
  גילוי אש, כיבוי, הידרנט, עשן, ג'וקי, תאורת חירום, יציאת חירום, בטיחות
  חיים, שחרור עשן.
- _Possibly safety_ (`possibly`, ambiguous): `fire`/`fires` alone,
  `emergency`, `standby`/`stand-by` (so "duty/standby pump"), `alarm(s)`,
  `critical`, `safety` (so "UPS for safety lighting"), `pressuris/zation`,
  `smoke` alone, `escape`, `essential`, `life support`, `firefighter`,
  `fireman`. Arabic حريق alone, طوارئ, إنذار, احتياطي, سلامة; Hebrew אש
  alone, חירום, אזעקה, גיבוי, בטיחות, פינוי. (Arabic حريق and Hebrew אש /
  חירום alone were life-safety before; they are possibly-safety now, which
  is still never a supported load.)

**Items.** A `board` or `load` whose context (below) or name/description
holds a life-safety term becomes `unsupported` with reason
`fire-pump or life-safety equipment is out of scope`; one holding only a
possibly-safety word becomes `unsupported` with reason
`possibly_life_safety` (the existing kind reused; no new kind). Neither is
ever placeable (422 `ai_placement_invalid`) nor a supported load; accept and
reject only record the user's decision.

**Context.** As §6.7/§6.9 (the span's sentences, sentences opening with a
back-reference in a chain, sentences naming the span's tags) and now every
_later_ sentence holding a reference to something said earlier: "the
former/latter", "the first/second/third/last", "the above", "the
aforementioned", "the same", "both", "either", "neither", "these/those/the
two/pumps/loads/motors/fans/ones/units/items/equipment", "all of them",
"each of them", "above-mentioned", "aforesaid", "said <noun>"; Arabic
الأول/الأخير/الثاني/كلاهما/المذكور/أعلاه; Hebrew הראשון/האחרון/השני/
שניהם/הנ"ל/הנזכר. Such a sentence is read with every earlier item, so
"The former is the fire pump" also catches the latter item (deliberately
over-inclusive).

**Record.** One open item per mention (each matched span): `ai_life_safety_mentioned`
or `ai_possibly_life_safety`, subject `{mention_id, term}`.
`mention_id` = `m` + the first 16 hex digits of SHA-256 of
`level NUL term NUL start` (term: the skeleton text with separators as
one space). The proposal response carries `safety_mentions` (each with
level, term and offsets) and the UI warns about possibly-safety words as
it does about life-safety ones. Exports print, per open mention:
`[OPEN] The description mentions fire-pump or life-safety equipment
('<term>'): out of scope and not designed; check that no placed load is
that equipment` or `[OPEN] The description may refer to life-safety
equipment ('<term>'): needs a person's safety review; not designed`.

**Clarification (owner's design, implemented).** `POST
/design/projects/{id}/revisions/{n}/requirements/safety-clarifications`,
strict body `{mention_id, statement: "not_life_safety", reason}` (reason
10–500 characters after whitespace is collapsed; any other key, including
`by_user_id`, `by_name`, `at`, `validated`, is 422). The server sets
`by_user_id`, `by_name`, `at`. 200 `{requirements}`.

- Rules as an acknowledgement's: under the project lock, then the
  revision's; the stored record must match its hash (409
  `snapshot_integrity_failed`); 409 `requirements_derived`,
  `revision_already_approved`, `revision_not_latest`; 404
  `revision_not_found` (another tenant's too), `mention_not_found` (unknown,
  or not open: already clarified).
- Who: a signed-in member holding `approve_revision` (403
  `clarification_requires_account`, `clarification_not_permitted`; checked
  with the approval's `FOR SHARE` locks), and **not** the account that
  created the AI proposal (403 `clarification_by_proposal_author`; a
  proposal whose creator is unknown is refused the same way). The plan's
  approval feature is not checked (the permission is the rule asked for).
- **Only `possibly`-level mentions are clarifiable (final review, owner).**
  A `life_safety`-level mention is never clarified: 409
  `mention_is_life_safety`; it stays `[OPEN]` for good on that proposal
  (to remove it, delete the text from the description and request a new
  proposal). While any life-safety mention stands in the description —
  whatever the model returned for it (omitted, asked as a question, or a
  plain load the context check missed) — the proposal has a safety concern
  (`life_safety_mentioned`), so no possibly-safety mention of it is
  clarifiable either (409 `safety_concern_unresolved`). The UI shows no
  clarify action on a life-safety mention, with that explanation.
- Never: 409 `safety_concern_unresolved` while any item of the proposal is
  `unsupported` as life-safety equipment (the server's reason, or a
  model-made unsupported item whose words are life-safety ones) — whatever
  its decision — or any `possibly_life_safety` item has no decision. A
  clarification changes no item, decision or placement; an `ai_unsupported`
  item stays open; nothing becomes a load.
- Stored append-only in the record's `ai.clarifications` (`{mention_id,
statement, reason, by_user_id, by_name, at}`), covered by
  `requirements_sha256` and so by the approval's
  `approved_requirements_sha256`; carried with the block to later
  revisions. The record's AI open items are re-derived: a clarification
  counts only while its mention is still detected (same id) and the
  proposal has no safety concern (checked again at derivation), and the
  first for a mention holds.
- Exports print each clarification in force: `[CLARIFIED] '<term>' judged
not life-safety by <name> on <date>: <reason> — not engineer-validated`
  (`<name>`: display name, else "a team member"; never an email). An
  unclarified mention stays `[OPEN]`.
- UI: the requirements view shows "Clarify as not life-safety" on a
  mention for an eligible member only, from the server flag
  `SavedProject.safety_clarification = {eligible, reason}`
  (`requires_account`, `not_permitted`, `proposal_author`,
  `requirements_derived`, `revision_already_approved`,
  `revision_not_latest`, `mention_is_life_safety` (only life-safety
  mentions are open), `safety_concern_unresolved`,
  `nothing_to_clarify`); an ineligible member sees why. A required reason
  (10–500); afterwards who, when and the reason. en/ar/he.

**Renderers.** Review export lines changed (per-mention terms, the
possibly-safety line, `[CLARIFIED]`): v1 `2026.11.04.1` → `2026.11.04.2`,
v2 `2026.11.04.v2-16` → `2026.11.04.v2-17`; reference exports unchanged,
fingerprints re-recorded for the version key. Every approval recorded under
`2026.11.04.1` or `2026.11.04.v2-16` (on this unmerged stack: every approval
made since M6b's bump) stops counting (`renderer_version`). `RULES_VERSION`
unchanged; no migration.

**Known bypasses (explicit, not exhaustive).**

- Phrasings not listed: "the pump that keeps the building wet when it
  burns", "the pump the fire brigade connects to" without "fire brigade",
  trade names ("FM-200" is listed; "Inergen", "Novec 1230" are not),
  standards ("NFPA 20 pump", "EN 12845 pump", "BS 5839") and abbreviations
  ("FA panel", "FAP", "FACP", "EL", "JP", "FP-1" as a tag alone).
- Letters spaced or split ("f i r e pump", "fi re"), leetspeak and digit
  look-alikes ("f1re", "sm0ke"), look-alikes outside the folded set
  (Armenian, Cherokee, mathematical letters NFKC does not fold, other
  scripts), misspellings ("sprinker", "hydrent", "evacuaton").
- Languages other than English, Arabic and Hebrew, and Arabic/Hebrew
  phrasings, spellings and inflections not listed (e.g. Arabic حريق in a
  compound not listed is possibly-safety only).
- References: a pronoun or noun that is not listed ("the pump" mid-sentence
  two sentences later, "it" not opening a sentence, "the one on DB-2"), a
  reference to a later item, or a reference whose antecedent is in a
  different description.
- Over-catching is accepted: "Fire" in a company name, "critical" or
  "standby" used of ordinary loads, "alarm" of an intruder alarm, and the
  far references pulling in neighbouring items all become non-loads and
  open mentions needing review; a clarification is the way to record that
  a mention was harmless.
- A clarification is a person's judgement: a member who may approve can
  wrongly clarify a possibly-safety word that was in fact about life-safety
  equipment (life-safety-level mentions cannot be clarified). Four-eyes,
  the reason, the printed `[CLARIFIED]` line and the approval (a further
  member) are the controls; nothing validates it.

**Final-review fixes (2026-11-04).** Small-capital Latin letters (U+1D00
range small capitals, ꜰ U+A730, ꜱ U+A731, ʀ, ɪ, ɢ, ʟ, ʙ, ʜ, ɴ, ʏ, ꞯ) are
folded as stated above. Review exports print a phrase, not the reason code,
for a server-made unsupported item: "may be life-safety equipment; needs a
person's safety review" (`possibly_life_safety`) and "names a
manufacturer's part or catalogue number" (`product_number`); exports are
English, as their other fixed lines are. The renderer guard's reference
exports are unchanged, so the versions (`2026.11.04.2`,
`2026.11.04.v2-17`) are not bumped again on this unreleased branch. Context
also reads a clause after ";" (or Arabic "؛") with the one it continues,
and follows a repeated untagged name of two or more words ("Pump one 15 kW
… Pump one feeds the sprinklers") as a tag is followed. Still bypasses
(reviewer's strict xfails in `app/tests/design/test_m6c_final_probes.py`):
"it" not opening a sentence, note references ("see note 3"), standards
instead of terms ("NFPA 20 listed"), and a one-word repeated name
("Pump … Pump feeds the sprinklers": not followed, it would draw in every
pump).

### 6.11 Safety concerns hold approval; two resolutions; approval scope (M6d)

**Why.** Before M6d a revision whose record held an unresolved life-safety
mention could still be review-approved; the `[OPEN]` line only printed on the
approved documents. Now an unresolved safety concern holds the approval.

**Detection is not complete.** Keyword detection (§6.10) and the model's own
reading cannot find every life-safety requirement: an unlisted phrasing, a
standard number, an abbreviation or a misspelling passes both (see the
bypasses in §6.10). The hold covers what was detected; it is not evidence that
nothing else is a life-safety requirement, and no resolution is engineer
validation.

**Safety concerns** (`ai_intake.safety_targets`), re-derived on every check
from the bound record's `ai` block — the stored description (`input_text`,
same detection as the proposal path) and the stored items — never from the
stored `open_items` list (a record whose open items were edited and rehashed
is still held):

| code                           | what                                                                                                                                                                         |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ai_life_safety_mentioned`     | a `life_safety`-level mention in the description                                                                                                                             |
| `ai_possibly_life_safety`      | a `possibly`-level mention                                                                                                                                                   |
| `ai_life_safety_item`          | an item `unsupported` as life-safety (server reason `fire-pump or life-safety equipment is out of scope`, or a model-made unsupported item whose words are life-safety ones) |
| `ai_possibly_life_safety_item` | an item `unsupported` with reason `possibly_life_safety`                                                                                                                     |

A decision (accept/reject) on an item, and a generic M6a acknowledgement,
never resolve a concern.

**The hold.** `approve_revision` refuses 409 `approval_blocked_by_safety`
(params `revision`, `count`, `codes` comma-joined, sorted) while any concern of
the stored record is unresolved. Checked under the project and revision locks,
after the revision's own refusals (approved, integrity, stale, blocking
findings) and before the author checks. `SavedProject.approval_held =
{code, count, codes}` (null when approvable or approved),
`SavedProject.safety_concerns` (each with `kind`, `target_id`, `level`,
`code`, `label`, offsets, `resolved`, the `resolution` in force and
`false_positive_refusal`) and `SavedProject.safety_resolution = {eligible,
reason}`. The approval panel shows the concerns, their resolve actions and
the hold (`approveHeld.safety`). A derived record (no stored one) holds none.

**The only two resolutions.** PanelPilot has no "separately engineered"
workflow and no other way to clear a safety concern.
`POST /design/projects/{id}/revisions/{n}/requirements/safety-resolutions`,
strict body `{mention_id | item_id (exactly one), resolution, reason}`
(reason 10–500 characters after whitespace is collapsed; `by_user_id`,
`by_name`, `at` or any other key is 422). 200 `{requirements}`.

- `excluded_from_scope` — the requirement is real and is **not** covered by
  this revision ("Fire pump is designed separately by the fire-protection
  contractor"). Allowed on every concern. Prints on every review export and in
  the approval scope: `[EXCLUDED FROM SCOPE] '<term or item text>' — not
designed or reviewed here: <reason> (recorded by <name> on <date>)`. It
  never turns anything into a load or a placeable item; an excluded item's
  `ai_unsupported` open item is replaced by this line.
- `false_positive` — the detected text is not a life-safety requirement.
  Allowed on a mention: a `possibly` one always; a `life_safety` one only when
  no item of the proposal is life-safety equipment from an overlapping span
  (else 409 `false_positive_not_allowed`, `reason:
life_safety_item_overlaps`: exclude it instead). Never on an item (409
  `false_positive_not_allowed`, `reason: item`). Prints `[FALSE POSITIVE]
'<term>' judged not a life-safety requirement by <name> on <date>: <reason>
— not engineer-validated`. Examples: "Fire Station Road" (`fire`),
  "no fire pump is required" (`fire pump`, no item), "Emergency contact on
  the door label", "duty/standby booster pumps".
- A possibly-safety item is also resolved when every possibly-level mention
  of the description (at least one, and no life-safety mention) is a false
  positive in force: the words it was raised for were judged harmless. It
  stays `unsupported` (printed "Not covered by this design"), never a load.

Rules: under the project lock, then the revision's; the stored record must
match its hash (409 `snapshot_integrity_failed`, `requirements_mismatch`);
409 `requirements_derived`, `revision_already_approved`,
`revision_not_latest`; 404 `revision_not_found` (another tenant's too),
`safety_target_not_found`; 409 `safety_concern_already_resolved` (a
resolution is never replaced). Who: a signed-in member holding
`approve_revision` (403 `safety_resolution_requires_account` — a trial too —
and `safety_resolution_not_permitted`, checked with the approval's `FOR
SHARE` locks), not the account that created the AI proposal (403
`safety_resolution_by_proposal_author`; unknown creator refused alike) and
not the revision's author (403 `safety_resolution_by_revision_author`; an
author not established is refused alike). Stored append-only in
`ai.resolutions` (`{target_kind, target_id, resolution, reason,
by_user_id, by_name, at}`, server-set who/when), covered by
`requirements_sha256` and so by `approved_requirements_sha256`; carried with
the block to later revisions. A resolution counts while its target is still a
concern (same id) and its resolution is still allowed; the first per target
holds.

**The M6c clarification** (`…/safety-clarifications`, `ai.clarifications`)
is kept for compatibility, unchanged, and deprecated: the web app no longer
offers it. A clarification in force counts as a `false_positive` of its
possibly-level mention and prints as `[FALSE POSITIVE]` (no longer
`[CLARIFIED]`).

**Approval meaning and scope.** `app/design/approval_scope.py`
(`APPROVAL_SCOPE_VERSION` `2026.11.04.1`). A review approval is a review of
this revision's study under PanelPilot's default rules. It does not cover
control circuits, board-to-board wiring, items listed as open or excluded from
scope, life-safety systems, standards compliance, electrical accuracy or
execution. It is not engineer validation. Printed as `[SCOPE]` lines after the
requirements lines on every review export (drawing set, calculations report,
quotation, v2 connection drawing and schedules); an approved export adds
`[SCOPE] The approver confirmed this scope (scope statement <version>)`. The
JSON export carries `approval: {approved, scope_version, scope_confirmed,
covers, does_not_cover, engineer_validated: false, excluded_from_scope[],
false_positives[]}`. The approval body requires `scope_confirmed: true`
(strict boolean; missing, false, `"true"`, `1` are 422); the panel shows the
statement, the exclusions and false positives, and a confirmation box Approve
waits for. Recorded in the new nullable column
`design_project_revisions.approved_scope_version` (migration `d2b6f4a8c1e3`,
additive, single head). A column, not a field of the bound record: the
confirmation is part of the approval act made after the record is final;
writing it into the record would change the hash the approval binds. It is
not compared when deciding whether an approval counts: the statement is
printed on every review export, so changing it changes the renderer's
reference output and forces a renderer bump, which already stops every earlier
approval counting.

**Conflicting values (`ai_conflicting_values`, open item, not a hold).**
Two confirmed items placed on one row that disagree on a field, and a placed
row changed in the editor after acceptance, each leave an open item, printed
`[OPEN] Conflicting values for <field>: <a> ("<quote>") and <b> ("<quote>")
were both accepted for one row; confirm which applies` and `[OPEN] Changed in
the editor after acceptance: <field>, AI value <v> ("<quote>"), entered value
<w>`. Placements now record `entered` (the row's value per changed field);
older placements print "not recorded". A question's reason `conflict` is
accepted as an alias of `contradictory`.

**Renderers.** v1 `2026.11.04.2` → `2026.11.04.3`, v2 `2026.11.04.v2-17` →
`2026.11.04.v2-18`: every review export now prints the scope lines and the
JSON export the `approval` block, and can print the new
`[EXCLUDED FROM SCOPE]`/`[FALSE POSITIVE]`/conflict lines. Fingerprints
re-recorded. **Every approval recorded before this change stops counting**
(`renderer_version`; on this unmerged stack every approval made since the
M6c bump): its revision is stale for export; save it again and have it
approved, with the scope confirmed, by another member. `RULES_VERSION`
unchanged. Execution export (`execution_package_incomplete`) and the
restricted formats (`export_format_unprotected`) stay refused.

## 7. Usage controls, retention and privacy (S2/S3, 2026-11-04)

### 7.1 Per-account usage control (S2)

- **What is limited.** Per `(tenant, account)`, in table `ai_intake_usage`
  (one row per tenant and account, tenant-scoped like `model_usage`):
  - `AI_INTAKE_CALLS_PER_USER_PER_MONTH` (setting, default **100**, unset =
    no per-account cap) AI intake calls per UTC calendar month. Refused with
    **429 `ai_user_limit_exceeded`**, `params: {limit, period}`, and
    `Retry-After` (seconds to the next UTC month). Distinct from
    `rate_limited` (per address, wait minutes) and `model_budget_exceeded`
    (the tenant's month).
  - **One call in flight** per account: **409 `ai_request_in_flight`** while
    another of the account's calls runs (parallel tabs cannot fan out).
- **Atomicity.** The account's row is read `FOR UPDATE` (created through a
  savepoint on first use, as `model_usage`), checked and incremented, and the
  lease set, in the **same short transaction** as the tenant's
  `charge_model_call`, committed before the provider is asked. Either refusal
  rolls back both: a refused call is counted nowhere and reaches no
  provider. Order: lease, account cap, tenant ceiling.
- **The lease** (`lease_id`, `lease_expires_at` = now + 60 s deadline +
  30 s margin) is released in a `finally` after the call, whatever its
  outcome: success, 502/503/504, a bug (500), or a store failure. Release
  clears only its own lease id. On a 504 the abandoned provider call may
  still run on its worker thread (until the client's own 60 s timeout): the
  lease is then released by that thread when it ends, not by the request,
  so the account's next call meanwhile gets 409 `ai_request_in_flight` and
  one account cannot tie up the shared 8-thread pool. A crashed worker
  never releases it; it lapses at `lease_expires_at` and the next call
  proceeds.
- **Failed calls count.** A call that reached the provider stays counted for
  the account as it stays charged to the tenant (consistent with §6.1/S1);
  refusals before the provider (consent, input bounds, lease, caps) count
  nothing. **No automatic retry**, server or client: the page offers Retry,
  which is a new counted call.
- **Remaining count.** The create response carries `calls_remaining`
  (the account's calls left this month after this one; `null` with no cap,
  and on GET/decision responses). The page shows it under the request
  button.
- **UI.** `ai-intake.tsx` says `ai_user_limit_exceeded` (with limit and
  month), `ai_request_in_flight`, `ai_consent_required` and
  `ai_proposal_not_yours` in en/ar/he, each with Retry and Continue
  manually.
- **Not decided here.** Pricing, plan-specific per-account caps, and
  whether owners get a higher cap. The cap is a setting, not a plan field
  (`billing.Plan` has only the pooled tenant allowance). The default 100 is
  a tenth of the default tenant ceiling (`MODEL_CALLS_PER_MONTH` 1000): an
  **open owner decision**.

### 7.2 Where the description, proposals, decisions and quotes go

| What                                                                                                                            | Where                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Lifetime                                                                                    | Removable today                                                                                         |
| ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Description (`input_text`)                                                                                                      | `design_ai_proposals.input_text` (+ `input_sha256`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Until the proposal is deleted                                                               | Unused proposal: yes (author's delete, retention purge). Used: no (it is the applied revision's source) |
| Description                                                                                                                     | Saved revision's `requirements.ai.proposal.input_text`, inside `requirements_sha256`, and every later revision that carries the block (§6.4)                                                                                                                                                                                                                                                                                                                                                                                                     | As long as the revision                                                                     | **No** (hashed, approvals bind to it); see §7.4                                                         |
| Description                                                                                                                     | Browser `sessionStorage` draft (`DESIGN_DRAFT_STORAGE_KEY`, `ai.description`, plus `proposalId`)                                                                                                                                                                                                                                                                                                                                                                                                                                                 | The tab session; removed on save/discard/trial change                                       | By the user (closing the tab, discarding the draft)                                                     |
| Description                                                                                                                     | API responses: create, GET proposal, saved-revision/project GET (`requirements.ai`)                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Client side only                                                                            | n/a                                                                                                     |
| Description sent to the provider                                                                                                | `LlmProposalProvider`: `system=SYSTEM_PROMPT` (fixed text), one user message `"<existing_boards>\n{board names, one per line, < > escaped, or (none)}\n</existing_boards>\n\n<description>\n{description, delimiter tags neutralised}\n</description>\n\nOffsets count from …"`, plus model id, `max_tokens`, the tool schema and `tool_choice`. Nothing else from the tenant: no project info, loads, supply, customer, account, email or tenant id (asserted in `test_the_provider_receives_only_the_prompt_the_description_and_board_names`). | Provider-side retention under the provider's terms; this code sets no zero-retention option | **Not by us**                                                                                           |
| Board names (from the editor's draft)                                                                                           | Sent to the provider (above); not stored with the proposal (`request` is read for names only)                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Provider side                                                                               | Not by us                                                                                               |
| Model output (items, quotes, `value_spans`, `server_rejected`)                                                                  | `design_ai_proposals.items/server_rejected` and the revision's `ai` block                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | As the description                                                                          | As the description                                                                                      |
| Decisions (who, name, when, value)                                                                                              | `design_ai_proposals.decisions` (append-only) and the revision's `ai.decisions`                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | As the description                                                                          | As the description                                                                                      |
| Quotes and question/unsupported texts                                                                                           | Review exports (drawing set PDF, calculations report, v2 connection drawing and schedules, JSON `requirements` lines): `[OPEN] …` lines cut at 120 characters (§6.6); the life-safety and possible-safety lines quote the matched term from the description (`('<term>')`)                                                                                                                                                                                                                                                                       | In every file already downloaded                                                            | **No**: downloaded files are outside the system                                                         |
| Clarifications (who judged a matched term not life-safety, when, and a free-text reason the user typed, at most 500 characters) | The revision's hashed `ai` block (`clarifications`), and review exports as `[CLARIFIED] '<term>' judged not life-safety by <display name> on <date>: <reason> — not engineer-validated` (display name, never the email)                                                                                                                                                                                                                                                                                                                          | As the revision; in every file already downloaded                                           | **No**: user-entered text inside the hashed record (§7.4); downloaded files are outside the system      |
| Full description in exports                                                                                                     | Not printed by any export (PDF, DXF, AML, QET, ECAD, lists, JSON)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | —                                                                                           | —                                                                                                       |
| Logs                                                                                                                            | `design.ai_proposal_created/deleted/malformed`, `ai_intake.in_flight/user_limit`: tenant id, proposal id, counts only; never text (malformed reasons are not logged; the request-body middleware logs no bodies)                                                                                                                                                                                                                                                                                                                                 | Log retention                                                                               | n/a (no text)                                                                                           |
| Error responses                                                                                                                 | Codes and English messages only; the description is never echoed (tested)                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | —                                                                                           | —                                                                                                       |
| Consent                                                                                                                         | `design_ai_proposals.ai_consent_at`, `ai_consent_by_user_id`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | As the proposal                                                                             | With the proposal                                                                                       |
| Counts                                                                                                                          | `ai_intake_usage` (calls, lease), `model_usage` (tenant)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Indefinite (one row per account / per tenant-month)                                         | Not text                                                                                                |
| Database backups                                                                                                                | Every table above                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Backup retention (infrastructure)                                                           | **No**: a purge or delete does not reach backups until they age out                                     |

### 7.3 What is implemented (S3)

- **Consent.** `POST …/ai-proposals` requires `ai_consent: true` (the user
  acknowledges that the typed description and the editor's board names are
  sent to the AI provider). Without it: **422 `ai_consent_required`**,
  checked before anything else that costs (nothing is counted, charged or
  sent). Recorded as `ai_consent_at` (= `created_at`) and
  `ai_consent_by_user_id` on the proposal, returned on the proposal. The
  page shows an unticked checkbox; the request button stays disabled until
  it is ticked. Proposals created before this change have null consent
  columns. Consent is not part of `proposal_sha256` or the revision's `ai`
  block (no hash changes).
- **Delete one's own unused proposal.** `DELETE
…/ai-proposals/{id}` → 204. 404 `ai_proposal_not_found` (absent or another
  tenant's), 403 `ai_proposal_not_yours` (another account's, even the
  tenant owner's), 409 `ai_proposal_used` (applied by a saved revision),
  403 `ai_requires_account` (a trial). Under the row lock, so it cannot race
  a save applying it. The page offers "Delete these proposals" on an
  unused proposal; rows already placed stay as the user's own entries.
- **Retention purge.** Worker job `purge-ai-proposals [--days N]`
  (crontab: daily 04:15 UTC) deletes proposals with `used_at IS NULL` older
  than `AI_INTAKE_UNUSED_RETENTION_DAYS` (default **30**, an open owner
  decision). Spans tenants (`worker/jobs.py`, already allowed). Used
  proposals, including those whose revision was deleted (`used_at` kept),
  are never purged. A save holding the row lock wins: the delete waits and
  re-reads `used_at`.
- Migration `e9d3b7a2c5f4` (revises `d4f8a1c3e6b2`), single head, additive:
  table `ai_intake_usage`; nullable `design_ai_proposals.ai_consent_at`,
  `ai_consent_by_user_id`.
- Deletion is **not** claimed beyond the rows named: provider-side copies,
  backups, downloaded exports and browser drafts are outside it.

### 7.4 Design for a separate change: redactable description in saved revisions

Not implemented. No snapshot, record or approval is rewritten by this change.

- **Today.** A saved revision's `requirements` record embeds
  `ai.proposal.input_text`; `requirements_sha256` covers it; an approval
  binds to the record hash; later revisions carry the block (§6.4). Removing
  the text changes the hash, so the record fails integrity and every
  approval of it stops counting.
- **Proposal.**
  1. New tenant-scoped table `design_ai_inputs (tenant_id, input_sha256 PK
per tenant, input_text NULL, redacted_at, redacted_by, reason)`.
  2. New records (a new `pp-ai-proposal/2`, renderer/record versions
     bumped) keep **only** `input_sha256` (and its length) in the hashed
     record; `input_text` moves to the side table. Quotes (`items[].quote`)
     are also substrings of the description: they would move too, as
     `(start, end)` offsets only, resolved through the side table, or be
     accepted as kept text (owner decision; keeping them leaves fragments
     of the description in the record).
  3. Redaction sets `input_text = NULL` and writes a tombstone (who, when,
     why). The record and its hash are untouched, so integrity checks and
     approvals keep verifying.
  4. Readers (UI, exports) show "description redacted on …" where the text
     is gone; open-item lines print without quotes.
  5. The same applies to other user-entered or description-derived text in
     the hashed record: matched life-safety terms (`subject.term`, printed in
     `[OPEN]` lines) are substrings of the description, and clarification
     reasons (free text up to 500 characters, printed in `[CLARIFIED]`
     lines with the clarifier's display name) are user content. Making them
     redactable means storing them as hashes or offsets in the record, with
     the text in the side table; until then they stay in the record and in
     every export, and a redaction of the description alone would not remove
     them.
  6. Existing `pp-ai-proposal/1` records are left as they are: their text
     stays in the hashed record. Migrating them means re-saving as a new
     revision (a new hash, a new approval), which is the user's act.
- **What an approval still proves after redaction:** that the approved
  record, with that `input_sha256`, is unchanged; that the placements and
  decisions it holds are the ones approved. It **no longer lets anyone
  re-read** what the description said, or re-check quotes against it;
  anyone holding a copy of the text can still prove it was that text
  (hash match).
- **Tradeoff.** Auditability of the source text against the right to remove
  it. A redacted revision cannot be re-reviewed for "did the model misread
  the description"; the review must rely on the decisions and placements
  recorded. A short description is guessable from its hash (low entropy):
  an unsalted hash is not anonymisation; a per-tenant salt/HMAC key
  (destroyed on tenant deletion) mitigates this at the cost of a key to
  manage.
- **Copies that remain whatever we do:** files already exported and
  downloaded (open-item lines with quotes ≤120 characters, matched terms,
  clarification reasons and clarifiers' display names); the provider's
  own retention of the request; database backups until they expire; browser
  session drafts until the tab closes; any external system the user sent an
  export to. Deletion must be described as "removed from PanelPilot's live
  database", never as erasure.

### 7.6 Deletion truth table, removed accounts, deployment limits (M6d, 2026-11-04)

Deletion here always means **removed from PanelPilot's live database**,
never erasure. The page says so in the AI step (`design.ai.deletionNote`,
under the consent box) and on the delete button (`design.ai.discardNote`,
which now names backups and the provider; it previously said "from
PanelPilot" without qualification).

| What                                                                                                        | Deletable?              | How / why not                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------------------------------------------------------------------------------------------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Unused proposal (description, items, decisions, consent)                                                    | **Yes**                 | The author's `DELETE …/ai-proposals/{id}`; the daily `purge-ai-proposals` job after `AI_INTAKE_UNUSED_RETENTION_DAYS`. Not by the tenant owner (403 `ai_proposal_not_yours`, open decision).                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Used proposal (applied by a saved revision)                                                                 | **No**                  | 409 `ai_proposal_used`; never purged, even after its revision is deleted (`used_at` kept).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Description, quotes, decisions, clarifications copied into a saved or approved revision's `requirements.ai` | **No**                  | Inside `requirements_sha256`; approvals bind to it (integrity). Redaction design: §7.4, not built.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Exports already downloaded                                                                                  | **No**                  | Files outside the system (open-item quotes ≤120 chars, matched terms, clarification reasons, display names).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Browser `sessionStorage` draft (`panelpilot.design.draft`, incl. `ai.description`)                          | The user's browser      | Removed on save (the draft is no longer dirty), discard, dismissing the restore offer, trial change and sign-out; gone when the tab closes. Not reachable by the server.                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Application logs                                                                                            | Nothing to delete       | Verified by grep of every logger call in `app/ai`, `design_ai.py`, `ai_intake.py`, `ai_intake_usage.py`: ids and counts only, never description, prompt or output; provider exception messages are fixed strings. **Fixed here:** with `LOG_LEVEL=DEBUG` the OpenAI/Anthropic SDKs and httpx log request options (the full prompt) at DEBUG; `configure_logging` now holds `anthropic`, `openai`, `httpx`, `httpcore` at INFO or above (`test_debug_level_keeps_the_model_sdks_request_bodies_out_of_the_log`). An unhandled 500 logs a traceback (exception text, not request bodies or locals). Log retention is the platform's. |
| Database backups                                                                                            | **No** (not by the app) | Outside the app; their retention is **not known** to this codebase. A delete or purge reaches them only when they age out.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Provider-side copies                                                                                        | **No** (not by us)      | **Unverified; no zero-data-retention is claimed.** What is sent: `SYSTEM_PROMPT`, one user message (board names + description), model id, `max_tokens`, the tool schema, `tool_choice` (asserted). No retention flag is configured: the Anthropic call sets none; the OpenAI path (`LLM_PROVIDER` defaults to `openai`) maps to Chat Completions and sends no `store`, `user` or `metadata` field. What each provider retains is under its API terms and the deployment's agreement, not visible here.                                                                                                                             |

**A removed (deactivated) account** — `test_ai_intake_removed_account_db.py`,
through the real `team.remove` and the production liveness check
(`auth.resolve_caller`, run by `get_current_user` on every request):

- Every AI intake call, read, decision and delete by it is **401** at
  authentication: nothing is counted, charged or sent. An unexpired token
  does not help. No code change was needed.
- A call already in flight when it is removed reaches the provider (it was
  authorised and counted at its start) but **stores nothing**: before the
  proposal is written, `create_proposal` re-reads the account and its tenant
  under a share lock (the `auth.resolve_caller` rule: account active, in that
  tenant, tenant active) and answers **401** if either is gone. The call stays
  counted for the account and charged to the tenant (it reached the
  provider); the lease is released by the call's `finally` (or lapses at
  `lease_expires_at`). The lease is per `(tenant, account)` and holds up no
  other account (`test_an_account_removed_while_the_provider_answers_stores_nothing`).
- Its unused proposals **stay** (visible to the tenant's members, deletable
  by nobody but the retention purge, which removes them on schedule); its
  used proposals stay; its decisions and clarifications in saved revisions
  stay byte-identical (asserted).
- Its `ai_intake_usage` row stays (counts only).

**Deployment limits, not engineering decisions.**
`AI_INTAKE_CALLS_PER_USER_PER_MONTH` (100) and
`AI_INTAKE_UNUSED_RETENTION_DAYS` (30) are deployment settings. There is no
owner-configurable, audited tenant-settings mechanism to hang a per-tenant
override on: `company_settings` is the engineering design profile (strictly
validated by `profile.load_profile`, plan-gated by `Feature.COMPANY_SETTINGS`,
editable by owner or admin, recording only the last saver), and `billing.Plan`
holds only the pooled tenant allowance. Adding one is not a small additive
change (table or JSON field, endpoint, permission, audit trail, UI), so it is
**recommended, not built**: a tenant-scoped `ai_intake_policy` (nullable
`calls_per_user_per_month`, `unused_retention_days`), owner-only, each value
validated server-side as `1 ≤ value ≤ deployment ceiling` (lower only,
never raise), every change appended to an audit row (who, when, old, new);
`acquire` would use `min(deployment, tenant)`, the purge
`min(deployment, tenant)` days per tenant. No pricing is implied.

### 7.5 Open owner decisions (S2/S3)

- The per-account default (100/month) and whether it varies by plan or role.
- The unused-proposal retention (30 days).
- Whether to build a per-tenant, owner-lowerable cap and retention (§7.6).
- The backup retention period, so the deletion note can name it.
- Whether a tenant owner (not only the author) may delete a member's unused
  proposal; whether account deletion should delete or anonymise that
  account's unused proposals (and add the foreign key, §6.8).
- Provider-side retention: whether to require a zero-data-retention
  arrangement with the provider, and to state it in the consent text.
- Whether to build §7.4, and whether quotes move with the text.
