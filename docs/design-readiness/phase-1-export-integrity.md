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

## Remaining blockers and decisions needing engineering input

1. **The design screen ignores the signed-in account (blocker for a real signed-in workflow).**
   - `/design` always works on its own anonymous trial session (`acquireTrial` in `design-screen.tsx`), and it did on the base and on `main` before this work.
   - A project saved there belongs to a trial tenant. Signing in on `/review` does not move it.
   - Tenancy, approval and export are enforced correctly within that tenant (verified), but design work is not yet attached to the user's account. Wiring the design screen to the signed-in session is authentication work outside this phase.
2. **Execution release is impossible by design.** It needs wiring and terminal connectivity, control schematics, orderable parts and mechanical layout, which are Phase 2 and later.
3. **Blocked formats (CSV, QET, AML, PLC).** Each needs an engineering decision on a persistent, import-safe draft identification before it can be re-enabled. Options to evaluate:
   - a status column whose effect on EPLAN and ACE mapping is understood;
   - a visible title-block text element in the QET or AML diagram itself;
   - a PLC program that refuses to run in a DRAFT state.

   Until then they stay refused.

4. **Stored settings audit.** Profiles saved before strict nested validation might hold now-refused nested keys. They would then be refused on the next design, and their revisions would read as stale.
   - The default profile and every market profile load.
   - A read-only audit of the stored rows was not run in this phase (it was denied in this environment).
   - Run it before deploying.
5. **`POST /design/quotation`** prices a project the browser sends, with no integrity check. It returns data, not a file, so it carries no approval claim. Decide whether it should price only saved revisions.
6. **Smaller UX items:**
   - The over-long-field error names raw API paths (`request.info.customer`), not field labels.
   - The "Execution export blocked" list is visible before it is requested, which is deliberate, so the gaps are always visible.
7. **Arabic and Hebrew strings** were written by agents and need a native speaker's review.

## Environments left in place

Not cleaned up, by request:

- **Dev database `panelpilot`:** migrated to `b4e7a9c2d1f6` after the backup `backups/20261023-1430-pre-phase1/panelpilot.dump`.
- **Disposable databases:** `panelpilot_phase1_test`, `_adv`, `_backend`, `_mig`, `_migclean` and `_migprev` (the last holds the legacy-row upgrade).
- **Compose services:** api, web, worker, postgres, opensearch and redis.
- **Local test accounts:** disposable `e2e-p1*@example.com` accounts on the local stack. Their credentials are kept outside git.
- **Untracked, not in this PR:**
  - the earlier readiness assessment (`docs/design-readiness/assessment.md` and `evidence/`), held back for a privacy review of its reference material;
  - the unrelated repo-root `src/`.

## How the work was done

- **Two Claude Code sessions, one owner.** The lead session coordinated. A second session (panelpilot-73) also worked on the branch until the user made the lead the sole owner. Its work was kept and reviewed, not reverted.
- **Agents:** backend, frontend, adversarial, a format-gating implementer, a review-fix implementer, a browser-flow verifier, and an independent reviewer who did not implement the code.
- **Git:** only the lead committed. The branch history was rebuilt onto the #199 + #202 base without the assessment evidence. The tree of `apps/` and `packages/` was verified byte-identical to the tested commit.
