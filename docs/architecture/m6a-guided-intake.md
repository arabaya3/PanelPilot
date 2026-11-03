# M6a — guided intake, server-owned feeders and a versioned requirements record

Status: contract for the first implementation slice. Review-only scope; no AI, no new
electrical rules, no execution output.

## 1. Server-owned feeders

A sub-board's feeder is generated exactly once, by the project designer, from the
sub-board's `fed_from`. The client cannot express it any other way.

- A load with `feeds` set is refused: HTTP 422, code `feeder_server_owned`,
  params `{board, load_index}`.
- A load of kind `sub_board` is refused for new designs: HTTP 422, code
  `sub_board_load_server_owned`, params `{board, load_index}`. The message tells
  the user to add the sub-board and set "Fed from" instead.
- `fed_from` naming an unknown board, the board itself, or forming a cycle keeps
  its existing refusals (verify them; add codes if missing).
- Historical revisions are never rewritten. A stored revision whose request holds
  such a row still opens from its stored snapshot. Re-designing it (a new
  revision) is refused with the codes above until the user removes the row.

## 2. Requirements record `pp-req/1`

Stored with each revision as `requirements` (JSON, hashed into the revision's
integrity like `request`). Absent on older revisions; the API then returns a
_derived_ record marked `"derived": true`, which never counts as acknowledged.

```json
{
  "schema": "pp-req/1",
  "intake": "guided",              // "guided" | "table" | "import" (later: "ai")
  "boards": [{"ref": "b1", "name": "MDB", "fed_from_ref": null}],
  "loads": [{"ref": "b1.l1", "board_ref": "b1", "description": "...",
             "source": "typed"}],
  "control": [{"ref": "b2.c1", "board_ref": "b2", "load_ref": "b2.l3",
               "needs": ["start_stop", "running_indicator"],
               "status": "not_designed"}],
  "questions": [{"id": "q-<stable>", "kind": "missing_value|assumption",
                 "subject": {"board_ref": "b1", "load_ref": "b1.l2"},
                 "field": "power_factor", "finding_code": "default_power_factor",
                 "state": "open|answered|acknowledged",
                 "answer": null,
                 "by_user_id": null, "at": null}],
  "open_items": [{"code": "control_not_designed|inter_board_wiring_not_modelled",
                  "subject": {...}}],
  "trace": [{"requirement_ref": "b1.l1", "design_ref": "MDB/c1"}]
}
```

- `ref`s are assigned by the server, stable across edits of the same project.
- Questions are **derived by the server** from (a) required inputs the user left
  empty (fault level, Ze, feeder length, motor nameplate current) and (b) findings
  the designer already raises for assumptions (e.g. `default_power_factor`). The
  designer's behaviour is unchanged: the 0.9 default still applies; the question
  makes it visible.
- **Answer** = the user supplies the value; it flows into the request as typed
  input. **Acknowledge** = the user accepts the stated assumption. Both are
  recorded server-side with `by_user_id` and `at` (server clock); the client
  never supplies who or when.
- Acknowledged ≠ validated. Exports print acknowledged assumptions as
  "Assumption acknowledged by <user> on <date> — not engineer-validated".
- Control needs are recorded as requirements only. The existing `controlled`
  flag remains the only control model in the designer. Each control need yields
  an open item `control_not_designed`.
- Each sub-board feeder yields an open item `inter_board_wiring_not_modelled`.
- `trace` links every load and generated feeder to the design element.
- Extension point: `intake: "ai"` reserved; AI would propose loads/questions into
  this structure with `source: "ai_proposed"` and require user confirmation. Not
  implemented.

## 3. API

- `POST /design/projects/requirements/preview` — body: guided input
  (boards, loads, control needs, answers). Returns the requirements record with
  derived questions and open items, plus the design preview. Saves nothing.
- `POST /design/projects` and `POST /design/projects/{id}/revisions` accept an
  optional `requirements_input`; the server builds and stores the record.
- `POST /design/projects/{id}/revisions/{n}/questions/{qid}` — body
  `{"action": "answer", "value": ...}` or `{"action": "acknowledge"}`. Answering
  creates a new revision (inputs changed); acknowledging is recorded against the
  revision's record without changing its design (append-only acknowledgements).
- Permission: the existing edit permission for answers; acknowledgement requires
  the same; tenant-scoped.

## 4. Integrity and security

- Self-approval: a revision cannot be approved by its author (`author` email or
  user id). Refused with 403 `approval_by_author`. Older revisions without a
  recorded author: approval allowed only with an explicit server-side finding
  `author_unknown` shown on the approval, never inventing an author.
- Duplicate printed designations within a board (v1 and v2) raise a blocking
  finding `designation_duplicate`; approval is refused while it stands; review
  exports carry it.
- Renderer/approval policy: any change to exported document content bumps the
  affected renderer version; report which approvals become stale.

## 5. As built (backend, M6a) — decisions and contract changes

Integration-owner decisions (2026-11-03) and what the backend implements.
Where this section and §1–4 differ, this section holds.

### 5.1 Feeders

- Enforced in the design and save paths, **not** in the pydantic model, so a
  stored request holding such a row still parses: the revision stays
  `validated`, its approval keeps counting, and it opens from its stored
  snapshot unchanged. Refused with 422 on `POST /design/project`,
  `POST /design/distribution-board`, `POST /design/projects`,
  `POST /design/projects/{id}/revisions`, the requirements preview, an answer
  (a new revision), and `POST …/configuration/validate` (latest request).
  Nothing is saved when refused.
- `feeds` is refused on a load of any kind (`feeder_server_owned`), checked
  before `sub_board_load_server_owned`. The designer itself also refuses a
  row whose `feeds` names a board of the project, so no path designs two
  feeders to one board.
- `params.load_index` is the 0-based index in that board's `loads`. Like every
  error `params` value in this API (`app.core.errors`), it is sent as a
  **string** (`"1"`), not a number.
- Feeding-graph refusals were verified unchanged: `board_name_twice`,
  `board_feeds_itself`, `board_supply_unknown`, `boards_loop` (each names
  `params.board`).
- Effect on re-design: a revision whose request holds a typed feeder row is
  re-saved only after the user removes the row and sets "Fed from"; the
  stored revision is never rewritten. A sub-board of a single-board design
  that used to be a `sub_board` load is now added as a board with "Fed from".

### 5.2 Requirements record

- Stored column `requirements` (JSONB) plus `requirements_sha256`
  (migration `a6c2e9f4b8d3`, single head). The hash is checked wherever the
  snapshot hash is: a mismatch is `snapshot_integrity_failed`,
  `reason: requirements_mismatch` (opened as not validated; approval and
  export refused). A NULL record reads as a derived one.
- Every new revision stores a record. Without `requirements_input` the record
  has `intake` from the previous revision's record, else `"table"`.
- `requirements_input = {"intake": "guided"|"table"|"import", "control":
[{"board_index", "load_index", "needs": [...]}]}` (strict; `"ai"` is
  refused). `needs` ⊆ `start_stop, running_indicator, fault_indicator,
hand_off_auto, remote_control, interlock, timed`, each once. Refusals (422):
  `control_load_unknown`, `control_need_requires_controlled` (the load's
  existing `controlled` flag must be set; it is the only control model),
  `control_given_twice`.
- Every `controlled` load is a `control` entry (`needs` empty unless given)
  and a `control_not_designed` open item. Needs carry to the next revision
  when not re-sent.
- Record additions to §2: `derived` (bool), `feeders` (generated feeders,
  `ref` `<board_ref>.feeder`, `source: "generated"`), `acknowledgements`
  (append-only `{question_id, by_user_id, by, at}`), and on each question
  `answerable` and `by_name` (the account's display name, never its email; empty
  when it has none). `subject` may carry `control_ref`/`supply_board_ref`.
- `trace`: each load `b<n>.l<m>` → `<board name>/c<index+1>`; each feeder →
  the supply board's circuit feeding it; `design_ref: null` where the design
  was refused.
- Questions (ids `q-<16 hex>`, a digest of kind, field, finding code, board
  name and load ref, so stable across revisions):
  - `missing_value`, answerable: `fault_level_ka` and `ze_ohm` on a board
    with no "Fed from" (`finding_code` `no_fault_level` / `earth_fault_no_ze`
    when raised), `feeder_length_m` on a sub-board. `ze_ohm` is written into
    `supply.earth_loop_ohm`.
  - `assumption`, answerable: `power_factor` per non-motor load without one,
    when the board raises `default_power_factor`; the answer sets that load's
    `power_factor`.
  - `assumption`, acknowledge only: `motor_current_a` per motor
    whose current came from `motor_current_table` or `motor_current_formula`
    (no input holds a nameplate current); and per board `fault_level_inherited`,
    `demand_factors`, `rules_unconfirmed`, `contactor_ac1` (`field: null`).
- `GET /design/projects/{id}` includes `requirements` (stored, or derived
  with `derived: true` for a revision saved before M6a; `null` if its request
  no longer validates or the stored record fails its hash).

### 5.3 Endpoints as built

- `POST /design/projects/requirements/preview` — body `{request,
requirements_input}`; 200 `{requirements, design: {project, profile},
findings}`. Saves nothing, reads no project.
- `POST /design/projects/{id}/revisions/{n}/questions/{qid}` — strict body
  `{"action": "answer", "value": <number>}` or `{"action": "acknowledge"}`;
  any other field (`by_user_id`, `at`) is 422.
  - answer → **201** with the project at its new revision (note
    `Answered: <field>`). Only on the latest revision (409
    `revision_not_latest`); 409 `question_not_answerable`,
    `question_answered`; 422 `answer_not_valid` (e.g. power factor > 1).
    Acknowledgements whose question still stands carry to the new revision.
  - acknowledge → **200** with the updated record; the design, snapshot hash
    and revision count are unchanged. 409 `requirements_derived` on a derived
    record (save a new revision first); 409 `revision_already_approved` on an
    approved revision (what was approved does not change); 403
    `acknowledgement_requires_account` for a trial. Acknowledging twice is a
    no-op.
  - 404 `revision_not_found` (also another tenant's project) and
    `question_not_found`.
- Permission: any member who may save a revision (no new permission);
  tenant-scoped as every project route.

### 5.4 Approval

- Self-approval is strict: `approval_by_author` (403) when the revision's
  recorded author (the saving account's email, compared case-insensitively)
  is the approver, owners included. **Product consequence:** a single-person
  tenant cannot approve; it needs a second member with approve permission
  (owner, admin or reviewer).
- A revision with no recorded author approves; `author_unknown: true` is on
  the opened revision and on each `RevisionSummary`, and no author is
  invented.

### 5.5 Exports

- Each acknowledged question of a stored record prints on every review
  export's findings sheet as an info line: "Assumption acknowledged by <name>
  on <date> — not engineer-validated: <finding or field> (<board>, <load>)"
  (code `assumption_acknowledged`, source `requirements`).
- `designation_duplicate` (blocking) prints like every finding; a v1
  revision prints its own (only the v2 structural one is v2-only).

### 5.6 Versions and approvals

- Renderers bumped: v1 `2026.10.31.4` → `2026.11.03.1`, v2
  `2026.11.02.v2-11` → `2026.11.03.v2-13` (acknowledged-assumption lines; v1
  prints its own `designation_duplicate`). Every approval recorded under the
  old versions stops counting and is granted again (by a non-author).
- `RULES_VERSION` bumped `2026.10.23.1` → `2026.11.03.1` (owner decision,
  2026-11-03): `designation_duplicate` changes what the findings checks can
  produce, and under the M2 cause rule an unchanged version would make an
  affected stored v2 row read as `snapshot_integrity_failed`
  (`findings_mismatch`), an integrity failure that looks like tampering,
  instead of stale. Impact: **every revision saved before this change reads
  as `stale`** (`changed: rules_version`); its exports are refused
  `revision_stale` and its approval, if any, stops counting (`rules_version`
  as well as `renderer_version`). The remedy is to save it again as a new
  revision and have a member other than its author approve it. **No stored
  row is rewritten**: snapshots, hashes, findings and approvals stay exactly
  as recorded. The findings fingerprints were re-recorded
  (`RECORD_FINDINGS_FINGERPRINTS=1`; no case's digest changed, only the
  version key) and the compat fixtures regenerated with
  `python -m app.tests.design.model.fixtures.generate` (only their
  `rules_version` changed).
- The v1 renderer bump is required by the guard although every reference
  export is byte-identical: the renderer has new output paths that the
  reference exports do not exercise (acknowledged-assumption lines on every
  review export's findings sheet, and a v1 revision's own
  `designation_duplicate`). A file printed by this renderer can therefore say
  what the previous one could not, so it carries a new version. The v2 bump to `v2-13` also covers the v2 connection drawing and schedules,
  whose headers print the rules version and so changed with it.
  Every approval on this unmerged stack (and any recorded under
  `2026.10.31.4` / `2026.11.02.v2-11`) is stale and is granted again.

### 5.7 Coordinator shape decisions (second round)

- Questions carry `by_name` (display name of `by_user_id`, server-resolved;
  empty when the account has none), not `by`.
- Motor current question: `field: "motor_current_a"`, `kind: "assumption"`,
  subject the load, acknowledge only.
- Acknowledge returns `{"requirements": <record>}` (200); answer returns the
  `SavedProject` (201).
- Preview returns `{requirements, design}` plus `findings` (the design's
  findings, as a revision would store them).
- `SavedProject.is_author` (the signed-in caller saved this revision;
  compared by the recorded account email, case-insensitive — revisions record
  no author user id) and `SavedProject.author_unknown`; each
  `RevisionSummary` has `author_unknown`.
- `params` values are strings (`"load_index": "1"`), as for every coded error.
