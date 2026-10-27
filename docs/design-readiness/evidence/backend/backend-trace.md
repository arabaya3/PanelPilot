# Backend design-path trace (Agent 2)

Repository at `main` 48d2a75. Read-only review: no code was changed. Paths are relative to `apps/api/app/`.
The test input used for the live calls is a **TEST INPUT** I made up for this check. It is not a real project and its electrical values are not engineering facts.

## Stage table

| Stage | Status | Evidence (path:function) | Notes |
|---|---|---|---|
| User request (plain language) | Implemented, not verified live | `api/v1/routes/design.py:suggest_load_schedule` → `domain/design.py:suggest_load_schedule` → `ai/schedule_writer.py:write_schedule` (LLM) → `design/schedule_split.py:split_points` | The LLM only pulls out *points* (description, kind, quantity, unit kW, 3-phase, `power_stated`). The split into circuits follows fixed rules. It returns a draft `LoadInput[]` plus assumption notes. It does **not** produce a design. I did not call it (it needs the model and has a trial rate limit). |
| Structured requirements | Implemented and verified (unit + live) | `models/schemas/design.py:DistributionBoardRequest/LoadInput/Supply/InstallationConditions`; `design/schedule_import.py:import_schedule` (Excel/PDF) | Load kinds are lighting, socket, AC, water heater, motor, fan, kitchen, lift, sub_board, control, data, spare and other. There is no kind for pump, fire pump, pool or KNX. |
| Missing-data questions | **Missing** | No endpoint or function asks the user anything | Missing data turns into assumption notes (`default_power_factor`, `motor_current_formula`, `earth_fault_no_ze`, `rules_unconfirmed`) or a hard refusal (`ValidationError`). Nothing asks the engineer a question. |
| Design model | Implemented and verified | `design/project.py:design_boards`, `design_order`, `_check_supply`; `design/distribution.py:design_distribution_board`; `designations.py:designate_project` | One `DesignProject` (boards → devices / cables / circuits / notes, plus parts). Feeding between boards is checked: loops, a board feeding itself, voltage or phase mismatch (`boards_loop`, `board_voltage_mismatch`, ...). |
| Calculations | Implemented and verified (unit + live) | `distribution.py:_size`, `_size_cable`, `_check_drop`, `_check_starting`, `_check_disconnection`, `_check_short_circuit`, `_incomer`, `_group_rating`, `balance_phases`; `voltage_drop.py`, `short_circuit.py`, `fault_level.py`, `disconnection.py`, `mccb.py`; `ai/tools/cable_sizing` | Each calculation cites a manufacturer table or an IEC clause (see the calculations PDF basis). Results are *notes* with no severity. "exceeded" results do not block anything (see the validation inventory). |
| Device selection | Partial | `design/motors.py:_coordinated` (ABB handbook Type 2 tables), `_drive` (ACS880 + Bussmann aR), `mccb.py`, `terminals.py:_articles` (Siemens 8WH1), `enclosure.py:notes_for` (4 hard-coded ABB Mini Center enclosures) | Motor-branch devices get a maker and type, and terminals get Siemens articles. **Modular MCBs, RCCBs and the incomer are rated only**, shown as "not selected" in the parts list. `motors.py:parts_for` sets `order_number=None` for every part. The enclosure is a *note*, and it is skipped when any device lacks a width (it was skipped in the live run). |
| Connections / control logic | Partial | `Device.upstream_id`, `Circuit.device_ids/feeds`; `terminals.py:strip`; `plc_program.py:build_program` | Feeding is modelled device to device. Terminals cover only one outgoing power strip, X1. There are no control circuits and no wire numbers (see below). The PLC Structured Text covers DOL, star-delta, drive and switched loads. |
| Drawing layout | Partial (schematic, not to scale) | `design/pages.py:build_drawing_set`, `_draw_layout`; `layout.py:rails` | The "Layout" page is a DIN-rail strip diagram in mm, drawn only where `rail_widths_mm` is known. Every device had "?" in the live run. There are no ducts, clearances, enclosure outline or mounting-plate drawing. The to-scale physical layout is deferred (`docs/tasks/panel-design-lane.md` PD-009). Duct fill (PD-004) is deferred. |
| Validation | Partial | Schema validation (pydantic); refusals via `ValidationError` (codes listed below); review notes | Refusals only cover cases that cannot be calculated. Results that fail a check go through with a note. |
| Approval | Implemented but not enforced | `domain/design_projects.py:approve_revision`, `revise_project`, `save_project` | Approval writes `approved_by/at` on a saved revision, once. **Export never looks at it.** |
| PDF | Implemented and verified (live) | `domain/design.py:export_design` → `pages.build_drawing_set` → `render_pdf.render_pdf`; `calc_report.render_calculations_pdf` | 10 pages for the test input (described page by page below). |

## Distinctions, with verdicts

1. **Reading plain language vs generating a design.** These are separate. The LLM (`ai/schedule_writer.py:write_schedule`) only drafts a schedule. The design itself is deterministic Python (`project.design_boards`). **Verdict:** the LLM interprets the request; everything after that is rule-based. No LLM decides ratings.
2. **Calculated ratings vs parts you can order.** Live run: 8 parts, all with `order_number: null`. MCBs, RCCB and the incomer show "not selected". Motor-branch keys such as `ABB/T2S160 MA 20` and `ABB/A30` come from the ABB handbook tables, not from a catalogue. The EPLAN catalogue path (`ingestion/eplan_catalogue.py`, `ai/tools/enclosure_sizing.py`, PD-001..PD-003) is **not imported anywhere in `app/design/`** (grep). Only `domain/calculations.py` (the separate calculation tools) uses `panel_bom`. **Verdict:** ratings are calculated. Most parts cannot be ordered from the output. The catalogue data is not connected to the design path.
3. **Feeding between devices vs connections at terminals.** The model is `Device.upstream_id` plus `Circuit.device_ids`. `terminals.py:strip` gives one strip X1 with a function per cable core (L1/N/PE, U/V/W, U1..W2). It has no bridges, no control terminals, no internal wiring and no wire numbers. `CompanyProfile.wire_numbering` exists in the schema but nothing in `design/` reads it (grep). AML and QET exports join "fed from" links. **Verdict:** connectivity is device-level, plus outgoing power terminals only.
4. **Circuit table vs electrical schematic.** `pages.py:_draw_main` and `_draw_distribution` draw **single-line** power diagrams with IEC 60617-style symbols (`symbols.py`) and page/column cross-references. `export_qet.py` writes an editable QElectroTech single-line diagram. There are **no multi-line or control-circuit schematics**: no coil, contact, pushbutton or lamp circuits. `PageKind` has no control page. **Verdict:** a single-line schematic exists. The control and wiring schematics a fabricator needs do not.
5. **Decorative geometry vs layout with real dimensions.** The rail strip uses real mm only when the company gives widths. Default profile: no widths, so every slot shows "?" and the enclosure is not selected (`enclosure_unknown_widths`). **Verdict:** the layout is not dimensionally meaningful by default. There is no enclosure, duct or clearance geometry.
6. **A PDF with content vs a complete execution package.** The PDF has: title, contents, main power single-line, distribution single-line pages, design notes, rail layout, terminals, cable list and parts list. A separate PDF holds the calculations. It lacks: control schematics, wiring/terminal diagrams with wire numbers, a dimensioned enclosure/GA drawing, door layout, labels/legend schedule, order numbers, and test/inspection sheets. **Verdict:** this is a populated PDF, not a fabrication package.
7. **Disclaimer vs enforced gate.** `export_design` checks only billing features (ECAD, quotation) and that the profile parses. It re-runs designations but **not** the design. Tamper test: I raised the design current to 200 A on a 10 A breaker with a 1.5 mm² cable. Export still returned 200 and wrote the values out (`export.tampered-circuits.csv`). The drawing title says "not confirmed by the company's engineers". The calculations PDF says "NOT APPROVED". These are labels, not blocks. **Verdict:** they are disclaimers. No gate blocks export.
8. **Control logic.** Starters are `MotorStarter = dol | star_delta | drive`. There is **no soft starter** (only mentioned in the text of `notes.py`). The star-delta interlock is a *note* plus PLC interlocks (`plc_program.py:_motor`, star timer T#6S, 100 ms changeover gap). H-O-A is hand/auto only (`Auto_Mode`, there is no "off" input), with `EStop_OK` and `Schedule_On`. Alarms: only overload and drive-ready feed the run condition. There are no alarm outputs and no lamps. **KNX:** `DeviceKind.BUS_ACTUATOR` exists with a designation letter and a page label, and nothing else. **ATS / MDB with two supplies:** not modelled. A board has one `Supply` and `incomer_ids`, and there is no ATS device kind (grep "ATS|transfer switch" found nothing in design). **Fire pump:** nothing (grep "fire.?pump|NFPA" found nothing). It would be treated as a generic motor with overload trips, which fire-pump practice generally forbids. That claim is engineering judgement and needs an engineer to confirm. **Pool panel:** nothing specific (no pool or SELV handling).
9. **One consistent model?** Yes and no. Every export is built from the `DesignProject` passed in (`export_design`), so the outputs agree with each other. But the client sends the whole project. The server does not rebuild it from the request, so an edited or stale project goes out unchecked. Saved revisions store the *request* (`SaveProjectRequest.request`), so reopening one regenerates the design deterministically. Changing a load and re-calling `/design/project` regenerates everything. Nothing ties an export to a saved or approved revision.
10. **Markets and standards.** `design/markets.py:MARKETS` holds about 10 markets (supply voltage, earthing, a regulation name, a voltage-drop limit, the RCD-on-every-final-circuit flag). `market_unknown` refuses unknown ones. The calculations follow IEC 60364-4-41/-4-43/-5-52, IEC 60909-0, IEC 60898-1, and ABB/Schneider handbook tables, with citations in notes and in the calculations PDF. Refused: three-phase load on a single-phase supply, motors outside the tables, ambient or grouping outside the tables, relay setting ranges (`relay_range`, seen live), starter fault level and voltage, and RCCB groups that are too large.
11. **Approval and revisions.** `save_project`, `revise_project` and `approve_revision` exist (plan feature `APPROVAL_WORKFLOW`; a revision can be approved only once). I found no stale-output protection: export takes no revision id, and the approval does not feed any gate. I did not check whether the approver name reaches the title block on export. The client supplies `info.revisions`.

## Inventory of checks and what each one establishes

| Area | Check | Where | What it establishes |
|---|---|---|---|
| Numerical calcs | Ib, breaker In ≥ Ib, cable Iz ≥ In with derating | `distribution._size/_size_cable`, `ai/tools/cable_sizing` | Correct arithmetic against cited tables (unit tests). Refuses inputs outside the tables. |
| | Voltage drop (running and starting) | `_check_drop`, `_check_starting`, `voltage_drop.py` | Upsizes the cable when it can. Otherwise only a `*_exceeded` **note**. |
| | Fault level, breaking capacity | `fault_level.py`, `_rate_breaking_capacity`, `short_circuit.py` | Rates breakers ≥ prospective fault current. Withstand is left to the engineer (note). |
| | Earth-fault disconnection | `disconnection.py`, `_check_disconnection` | Only when Ze is given. Otherwise the note `earth_fault_no_ze`, unchecked. |
| | Discrimination | `_group_rating`, `_incomer` | 1.6× ratio for overload only. Short-circuit selectivity is left to the engineer. |
| | Phase balance | `balance_phases` | Assigns phases. Imbalance gives a note. |
| Device compatibility | Type 2 motor coordination, drive and fuse | `motors._coordinated/_drive` | Combinations from the tables. Refuses relay range, voltage and fault level. |
| Connectivity | Board feed graph | `project._check_supply`, `design_order` | No loops, voltage and phase consistent. |
| | Terminals | `terminals.strip` | One terminal per outgoing core. No control wiring. |
| Control-state behaviour | PLC ST check | `plc_program.build_program`, `ai/plc/validation.py` (parser) | The ST parses and the fail-safe E-stop convention holds. No simulation of states, timers or interlock behaviour. |
| Drawing/model consistency | Pages and exports from the same project object | `pages.build_drawing_set`, exporters | Agree with the *given* object. Nothing checks it against the calculations. |
| Materials completeness | Parts list, quotation `complete/unpriced` | `pages._part_rows`, `quotation.price_project` | Flags missing prices and lengths. Does not flag missing order numbers or unselected parts. |
| Missing data / unsupported | `ValidationError` refusals, assumption notes | listed above | Refuses what cannot be calculated. Assumes typical values (motor η 0.9, cos φ 0.85/0.9). |
| Approval / stale protection | Approve once | `design_projects.approve_revision` | Records the approval. **Not enforced on export.** |

## Critical checks that are missing or left to model or engineer judgement

- No gate on export: designs with `*_exceeded`, `earth_fault_unchecked` or unselected parts export freely. Notes have no severity field.
- The server does not re-check a client-supplied `DesignProject` on export (tamper test passed through).
- No order numbers. Modular devices are not selected. The EPLAN catalogue (PD-001..003) is not connected to the design path.
- No control or wiring schematics, no wire numbering (`wire_numbering` is unused), no bridges, no control terminals.
- No soft starter, ATS/changeover, fire-pump, KNX-actuator or pool logic.
- The enclosure is selected only from 4 ABB modular boards, and only when every width is known. No duct fill (PD-004 deferred), no to-scale layout (PD-009 deferred).
- No approval or revision binding on exported files, so nothing stops stale outputs.
- Missing data is never turned into questions. The engineer has to read the assumption notes.
- The LLM schedule draft (`schedule_writer`) relies only on the model for point extraction. The engineer must check it.
- PLC logic is checked only by syntax and convention, not behaviourally.

## Commands run and outcomes

1. Unit tests (no environment, no services):
   `docker compose run --rm --no-deps -T -v "$(pwd -W)/apps/web:/web:ro" --entrypoint sh api -c "env -i PATH=/opt/venv/bin:/usr/bin:/bin HOME=/tmp pytest -p no:cacheprovider -q app/tests/design"`
   Exit 0. 315 tests, all passed (315 dots, no F or E). Output is in `pytest-design.txt`. The output has no summary line, so the count is from the progress dots.
2. `POST /api/v1/auth/trial` (×2; the first token was not captured). A disposable trial token, stored only in the scratchpad.
3. `POST /api/v1/design/project` with the TEST INPUT (one board PP1, 400 V TN-S, 10 kA; lighting 0.2 kW, DOL 7.5 kW, star-delta 15 kW, drive 5.5 kW). Result: **422 `relay_range`** (15 kW star-delta below the TA75DU25 range): `design-project-refusal-15kW-star-delta.json`, request in `design-project-request-refused.json`.
4. The same request with the star-delta motor at 30 kW returned **200**: `design-project-request.json`, `design-project-response.json`.
5. `POST /api/v1/design/export` for pdf, dxf, terminals_csv, cables_csv, parts_csv, calculations_pdf, eplan_devices_csv and plc_st. All returned 200: `export.*`.
6. Tamper export (circuits_csv, lighting circuit edited to 50 kW / 200 A): **200**, the edited values were exported: `export.tampered-circuits.csv`.
7. PDF text dump via pdfplumber in the api container (`python -I`, no environment): `pdf-text-dump.txt`.

Nothing was saved, approved or signed up.

### Drawing-set PDF (A3 landscape, 10 pages)
1. Title page: project, job number, "Design rules software defaults, not confirmed by the company's engineers". The Drawn, Checked and Approved fields are blank.
2. Table of contents.
3. Main power single-line: supply 3/N/PE 400 V TN-S Ik 10 kA, incomer -Q1 C100 4P, feeders -Q2/-F1, -Q4 MA20, -Q6 MA80, -F4 25 A aR, with cross-references.
4. Distribution: RCD group with the lighting circuit -Q3 C10, cable -W1 3G1.5.
5. Distribution: motor chains (-Q4/-Q5/-F2 DOL, -Q6/-Q7/-Q8/-Q9/-F3 Y/D, -F4/-T1 drive), cables and loads.
6. Design notes (the review notes, verbatim).
7. Layout: DIN-rail rows. Every device shows "?" (no width) except the RCD group at 140 mm.
8. Terminals: X1:1–18, power cores only, Siemens 8WH1 articles.
9. Cable list: 4 cables, from/to, type, length.
10. Parts list: MCBs, RCCB and incomer "not selected". ABB, Bussmann and Siemens type numbers. The order-number column is empty.

### Calculations PDF (A4 landscape, 2 pages)
Says "NOT APPROVED: issued for review only". Gives the basis of calculation with clause citations, then the per-board notes.
