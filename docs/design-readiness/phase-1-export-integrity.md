# Phase 1: trustworthy export and approval

Status: **ready for review, not merged, not deployed.** Phase 2 (terminal
connectivity, soft-starter control, mechanical layout) has not started.

|             |                                                                                                                                                          |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Branch      | `feat/phase1-trustworthy-export`                                                                                                                         |
| Base        | `daeb143`: a merge of #199 (`73ce76c`) and #202 (`6a96b74`), with no other changes                                                                       |
| Tested head | `3837972` (code and evidence); the commit after it changes only this report                                                                              |
| Depends on  | **#199** (design-screen stale-result handling) and **#202** (dependency advisories; supersedes #200 and #201). Retarget to `main` once both have merged. |
| Contract    | [phase-1/contract.md](phase-1/contract.md)                                                                                                               |

## What was wrong (reproduced on the base)

`node phase-1/repro.cjs base` against the base gave these results:

1. **Tampered ratings.** A 200 A breaker the browser sent on a 1.5 mm² circuit was exported as 200 A.
2. **Forged approval.** Approver and checker names typed in the browser were printed in the drawing and calculations PDFs. The calculations PDF then dropped "NOT APPROVED".
3. **Unknown fields.** Fields the API does not know (`standby_supply`, `transfer_switch`, a load's `colour`) were accepted and silently dropped.
4. **Nothing gated an export.** Findings had no severity, so nothing could block.

## What a revision is now

- **The server designs a saved revision itself.** Saving or revising designs the request on the server and stores, on the revision row:
  - the resulting design as `design_snapshot`;
  - its canonical SHA-256 (`snapshot_sha256`, computed over sorted keys and compact UTF-8, with floats refused);
  - the profile hash, the rules version, the catalogue version (`none`) and the structured findings.
- **Nothing a client sends becomes the snapshot.** The title-block revision rows the client sends are stripped before the server designs.
- **Approval is owned by the database.** Approving locks the project row, then the revision row (`SELECT … FOR UPDATE`, the same order a save and a delete use). It refuses a revision that is approved already, unvalidated, altered, stale or blocked, and records `approved_snapshot_sha256`. An approval counts only while that hash equals the snapshot's.
- **Revision export** (`POST /design/projects/{id}/revisions/{n}/export`) loads the snapshot. It then:
  - recomputes the hash (`snapshot_integrity_failed`);
  - checks the rules, profile, catalogue and findings policy (`revision_stale`, naming what changed);
  - reads the approval only from the database row, never from the request. The approver shown and printed is the approving account (its display name, or "a team member"), never a typed name; see "Approver identity and privacy".

  It never recalculates. Another tenant's project, or an unknown one, is `revision_not_found`, the same answer for both.

- **Execution is always refused.** `purpose: "execution"` is always 409 `execution_package_incomplete`, approved or not. The refusal names what is missing: wiring and terminal connectivity, control schematics, orderable parts and mechanical layout. **Approval is not execution readiness**, and no file is returned.
- **Draft export** (`POST /design/export`) keeps the old body for unsaved work. It is always marked `DRAFT — NOT FOR EXECUTION`, blanks `checked_by` and `approved_by`, and lists the integrity findings of the project it was sent.
- **Validation is strict.** Every request model, the company profile and its nested circuit rules included, refuses unknown fields. Every 422 uses the error envelope:

  ```json
  {
    "error": "RequestValidationError",
    "code": "request_invalid",
    "params": { "count": "1" },
    "detail": [
      {
        "loc": ["body", "request", "boards", 0, "standby_supply"],
        "type": "extra_forbidden",
        "msg": "…"
      }
    ]
  }
  ```

  The input value is stripped from the response, and the page names the field in English, Arabic and Hebrew.

### What a revision freezes, and what stays live policy

| Frozen in the revision (never rewritten)                                   | Live policy, checked on every open, approval and export                                      |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| The request as entered, including the company profile it was designed with | `RULES_VERSION`: calculation and rule tables                                                 |
| The server-built design snapshot and its hash                              | The software's default profile, which the revision's settings inherit (re-hashed: `profile`) |
| The findings recorded at save time                                         | The findings policy and integrity rules, re-run on the frozen snapshot (`findings_policy`)   |
| The approval: who, when, and the hash it covers                            | `CATALOGUE_VERSION`                                                                          |
|                                                                            | That execution export is refused                                                             |

- **Company settings never change a saved revision.** They only pre-fill new designs, because the design reads the profile inside its own request. Changing ordinary company defaults therefore cannot rewrite an approved snapshot, and cannot override the revision's declared engineering profile.
- **A policy change is caught, not silently passed.** Re-running today's findings policy on a revision's frozen snapshot recalculates nothing. If it now classifies a finding differently (say a warning becomes blocking), the revision is `revision_stale` with `findings_policy`. Its snapshot and approval are untouched, and it cannot be exported until it is saved again as a new revision.
- **Lists use a cheaper check.** A project's revision list, and the earlier revisions in an export's title block, are checked from their columns alone, without reading their snapshots (see "Cheap and thorough approval checks" under Settings and approval). The revision opened, approved or exported is always checked in full.

## Export formats

PanelPilot does not claim any warning is tamper-proof: anyone can edit a file after it is downloaded. The question for each format is whether a meaningful DRAFT or review mark **survives normal opening or import**.

| Format                                                                                                 | Available                               | Draft or review identification                                                                                                                                | Evidence                                                                            |
| ------------------------------------------------------------------------------------------------------ | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Drawing PDF                                                                                            | Yes                                     | Red label and identity at the top of every page, a banner at the foot, and a findings sheet                                                                   | 11/11 pages marked (pdfplumber); page renders show the stamp is legible             |
| Calculations PDF                                                                                       | Yes                                     | Stamp on every page and a findings section. An approved revision shows the database approver, the short hash and the rules version; a draft says NOT APPROVED | 3/3 pages marked                                                                    |
| Quotation PDF                                                                                          | Yes                                     | Stamp on every page and a findings section                                                                                                                    | 3/3 pages marked; the overlap of label and identity was found and fixed (see below) |
| DXF                                                                                                    | Yes                                     | 20–22 visible TEXT entities on the visible `TEXT` layer, on every sheet                                                                                       | Opened with ezdxf: 0 audit errors, the layer is on and not frozen                   |
| Project JSON                                                                                           | Yes                                     | An envelope `{export_status: {label, identity}, findings, project}`. An importer has to unwrap it, so the status cannot be skipped by accident                | Parsed                                                                              |
| Every CSV (devices, parts, cables, circuits, terminals, EPLAN, both ACE lists, quotation CSV, PLC I/O) | **No**, 409 `export_format_unprotected` | A comment line is dropped by spreadsheet and ECAD imports, and a status column would change the columns importers map                                         | Refused on both endpoints, before rendering                                         |
| QElectroTech (.qet)                                                                                    | **No**, 409                             | An XML comment is dropped when the project is opened and saved                                                                                                | Refused                                                                             |
| AutomationML (.aml)                                                                                    | **No**, 409                             | Same as QElectroTech                                                                                                                                          | Refused                                                                             |
| PLC program (ST), including `POST /design/plc`                                                         | **No**, 409                             | A comment is ignored by the compiler and dropped by IDE imports, so the program would run unmarked                                                            | Refused; the PLC panel no longer shows copyable source                              |

- **The blocked set lives in one place:** `UNPROTECTED_FORMATS` in `apps/api/app/domain/design.py`, with a test that fails if it drifts from the code.
- **The refusal is structured.** Its params are `{format, reason: "draft_mark_not_persistent", allowed}`, and the UI shows the formats as unavailable with that reason in all three languages.
- **Unblocking a format** needs a representative import shown to keep a visible mark without changing the electrical or program meaning. That is open item 3 below.

## Tests

Run on the tested head from a frozen `git worktree`, never from the live checkout (see the intermittent failure below).

| Check                                                                                                                                                               | Result                                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Backend full suite                                                                                                                                                  | 3777 passed, 0 failed, 60 skipped                                                                                                                                                                                                    |
| DB-backed design, approval, tenancy, locking and concurrency (`test_design_projects`, `test_company_settings`, `test_export_integrity`, `test_export_integrity_db`) | 209 passed ×3 on the review fixes, 210 passed ×2 on `3837972`; **0 skipped**                                                                                                                                                         |
| black / ruff / mypy                                                                                                                                                 | clean / clean / clean (402 files)                                                                                                                                                                                                    |
| Alembic                                                                                                                                                             | `alembic check` reports no drift. Clean database: upgrade, downgrade −1, upgrade. Previous schema `a7d1e3f5b2c8` with a legacy approved revision: upgrade keeps the row, its new columns are null, and its old approval is not bound |
| Web                                                                                                                                                                 | vitest 892/892; typecheck, lint (0 errors) and prettier clean; build exits 0 (it logs next-intl `ENVIRONMENT_FALLBACK` during static generation, from the unchanged locale provider)                                                 |
| Shared types                                                                                                                                                        | regenerated with the OpenAPI dump; byte-identical after the later fixes                                                                                                                                                              |

**Skips, all environmental, none in the required DB tests:**

- 55 need a reachable OpenSearch, which CI provides as a service.
- 4 need the live model or a photo corpus.
- 1 is "the registry holds no formulas", which skips on the base too.

### The five failures from the 3719-pass run

| Failure                                                                              | Root cause                                                                                                                                                                | Resolution                                                                                   |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `test_drawing_text[ar]`, `[he]` and the two `test_notes` catalogue tests             | The test command, not the code. Git Bash rewrote the `-v "$(pwd)/apps/web:/web"` path, so the web message catalogues were never mounted and the tests could not read them | Mount with `MSYS_NO_PATHCONV=1` and a Windows path. All four pass, and no assertions changed |
| `test_calc_report::test_render_calculations_pdf_lists_every_circuit_and_its_figures` | An in-between state. The report's approval line was reworded to "Review approved by {name}: a study, not for execution." before its assertion was updated                 | The assertion was updated to the new contract wording and still checks the approver's name   |

### The intermittent `test_package.py` failure

- **Symptom:** in coverage runs, a few `test_one_table_module_alone_resolves_every_foreign_key[...]` cases failed. They passed when re-run.
- **Root cause:** each case starts a fresh interpreter that imports the table modules from disk, while the main pytest process imported them long before. The failing runs were on the live bind-mounted checkout while other agents were editing `models/tables/*.py`, so a fresh interpreter could read a half-written file.
- **Reproduction:** on a disposable copy of the code, truncating `design_projects.py` for 3 seconds mid-run fails several cases with `Mapper 'DesignRevisionRow' has no property 'project'`. The same run on the restored file passes 11/11.
- **What was ruled out:**
  - Memory pressure: it passes under 400/250/180 MB caps.
  - Coverage in child processes: pytest-cov 7 does not start it there.
  - Load: 1 isolated run plus 5 full coverage runs all passed.
- **Resolution:** no code change, retry, exclusion or coverage reduction. Verification now runs only on a frozen worktree of the commit under test. CI checks out a frozen tree, so it is not exposed.

### Concurrency (independently reviewed)

- **Revisions:** numbered under the project lock. A unique-key collision becomes 409 `revision_conflict`, where it used to be a 500 (already present on the base). Live: 4 parallel saves gave revisions 3, 4, 5 and 6.
- **Approvals:** live, 3 parallel approvals of one revision gave one 200 and two 409 `revision_already_approved`. 8 concurrent approvals in the DB tests give the same result.
- **Delete versus approve:** both lock the project first. A delete waits for an approval in progress and is then refused, and an approval waiting on a delete finds no revision.
- **Unexpected errors:** any exception rolls back at once, so no lock outlives a failed request. A rollback that itself fails is logged and does not mask the original error.
- **Exports racing an approval** never mislabel. A revision written during approved exports keeps revision 1's binding.

## Independent review

A reviewer who wrote none of the code reviewed the full diff twice. Confirmed findings, all fixed with failing-first tests:

1. `POST /design/plc` returned unmarked Structured Text for any project the browser sent, getting round the PLC export refusal. It is now refused with the same code.
2. Deleting a project could race an approval and remove a just-approved revision. Fixed with project-first locking.
3. An approver name of only spaces recorded an approval naming nobody. It is now a 422.
4. Unknown nested profile settings, such as `circuit_rules.lighting.breaker_amps`, were silently dropped. They are now refused with their dotted path.
5. An older revision whose text exceeds the new length limits made opening it return 500. It now opens as stored, marked `not_validated` with a `request_not_valid` finding, and cannot be approved or exported.
6. Locks could outlive a non-domain error. Every path now rolls back.
7. Every read re-ran the full checks for every revision (cost). Only the opened revision is now checked in full.
8. A failed rollback could mask the original error.

Found while inspecting the exports: the PDF stamp drew a long identity over its label, making it unreadable on the calculations and quotation PDFs. The identity now has its own line, and a test reads both lines back.
Found in the browser flow: right after a save, the page said the inputs had changed and held back the draft export until the board was designed again. The save's reply carries the title-block revision rows, and #199's input signature included them. A design now keeps a signature of its calculation inputs, so a save of unchanged inputs stays current, while a real edit still makes it stale. A test fails without the fix. This fix is covered by vitest only; it was not re-checked in the live browser.

## Live verification

**Live attack** (`phase-1/adversarial/attack2.cjs`, disposable local accounts, results in `phase-1/adversarial/final/`):

- Forged and extra fields at every level were refused with 422 `request_invalid`, naming the exact location.
- Revision numbers 0, −1, 2³¹, 1.5, `abc`, `1e3` and blank were refused with 422.
- Fuzzed format names were refused with 422.
- Cross-tenant GET, export, approve, revise and delete all returned 404.
- Injecting approval fields into the approval body was refused with 422.
- A second approval was refused with 409.
- An execution export was refused with 409 both before and after approval.
- A 20 MB body was refused at the connection, with no 500.

**Signed-in browser flow** (`phase-1/browser-final/log.md`, screenshots 01–15, page renders 20-*, downloads). These all passed:

1. Design, save 01, edit, then save 02.
2. The findings show where to fix each one.
3. The readiness steps (Saved → Validated → Review approved → Execution blocked) are shown.
4. An approver of only spaces is refused, with the input kept.
5. The approval was recorded, and the exact revisions reopen, with the approval only on 02.
6. Revision exports downloaded; the blocked formats are shown as unavailable with their reason.
7. Execution export was refused.
8. A draft PDF of an unsaved edit is marked DRAFT.
9. Over-long fields were refused in English, Arabic and Hebrew, naming nested paths, with the data kept.
10. RTL layout does not mirror the drawing.
11. The unknown-field envelope was returned.
12. An unauthenticated request got 401, and a second account got 404 with no data leaked.
13. The tampered 200 A breaker was refused, with "[BLOCKING] DB1 / c2: 200 A protection is above the 17.5 A its 3G1.5 cable carries".
14. The forged approver appears nowhere.
15. The concurrency results above.

**Exports inspected** (pdfplumber and ezdxf in a throwaway container; renders looked at by eye):

- Approved revision: "REVIEW APPROVED — STUDY, NOT FOR EXECUTION" on every page, with the database approver, the hash (matching the row) and rules `2026.10.23.1`.
- Drafts: "REVIEW APPROVED" and "FORGED" absent, DRAFT on every page.
- DXF: 20–22 mark texts on a visible layer.

## Closure round (after the first green CI)

### Signed-in design ownership

- **One shared session.** `/design` now runs under the signed-in account. An app-wide `AccountSessionProvider` holds the token pair in memory only. Home, `/review` and `/design` share it, with a single refresh in flight at a time.
- **Every action uses the account.** Save, reopen, revise, approve and export all use the account's token and tenant.
- **Trial mode is explicit.** A "Trial workspace" notice says saved work belongs to this browser's trial, and offers sign-in or account creation.
- **Trial to account.** Trial work becomes an account's only through the existing server-side signup claim, which needs the trial's secret. The new user joins the trial's tenant, and no rows move. Signing in to an _existing_ account moves nothing: the typed work stays on the page, and saving it creates a new project in that account.
- **Expiry and identity changes:**
  - **401 while signed in:** never falls back to a trial. The page tries one refresh, then shows "Your session has ended" with a sign-in form, and keeps the entered data and the revision note.
  - **A different account signing in:** clears the previous account's unsaved work, with a notice.
  - **Late responses:** results from requests started under another identity are ignored.
- **Sign-out and approver identity:** _superseded by the final round below._ Sign-out now ends the server session, including its access tokens, at once. Approvers are shown by display name, never by email.

### `/design/quotation`

- **No persisted or officially issued quotation exists.** The only issued one is `quotation_pdf` from a saved revision's export. It is built from the database snapshot, hash-checked, approver-from-row, tenant-scoped and marked on every page.
- **Gap found and fixed.** On a revision export, the client's `pricing.cable_length_m` could add metres to cables with no length in the snapshot. It is now dropped, so quantities and devices come only from the snapshot.
- **The client-input estimate stays, labelled.** `POST /design/quotation` returns a `QuotationEstimate`:
  - `status: "provisional_estimate"`, a label, and the integrity findings with a `blocking` flag. A tampered 200 A on 1.5 mm² comes back blocking.
  - The checker and approver are blanked, and there is no revision or approval field.
  - The panel heading is "Provisional price estimate", with a banner in all three languages.
- **Pricing bounds:**
  - values 0…1e9 with 6 decimal places, and NaN and Infinity refused;
  - a 3-letter currency code;
  - cable length 0…10 000 m;
  - at most 20 000 price-list entries.
  - Bad price-list rows and keys over 120 characters are skipped, not truncated.
- **Tests:**
  - tampering: forged ratings, forged fields refused with 422, out-of-bound prices;
  - authorization: 401 without a token, 404 for another tenant's revision;
  - the provisional label, and that pricing cannot change revision quantities.

### Export restrictions and the conditions for lifting them

- **Execution export.** It stays refused unconditionally. A test pins that it is still refused when the list of missing parts is empty, so producing Phase 2's parts does not unlock it by itself.
  - Releasing an execution package needs its own explicit, tested gate:
    - a package-completeness check over every deliverable;
    - an engineering validation recorded against the revision (who, when, and against which snapshot hash);
    - an approval bound to that hash;
    - no blocking findings;
    - no stale rules, profile or policy.
  - Each condition needs a refusal code. Until that gate exists and is reviewed, `execution_package_incomplete` is the only answer.
- **Refused formats.** Each stays refused (409 `export_format_unprotected`, a localized reason in the UI) until its condition is shown with a representative import. None of these makes an editable file tamper-proof; the aim is only that a normal import cannot strip the warning without anyone noticing.

| Format              | Acceptance criteria for re-enabling                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| CSV (all lists)     | A status column with a fixed name and value, `DRAFT — NOT FOR EXECUTION` or the review label, on every data row. It must be shown to import into the target tools (EPLAN device import, the AutoCAD Electrical component and terminal imports, a spreadsheet) with the column kept or mapped, and with no change to the electrical columns' mapping. The import templates must be published with the export. |
| QElectroTech (.qet) | A visible text element on every folio's title block or drawing area, not a comment. It must survive open → save in QElectroTech and be visible when printed.                                                                                                                                                                                                                                                 |
| AutomationML (.aml) | A status attribute on the top-level InstanceHierarchy that a representative AML tool keeps on import and re-export, plus a visible element where the tool renders one.                                                                                                                                                                                                                                       |
| PLC program (ST)    | A program-level guard that refuses to run while the program is a draft (for example a constant checked at start-up, with outputs held safe), as well as the comment. It must compile in the target IDE with the guard intact, and the guard must be reviewed by a controls engineer.                                                                                                                         |

### Development settings inspection

The owner authorized this inspection. It was read-only: one `BEGIN READ ONLY … ROLLBACK` on `panelpilot`, returning key names and counts only, with no values, secrets or customer text. It was not blocked.

- **Company settings:** no rows saved.
- **Revisions:**
  - 39 in total; 32 have a snapshot and 7 predate snapshots.
  - 13 carry a design profile. All 13 use only the top-level keys `key` and `market`, all market values are valid codes, and no nested keys exist. **No profile is broken by the strict nested validation.**
  - 3 have a project name longer than 200 characters. None is approved. They open as stored but read `not_validated` (`request_not_valid`), and cannot be approved or exported until saved again with a shorter name. **Remediation:** shorten the name and save a new revision.
- **Approvals:** 10 approved revisions. 9 are bound to their snapshot. 1 predates Phase 1 and is not bound, so it reads as not approved until it is saved again and re-approved. That is the intended behaviour.
- **Schema:** at `b4e7a9c2d1f6`.

### Changes between `3837972` and `b16639f`

- **`b83bd8d`:** the report only.
- **`b16639f`:** only the two export-integrity test files changed, in how they find their database:
  - **No `TEST_DATABASE_URL`:** they skip with a reason that starts "needs Postgres", as the repository's other DB tests do. That is the unit job. CI's integration job fails the build on any skip with that reason, so in CI they always run.
  - **Locally:** the database-name guard is kept, and it refuses anything not named `panelpilot_phase1_*`. The dev database `panelpilot` therefore cannot be used.
  - **On GitHub Actions** (`GITHUB_ACTIONS=true`): the guard accepts the job's own service-container database, which is created for that job and discarded with it.
  - **Checked locally:** without a database they skip; on the disposable database 173 pass; in CI mode on `panelpilot_ci_sim` 173 pass; the local guard refuses a non-disposable name.
  - **No code path changed.** Neither test file can reach a development or production database: production is never configured in these environments, and the dev database is refused by name.

## Final round: persistent sessions, effective logout, approver privacy

These are the owner's decisions, implemented on the existing authentication architecture.

### Persistent sign-in

- **The refresh session is an HttpOnly cookie.**
  - Over HTTPS it is `__Host-pp_refresh`: `Secure`, `Path=/`, no `Domain`. The `__Host-` prefix blocks cookie-tossing from a sibling subdomain.
  - In dev over plain http it is `pp_refresh` on `Path=/api/v1/auth/session`.
  - Both use `SameSite=Strict`. The cookie is only ever needed by the app's own same-site `fetch`.
  - `Max-Age` is the refresh lifetime (30 days).
  - The server reads it only on `/api/v1/auth/session/{refresh,logout,logout-everywhere}`. The web proxy forwards it only to those three paths, by exact match.
- **Access tokens stay in memory.** Login, signup and refresh return only `{access_token, token_type, expires_in}`. Nothing goes to localStorage or sessionStorage.
- **Reload restores the sign-in.** The root `AccountSessionProvider` refreshes with the cookie before any page starts a trial. The refresh times out after 8 s; on timeout the visitor sees a notice and a sign-in form, and no trial starts silently. Unsaved design work survives the reload through the existing per-account draft, and is offered back.
- **Protection against cross-site requests** (`app/api/csrf.py`) on every cookie endpoint:
  - The Origin must exactly match `WEB_ORIGINS`, or `CORS_ALLOWED_ORIGINS` if that is unset. When Origin is absent, Referer's origin is used. `Origin: null` is refused.
  - The request must also carry the header `X-PanelPilot-CSRF`.
  - A refusal is a 403 with code `csrf_origin_refused` or `csrf_header_missing`.
  - In dev, both loopback web origins are always trusted. Outside dev, only the configured list is, and nothing configured means every refresh is refused.
  - `WEB_ORIGINS` is documented in `.env.example`, and **must be set in every deployment**.
- **Concurrent refreshes:**
  - The account's sessions are locked in id order, so refresh, logout and replay detection cannot deadlock.
  - A refresh token that was just rotated is honoured **once** more within **10 s** (`grace_used_at`). That covers two tabs refreshing at the same moment.
  - A second presentation inside the window, or any presentation after it, is replay. Replay revokes all of the account's sessions.

### Effective logout

- **Server sessions** (`auth_sessions`). Each sign-in creates a session, carried in every access token as the `sid` claim. Each authenticated request checks the session with one primary-key read, and refuses it if it is revoked, unknown, or belongs to another user.
- **Logout** (`/auth/session/logout`, 204, idempotent) revokes the current session and its refresh tokens. Access **and** refresh tokens issued under it are refused at once with 401 `session_ended`. Other sessions stay active.
- **Logout everywhere** (`/auth/session/logout-everywhere`, from `/design`) ends every session of the account.
- **Tokens issued before this change** carry no `sid` and are refused. Anyone signed in at deploy time signs in once more. Trial tokens are unaffected.
- **Tests:**
  - replay of the access token and of the refresh token after logout;
  - a refresh racing a logout (threaded);
  - concurrent refreshes in one session;
  - the once-only grace and the 10 s window;
  - concurrent replays on two sessions with no deadlock;
  - expiry;
  - account switching;
  - one session's logout leaving another alive;
  - logout everywhere;
  - a borrowed `sid`;
  - cookie attributes in both modes;
  - every refusal of a cross-site request.
- **Run against the code before the fixes,** the once-only grace, the 10 s window, the deadlock and the dev-loopback tests fail. The `__Host-` cookie tests cannot import there.

### Approver identity and privacy (final)

- **The approver is the authenticated account, never a typed name.**
  - The approval request has no name field: only an optional `note` of up to 200 characters. A client that sends `approver`, `approved_by`, or any other field gets 422, so nobody can submit someone else's name.
  - Approving needs a real signed-in account. A trial gets 403 `approval_requires_account`, and the approve control shows the reason.
- **What is recorded.** Every approval stores the server-owned audit identity: `approved_account` (the email from the token) and `approved_user_id` (migration `f8c4a2e6d9b3`; no foreign key, so the record survives deleting the account), plus `approval_note`. `approved_snapshot_sha256` is unchanged.
- **What is shown.** The account's verified display name, frozen at approval in `approved_display_name` (see "Settings and approval"), or the neutral fallback "a team member". The same value appears in:
  - the API (`approved_by` / `approver_name`);
  - the UI, as "You are approving as …" before approving and the approver afterwards;
  - the drawing title block, the DXF, the calculations PDF ("Review approved by …"), the export identity line and the JSON.
  - It is never an email.
- **The note** is labelled "Approval note" wherever it appears. It is never in an approver slot.
- **Legacy rows.** The typed `approved_by` of approvals made before this change is kept in the database for audit only. It is never shown or exported. Such rows show the account's name if their `approved_account` resolves in the tenant, otherwise the fallback.
- **`author`** is a display name too, or nothing.
- **Tests,** which fail against the old code:
  - someone else's name is refused with 422;
  - the account's name appears in the API, UI and every export;
  - no "@" anywhere;
  - the fallback;
  - legacy rows;
  - a trial is refused;
  - another tenant gets no approval identity.
- **No role is allowed to see the email.** Any account in the tenant whose plan includes approval may approve; there is no role check beyond that. Whether approval needs a role is an owner decision.

### Trial projects

The rule is unchanged: signing in to an existing account moves nothing, and no transfer feature was added.

- **The user is warned first.** On `/design`, signing in from a trial that has saved projects (or whose projects cannot be listed) asks for confirmation first, with three options: "Sign in anyway", "Create an account instead" (the claim), or "Cancel".
- **Home asks every trial.**
- **The trial stays recoverable.** It is never cleared on sign-in, so "Continue as a trial" after signing out brings its projects back. They are never shown under the account.

### Localization

- **Citations and product names stay whole in RTL.** Under the drawing, citations, product names, order codes, drive codes, MCCB types, devices and cables are each isolated as one left-to-right run, with their characters unchanged. The enclosure note now gets its `product` from the API, so the page and the drawing say it the same way.
- **Clear errors fixed in both locales,** web and drawing, for example the Arabic word for "blocked", "title block", and the Hebrew "review" in the PDFs.
- **Not a native-speaker review.** Every Arabic and Hebrew string in this PR still needs a human native reviewer, especially:
  - the electrical terms (drive, relay, ampacity, terminal connectivity, control schematics, fingerprint);
  - النسخة for revision against المراجعة for review, and the gender agreement;
  - the Arabic dual, few and many forms, and the Hebrew dual forms;
  - the new account, expiry, timeout, trial-sign-in and approver strings;
  - "a study" in "Review approved by {name}: a study, not for execution";
  - the regulation and country names;
  - the imperative register of Hebrew buttons.

### Browser evidence for the final round

The real browser, API and database, on `ec399e9`, at `127.0.0.1:3000` and `localhost:3000` (`phase-1/browser-final-auth/` and `phase-1/browser-final-auth/recheck/`). The flow ran sign in → reload restores the sign-in and the unsaved work → reopen revision 02 → approve → export → sign out → replay. Results:

- The cookie is HttpOnly and SameSite=Strict, and no token is in web storage.
- The approval shows a display name or "a team member", and no "@" appears in any response or export.
- The PDF is marked REVIEW APPROVED on every page, with the approver, hash and rules. Execution and CSV exports are refused.
- After logout, the access token is refused with 401 at once, the refresh fails, and the reload comes back as an explicit trial.
- One session's logout leaves another alive, and sign-out-everywhere ends both.
- Two tabs refreshing at once both stay signed in.
- Signing in from a trial warns first, and the trial stays recoverable.
- A request without the CSRF header gets 403.
- In Arabic, citations and product names under the drawing read as whole left-to-right runs.

### Changes after the last full backend run on `32c6b83`

Every behavioural change after it was covered by its own tests, and the final behavioural commit `ec399e9` was then run in full from a frozen worktree:

- **Backend:** 3918 passed, 0 failed, 60 skipped (environmental).
- **Unit suite with an empty environment:** 3382 passed.
- **DB-backed approval, tenancy, session and concurrency set:** 323 passed ×2.
- **Lint and types:** black, ruff and mypy clean.
- **Migration:** `alembic downgrade -2` / `upgrade head` / `check` clean.
- **Web:** 988/988 ×2; typecheck, lint (0 errors) and prettier clean; build OK.

| Commit    | Change                                                                                                                                            |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `098fe73` | Load schedule regulation localized, Arabic customer label, final browser re-check evidence                                                        |
| `1161c0f` | Lint fix in the market-basis test                                                                                                                 |
| `22ddaf5` | Settings for the estimate's 401 test, so it runs in CI's empty environment; formatted logs                                                        |
| `3d40f10` | Persistent cookie session, server sessions and logout, approver privacy, citation isolation                                                       |
| `ac2cc5b` | mypy annotations in the auth tests                                                                                                                |
| `ec399e9` | Once-only 10 s grace, `__Host-` cookie, ordered session locks, restore timeout, proxy allowlist, dev loopback origins, whole product names in RTL |

### Migration compatibility

| Revision       | Change                                                                                                       | Existing rows                                                          |
| -------------- | ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| `b4e7a9c2d1f6` | Nullable snapshot, hash, versions, findings and approved-hash columns on revisions                           | Kept: legacy revisions open, and are `not_validated` until saved again |
| `c3f8a1d6e2b7` | New `auth_sessions` table; nullable `refresh_tokens.session_id`                                              | Kept: legacy refresh tokens are refused, so sign in again              |
| `e7b2c9d4a1f3` | Nullable `refresh_tokens.grace_used_at` (now unused, kept for rolling deploys)                               | Kept                                                                   |
| `a9d3f6b1c8e4` | Nullable `auth_sessions.token_salt` and `refresh_tokens.generation`                                          | Kept: a legacy session gets its salt at its next rotation              |
| `b2e8d5f7c3a1` | Restores `grace_used_at` where an earlier draft dropped it; revokes all but each session's newest live token | Kept: an old parallel token is revoked; the newest one keeps working   |

- **Every change is additive.** Downgrades drop only the new session data.
- **Contract migration later:** `grace_used_at` is unused and will be dropped by a later contract migration, once no old pod can run.
- **Checks on the final head:** `downgrade -3`, `upgrade head`, and `alembic check` (no drift); clean upgrade from the previous schema.
- **The dev database** is at `b2e8d5f7c3a1`. Readable backups were taken before each step (27 tables each):
  - `backups/20261024-pre-auth-sessions/`
  - `backups/20261024-pre-token-generations/`
  - `backups/20261024-pre-b2e8/`

## Multi-tab sessions and the integrated candidate

### Concurrent tabs (owner requirement: no theft false-positives)

Three tabs refreshing at once used to end every session. That rule is replaced:

- **One refresh per browser.** A browser-wide Web Lock (`pp-refresh`) lets one tab refresh at a time, and it shares the result over a BroadcastChannel. A shared token is adopted only if it is unexpired and matches the tab's user and session, and a sender's future timestamp is ignored.
- **One live refresh token per session.** Values are derived with HMAC under a dedicated key: `REFRESH_TOKEN_KEY`, or HKDF from `JWT_SECRET` with a versioned label.
- **A young live token is not rotated** (`ROTATE_AFTER` = 5 min). The refresh returns the same token with a fresh access token, so a burst mints nothing and a late response cannot put an older cookie back.
- **The immediate predecessor of the live token** gets the same live token back in two cases:
  - within 10 s, by cookie alone;
  - within one access-token lifetime, with a bearer access token of the same session, user and generation (`rgen`).
- **Reload recovery:** a reloading tab first borrows a current access token from other open tabs (at most 300 ms) to use as that proof.
- **Replay scope:** anything else is a replay, and ends **only that session**. Ending every session would let anyone holding one old cookie sign a user out everywhere. "Sign out everywhere" stays the explicit way to end them all.
- **Bounds:**
  - a cookie-only thief gets the 10 s window and only the victim's own live token, never a second one;
  - a party that once held both cookie and access token can follow the session only until the next rotation, after which its stale use is a replay;
  - **the earlier residual edge is closed.** It was a burst at a rotation boundary, an out-of-order answer, every tab closed or reloaded, and more than 10 s. The final independent review traced it to the "keep" answer for a young token re-setting the cookie. A late keep answer could therefore move the jar back after a rotation. A keep answer now sends no Set-Cookie (`TokenPair.sets_cookie`), so every cookie the server sets is for the live token as of that answer, and no late answer can move the jar backwards. Tests fail before the fix and pass after it, and no bound was widened.
- **Tests** run against the old code first, where they fail:
  - 2, 3 and 5 concurrent tabs;
  - delayed and out-of-order responses (the exact browser sequence);
  - a burst straddling a rotation;
  - reloads with and without another open tab;
  - refresh racing logout and logout-everywhere;
  - stale replay revoking only its session;
  - divergence across rotations in both orders;
  - the follower attack;
  - two legacy live tokens;
  - shared-token checks.
- **Browser evidence** (`phase-1/browser-multitab/final/`, real Chromium, one context with 6 tabs plus a second session), on `e9c14c3`:
  - 2, 3 and 5 tabs refreshing and reloading at once all stay signed in;
  - the exact D1 sequence leaves the cookie current, both with a young token and with a token due for rotation;
  - a cookie-only refresh 15 s later succeeds, and so does a reload 15 s later;
  - a genuinely stale cookie replayed after 5½ minutes ends only its session, while the other session stays signed in;
  - refreshes racing a logout leave everything signed out;
  - unsaved work is kept.
- **Independent security review:** acceptable for this phase, with no confirmed defects at the final design. Earlier rounds found and fixed:
  - the unbounded grace;
  - session fixation by cookie tossing;
  - parallel legacy tokens;
  - an unchecked broadcast;
  - key separation;
  - an unbound bearer proof.
- **Accepted tradeoffs:**
  - the 5-minute minimum token age;
  - the generation-bound 1 h bearer path;
  - the 300 ms borrow wait;
  - same-origin code can read the access token over the channel, which is acceptable only while nothing else is hosted on the app's origin;
  - a lone stale tab with no lender ends its session, which fails safe.
- **HTTPS:** verified against a real TLS server outside dev (`phase-1/https-check/`).
  - The cookie is `__Host-pp_refresh` with Secure, HttpOnly, SameSite=Strict, Path=/ and no Domain, and no refresh token is in the body.
  - Untrusted origin, `null` origin and missing header are each refused with 403; the trusted origin succeeds.
  - Logout clears the cookie with the same attributes.
  - The proxy's forwarding over HTTPS is covered by a unit test with a TLS upstream; Next itself was not run over TLS.

### Dependencies in the candidate

- **#202's exact head** (`6a96b74`) is in `phase1-base` and in the branch.
- **#204's exact commit** (`b4b9153`, filelock 4.0.6 → 4.2.4) is merged into the branch unchanged, so its authorship and its separate PR are preserved. `phase1-base` is unchanged.
- **The combined lock files** re-resolve byte for byte with the uv version CI pins (0.8.17):
  - Pillow 13.0.0, black 26.10.1, certifi 2026.10.19, opentelemetry-api 1.45.1 and filelock 4.2.4;
  - `pip-audit` finds no known vulnerabilities in either lock;
  - `npm audit --omit=dev --audit-level=high` finds 0.

## Final Phase 1 review (2026-10-27)

- **GitHub state, verified directly:**
  - PR #203 is open and mergeable (CLEAN).
  - The base is `phase1-base` at `daeb143`, a merge of #199 `73ce76c` and #202 `6a96b74`.
  - The head contains #199, #202 and #204 (`b4b9153`) at their exact commits. All three dependency PRs are still open.
- **Evidence mapped to code:**
  - The final multi-tab browser run was on `e9c14c3`. Up to `c8802d6` only two test files changed (`raising=False` removed), and `8f21ab9` changed the report only. So that browser evidence covers the application code of `8f21ab9`.
  - Earlier adversarial evidence (`phase-1/adversarial/`, `browser-signed-in/`, `browser-final-auth/`) was recorded on older code. It is kept as history.
- **Attacks re-run on the final head** (`phase-1/final-attack/`), on the live stack at `8f21ab9`: 44 of 44 checks pass. They cover:
  - snapshot integrity;
  - the server-owned approval, with no "@" in any response or export;
  - tenant isolation;
  - logout, and replay after a genuine rotation (waited 5 min 10 s);
  - cross-site refusals;
  - execution refusal in all 18 formats;
  - restricted formats on every endpoint;
  - the quotation estimate;
  - concurrency.
- **Migrations, on new disposable databases:**
  - A clean database upgrades to head. It also downgrades to the pre-Phase-1 revision and upgrades again, with no drift.
  - A pre-Phase-1 database seeded with a user, two legacy refresh tokens and an approved revision upgrades with every row kept. The revision's old approval is unbound, and the legacy tokens are refused, which means the one-time sign-in.
- **Final independent review** (an agent that implemented none of this, reading the full diff `daeb143..8f21ab9`): approved for Phase 1, with no blocking defects in the code it read. It named what it did not read: integrity rules, findings policy, quotation pricing bounds, frontend components, and the tenant filter itself. Its findings:
  - **D-1, the multi-tab edge:** confirmed and fixed as above.
  - **P-1:** a float written into a stored snapshot gave a 500. It now counts as a hash mismatch (`snapshot_integrity_failed`), with a test.
  - **P-2:** the printed approver was the typed name. Now fixed: the approver is the authenticated account (see "Approver identity and privacy").
  - **P-3:** an old-key token that cannot be re-derived still clears the cookie. Low; it only affects old tokens.
  - **P-4:** rolling-deploy legacy tokens. Documented.
  - **P-5:** most auth tests use `ROTATE_AFTER = 0`. The production rule is covered by the `real_rotation` tests, now including the keep-then-rotate reversal.

### Deployment steps

1. Back up the database.
2. Set `WEB_ORIGINS` to the exact HTTPS web origins. Optionally set `REFRESH_TOKEN_KEY`. Keep `AUTH_COOKIE_SECURE` at its default (true outside dev).
3. Run `alembic upgrade head` (through `b4e8d2f6a9c1`). Every migration is additive and safe while old pods still run; legacy tokens are refused.
4. **Mandatory gate:** run `python -m app.worker owner-report`. Exit code 1 blocks the rollout: at least one tenant has no active owner and no active admin, so nobody in it can manage its team (invite, remove, grant nothing, transfer nothing) and nobody can approve, delete or change settings, because existing members no longer hold those powers. For each line marked `needs assign-owner`, record an active account of that tenant as its owner with `python -m app.worker assign-owner person@example.com` (agreed with the customer), then run `owner-report` again until it exits 0. A deactivated tenant is refused (`tenant_inactive`): reactivate it first, or leave it. Lines without the mark (a tenant whose admins still manage it) do not block.
5. Tell users they will sign in once. Saved projects and drafts are kept.
6. Revisions saved before Phase 1 must be saved again to be validated and re-approved. Revisions with names over 200 characters must be renamed and saved again.
7. Later, a contract migration drops the unused `grace_used_at`, once no old pod can run.

## Second independent review and final verification (2026-10-27)

Four reviewers, none of whom implemented the area they reviewed, read the code at `55bdbf8`. What they confirmed and how it was fixed, each fix with a test that fails without it:

| Area              | Confirmed finding                                                                                                      | Resolution                                                                                                                                                                                                                                                                           |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Approval identity | The approver was free text that anyone could type; a trial could approve                                               | Approval names the authenticated account (display name, or "a team member"). The request takes only an optional note; `approver` is a 422; a trial gets 403 `approval_requires_account`. `approved_user_id` is recorded (`f8c4a2e6d9b3`); legacy typed names are kept for audit only |
| Integrity         | A protected circuit with no cable was never checked                                                                    | Blocking `integrity_cable_missing`                                                                                                                                                                                                                                                   |
| Integrity         | `starter: "drive"` on any circuit turned the capacity check into info                                                  | A starter is allowed only on motor loads (422); the drive check is a warning needing confirmation                                                                                                                                                                                    |
| Integrity         | An unrated or removed breaker or fuse, or no overload relay, gave only a warning                                       | Blocking `integrity_device_unrated` and `integrity_protection_missing`                                                                                                                                                                                                               |
| Integrity         | Ratings and sections had no bounds; huge exponents gave a 500 or a 1 MB finding                                        | Bounds on every engineering quantity; no pipeline value is rejected                                                                                                                                                                                                                  |
| Integrity         | A draft can drop its own blocking notes                                                                                | Drafts carry `draft_findings_not_rechecked`; saved revisions are re-designed on the server                                                                                                                                                                                           |
| Integrity         | A pass against the upper bound only was silent                                                                         | Info finding `integrity_conditions_unknown`                                                                                                                                                                                                                                          |
| Quotation         | The client's price-list description replaced the snapshot device name on an approved-stamped quotation PDF             | Every line is named by the snapshot item                                                                                                                                                                                                                                             |
| Quotation         | A huge CSV cell gave a 500; duplicate keys were silently overwritten                                                   | Structured 422s `csv_unreadable`, `csv_field_too_long` and `price_list_duplicate_key`                                                                                                                                                                                                |
| Tenancy           | Invite acceptance counted seats and plans across every tenant (only with billing enforced)                             | Filtered by tenant                                                                                                                                                                                                                                                                   |
| Tenancy           | Access tokens of a deactivated tenant kept working                                                                     | `resolve_caller` refuses them                                                                                                                                                                                                                                                        |
| Frontend          | An open during an in-flight save could attach project B's form to project A, so the next save stored B as A's revision | Open, select and delete are disabled while saving, and only the latest action's answer applies                                                                                                                                                                                       |
| Frontend          | Exports had no in-flight guard; results landed after an identity change                                                | One request per click; stale results are dropped                                                                                                                                                                                                                                     |
| Frontend          | Stale and not-validated approval refusals didn't refresh the readiness panel                                           | The revision is reopened                                                                                                                                                                                                                                                             |
| Frontend          | Download names were unsanitised; the object URL was revoked immediately                                                | Sanitised names; the revoke is deferred                                                                                                                                                                                                                                              |
| Frontend          | The history row showed approvals that don't cover the snapshot; an unchanged revision could be saved                   | Fixed                                                                                                                                                                                                                                                                                |

**Not fixed, product decision:** any account in the tenant whose plan includes approval may approve, delete projects and change company settings. Only team management checks roles.

**Dependencies:** a new advisory, CVE-2026-114605 (`dnspython` 2.8.0), appeared after the previous green run. It is fixed in its own PR #205, whose exact commit is merged here. The combined locks re-resolve byte for byte, and `pip-audit` finds no known vulnerabilities.

**Dev data check** (read-only, counts only): no stored request or snapshot sets a starter on a non-motor load or carries a rating or section outside the new bounds. All 21 approved revisions predate `approved_user_id` and resolve their approver by email.

**Final browser verification** on the final code (`phase-1/browser-phase1-final/`), all PASS:

- **Sign-in and saving:** sign-in persists across reloads. Save, edit and save work; an unchanged revision cannot be saved, and open, select and delete are disabled while saving.
- **Approval:** the form says "You are approving as Eng. Final Reviewer" and offers only a note. A forged `approver` gets 422 and nothing is recorded.
- **Exports:** every one names the account, carries the stamp on every page, holds the note only as "Approval note" on the calculations PDF, and contains no "@". A double click sends one request.
- **Gates:** execution is refused, and CSV, QET and AML are disabled with a localized reason.
- **Trial:** cannot approve, with the reason in en/ar/he.
- **No-name account:** shows the localized fallback.
- **Logout:** the replayed access token gets 401.
- **Multi-tab:** 2, 3 and 5 tabs refreshing and reloading at once stay signed in. The exact out-of-order sequence holds, and a young-token refresh sends no Set-Cookie (checked in the browser). Refreshes racing a logout end signed out.
- **Environment note:** the first multi-tab attempt failed because the dev web container ran out of memory (ENOMEM in the Docker VM, with other projects' stacks running). After a restart the run passed.

**Minor observations:**

- Sign-up on the design screen asks for no name, so those users approve as "a team member".
- In Arabic and Hebrew drawings the English stamp line keeps the English fallback.
- The "Saved as revision N" notice stays in English.

## Permissions

Three actions used to need only a signed-in account. They are now explicit permissions, checked by the server in the domain layer (`app/domain/permissions.py`). The page only hides or disables what a caller may not do; the server checks every request again.

| Action                | Permission              | Owner | Admin | Reviewer | Other member | Trial | Refused with                                                        |
| --------------------- | ----------------------- | ----- | ----- | -------- | ------------ | ----- | ------------------------------------------------------------------- |
| Approve a revision    | `approve_revision`      | yes   | yes   | yes      | no           | no    | 403 `approval_not_permitted` (a trial: `approval_requires_account`) |
| Delete a project      | `delete_project`        | yes   | yes   | no       | no           | no    | 403 `project_delete_not_permitted`                                  |
| Save company settings | `edit_company_settings` | yes   | yes   | no       | no           | no    | 403 `settings_not_permitted`                                        |
| Manage the team       | (team routes)           | yes   | yes   | no       | no           | no    | 403 `team_owner_only`                                               |
| Transfer ownership    | `POST /team/owner`      | yes   | no    | no       | no           | no    | 403 `team_transfer_owner_only`                                      |
| Remove the owner      | -                       | no    | no    | no       | no           | no    | 422 `team_owner_not_removable`                                      |

- **Owner** is the tenant's recorded owner, `tenants.owner_user_id`, never the oldest account. It must be an account of the same tenant (a composite foreign key to `users (id, tenant_id)`). **Admin** and **Reviewer** are stored roles.
- **One `reviewer` role, two duties (deliberate):** the same stored role that lets an account clear operator review items (ADR 0001) now also lets it approve design revisions. The requirement named the existing role rather than a new one, so a reviewer granted for either duty holds both.
- **Where the owner comes from:** the signup that creates a tenant records its account as owner; the signup that claims a trial records the claiming account (a trial tenant has no accounts until then). Joining by invitation never does.
- **Transfer:** the owner hands ownership to another active member of the same tenant with `POST /team/owner {"member_id": ...}` (returns the team; logged as `team.ownership_transferred` with tenant and account ids). The former owner stays a member with only the roles it holds. An admin cannot transfer or take ownership. Refused: not the owner (403 `team_transfer_owner_only`), naming oneself (422 `team_transfer_self`), an inactive, missing or other tenant's account (404 `team_member_not_found`). `GET /team` carries `owns` for the caller, and each member's `owner` flag.
- **No removal of the owner:** nobody, admin or owner, removes the owner through the API (422 `team_owner_not_removable`); the owner transfers first. The worker has no deactivation command. An owner deactivated outside the API (an operator, in the database) passes ownership to **nobody**: the tenant's admins keep managing it, and `owner-report` lists it.
- **Operator remedies:** `python -m app.worker owner-report` lists every active tenant with accounts but no active owner (`no_owner` or `owner_inactive`), its active admin count, and marks one with no admin `needs assign-owner` (exit code 1 while any does). `python -m app.worker assign-owner person@example.com` records that active account as its tenant's owner, only where the tenant has no active owner (otherwise 1, `team_owner_present`).
- **Atomic check and action:** permissions are read from the database, never from the token. `permissions.require` runs inside the acting transaction and locks the tenant row `FOR SHARE`, then the caller's account `FOR SHARE`, then reads its roles; `grant-role`/`revoke-role` lock the account `FOR UPDATE`, and a removal, transfer or `assign-owner` lock the tenant row (and the affected account) `FOR UPDATE`. Either the revocation, removal or transfer commits first and the waiting action is refused, or the action commits first and the change waits for it. Tested against Postgres with held locks in both orders (revocation and approval, removal and approval, transfer against approval and delete, two transfers).
- Every member still reads projects and company settings, and saves, opens and exports revisions. Nothing new is granted to every member.
- **Not found first:** the permission is checked after the project is found as the caller's, so another tenant's project is a 404 (`revision_not_found` / `project_not_found`) for every caller, never a 403.
- **What the client sees:** `ProjectPage`, `SavedProject` and `CompanySettings` carry a `permissions` list for the caller, read from the database the same way as the check. The projects panel hides Delete without `delete_project`, the Approve control is disabled with an explanation without `approve_revision`, and the company settings form disables Save with an explanation without `edit_company_settings`. Each refusal code above has a message in English, Arabic and Hebrew. On `/team`, the owner (`owns`) sees "Make owner" beside each other active member; it asks for confirmation, then calls `POST /api/v1/team/owner`, and `team_transfer_owner_only`, `team_transfer_self` and `team_member_not_found` are said in the reader's language.
- **Locks:** approve and delete both lock the project row first, then the tenant and account rows. A refusal rolls back at once, so it holds no lock. Approve racing delete ends with exactly one of them applied and no 500 (tested).

### Compatibility for existing tenants

Migration `a7d3e9f1c2b4` adds `tenants.owner_user_id` and backfills it deterministically, never granting more than the old derived rule (the oldest active account) did:

- **Owner recorded:** the tenant's oldest account (by `created_at`, active or not) is active and no other account shares its `created_at`. Several admins do not make this ambiguous: admins keep admin's powers whoever owns the tenant.
- **Left without an owner** (its admins still manage it):
  - `first_account_inactive`: the oldest account is deactivated. The old rule handed ownership silently to the next-oldest member; nobody is chosen now.
  - `created_at_tie`: two or more accounts share the oldest `created_at`; the old tie-break by id was arbitrary.
  - `no_accounts`: an unclaimed trial; the claiming signup records the owner.
- The upgrade prints the count of each case and of those with no active admin (no ids, no personal data). A tenant with no owner and no active admin is locked out: nobody can manage its team, and no member can approve, delete or change settings. `owner-report` after the upgrade is a mandatory gate (Deployment steps, step 4); resolve each such tenant with `assign-owner`. `users.created_at` is `NOT NULL` (since the initial schema), so a missing creation time cannot make the backfill's choice ambiguous.
- **Other existing members lose** approve, delete and settings. To let a member approve again, an operator grants `reviewer` (approval only) or `admin` (everything but transferring):
  ```
  python -m app.worker grant-role engineer@example.com reviewer
  ```
  Both take effect on the member's next request.
- **Downgrade** drops the column: transfers made since are lost, and ownership is derived from the oldest active account again.

## Settings and approval

Decided by the integration owner: an approval binds the policy that applied when it was granted, and live tenant company settings are not part of that policy.

### What the code does (traced)

- **Tenant company settings are never read for an existing revision.** A save freezes the settings the form sent into the revision's own `request.profile`. The revision stores `snapshot_sha256`, `profile_sha256` (the hash of that profile resolved over this software's default profile), `rules_version`, `catalogue_version` and its findings. `PUT /design/company-settings` changes only what pre-fills the next design.
- **Staleness was already checked.** On open, approve and export, `_changed` compares the revision's recorded profile hash, rules and catalogue versions, and its findings classification with today's (the findings policy re-run on the frozen snapshot). Any difference is `revision_stale`, listing what changed.
- **Before this change** an approval recorded only `approved_snapshot_sha256` and who approved; the approver's display name was resolved live from `users` each time a response or export was built.

### What an approval records now (migration `b4e8d2f6a9c1`)

| Column                       | Value at approval                                                                                     |
| ---------------------------- | ----------------------------------------------------------------------------------------------------- |
| `approved_profile_sha256`    | The revision's profile resolved now (checked equal to the revision's own, or the approval is refused) |
| `approved_rules_version`     | `RULES_VERSION`                                                                                       |
| `approved_catalogue_version` | `CATALOGUE_VERSION`                                                                                   |
| `approved_findings_policy`   | SHA-256 of the canonical JSON of the sorted (code, severity, engineering confirmation) classification |
| `approved_display_name`      | The approving account's display name, or `""` when it has none ("a team member")                      |

- **When an approval counts** (review-approved status and mark, the title block's approver, `approval: present` and no `approval` blocker for execution): the approved snapshot is the revision's, it still passes its hash check and reads as a design (a full check; otherwise `snapshot`), and every recorded policy value equals the value that applies now. Otherwise:
  - `SavedProject` and each `RevisionSummary` carry `approval_current: false` and `approval_changed` (`snapshot`, `rules_version`, `catalogue_version`, `profile`, `findings_policy`); `approval_current` is `null` while not approved. The revision opened is checked in full; the other revisions in its list by the cheap check: from columns alone, no snapshot read (rules and catalogue versions, the recorded profile hash against the profile its stored request resolves to now, and the recorded findings fingerprint against its stored findings).
  - Export is refused with `revision_stale`, its `changed` naming the policy parts (from the revision and from its approval). An approval of another snapshot is unchanged: the export is a draft, as before.
  - Execution stays refused, with `approval` among its blockers.
  - The web shows "This approval no longer counts: … changed since revision N was approved" on the projects panel and in Review & export, and "No longer counts" in the revision list, in English, Arabic and Hebrew.
- **Exports print the frozen name.** Renaming, deleting or reusing the approving account changes no export. Who would approve now (`approving_as`) is still read live.

### The two cases

1. **Tenant company settings change after approval** (including engineering values such as `default_max_voltage_drop_percent`): nothing about the approved revision changes. It stays review-approved and `approval_current: true`, and its JSON export is byte-identical before and after (tested).
2. **An applicable code policy changes after approval** (`RULES_VERSION`, `CATALOGUE_VERSION`, a value of `DEFAULT_PROFILE` that the revision's settings inherit, or a findings classification): the approval is no longer current, export is refused `revision_stale` naming the part, and execution stays blocked (tested for each of the four). The engineer saves the project again as a new revision and approves that.

### Cheap and thorough approval checks

- **Thorough** (the revision opened, approved or exported; one snapshot read per request): the snapshot's hash and that it reads as a design, the rules and catalogue versions, the profile hash, and today's findings classification re-run on the snapshot, each against what the approval recorded.
- **Cheap** (every other revision in the list, and in an export's title block): the cheap check: from columns alone, no snapshot read (rules and catalogue versions, the recorded profile hash against the profile its stored request resolves to now, and the recorded findings fingerprint against its stored findings). Reclassifying findings needs the snapshot, so the cheap findings check only catches a fingerprint that disagrees with the stored findings (a row altered after approval), not a policy change since. An earlier approval that fails the cheap check shows `approval_current: false` in the list and names no approver in an export's title block.

### Legacy approved rows

- **Backfill, deterministic:** the profile hash, rules and catalogue versions are copied from the revision's own stored values. An approval was only granted to a revision that was not stale, so those were the applicable values then.
- **`approved_findings_policy` stays NULL** for them, and NULL is read as the fingerprint of the revision's own stored findings classification (which approval required to match the policy then). It is not computed in the migration, because the findings policy is code and a migration must not depend on the version installed when it runs.
- **`approved_display_name`** is the approving account's display name at migration time, resolved as exports resolved it until then (by `approved_user_id`, else by `approved_account` case-insensitively, within the revision's tenant; blank or "@"-containing names are no name), or `""`.
- The upgrade prints counts only (approved rows; named versus "a team member"). A row neither the approval nor the migration wrote (NULL columns) reads the same way as a backfilled one, and its approver is resolved live as before.
- **Downgrade** drops the five columns: approvals bind to the snapshot alone again and names are resolved live.

### Limitations

- **Renderer wording is not versioned.** A change to how an export is laid out or worded (titles, labels, PDF layout) is not part of the recorded policy, so an approved revision exported after such a change reads differently while its approval still counts.
- **Strict by design:** any change to the default profile that the revision's settings inherit changes the resolved profile hash, including purely presentational defaults (a title-block field, a page order). Such a change makes approved revisions not current and their exports refused until saved again.
- The cheap check (below) misses a findings-policy reclassification and a snapshot that no longer reads; such an approval shows as not current once that revision is opened, approved or exported.

## Remaining limitations

1. **Execution release** stays blocked until its own completeness and engineering-validation gate exists. Finishing Phase 2 alone does not unlock it.
2. **CSV, QET, AML and PLC** stay refused until their documented acceptance criteria are shown.
3. **Deployments must set `WEB_ORIGINS`** to the exact web origins (see "Deployment" below). Without it, every session refresh is refused.
4. **One-time sign-in after deploy:** tokens issued before the session migrations are refused, so every signed-in user signs in once. Saved projects and unsaved drafts are kept, and trials are unaffected.
5. **Sessions:**
   - **Single tab:** a lone tab whose refresh answer is truly lost, followed by its next refresh more than 10 s later with no other open tab to lend a current access token, ends that session. It fails safe, and the user signs in once.
   - **Key compromise:** follow the documented revoke-all-sessions step.
   - **Approval roles:** superseded. Approval is now limited to the tenant's owner, admins and reviewers (see "Permissions" above).
6. **Approvals show "a team member"** when an account has no display name. This is accepted for this phase.
7. **Trial projects stay with the trial** when signing in to an existing account, by decision.
8. **Native Arabic and Hebrew review** of every new string is still outstanding. The AI review is not a native-speaker review.
9. **Smaller items:**
   - The Contents page lists drawing pages but not the appended findings sheets; the "N of M" counts are correct.
   - The quotation PDF and CSV are English throughout.
   - Units and catalogue or supplier text stay as entered.
   - A reopened project shows "Nothing designed yet" on Review & export until it is designed again, although exporting the saved revision works.
   - In dev, each page load calls the design endpoints twice, because of React StrictMode. A production build calls each once.

## Environments left in place

Not cleaned up, by request:

- **Dev database `panelpilot`:** at `b2e8d5f7c3a1`. Backups: `backups/20261023-1430-pre-phase1/`, `backups/20261024-pre-auth-sessions/`, `backups/20261024-pre-token-generations/` and `backups/20261024-pre-b2e8/`.
- **Disposable databases:** `panelpilot_phase1_test`, `_adv`, `_backend`, `_mig`, `_migclean`, `_migprev` (the last holds the legacy-row upgrade) and `panelpilot_ci_sim` (used to check CI-mode database discovery).
- **Compose services:** api, web, worker, postgres, opensearch and redis.
- **Local test accounts:** disposable `e2e-p1*@example.com` accounts on the local stack. Their credentials are kept outside git.
- **Untracked, not in this PR:**
  - the earlier readiness assessment (`docs/design-readiness/assessment.md` and `evidence/`), held back for a privacy review of its reference material;
  - the unrelated repo-root `src/`.

## How the work was done

- **Two Claude Code sessions, one owner.** The lead session coordinated. A second session (panelpilot-73) also worked on the branch until the user made the lead the sole owner. Its work was kept and reviewed, not reverted.
- **Agents:** backend, frontend, adversarial, a format-gating implementer, a review-fix implementer, a browser-flow verifier, and an independent reviewer who did not implement the code.
- **Git:** only the lead committed. The branch history was rebuilt onto the #199 + #202 base without the assessment evidence. The tree of `apps/` and `packages/` was verified byte-identical to the tested commit.

## Deployment: WEB_ORIGINS and sessions

### The supported topology

The repo ships one browser topology: **same-origin through the web proxy**. Nothing else is supported for signed-in browsers.

- The browser loads the Next web app (`apps/web`, port 3000 in its image) and calls only relative `/api/v1/*` paths on that same origin.
- The web server's route handler (`apps/web/src/app/api/[...path]/route.ts`) forwards them to the API at `API_PROXY_TARGET`. That is `http://api:8000` in `docker-compose.yml`.
- The API is never called by the browser directly. `infra/README.md` lists the deploy targets (`web`, `api`, `worker`); there is no cloud IaC yet.

The session cookie is set and read on the web origin:

- Over HTTPS it is `__Host-pp_refresh` (Path=/, Secure, no Domain).
- Over dev http it is `pp_refresh` on `/api/v1/auth/session`.

The proxy forwards it only to `refresh`, `logout` and `logout-everywhere`. CORS keeps `allow_credentials=False`. A **separate API host called from the browser** (`https://api.example.com` with the page on `https://app.example.com`) is therefore **not supported**:

- the cookie belongs to the web origin;
- a credentialed cross-origin call is refused by CORS by design.

To run the API on its own host, keep the browser on the web origin and point `API_PROXY_TARGET` at it.

### What WEB_ORIGINS must be

`WEB_ORIGINS` is the origin the **browser** shows in the address bar for the web app: scheme, host, and port only if it is not the default. It is not the API's address, and not `API_PROXY_TARGET`.

| Environment                                             | Browser loads                                                  | `WEB_ORIGINS`                                                                                          | Also set                                                             |
| ------------------------------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| Local compose (`docker compose up`)                     | `http://localhost:3000` or `http://127.0.0.1:3000`             | unset. In dev both loopback origins are always trusted, alongside `WEB_ORIGINS`/`CORS_ALLOWED_ORIGINS` | `ENVIRONMENT=dev`; the cookie is not Secure (plain http)             |
| Dev web on another port, e.g. `next dev -p 3001`        | `http://localhost:3001`                                        | `http://localhost:3001`                                                                                |                                                                      |
| Staging behind TLS                                      | `https://staging.panelpilot.example`                           | `https://staging.panelpilot.example`                                                                   | `ENVIRONMENT=staging`; `AUTH_COOKIE_SECURE` unset (true)             |
| Production behind TLS                                   | `https://app.panelpilot.example`                               | `https://app.panelpilot.example`                                                                       | `ENVIRONMENT=prod`                                                   |
| Production on two hostnames for the same web app        | `https://app.panelpilot.example`, `https://panelpilot.example` | `https://app.panelpilot.example,https://panelpilot.example`                                            | each hostname gets its own `__Host-` cookie; a user signs in on each |
| TLS terminated at a load balancer on a non-default port | `https://app.panelpilot.example:8443`                          | `https://app.panelpilot.example:8443`                                                                  |                                                                      |

Rules:

- **No path or trailing slash.** `https://app.example.com/` is normalised, but write it without the slash.
- **No `*`.** It is refused at startup outside dev, and never matches anything in dev.
- **Outside dev, unset means refused.** If neither `WEB_ORIGINS` nor `CORS_ALLOWED_ORIGINS` is set, every refresh is a `403 csrf_origin_refused`. A reload then loses the sign-in (the web logs "The API refused this origin for the session."). This fails closed on purpose.
- **Staging and production need HTTPS end to end at the browser.** `AUTH_COOKIE_SECURE=false` is refused at startup there, and a `__Host-` cookie is dropped by the browser over plain http.

The HTTPS behaviour is checked by `docs/design-readiness/phase-1/https-check/check_https.py`; its last output is in `output.txt` there. It runs the API with `ENVIRONMENT=staging` over TLS with a throwaway CA and checks:

- the `__Host-` cookie attributes;
- that the trusted origin is allowed;
- that an untrusted origin, a `null` origin and a missing CSRF header are refused;
- that logout clears the cookie with the same attributes.

### The one-time sign-in after the session migration

The migrations `c3f8a1d6e2b7` (sessions), `e7b2c9d4a1f3`, `a9d3f6b1c8e4` and `b2e8d5f7c3a1` (derived refresh-token generations) leave existing rows in place, but credentials issued before them stop working:

- **Access tokens without a `sid` are refused** with a `401 session_ended`. A logout could never reach them.
- **Refresh tokens without a session are refused.** Before this the web held refresh tokens in memory only, so after a reload no browser had one anyway.

So **every account signed in at deploy time signs in once more**, at most one access-token lifetime (1 h) early. Nothing is lost:

- saved projects are on the server;
- unsaved design work stays in the tab's draft and is offered again after signing in.

Trials are unaffected: a trial has no session.

### How concurrent tabs are handled

Tabs of one browser share one cookie, so they must not count as theft.

**In the browser.**

- A refresh takes a browser-wide Web Lock (`pp-refresh`), and the tab that refreshed shares its access token over a `BroadcastChannel`.
- A tab that waited adopts that token only if it is unexpired and names the same account and session (`sub`, `sid`). On load, any unexpired account token is accepted. Otherwise the tab refreshes itself.
- A shared timestamp more than 2 s ahead of the tab's clock is ignored, and the shared token is forgotten on sign-out.
- Without Web Locks the tab refreshes directly.
- Every refresh also sends the tab's in-memory access token as the bearer.

**On a reload.** Before its cookie-only refresh, a reloaded page asks the browser's other open tabs, over the same channel, for a current access token (waiting at most 300 ms). It sends the first one that passes the same checks as its bearer.

**On the server.** A session has exactly one live refresh token, and only the newest live token may rotate.

- **No rotation inside a burst.** A live token younger than `ROTATE_AFTER` (5 minutes) is not rotated. A refresh presenting it gets the same token back, re-derived, with a fresh access token. A burst of refreshes from several tabs or reloads therefore mints nothing, every answer carries the jar's own value, and a late answer cannot move the jar back.
  - 5 minutes is far longer than a burst (seconds) and far shorter than an access token's life (1 h), so the hourly renewal still rotates every time.
- **Re-issue to the live token's immediate predecessor**, for bursts that straddle a rotation:
  - **cookie alone:** spent within **10 s**;
  - **cookie and an access token of the live generation:** spent within an access-token lifetime, with an unexpired bearer whose `sid` is the session, whose `sub` is its user, and whose `rgen` (the refresh generation the access token was issued with) equals the live token's generation.
- **Every access token now carries `rgen`.** Login sets 1; a rotation, a re-issue and a kept-young answer each set the live generation. A token without `rgen` gets no extended path.

Everything else spent is a replay and ends **that session only**:

- a token two or more generations behind;
- a predecessor older than these bounds;
- a bearer from another session or user, an expired one, or one of an older generation.

"Log out everywhere" ends the rest.

**The bounds.**

- **A follower cannot keep up.** A party that once held the cookie and an access token can follow the chain only until the next rotation: its access token names the old generation, so its next late copy is a replay.
- **A cookie-only thief** gets the 10 s window and nothing more.
- **The cost of `ROTATE_AFTER`:** a stolen _live_ cookie is usable until the next rotation. That is no worse than a stolen live cookie before this change, which was also usable until the victim's next rotation. When victim and thief diverge across a rotation, the one left holding the spent token is a replay at its next refresh (tested both ways).
- In every case the answer is the one live token.

**What is still an edge.** Every one of these must happen, in order:

1. a burst that straddles a rotation, the 5-minute boundary falling inside it;
2. an out-of-order answer, so the jar is left on the predecessor;
3. every tab of the browser that holds an access token of the new generation is closed or reloaded;
4. then more than 10 s pass before the next refresh.

Only then is that refresh cookie-only with a stale predecessor, a replay, and the user signs in once.

### The refresh-token key, and a key compromise

- Refresh tokens are derived under their own key, never the access-token signing key itself:
  - `REFRESH_TOKEN_KEY` when it is set;
  - otherwise HKDF-SHA256 of `JWT_SECRET` with the label `panelpilot refresh-token derivation v1`.
- **Changing either key does not sign anyone out.** A live token is looked up by its stored hash and rotates normally; its successor is derived under the new key. Only re-issuing a live token that was derived under the old key fails, and that is refused without ending the session.
- This applies to the tokens derived straight from `JWT_SECRET` by `a1bff09`, which never left the dev and disposable databases.
- **A compromise of the refresh-token key** (or of `JWT_SECRET` while `REFRESH_TOKEN_KEY` is unset) **together with the database** lets an attacker derive every live refresh token. Rotating the key does not revoke them. Revoke every session, then rotate the key:

```sql
BEGIN;
UPDATE auth_sessions  SET revoked_at = now() WHERE revoked_at IS NULL;
UPDATE refresh_tokens SET revoked_at = now() WHERE revoked_at IS NULL;
COMMIT;
```

This ends every sign-in at once: access tokens are refused from the next request, and every user signs in again. A `JWT_SECRET` compromise also lets anyone mint access tokens, so rotate `JWT_SECRET` in the same change.

### Migrations of this series

- `c3f8a1d6e2b7` adds sessions.
- `e7b2c9d4a1f3` adds `grace_used_at`, now unused.
- `a9d3f6b1c8e4` adds the salt and the generation, and keeps `grace_used_at`: pods still running the previous code use it during a rolling deploy. A later contract migration drops it.
- `b2e8d5f7c3a1`:
  - re-adds `grace_used_at` where an unmerged earlier version of `a9d3f6b1c8e4` dropped it (the dev and disposable databases);
  - revokes all but the newest live refresh token of every session.
    It is idempotent and its downgrade changes nothing. That earlier version was edited only because it was never merged; the new revision exists because an applied revision never runs again.
