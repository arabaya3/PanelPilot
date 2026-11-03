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
