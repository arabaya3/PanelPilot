# Phase 1, signed-in browser verification (local dev app)

- Date: 2026-10-23. Web `http://127.0.0.1:3000`, API `http://127.0.0.1:8000/api/v1`, branch `feat/phase1-trustworthy-export` at `32b2e96` (as restarted by the caller; not re-checked).
- Driver: Playwright MCP (Chromium). Each actor ran in its own fresh browser context (`browser.newContext()`), so no storage was shared. The scripts are kept in the git-ignored `.playwright-mcp/s/`.
- Accounts: three disposable accounts (A, B, C), `e2e-p1-signed-<hex>-<n>@example.com`, with random passwords. They are stored only in `.test-accounts.local`, which is git-excluded. Neither passwords nor tokens were printed. The tokens were compared in memory only.
- A and B were created through the app's own "Create an account" form on `/design`, starting from an empty trial (signup 201 each). C was created in step 7.
- "Network panel": a Playwright `request` listener on the page records the method, the path and whether an `Authorization: Bearer` header was present. The token value itself was never logged. The MCP's own network panel only covers its default context, which was not used for the actors.
- "Console fetch": `page.evaluate(() => fetch('/api/v1/...'))`, run in the page itself. Access tokens are held only in memory by design, so the script read the header from one of the page's own requests and passed it into that fetch.

Project under test: `cde1feb4-2ebc-4d71-9231-164451017d49` ("P1 Signed Board", account A).

## 1. Sign in as A on /design: PASS

- Fresh context, `/design`, "Sign in to save to your account", then the inline "Sign in" form. `POST /auth/login` returned 200.
- The page shows "Signed in. Projects you save, approve and export here belong to your account." with a "Sign out" button. The "Trial workspace" notice is not shown.
- Evidence: `01-signed-in-A-design.png`

## 2. Design, save revision 01, check ownership and isolation: PASS

- Board DB1 has two circuits: Sockets hall (2 kW, 20 m) and Lighting hall (lighting, 0.8 kW, 15 m). After "Design the board" and "Save project" with the note "first issue": `POST /api/v1/design/projects` returned 201, `{id: cde1feb4…, revision: 1}`. Evidence: `02-designed-board.png`, `03-saved-rev01.png`.
- Network: all 20 `/design*` requests (GET list, POST `/design/project`, POST `/design/projects`) carried `Bearer`, and all used the same value.
- Console as A:
  - `GET /api/v1/design/projects` returned 200 and lists `cde1feb4… "P1 Signed Board"`.
  - The same call without the header returned 401.
- B, signed in through the UI in a fresh context: login 200, and B's token differs from A's.
  - `GET /design/projects` returned 200 with `{"projects":[]}`.
  - `GET /design/projects/cde1feb4…` returned 404 `project_not_found`.
  - The page says "No saved projects yet." Evidence: `04-account-B-does-not-see-A-project.png`.

## 3. Revision 02, approved as "Eng. Signed Test": PASS

- Sockets changed to 2.5 kW, designed again, then "Save as new revision" with the note "sockets 2.5 kW". `POST …/revisions` returned 201 with revision 2.
- Approved revision 2 as "Eng. Signed Test". The page shows "Revision 2 approved by Eng. Signed Test on 2026-10-23." and "Approved while signed in as <A's email>." Evidence: `05-rev02-approved.png`.
- The server's record, `GET /design/projects/{id}?revision=2`, has rev 2 with:
  - `approved_by: "Eng. Signed Test"` and `approved_account: <A's email>`;
  - `export_status: review_approved`;
  - `approved_snapshot_sha256 = snapshot_sha256 = 3c04b20d09f599bf…7331`;
  - rules `2026.10.23.1`.

  Rev 1 has `approved_by`, `approved_at` and `approved_account` all null, and is `draft`.

- Forging the account: `POST …/revisions/2/approval {approver, approved_account:"forged@example.com"}` returned 422 `request_invalid`, `extra_forbidden` at `body.approved_account`. The client cannot supply the account.
- Seen here: right after the approval, the page showed "The inputs have changed since this design was made…". See defect D1.

## 4. Reload, sign in again, reopen revisions 01 and 02: PASS

- After a reload the page is signed out (tokens are memory-only) and shows the explicit "Trial workspace" notice. Evidence: `06-after-reload-signed-out.png`.
- Signed in as A again; the list shows "P1 Signed Board, 2 revisions". Clicking "Open" opens revision 2:
  - powers `[2.5, 0.8]`, with the approval paragraphs ("approved by Eng. Signed Test", "Approved while signed in as <A>");
  - evidence: `07-reopened-rev02-approved.png`.
- Revision 1: powers `[2, 0.8]`. It has no approval text, and the approval input "Approve revision 1" is offered. Evidence: `08-reopened-rev01-no-approval.png`.
- Revision 2 again: powers `[2.5, 0.8]` and the approval is back. The stale banner is not shown.

## 5. Export revision 02, blocked formats, execution: PASS

- Every download went through `POST …/revisions/2/export` with `purpose: "review"` and returned 200:
  - `pdf` → `downloads/rev02-P1-Signed-Board.pdf`
  - `calculations_pdf` → `downloads/rev02-P1-Signed-Board.calculations.pdf`
  - `dxf` → `downloads/rev02-P1-Signed-Board.dxf`
  - `json` → `downloads/rev02-P1-Signed-Board.json`
- Blocked formats: QElectroTech, AutomationML and all 8 CSVs are disabled, with "Not available yet: this format cannot keep a visible draft warning once imported. Use the PDF or DXF for review." Evidence: `09-export-panel-blocked-formats.png`.
- From the console, `devices_csv` returned 409 `export_format_unprotected`, `{reason: draft_mark_not_persistent, allowed: pdf,dxf,quotation_pdf,calculations_pdf,json}`.
- "Request execution export" sent `{format: pdf, purpose: execution}` and got 409 `execution_package_incomplete`:
  - `missing = wiring_and_terminal_connectivity, control_schematics, orderable_parts, mechanical_layout`;
  - `approval: present`.

  The UI shows "Execution export refused. No execution package was exported. Still needed:" followed by the 4 items. Evidence: `10-execution-refused.png`.

### Inspection of the downloaded files

The files were read by pdfplumber and ezdxf in a throwaway `panelpilot-api` container, with the files mounted read-only and the script run with `-I`.

| File             | Mark on every page                                                                                                 | Approver           | Hash            | Rules           | Approving account |
| ---------------- | ------------------------------------------------------------------------------------------------------------------ | ------------------ | --------------- | --------------- | ----------------- |
| Drawing PDF      | "REVIEW APPROVED — STUDY, NOT FOR EXECUTION" on 10/10 pages; no DRAFT, NOT APPROVED or FORGED                      | "Eng. Signed Test" | `3c04b20d09f5`  | `2026.10.23.1`  | **absent**        |
| Calculations PDF | 2/2 pages; "Review approved by Eng. Signed Test: a study, not for execution."                                      | yes                | yes             | yes             | **absent**        |
| DXF              | ezdxf audit: 0 errors. 40 mark texts on layer `TEXT` (on, not frozen)                                              | yes                | yes             | yes             | **absent**        |
| JSON             | envelope `{export_status:{label:"REVIEW APPROVED — STUDY, NOT FOR EXECUTION", identity:…}, findings[11], project}` | in the identity    | in the identity | in the identity | **absent**        |

- The hash and approver match the server record from step 3.
- Renders of page 1, which I looked at: `30-render-drawing-pdf-page1.png` and `31-render-calculations-pdf-page1.png`. The stamp and identity line are legible top and bottom. The revision table shows 01 "first issue" without an approver and 02 with "Eng. Signed Test".
- See D5 (the account is not in the exports) and D6 (page count).

## 6. Sign out, then a simulated session expiry: PASS (with D2 and D3)

- Sign out: the workspace cleared (project name empty, saved list gone). The page shows "You are signed out. Sign in to work in your account, or continue as a trial…" with "Continue as a trial".
  - The only API calls were 2× `GET /design/markets` without auth.
  - No trial session was acquired, so there was no silent trial.
  - Evidence: `11-signed-out-cleared.png`.
- Expiry: signed in as A, opened the project, then made unsaved edits:
  - Customer set to "Unsaved Customer X";
  - a third circuit added, "Water heater unsaved" (3 kW, 12 m);
  - designed again.

  Then a devtools-style `window.fetch` override was installed:
  - every non-GET `/api/v1/` write gets `Authorization: Bearer invalid-simulated-expiry`;
  - `/auth/refresh` gets an invalid refresh token.

  Then "Save as new revision" with the note "expiry test".

- Network:
  - `POST …/revisions` (invalid auth) returned 401;
  - `POST /auth/refresh` returned 401;
  - there was no trial request and no other save.
- The page shows "Your session has ended. Sign in again to keep working in your account. Nothing on this page has been lost, and nothing is saved anywhere else meanwhile.", "Save failed", and an inline sign-in form.
  - Customer and all 3 circuits were kept.
  - Evidence: `12-session-ended-data-kept.png`.
- With the override removed, A signed in again through the inline form (200). The edits were still there.
  - "Save as new revision" sent `POST …/revisions` with Bearer and returned 201: revision 3, `author = <A's email>`.
  - Evidence: `13-relogin-saved-under-A.png`.
  - **But the revision note "expiry test" was lost: revision 3 has `note: ""`** (D2).
- An extra check: an access token captured before "Sign out" still returned 200 on `GET /design/projects` after sign-out (D3).

## 7. Trial → account: PASS

- Trial into a new account (C): a fresh context showed the "Trial workspace" notice.
  - Designed and saved "P1 Trial To C": 201, id `2ab61e1d-31c4-4a6e-9d18-0396ed688ace`, author `""`. Evidence: `14-trial-saved.png`.
  - "Create an account" shows "This trial's saved projects move into the new account, and what is on the page stays." The signup was 201 and sent `claim_session_id` and `claim_secret`.
  - The page now says "Signed in…" and the project name was kept.
  - As C: `GET /design/projects` lists `2ab61e1d… "P1 Trial To C"`, and `GET` on its id returns 200. Evidence: `15-trial-claimed-by-C.png`.
- Trial, then signing in to the existing account A: a fresh trial saved "P1 Trial Stays" (201, id `82dfc633-98b2-4eaa-b1e5-0ccb8cdbce79`). Customer "Typed after save" was then typed and not saved, and A signed in.
  - Notice: "The project you had open belongs to where you were working before, so it stays there. What you entered is still here, unsaved: save it to keep it in this account."
  - Name, customer and power (1.2) were all kept.
  - A's list contains only "P1 Signed Board", and `GET` on the trial id as A returns 404.
  - Evidence: `16-trial-not-moved-into-A.png`.
  - Minor: the stale status "Saved as revision 1." from the trial save is still shown under A's list (D7).

## 8. Quotation estimate: PASS

- On the Materials tab, the heading is "Provisional price estimate". I entered rates and clicked "Price the board".
  - `POST /design/quotation` returned 200, `status: provisional_estimate`, `blocking: false`.
  - Banner: "Provisional estimate — not a quotation, not for execution" / "Priced from the unsaved design on this page. A quotation is exported from a saved revision."
  - The word "approv…" does not appear in the panel. Evidence: `17-quote-en.png`.
- Arabic: heading "تقدير مبدئي للسعر", banner "تقدير مبدئي — ليس عرض سعر، وليس للتنفيذ".
- Hebrew: heading "הערכת מחיר ראשונית", banner "הערכה זמנית — לא הצעת מחיר, לא לביצוע".
- No approval wording ("معتمد" / "מאושר") appears in either panel. Evidence: `18-quote-ar(.png|-full.png)`, `18-quote-he(.png|-full.png)`.
- Tampered request from the console: the page's own quotation body, with `c2-breaker.rated_current_a = "200"` on the 3G1.5 cable `c2-cable`, sent with A's Bearer. It returned 200 with:
  - `status: provisional_estimate`, `blocking: true`;
  - a blocking finding `integrity_device_above_cable_capacity` (circuit c2, device c2-breaker): "Lighting hall: 200 A protection is above the 17.5 A its 3G1.5 cable carries (method B1); In <= Iz does not hold."

## 9. Arabic and Hebrew: PASS with translation and RTL defects (D4, D8)

- `<html dir="rtl">` with lang ar or he.
- These are translated, with no raw codes (snake_case or message keys) found by a text scan of `main`:
  - the account notice (signed in), the trial notice and the sign-up form;
  - the readiness steps (محفوظ / متحقَّق منه / معتمد للمراجعة / جاهز للتنفيذ ممنوع; נשמר / נבדק / אושר לסקירה / מוכן לביצוע חסום);
  - findings, the export panel and the quotation panel.
- Evidence: `20-review-export-{ar,he}(-full).png`, `22-trial-notice-{ar,he}.png`, `23-trial-signup-form-{ar,he}.png`.
- The drawing is not mirrored: the sheet table stays left to right (Circuit column at the left). Evidence: `21-drawings-{ar,he}.png`. But see D8 for the notes inside the sheet.
- English that remains inside ar and he text (D4):
  - the market name "Palestine" and "(adopted by the Palestine Standards Institution)" in the market finding;
  - quotation line items ("Circuit-breaker 4P C63", "Cable 3G2.5 Cu PVC", "Wiring and testing, per circuit"…);
  - the units "pcs" and "(m)".

## Defects

- **D1: the page shows a design as stale right after an approval, or after switching language** (medium; it blocks pricing and export until "Design again").
  - Reproduce (approval): sign in, design a board, "Save project" or "Save as new revision". The banner is absent. Then enter an approving engineer and approve. "The inputs have changed since this design was made… design again before exporting or pricing." appears.
  - Reproduced twice (revisions 2 and 4). Evidence: `24-DEFECT-stale-banner-after-approval.png`.
  - Reproduce (language): with a current design, switch Language to العربية or עברית. The same banner appears, and the Materials tab replaces the estimate with "التصميم لم يعد محدّثًا" / "התכנון אינו עדכני". Evidence: `19-DEFECT-language-switch-marks-design-stale-ar.png`.
  - Switching back to English also marks it stale.
  - Likely cause: the approval's title-block rows, or the locale, are part of the calculation-input signature. This is the same family as the fix described in the Phase 1 report ("covered by vitest only; not re-checked in the live browser").
  - Also, the quotation rate inputs (labour, enclosure, cable length) were cleared after a language switch and redesign.
- **D2: the revision note is lost across a session expiry** (low).
  - Reproduce: signed in, open the project and edit it. Type the Revision note "expiry test", make the token invalid (step 6 override), then click "Save as new revision": 401 and "Your session has ended".
  - Sign in again in the inline form and click "Save as new revision". The new revision has `note: ""`.
  - The page claims "Nothing on this page has been lost".
- **D3: Sign out does not end the session on the server** (medium, security).
  - "Sign out" makes no API call. An access token captured before sign-out still gets 200 on `GET /api/v1/design/projects` afterwards.
  - The refresh token is not revoked either (no logout request). It is unclear whether this is within Phase 1, but anyone holding a copied token keeps access until it expires.
- **D4: English inside Arabic and Hebrew text** (low, translation):
  - the market name and the standard's "adopted by…" phrase in findings and drawing notes;
  - quotation line descriptions and units ("pcs", "m", "(m)").
- **D5: exports do not carry the approving account** (low, or a decision for the caller).
  - The drawing PDF, calculations PDF, DXF and JSON identity name the approver "Eng. Signed Test", the hash and the rules version, but not `approved_account`. The account is only in the API and UI.
  - If the account is meant to be printed, it is missing.
- **D6: page numbering in the drawing PDF** (low).
  - pdfplumber counts 10 pages, but the title block on page 1 says "1 of 9" (the findings sheet is probably not counted). Not investigated further.
- **D7: a leftover status after the account switch** (low).
  - After a trial save and then signing in to an existing account, "Saved as revision 1." (from the trial save) is still shown next to the account's list.
- **D8: RTL text inside the drawing sheet is laid out left to right** (low, RTL).
  - In he and ar, on the Drawings tab, the design notes under the sheet table are Hebrew or Arabic sentences in a left-to-right block. Their punctuation and numbers are reordered (for example a line starting "32-מפסק קבוצה 1 הוגדל ל A, 16…" and ending with "לסלקטיביות.").
  - The geometry correctly stays left to right, but the text paragraphs need `dir="auto"` or `dir="rtl"`. Evidence: `21-drawings-he.png`.
- Observation, not a defect: a reload drops the signed-in session (memory-only tokens) and the page falls back to the explicit "Trial workspace" notice. It is not silent, but every reload needs a new sign-in.
- Observation: the list `GET /design/projects` is fetched frequently (12+ times during one design-and-save sequence).
