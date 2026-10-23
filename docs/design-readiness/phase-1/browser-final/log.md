# Phase 1 browser verification (final), 2026-10-23

Local only: web http://127.0.0.1:3000, API http://127.0.0.1:8000/api/v1 (commit 286cc93). Test accounts A and B were created with POST /auth/signup (201 each). Their credentials are in `.test-accounts.local` only.

**Browser note.** Claude in Chrome loaded the pages, but every screenshot timed out (`Page.captureScreenshot` 30 s, then the extension disconnected). The run was therefore done in the Playwright Chromium browser on the same local app. The Playwright MCP also left copies of the downloads and console logs in `<repo>/.playwright-mcp/`.

**Identity note (important).** I signed in as A through the app's sign-in form (`/review`). The `/design` screen does not use that session, though. It always runs on its own anonymous **trial** session (`design-screen.tsx` `connect` → `acquireImpl`), so the project was saved under the trial tenant, not under A. A's own token lists 0 projects and gets 404 on the project. The console steps below therefore use the design screen's own trial token, taken from the screen's request header and never printed. In the steps below, "A" means that trial tenant.

Project id: `9e1c90ef-552d-4598-9f1b-f8be3a795cc2`.

| # | Step | Result | Evidence |
|---|---|---|---|
| 1 | Designed a 3-load board (sockets 2 kW, lighting 0.8 kW, AC 2.5 kW) and saved it as revision 1 | PASS | 01-designed-board.png, 02-saved-rev1-review.png |
| 2 | Findings show a severity (Warning/Information), the place to fix (Board DB1, "Go to board" / "Go to row 3"), and "Needs an engineer's confirmation". Readiness reads Saved Done, Validated Done, Review approved Not yet, Execution ready Blocked, with the reason | PASS | 02 |
| 2a | Right after saving, the banner "The inputs have changed since this design was made" appears and Draft export says "out of date", although only the revision note was typed after designing. Seen after save 1 and again after save 2 | DEFECT (minor) | 02, 05 |
| 3 | Edited the AC to 3.5 kW. Saved and Validated show Out of date; "The page has changes revision 1 does not hold…"; findings marked "Recorded for the saved revision"; revision export says "Save a new revision…"; after designing again, draft export reads DRAFT — NOT FOR EXECUTION. The edited value 3.5 was kept | PASS | 03, 04 |
| 4 | Saved revision 2. A whitespace-only approver leaves the Approve button disabled, and the input is kept ("    "). The API also refuses `{"approver":"   "}` with 422 string_too_short. Approved as "Eng. Browser Test": header shows Rev. 2 Approved | PASS | 05, 06 |
| 5 | Opened revision 1 (AC 2.5, Not approved, Approve button offered), then revision 2 (AC 3.5, approved by Eng. Browser Test). The history shows the approval only on 02 | PASS | 08-opened-rev1.png, 09-opened-rev2.png |
| 6 | Revision 2 review export: PDF, calculations PDF, DXF and JSON downloaded. QET, AML and all 8 CSV buttons are disabled with "Not available yet: this format cannot keep a visible draft warning…". The quotation PDF and PLC program are not offered on this screen. The execution request returned 409 execution_package_incomplete, which lists wiring/terminals, control schematics, orderable parts and mechanical layout (approval present, no blocking findings); no file was returned | PASS | 10, 11, downloads/06-* |
| 6a | The "Execution export blocked" box is shown before the button is pressed | Note | 02, 10 |
| 7 | Draft PDF of the unsaved edit: DRAFT on all 10 pages, plus "Unsaved draft: not a saved, validated or approved revision"; no approver | PASS | downloads/07-*, 20-render-07-* |
| 8 | Customer of 250 characters and circuit description of 600 characters (the API limit is 200 for both, not 500). Save gives 422 and the message names both fields with nested paths (`request.info.customer`, `request.boards[0].loads[0].description`) in EN, AR and HE. The data is kept (250/600). The page is dir=rtl in AR/HE, and the drawing table stays left to right, not mirrored | PASS; the field paths are raw API paths, not translated labels (cosmetic) | 12-14, 15-drawings-hebrew-rtl.png |
| 9 | POST /design/projects with `boards[0].standby_supply` and `loads[0].colour` returns 422 `{error: RequestValidationError, code: request_invalid, detail: [extra_forbidden loc body.request.boards.0.loads.0.colour, …standby_supply], params.count 2}`. I found no UI path for it, so the API response is recorded here | PASS (API) | this log |
| 10 | Without a token: GET project 401, revision export 401. Account B: GET, export, approve, revise and delete all 404 (project_not_found / revision_not_found), and B's list is empty; the project remained intact (6 revisions). Account A's real session, being a different tenant from the trial, also got 404. The UI has no project URL to open (/design carries no id) | PASS (API) | this log |
| 11 | Draft export with breaker c2 set to 200 A on 3G1.5 and every revision row's approved_by/checked_by set to "FORGED": PDF, calc PDF and DXF returned 200. DRAFT is on every page; "[BLOCKING] DB1 / c2: 200 A protection is above the 17.5 A its 3G1.5 cable carries"; no FORGED and no REVIEW APPROVED anywhere; the calc PDF says NOT APPROVED | PASS | downloads/11-*, 20-render-11-* |
| 12 | 4 parallel revision saves → 201 ×4 with distinct revisions 3, 4, 5, 6. 3 parallel approvals of revision 5 → one 200 and two 409 revision_already_approved; no 500. approved_snapshot_sha256 equals snapshot_sha256 | PASS | this log |

## File inspection (pdfplumber/ezdxf in panelpilot-api)
- 06 revision-2 PDF (10 pages) and calc PDF (2 pages): "REVIEW APPROVED — STUDY, NOT FOR EXECUTION" on every page; "revision 02, approved by Eng. Browser Test on 2026-10-23, design f47fd7310f37, rules 2026.10.23.1" (sha f47fd731… matches the database). No DRAFT, no FORGED.
- 06 JSON: carries the approval and the hash.
- 07 and 11 draft PDFs: DRAFT on every page, no REVIEW APPROVED, no FORGED.
- DXF: 20 mark texts in each file (06 REVIEW APPROVED, 11 DRAFT), on layer TEXT, which is on and not frozen. FORGED is absent from the draft DXF.
- Page-1 renders (20-render-*.png): the stamp is legible, the identity line sits separately at the right/top and bottom, and nothing overlaps.
