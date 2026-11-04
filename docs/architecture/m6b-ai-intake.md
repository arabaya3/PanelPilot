# M6b — AI-assisted requirements intake (contract)

Status: integration-owner contract, 2026-11-04. Builds on M6a
(`m6a-guided-intake.md`). Draft; not for merge or deployment.

The model reads a designer's plain-language description and **proposes**
entries for the existing guided requirements step. It designs nothing. Only
what the designer confirms reaches the request, and only the request reaches
the deterministic designer. There is no second design or control model.

## 1. What may be proposed

Only what `ProjectDesignRequest` and the `pp-req/1` control model already hold:

| Item kind | Fields it may set |
|---|---|
| `board` | `name`; `fed_from` (another proposed or existing board's name) |
| `load` | `board` (a board name), `description`, `kind` (the load kinds the editor offers; never `sub_board`), and, **only where the text states them**: `power_kw`, `phases`, `power_factor`, `length_m`, `controlled` |
| `control_need` | `load` (a proposed load's item id), `need` (one of the seven M6a needs); implies `controlled: true` on that load |
| `question` | `text`, `about` (an item id or `null`), `reason`: `missing`, `ambiguous` or `contradictory` |
| `unsupported` | `text`, `reason`: what the schema cannot hold (a product or part number, a cable spec, a connection between boards, fire-pump or life-safety equipment, a standard edition, …) |

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
