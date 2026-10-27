# Frontend workflow assessment (Agent 3)

Scope: the `/design` workflow on `main` at 48d2a75. I assessed it in two ways: by reading the code in `apps/web/src`, and by running one real browser trial against the live local stack (web on :3000, API on :8000). The trial used headless Chromium driven by Playwright, an anonymous trial session, and nothing else: no sign-up, no login, no save and no approval. Date: 2026-10-23.

All the inputs in the trial are labelled TEST and are not real project data.

## 1. Code walkthrough

### Routes
- `app/design/page.tsx` renders `components/design-screen.tsx`, which is a single client component of 1108 lines.
- The screen has seven views, selected with `?view=`: Overview, Load schedule, Drawings, Panel layout, Materials, Revisions, and Review & export. Every view stays mounted while hidden.
- Other routes exist but sit outside this workflow: `/calc`, `/plc`, `/review`, `/search`, `/team`, `/pricing`, `/tokens`, `/join`.
- On load, the screen gets an anonymous trial token itself (`acquireTrial`, which calls `POST /auth/trial`).

### Natural-language input (`components/design/schedule-sources.tsx`)
- There is one textarea, "Describe the board", and a "Suggest a schedule" button. The button calls `POST /design/load-schedule/suggest`.
- The result replaces the active board's load rows. There is also an import path that reads a consultant's .xlsx or .csv file.
- The suggestion produces load rows only: description, load type, kW and phases. It does not produce:
  - board header data (voltage, fault level, earthing, Ze)
  - cable lengths
  - power factors
  - starters
  - sub-boards
- Assumptions are shown as a bullet list under the button, for example "Socket power is a typical figure (150 W each)", "Diversity is not applied" and "Spares are not included".
- **The app never asks a question.** Missing facts are filled with typical values and listed as assumptions. The UI copy says so: "Typical powers are used where you give none; every assumption is listed".
- After design, missing cable lengths become notes in the result ("4 circuits have no cable length given; their voltage drop is not checked"). They are never prompted for.
- The only gate is `isComplete`: a project name, plus a name for each board and a description and power for each circuit. Its message reads "To design, name the project, and give every board a name and every circuit a description and a power."

### Editing
- **Boards:** the header fields are name, location, voltage, phases, fault level, earthing, Ze and fed-from. A sub-board takes a feeder length. Boards can be added and removed, and renaming a board keeps the boards it feeds pointing at it (`updateBoard`).
- **Loads** (`load-rows.tsx`):
  - Fields: description, kind, kW, phases, power factor, length, starter, and a "PLC switched" flag.
  - Load kinds: lighting, socket, AC, water heater, kitchen, fan, motor, lift, sub-board, control, data, other.
  - The starter choice is DOL, star-delta or VSD, offered only for motor, fan and lift.
  - **There is no soft starter, no ATS, no fire-pump kind and no generator or changeover.** A search of `apps/web/src` and `apps/api/app` for soft_starter, ATS, transfer switch or fire pump finds nothing outside a schematic symbols file.
- **Basis panel:**
  - Market: IEC, or one of 10 Middle East markets. It was auto-detected as Palestine in the trial.
  - Drawing language: en, ar or he.
  - Company settings (`company-settings.tsx`): per-kind rules for breaker, curve, RCD, minimum cable and demand factor; designation letters; title-block field order and labels; drawing-set page order; and raw JSON.
- **Stale-result protection on main: none.**
  - `result` (the last design) is never cleared when inputs change.
  - `download()` exports `result.response.project` (the old design) together with the current `profile`.
  - The Drawings, Materials, PLC and Markups views keep showing the old project with no warning.
  - The trial confirmed all of this (section 4).

### PR #199 (open, branch `fix/redesign-e2e-verification`)
What its description claims:
- M1: each result records the inputs it was built from. When they differ, a "Design again" warning appears and exports, pricing and PLC are blocked. Exports use the profile from design time.
- M1b: title-block revisions are part of the stale check.
- M2: Approve is disabled while there are unsaved changes.
- H1/H2: unsaved-changes guards on navigation and on opening another project.
- Also: dark-mode sheet contrast, tab history, and second-pass backend fixes (diagnostics citations, calculator validation, PLC verdicts).

What it does not claim:
- The description says plainly that it does not fix the views: Drawings is still a table, Layout is still an empty state, and Materials is still the quotation form only.
- Per `gh pr view --json files`, its file list in this repo view shows API files (diagnostics, retrieval, calculations).

I did not check out or run #199. Everything above is its description, not something I verified.

### Views on main
| View | What it actually shows |
|---|---|
| Overview | Project fields (name, number, customer, consultant, contractor) and the saved-projects panel (save, open, approve; needs an account). |
| Load schedule | Board tabs, the board header, the NL and import box, the load rows, and the basis (market, language, company settings). |
| Drawings | **A circuit table per board, not a schematic.** Columns: circuit, phase, Ib, breaker, RCD, cable, VD, Zs. It also shows design notes, a zoom control and an inspector. The page itself says "The drawings themselves are the drawing set (PDF) and DXF". |
| Panel layout | **An empty state:** "Panel layout preview is not available yet… the layout pages are part of the drawing set (PDF)". |
| Materials | **The quotation form only.** It takes a price-list upload, rates, markup and VAT, and offers quotation PDF and CSV. No BOM is shown; the parts list is a CSV export. |
| Revisions | Revision history and title-block revisions, both only for a saved project, plus the markups panel (read marked-up drawings into suggested edits). |
| Review & export | Approval status (only for a saved project; the trial shows "not saved"), 14 export buttons, and the PLC panel when there are contactors or drives. |

### Exports offered (`design-result.tsx` FORMATS)
- Drawings and reports: PDF drawing set, calculations PDF, DXF, QElectroTech (.qet), AutomationML (.aml).
- CSV lists: devices, parts, cables, circuits, terminals, EPLAN device import, AutoCAD Electrical components, AutoCAD Electrical terminals.
- Project: JSON.
- Elsewhere: quotation PDF and CSV in Materials, which needs prices. PLC program in Review.
- There is **no native EPLAN project** and **no AutoCAD Electrical drawing**. Those two targets get CSV import lists only.

### Approval and revisions
- Approval needs a saved project and a signed-in account, so it cannot be done in a trial.
- The calculations PDF prints "NOT APPROVED: issued for review only". **The drawing set PDF carries no approval or draft marker.** Its title-block Rev., Date, Drawn, Checked and Approved boxes were all empty. The two documents in one package disagree.

## 2. Real UI trial: inputs (all TEST values)
- Project: `TEST-POOL-01 (assessment test, not a real project)`, job number `TEST-0001`, customer `TEST customer`.
- Board: `TEST-DB-POOL`, location `TEST plant room`, 400 V, 3 phase, 10 kA, TN-S, Ze 0.35 Ω. The market was auto-detected as Palestine; I did not change it.
- Description: "TEST: pool plant room with one 5.5 kW three-phase filtration pump, 4 LED underwater lights, 2 sockets and a 3 kW water heater".
- Edit for the regeneration test: pump 5.5 kW → 7.5 kW.

## 3. Timings
These are measured by the script in a headless run. Scripted typing makes data entry much faster than a person would be.

| Step | Measured |
|---|---|
| Page load and trial session ready | 1.17 s |
| Header data entry (scripted fill) | 0.96 s |
| Suggest a schedule (real LLM call) | **8.07 s** |
| Design the board | 0.20 s |
| Each export | 0.15–0.70 s (PDF 0.70 s the first time, 0.23 s after) |
| Redesign after the edit | 0.98 s (includes a UI wait) |

**Estimate, not measured:** an engineer would need about 3–6 min. That covers typing the header, writing the description, then checking and correcting the four rows, and the missing lengths, power factors and starter, before designing.

### Suggestion output
It produced four rows:

| Row | Kind | kW | Phases |
|---|---|---|---|
| Filtration pump | motor | 5.5 | 3 |
| Underwater light | lighting | 0.12 | 1 |
| Plant room socket | socket | 0.3 | 1 |
| Water heater | water_heater | 3 | 1 |

What it left out:
- It assumed 30 W per light and 150 W per socket, and listed those assumptions.
- No cable lengths, no power factor, and **no starter for the pump**.
- **No RCD for the pool circuits beyond the default group RCD.** The pump circuit has no RCD at all.

### Design output (from the UI)
- Incomer -Q1 C63 4P.
- Group -Q2 C32 3P feeding -F1 RCCB 40 A 30 mA 4P, which supplies the light, socket and water heater circuits.
- Pump -Q6 D10 3P, 5G1.5, with **no RCD, no contactor and no overload**.
- 14 notes, including "Phase imbalance 59.6 % exceeds the company's 10 %" and the enclosure choice (ABB Mini Center).

## 4. Regeneration-consistency test (pump 5.5 → 7.5 kW)

**Before redesigning (stale-state check):**
- The Drawings view still showed 8.82 A / D10 for the pump.
- There was no warning anywhere. The page reported unsaved, but there is no "result is out of date" notice.
- I exported the PDF in this state. It is **byte-identical to the v1 PDF** (`cmp`), so the old design was exported silently.
- Evidence: `08-drawings-after-edit-before-redesign.png`, `stale-after-edit-before-redesign-*.pdf`.

**After "Design the board", by output:**

| Output | Changed | Unchanged |
|---|---|---|
| UI table | Pump Ib 8.82 → 12.03 A, breaker D10 → D13; imbalance note 59.6 % → 52.3 % | Cable 5G1.5 |
| circuits.csv | kW 5.5 → 7.5, Ib, rating 10 → 13 | Cable 5G1.5 |
| devices.csv, parts.csv | Q6 rating 10 → 13 | — |
| DXF | The same three values (D13, "7.5 kW Ib 12.03 A", imbalance) | — |
| PDF pages 3, 5, 6, 10 | D13, 7.5 kW Ib 12.03 A, 52.3 %, parts "D13" | — |
| calculations.pdf | Ib 12, D13 | — |
| cables.csv, terminals.csv | — | Byte-identical to v1 |

Verdict: once you redesign, every dependent output is regenerated consistently from the one project. Nothing disagreed across UI, CSV, DXF and PDF. The 1.5 mm² cable on the 13 A pump circuit is plausible by ampacity, but its voltage drop was never checked because there was no length.

The real risk is that nothing forces the redesign. On main, an edit followed by an export ships the old design without any warning. PR #199 claims to fix this.

## 5. Generated PDF drawing set (v2, 10 pages, A3 landscape, column/row grid frame, title block on every page)
I rendered the pages to PNG with pdf.js and inspected them visually, and extracted the text with pdftotext.

| Page | Content | Notes |
|---|---|---|
| 1 | Title page | Project, customer, job, "Issued under iec-default", "Design rules: software defaults, not confirmed". **It says iec-default while note 12 says "Designed for Palestine".** |
| 2 | Contents | Lists pages 1–10 correctly. |
| 3 | Main power (single-line) | Supply 3/N/PE 400 V TN-S Ik 10 kA → -Q1 → busbar → -Q2 → -F1 "to /4.0"; -Q6 "to /5.0 Filtration pump". Symbols are legible, with no overlap. The pump has no starter or overload. |
| 4 | Distribution loads | "from -F1 … /3.1", -Q3/-Q4/-Q5, cables -W1..-W3, loads with kW and Ib. |
| 5 | Distribution loads | Pump -Q6 D13, -W4 5G1.5, 7.5 kW Ib 12.03 A. |
| 6 | Design notes | 14 numbered notes. Minor: "Leave 1 spare outgoing ways". |
| 7 | Layout | Rail-length blocks per group (incomer 70 mm, RCD group 175 mm, busbar 52.5 mm). Terminals are dashed "-X1:1 ? … -X1:14 ?" because no rail width is set. This is a rail allocation, not a dimensioned enclosure or door layout. |
| 8 | Terminals | -X1:1..14 with conductor, circuit, cable and article (Siemens 8WH). It agrees with the cable list. The text extraction interleaves columns, but the rendered table is clean. |
| 9 | Cable list | -W1..-W4 from/to/type. **The Length column is empty.** |
| 10 | Parts list | Breakers and RCCB are "not selected" (no manufacturer or order number); terminals are Siemens 8WH1000. Quantities agree with parts.csv. |

Overall:
- Page numbering ("n of 10") and cross-references (/3.1, /4.0, /5.0) are consistent.
- Device ids (-Q, -F, -W, -X) match across the drawings, terminals, cable list, BOM and CSVs.
- Title-block Company, Date, Rev, Drawn, Checked and Approved are blank. The project name is truncated in the title block ("TEST-POOL-01 (assessment t…").

**Calculations PDF (2 pages):** basis of calculation with IEC/ABB references, a per-circuit table, and notes. It is readable. L, dU and Zs are "-" because no lengths were given.

**Arabic drawing set (v3, 10 pages):**
- Title-block labels and page titles are in Arabic ("أحمال التوزيع", "4 من 10").
- Device text and user-entered circuit names stay in English, as entered.
- The cross-reference "‎-F1 40 A 30 mA 4P /3.1 من" has a bidi-reordered word order. That is readable but awkward.
- Hebrew was not tested.

**Other files:**
- DXF: 89 kB, with the same texts as the PDF.
- QET: XML project.
- AML: AutomationML XML.
- EPLAN CSV: semicolon-separated, DT `=TEST-DB-POOL+TEST plant room-Q1`.
- ACE CSVs: component and terminal rows.
- JSON: the full project.
- The location "TEST plant room", with spaces, goes straight into the `+` location designation.

## 6. UX gaps blocking "a few simple steps"
1. **No missing-data questions.** The workflow is "describe, then the app silently assumes". It never asks for cable lengths, power factor, starter type, Ze or diversity. The result therefore leaves VD and Zs unchecked and only says so in notes.
2. **NL input fills loads only.** It does not set board type or family, supply, starters or sub-boards. A family such as "pump with soft starter", "MDB with ATS" or "fire pump" cannot be expressed: there is no soft starter, ATS or fire-pump support in the UI or API schema. The pool pump got no RCD and no motor starter or overload, and nothing prompted for one.
3. **Stale results on main.** Edits after a design leave old Drawings, Materials and exports with no warning. The trial exported a stale PDF. PR #199 claims a fix, which I did not verify.
4. **The Drawings view is a table.** Panel layout is an empty state, and Materials has no BOM view. Reviewing the actual drawings needs a PDF download. Editing happens only through the schedule form, or markups of uploaded drawings; there is no on-drawing editing.
5. **Approval needs an account and a saved project.** The exported drawing set has no draft or approval stamp and empty Rev/Date/Drawn fields, while the calculations PDF says "NOT APPROVED". Approval status in the package is inconsistent.
6. **Market and profile inconsistency.** The market was auto-detected (Palestine) without asking, and the title page says "iec-default".
7. **BOM devices are "not selected".** There are no manufacturer part numbers for the breakers, so the parts list cannot be used to order material without manual work.
8. **The layout page is rail allocation only.** Terminals show "?" widths with default settings.

## 7. Files saved (this folder)
- Screenshots:
  - `01-overview-test-project.png`
  - `02-schedule-after-suggestion.png`
  - `03-drawings-view-v1.png`
  - `04-panel-layout-view.png`
  - `05-materials-view-v1.png`
  - `06-revisions-view.png`
  - `07-review-export-view-v1.png`
  - `08-drawings-after-edit-before-redesign.png`
  - `09-drawings-view-v2.png`
- First design (5.5 kW pump), all 14 export formats: `v1-TEST-POOL-01-assessment-test-not-a-real-project.*`
  - `.pdf`, `.calculations.pdf`, `.dxf`, `.qet`, `.aml`, `.json`
  - `.devices.csv`, `.parts.csv`, `.cables.csv`, `.circuits.csv`, `.terminals.csv`, `.eplan-devices.csv`, `.ace-components.csv`, `.ace-terminals.csv`
- Stale export (pump edited, not redesigned): `stale-after-edit-before-redesign-*.pdf` (identical to v1).
- After redesign (7.5 kW pump): `v2-*` with `.pdf`, `.calculations.pdf`, `.dxf`, `.parts.csv`, `.cables.csv`, `.circuits.csv`, `.devices.csv`, `.terminals.csv`, `.json`.
- Arabic drawing set: `v3-arabic-*.pdf`.
- Rendered PDF pages:
  - `pdf-v2-p01.png` … `pdf-v2-p10.png`
  - `pdf-v3-arabic-p01.png` … `pdf-v3-arabic-p10.png`
  - `pdf-v2-calc-p01.png`, `pdf-v2-calc-p02.png`
- `trial-log.txt`: the raw step log, timings, UI texts and API calls.

The trial made these API calls, all 200/201:
- 1 × trial start
- 1 × suggest
- 3 × design
- 24 × export

There were no 429 responses.
