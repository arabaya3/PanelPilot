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
