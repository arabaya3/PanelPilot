# Phase 1 contract: trustworthy export and approval

Working agreement for the three Phase 1 agents. The lead owns this file; ask the lead before changing it.

- **Branch:** `feat/phase1-trustworthy-export`
- **Base:** PR #199, `fix/redesign-e2e-verification` at `73ce76c`, chosen because #199 already changes the design screen's stale-result handling.
- **Rebased baseline (2026-10-23):** `f27b01a` = #199 + #202 (dependency advisories: Pillow 13.0.0, certifi 2026.10.19, opentelemetry-api 1.45.1, black 26.10.1, next 15.5.28) + checkpoint `bef3581` of the Phase 1 work so far. The compose `api` and `worker` images were rebuilt from it, and the dev database is migrated to `b4e7a9c2d1f6`.

## Findings reproduced on the base (`repro.cjs base` → `repro-base.json`)

1. **Tampered ratings.** A 200 A breaker sent from the browser is exported as 200 A (HTTP 200).
2. **Forged approval.** Approver and checker names supplied by the browser print in both the drawing PDF and the calculations PDF, and the calculations PDF then drops "NOT APPROVED".
3. **Unknown fields.** `standby_supply`, `transfer_switch` and the load-level `colour` are accepted (HTTP 200) and silently dropped.
4. **Validation.** `export_design` gates nothing. Note codes carry no severity, so none of them can block.

## Ownership

These rules keep the agents from editing the same files.

| Area | Owner |
|---|---|
| `apps/api/**`, except the test files listed for the adversarial agent | **Backend agent** |
| `alembic/versions/*` | Backend agent |
| `packages/shared-types/**`, regenerated only through the repository's generator | Backend agent |
| `apps/web/**` | **Frontend agent** |
| `apps/api/app/tests/api/v1/routes/test_export_integrity.py` (new file) | **Adversarial agent** |
| `apps/api/app/tests/domain/test_export_integrity_db.py` (new file) | Adversarial agent |
| `docs/design-readiness/phase-1/adversarial/**` | Adversarial agent |
| Everything else in `docs/design-readiness/` | Lead |

Rules for every agent:
- **Nobody commits, pushes, rebases or switches branches.** The lead does all git work.
- Do not touch files another agent owns. If you need a change in one, ask the lead.

## Data model (backend)

Add nullable columns to `design_project_revisions` in a new Alembic migration, so older rows stay valid:

| Column | Type | Meaning |
|---|---|---|
| `design_snapshot` | JSONB | The `BoardDesignResponse.project` the **server** built from `request` when the revision was saved |
| `snapshot_sha256` | String(64) | SHA-256 of the canonical JSON of `design_snapshot`: sorted keys, no whitespace, UTF-8 |
| `profile_sha256` | String(64) | SHA-256 of the canonical company profile used for the design |
| `rules_version` | String(40) | `app.design.RULES_VERSION`, a constant bumped whenever calculation or rule tables change |
| `catalogue_version` | String(40) | Currently `"none"`, because no catalogue is wired into the design |
| `findings` | JSONB | The structured findings (below) recorded at save time |
| `approved_snapshot_sha256` | String(64) | Set at approval; must equal `snapshot_sha256` |

Behaviour:
- **Saving a revision** designs from the request on the server and stores everything above. A client-sent design is never stored as the snapshot.
- **Approving** sets the existing approver fields and `approved_snapshot_sha256` in the same transaction, with the revision row locked (`SELECT … FOR UPDATE`).
- **Approval is refused** with `revision_not_validated` for a legacy revision that has no snapshot (it must be saved again), and with `revision_has_blocking_findings` when the revision has blocking findings.
- **Older revisions** keep their `request`. Opening one still works. Exporting one at revision level needs a snapshot, so it is either re-saved as a new revision or refused with `revision_not_validated`.

## Findings (backend)

`ValidationFinding`:
- `code`: stable identifier
- `severity`: `"blocking" | "warning" | "info"`
- `board_id`, `circuit_id`, `device_id`: optional
- `values`: the relevant values
- `message`: user-facing text in English; the web app translates it by code
- `requires_engineering_confirmation`: bool
- `source`: the rule's source, or `"policy"`

Policy rules:
- **Severity comes from a documented policy table**, `app/design/findings_policy.py`, keyed by the existing `DesignNote` codes.
- **Thresholds come from existing sourced rules.** Do not invent electrical thresholds.
- **A note code the policy does not classify** gets `requires_engineering_confirmation=true` and severity `warning`, and an execution-ready claim stays impossible.
- **Integrity checks run on any project being exported**, using existing sourced sizing functions. At minimum: the protective device rating is not above the cable's sourced current-carrying capacity for that circuit, and the device and cable referenced by each circuit exist. The tampered 200 A on 1.5 mm² case must produce a `blocking` finding.

## API (backend; frontend and adversarial agents code against this)

| Endpoint | Change |
|---|---|
| `POST /api/v1/design/export` (existing) | **Draft only** (below) |
| `POST /api/v1/design/projects/{project_id}/revisions/{number}/export` (**new**) | Revision export (below) |
| `POST /api/v1/design/projects/{project_id}/revisions/{number}/approval` (existing) | Behaves as described under **Data model** above |
| `POST /api/v1/design/projects/{project_id}/revisions` and `POST /api/v1/design/projects` | Persist the snapshot, versions and findings, and return the findings |
| `GET` of a saved project/revision | Exposes `findings`, `snapshot_sha256`, `rules_version`, `approved_snapshot_sha256`, `validated` (bool), and `export_status` |

### Draft export (`POST /api/v1/design/export`)

Body: `{ project, profile?, format, pricing? }`. The existing shape is kept so existing workflows work.

- The output is **always** marked `DRAFT — NOT FOR EXECUTION` in every format that can carry a mark (PDF: every page; calculations PDF; DXF: a text entity; CSV: a first comment line or a column; other formats as their structure allows). The lead must sign off any format that cannot carry a mark.
- Revision rows sent by the client are kept as drawing metadata, **except** `checked_by` and `approved_by`, which are blanked. No approval text is ever printed on a draft.
- The draft carries a findings section listing the unresolved findings, from the integrity checks run on the project that was sent.
- Strict schema: unknown fields return a 422 with a precise location.

### Revision export (`POST /api/v1/design/projects/{project_id}/revisions/{number}/export`)

Body: `{ format, pricing?, purpose: "review" | "execution" }`. The default `purpose` is `"review"`.

- **Authorization:** the caller is authenticated and the project belongs to the caller's tenant; otherwise 404 `revision_not_found`.
- **Source of truth:** the server loads `design_snapshot`, recomputes its hash and compares it with `snapshot_sha256` (`snapshot_integrity_failed`). It also compares the current `RULES_VERSION` and the current company profile hash with those recorded; a mismatch returns 409 `revision_stale` with what changed. It **never** recalculates silently and presents the result as the same revision.
- **Approval identity:** read only from the database row, and valid only when `approved_snapshot_sha256 == snapshot_sha256`.
- **Labelling:**
  - an unapproved revision prints `DRAFT — NOT FOR EXECUTION` with the revision identity;
  - an approved revision prints `REVIEW APPROVED — STUDY, NOT FOR EXECUTION`, with the approver, approval date and revision from the database, the snapshot hash (short) and the rules version.
- **Execution purpose:** `purpose: "execution"` returns 409 `execution_package_incomplete`, because no package-completeness check exists. The response names what is missing (wiring and terminal connectivity, control schematics, orderable parts, mechanical layout) and also includes any blocking findings or missing approval. It **never** returns a file.
- **All export formats** share this path: PDF, calculations PDF, DXF, QET, AML, every CSV, the quotation PDF/CSV, the project JSON and the PLC program.

### Errors

Errors use the existing envelope `{error, detail, code, params}`, so the frontend can translate them by code.

## Frontend

- Keep the graphite/gold design.
- Distinguish **Saved → Validated (findings) → Review approved → Execution ready (always blocked for now, with reasons)**.
- Show the findings with where to fix each one: board, circuit or device.
- An export that has not been saved and validated is a draft export, and the UI says so.
- Revision export goes through the new endpoint.
- Keep entered data on failure.
- After an edit, mark results and approval eligibility as stale (building on #199's input-signature check).
- Handle older revisions with no snapshot: show "save again to validate".
- Show the 422 for unknown fields as an actionable message.
- New text in English, Arabic and Hebrew.
- Never mirror drawings in right-to-left languages.

## Verification

- **Disposable database:** `panelpilot_phase1_test` on the compose Postgres, migrated with `alembic upgrade head`.
- **How to run the tests:** this exact environment, which is verified to work:
  ```
  cd "/c/Users/UnclePC/Desktop/Snorkel- proj/PanelPilot"
  E='env -i PATH=/opt/venv/bin:/usr/bin:/bin HOME=/tmp ENVIRONMENT=dev DATABASE_URL=postgresql+psycopg://panelpilot:panelpilot@postgres:5432/panelpilot_phase1_test TEST_DATABASE_URL=postgresql+psycopg://panelpilot:panelpilot@postgres:5432/panelpilot_phase1_test OPENSEARCH_URL=http://127.0.0.1:9 REDIS_URL=redis://127.0.0.1:9/0 JWT_SECRET=phase1-disposable-test-secret-not-real-0123456789abcdef OPENAI_API_KEY=test-key ANTHROPIC_API_KEY=test-key'
  docker compose run --rm --no-deps -T --entrypoint sh api -c "$E alembic upgrade head"     # after adding a migration
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -T -v "$(pwd -W)/apps/web/src/messages:/web/src/messages:ro" --entrypoint sh api -c "$E pytest -p no:cacheprovider -o addopts= -rs <paths>"
  ```
  Baseline on `73ce76c`: `test_design_projects.py` plus `routes/test_design.py` gave 26 passed, 0 skipped.
  The `-v …/messages` mount is required: several design tests read the web message catalogues at `/web/src/messages`, as CI's full checkout provides.
- Under Git Bash, `MSYS_NO_PATHCONV=1` and `$(pwd -W)` are required, or the mount path is rewritten and `/web/src/messages` is empty.
- Each agent uses its **own** disposable database (`panelpilot_phase1_backend`, `panelpilot_phase1_test` for the adversarial agent), because the `TRUNCATE` fixtures deadlock when two runs share one.
- **Never** run tests against the dev database `panelpilot` or through `docker compose exec api`.
- Database-backed approval, locking and tenant tests must **run, not be skipped**. Report any skip.
