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
  - reads the approver only from the database.

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
- **Lists use a cheaper check.** A project's revision list compares recorded versions only. The revision opened, approved or exported is always checked in full.

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

### Approver privacy

- **The API never sends an account's email.** `RevisionSummary` now carries `approver_name`, the account's display name resolved in the project's own tenant, and `approver_account_recorded`.
- **`author` is a display name too.** A missing name, or one containing "@", is sent as nothing, and the UI says "a team member".
- **The audit identity is unchanged in the database.** `approved_account` keeps the email, and `approved_by` keeps the name typed.
- **Exports** (PDF, calculations PDF, DXF, JSON) carry only the typed approver name, the hash and the rules version.
- **Tests:** no "@" in any approval or project response, nor in any export; the display name and its fallback; another tenant gets 404 and an empty list; the database keeps the audit identity.
- **No role is allowed to see the email.** An `admin` role exists, but nothing grants it that.
- **`approved_by` is free text,** so a user can type an email there themselves.

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

| Revision       | Change                                                                             | Existing rows                                                          |
| -------------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `b4e7a9c2d1f6` | Nullable snapshot, hash, versions, findings and approved-hash columns on revisions | Kept: legacy revisions open, and are `not_validated` until saved again |
| `c3f8a1d6e2b7` | New `auth_sessions` table; nullable `refresh_tokens.session_id`                    | Kept: legacy refresh tokens are refused, so sign in again              |
| `e7b2c9d4a1f3` | Nullable `refresh_tokens.grace_used_at`                                            | Kept                                                                   |

- **Every change is additive.** Downgrades drop only the new session data.
- **Checks:** clean upgrade from the previous schema, `downgrade -2`, `upgrade head`, and `alembic check` (no drift).
- **The dev database** is at `e7b2c9d4a1f3`, after a fresh backup taken before the session migrations, `backups/20261024-pre-auth-sessions/panelpilot.dump`, verified readable (27 tables).

## Remaining blockers and decisions needing engineering input

1. **Execution release** stays blocked until the gate above exists. This is deliberate and not part of Phase 2's completion.
2. **CSV, QET, AML and PLC** stay refused until their acceptance criteria are shown.
3. **Deployments must set `WEB_ORIGINS`** (HTTPS origins). Without it every session refresh is refused, and sign-in does not persist across reloads.
4. **Everyone signed in at deploy time signs in once more,** because legacy tokens are refused.
5. **The grace window** honours one reuse within 10 s. Three tabs refreshing at the same instant count as replay and end every session.
6. **Trial projects after signing in to an existing account** stay with the trial, by decision.
7. **Native Arabic and Hebrew review** of every new string (list above).
8. **Smaller items:**
   - The Contents page lists drawing pages but not the appended findings sheets; the "N of M" counts are correct.
   - The quotation PDF and CSV are English throughout.
   - Units and catalogue or supplier text stay as entered.
   - A reopened project shows "Nothing designed yet" on Review & export until it is designed again, although exporting the saved revision works.
   - Sign-up asks for no display name, so approvals show "a team member" until profiles carry one.

## Environments left in place

Not cleaned up, by request:

- **Dev database `panelpilot`:** at `e7b2c9d4a1f3`. Backups: `backups/20261023-1430-pre-phase1/panelpilot.dump` (before Phase 1) and `backups/20261024-pre-auth-sessions/panelpilot.dump` (before the session migrations).
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
