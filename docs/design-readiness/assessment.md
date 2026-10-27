# PanelPilot design readiness assessment

**Assessed:** `main` at `48d2a75` ("Say the limits strict mode cannot carry…", #198), 2026-10-23.
**Pending, described separately:** PR #199 at `73ce76c` (open, mergeable); PR #87 at `2273672` (open, conflicting, stale since 2026-09-29).
**Method:** four delegated agents working at the same time (reference analysis, backend trace, frontend trial, independent verification). The lead then reconciled their findings and re-checked the claims that matter most. Evidence is in [`evidence/`](evidence/).
**Constraints honoured:** no code, behaviour or production data was changed. Every trial used an anonymous trial session and labelled test inputs, and nothing was saved, approved or signed up. Customer PDFs were not copied into the repository and are referred to by short id.

---

## A. Verdict in plain language

**What PanelPilot can do today.** You can enter, or describe in a sentence, the loads of a **plain low-voltage distribution board**. PanelPilot then sizes each circuit with traceable rules: design current, breaker, cable, voltage drop, disconnection time, fault level, RCD grouping, phase balance and DOL / star-delta / drive starter coordination. Each figure names its source table (IEC 60364, IEC 60909, ABB and Schneider handbooks), and the hand-checked figures reproduce (§5). The design is exported as a consistent 10-page drawing set:
- title page and page index
- single-line power pages
- design notes
- a DIN-rail strip
- one outgoing terminal strip
- cable list
- parts list
- a 2-page calculations report

It can also be exported as DXF, QElectroTech, AutomationML and CSV lists, in English, Arabic or Hebrew.

**What still prevents an engineer from producing the required execution PDF in a few simple steps:**
1. **None of the five reference panel families can be produced.** There is no soft starter, no ATS (one supply per board), no KNX beyond a labelled "bus actuator" device, no fire-pump logic, and nothing specific to pools.
2. **The output is a single-line study set, not an execution package.** The references contain control schematics, device terminals with cross-references, wire numbers, a dimensioned enclosure, a dead front and a mounting plate, and a parts list with order numbers. PanelPilot produces none of these: its parts list has no order numbers and its layout has no dimensions.
3. **Nothing in the export is enforced.** An export re-checks nothing, prints whatever approval names the client sends, ignores failed checks, and silently drops fields it does not understand.
4. **It never asks.** Missing facts become silent defaults or notes. Plain-language input only drafts load rows.

The program is a **credible calculation core for one family**. It is not yet a tool that replaces drawing the panel in ECAD for any of the five reference families.

---

## B. Capability matrix

Status uses one of six labels:
- **Verified:** implemented and verified.
- **Unverified:** implemented but not verified.
- **Partial:** partially implemented.
- **Missing:** not implemented.
- **Blocked:** blocked by data, engineering input or a credential.
- **Defect:** implemented, with a defect found.

Priority: P0 is a blocker, P1 is required for the first family, P2 is required for the full scope.

| Capability | Needed for | Current evidence | Status | Gap | Priority |
|---|---|---|---|---|---|
| Load schedule from plain language | all | `ai/schedule_writer.py:write_schedule` → `design/schedule_split.py:split_points`; the frontend trial took 8.07 s | Verified (rows only) | Produces names, types, kW and phases only. Typical values are filled silently and listed as assumptions. | P1 |
| Missing-data questions | all | none; gaps become notes (`default_power_factor`, `earth_fault_no_ze`, `rules_unconfirmed`) | Missing | No question loop; lengths, fault level, Ze and starters are never asked for. | P0 |
| Circuit sizing (Ib, breaker, cable, ΔU, Zs, k²S²) | all | `design/distribution.py`, `voltage_drop.py`, `disconnection.py`, `fault_level.py`; 12 sets of figures hand-checked (verification) | Verified | U0 = 231 V in the calculations report, about 0.4 % non-conservative on Zs max. | P1 |
| Motor starters | pump, fire, pool | `MotorStarter` = `dol`, `star_delta`, `drive` (`models/schemas/design.py:46`) | Partial | **No soft starter** (a request with `soft_starter` gets 422). No H-O-A or Auto/Off/Manual modes. | P0 (pump) |
| Control logic and schematics | pump, pool, MDB, fire | none in the drawings; `plc_program.py` writes Structured Text (hand/auto, E-stop, star-delta timer), checked for syntax only | Missing | No control schematic, coils, contacts, timers, lamps or interlock wiring. | P0 |
| ATS / two-source transfer | MDB | a board has one supply; unknown fields are dropped (no `extra="forbid"` in `models/schemas/design.py`) | Missing | No second source, transfer controller, mechanical or electrical interlock, or timers. | P0 (MDB) |
| KNX | hall, pool | `BUS_ACTUATOR` device kind with a label; `load:"knx"` gets 422 | Missing | No actuator channel map, bus power supply, topology or ETS hand-off. | P2 |
| Fire-pump controller | fire | nothing; as a "motor" it gets an overload relay, Type-2 coordination and E-stop drop-out | Missing (and unsafe if forced) | Needs dedicated logic and certified or listed equipment (see C-5). | Out of first scope |
| Orderable parts | all | `order_number=None` (`design/motors.py:341`); breakers print "not selected"; the EPLAN/Rittal catalogue (PD-001…003) is not used by `app/design/` | Blocked (data) | No manufacturer part selection. | P0 |
| Device terminals, wire numbers, connection list | all | one outgoing strip X1 (`design/terminals.py:strip`); `CompanyProfile.wire_numbering` is never read in `design/` | Missing | No device terminals, bridges, cores, wire numbers or source→target list. | P0 |
| Cross-references | all | page.column references exist on single-line pages (`pages.py`, `sheet.py`) | Partial | No coil↔contact references; the references show controls. | P1 |
| Enclosure, external view, dead front, mounting plate | all | enclosure is a note picked from 4 hard-coded ABB boards (`enclosure.py:notes_for`) and skipped when widths are unknown; layout is a rail strip with "?" widths | Missing | Not dimensioned; no dead front; PD-004 and PD-009 deferred. | P1 |
| Parts quantities | all | parts list (PDF) and parts CSV | Defect | Terminals in the PDF but not in the CSV; cables not listed; enclosure only in a note. | P1 |
| Cable list | all | `export_lists.cable_list` | Defect | No lengths; `-W4` is "from -Q6" in the list but "from -F2" in the drawing (verification). | P1 |
| One consistent project model | all | every export is built from one project object; regeneration after a redesign is consistent (frontend and verification) | Verified | The object comes from the **client** and is not rebuilt on export. | P0 |
| Stale-output protection | all | on main, an edit followed by an export gives a byte-identical PDF (`ffef171d…`, frontend); PR #199 claims a fix | Defect on main | Re-check pending #199. | P0 |
| Validation gate on export | all | `domain/design.py:export_design` checks billing and the profile only | Missing | A 63 A breaker on 1.5 mm² cable and 200 A on a 10 A breaker both exported with HTTP 200. | P0 |
| Approval and revision identity | all | `domain/design_projects.py:approve_revision` records the approver; exports read `approved_by` from the client's project (`pages.py:415-416`, `calc_report.py:266`) | Defect | Approval can be forged in the output; the drawing PDF has no draft/approved mark. | P0 |
| Markets and standards | all | `design/markets.py:MARKETS` (about 10), refuses unknown markets | Verified (scope) | Title page says "iec-default" while the notes say Palestine. | P2 |
| Editing and regeneration | all | redesign updates every output consistently (5.5 → 7.5 kW pump; 2 → 3.5 kW socket) | Verified | The company's 16 A socket rule exceeded is up-rated silently to 20 A with only a note. | P1 |
| Arabic and Hebrew drawing language | all | Arabic set rendered (frontend) | Partial | One cross-reference in jumbled RTL order; Hebrew untested. | P2 |

---

## C. Per-reference assessment

The references are vector ECAD exports from one panel builder's template (80 pages; 69 unique). In [`evidence/references/reference-analysis.md`](evidence/references/reference-analysis.md), 19 pages were inspected visually and the rest from extracted text; the lead also inspected ZALMOUT p7–p8 visually. **What all five share** is what an execution team works from:
- an approval cover and revision rows
- dimensioned external views, a dead front and a mounting plate
- power and control schematics with device terminals and page.column:row cross-references
- a parts list with manufacturer and order numbers for most items

**What none of them has:**
- a design basis (standard and edition, earthing, fault level)
- calculations
- a terminal or connection list
- a cable list
- a written sequence of operation

Only SATAFI has wire numbers. PanelPilot's calculations, notes and cable list go **beyond** the references in those areas. The references go far beyond PanelPilot on drawings and parts.

### C-1 POCKET-HALL (distribution / KNX hall), 17 pages
- **Reproducible today:** the circuits, protection, RCD grouping and single-line pages for the distribution part.
- **Manual work:** order numbers, the enclosure and dead-front drawings, and contactor- or KNX-switched lighting, which the reference shows as actuator channels.
- **Unsupported:** KNX (the reference itself shows hardware only, with no bus power supply or addressing).
- **Reference defects:**
  - wrong function tags
  - the cover says revision 01 while three revisions are listed
  - an ETEK part listed under ABB
  - the bus cable drawn as 2×1.5 mm² XLPE
- **Closest of the five to today's capability.**

### C-2 SATAFI-PUMP (pump with soft starter), 10 pages
- **Unsupported:** the soft starter is refused (422).
- **Reproducible:** the feeder sizing only.
- **Missing:** the control schematic (E-stop, start/stop or Auto/Off/Manual, interlocks through overload and breaker auxiliaries, run and fault lamps, phase-failure relay) and wire numbering. The wire numbering is the one thing this reference has that the others lack.
- **Reference defect:** a C-curve breaker on the soft-starter branch, with no coordination type stated.

### C-3 SHANAA-POOL (pool), 13 pages
- **Accepted but modelled wrongly:** nothing specific to pools. The pump `-Q6` sits outside the 30 mA RCD, and there is no SELV supply for the underwater lights.
- **Missing:** the pump control, as with SATAFI.
- **Reference defects:**
  - contents and footer numbering disagree (of15 against of13)
  - the jacuzzi pump is labelled "Spare"
  - TA25DU-6.5M overloads look high for 1.3–2.3 A motors; not checked against the datasheet
  - the 12 V transformer is not a declared safety type
  - a 30 mA RCD feeds the whole board
- **Treat the reference as a starting point that needs engineering review**, not as a specification.

### C-4 KHAMIS-MDB (MDB with ATS), 29 pages (18 unique)
- **Unsupported:** the ATS. PanelPilot accepts the request, silently drops `standby_supply` and `transfer_switch`, and returns a one-supply board.
- **What the reference requires:** two 4-pole motorised ACBs with closing and opening coils, a mechanical and an electrical interlock, a transfer controller (DKG-173), phase-failure relays, a generator start relay, metering CTs and busbars.
- **Reference defects:**
  - pages 4–6 and 19–29 duplicate earlier pages, and footer totals read 15/16 against 29 physical pages
  - the ACB trip unit is LSIG on the schematic but LI in the parts list
  - feeders are drawn as thermal-magnetic but listed as magnetic-only
  - a Siemens type code sits against an ABB order number
  - incomer cables are left as "XX"
  - 1600 A on a single 80×10 bar per phase, with no calculation

### C-5 ZALMOUT-FIRE (fire-pump-related), 11 pages ("V1 .pdf"; the file named in the request, "V1 (1).pdf", does not exist)
- **Unsupported, and unsafe to force through.** As a "motor" it gets an overload relay, ordinary Type-2 coordination and a PLC program that drops out on E-stop.
- **What the reference shows:** jockey, duty and standby pumps on pressure switches, Auto/Off/Manual per pump, run and stop timers, the jockey locked out while a main pump runs, main pumps DOL on magnetic-only breakers, and phase-failure monitoring.
- **Lead's check of p7–p8:** the control rail that feeds the K.Jockey, K.Duty **and K.Standby** coils passes through the `EMG` emergency stop and the dry-run relay R1 contact. **So emergency stop and dry-run stop the main fire pumps**, not only raise an alarm.
- **Why that matters:** from general knowledge (not checked against the text of the standard), a listed fire-pump controller does not normally let such devices stop the automatic main pump. The reference also lacks phase-reversal alarms and remote contacts to the fire alarm panel.
- **Requirement:** this family needs a certified or listed controller and requirements checked against a named edition of the applicable code (e.g. NFPA 20, EN 12845 or the local authority's). It should stay **out of the supported scope** until an engineer defines that scope. Neither PanelPilot nor this reference can be the authority for it.

---

## D. Five most important blockers

1. **No terminal-level connectivity model.** The model is a device tree (`Device.upstream_id`). Control schematics, wire numbers, terminal and connection lists, coil↔contact cross-references and the cable list all depend on modelling terminals, conductors and their ends. Every family needs this.
2. **No control-logic model.** Starters stop at DOL, star-delta and drive. Soft starters, H-O-A modes, interlocks, timers, alarms and transfer sequences have nothing to sit in. The PLC program is generated beside the design, not from a control model that also drives the drawings.
3. **Parts are ratings, not products.** `order_number=None`, and the catalogue ingestion (PD-001…003) is not connected to `app/design/`. There can be no orderable BOM, real enclosure sizing or real layout.
4. **Exports are not gated or trustworthy.** The server neither rebuilds nor re-checks the project on export, prints client-supplied approvals, ignores failed checks, and silently drops unknown fields. Nothing can be called "approved for execution" until this is fixed.
5. **No dimensioned mechanical output.** There is no enclosure, external view, dead front, mounting plate, ducts or clearances; PD-004 and PD-009 are deferred. Every reference has them, and fabrication needs them.

---

## E. Roadmap

**Suggested first family: SATAFI-PUMP (one motor with a soft starter).** It is small (10 pages), it is the only reference with wire numbers, and it exercises every blocker except ATS and KNX: control logic, terminals and wires, orderable parts, gating and the enclosure. It also extends what already works: motor circuits and starter coordination. POCKET-HALL is easier but would postpone blockers 1 and 2, which every other family needs.

| # | Deliverable | Depends on | Acceptance criteria | Reuses | Needs engineering data or approval |
|---|---|---|---|---|---|
| 1 | **Trustworthy export:** the server rebuilds the project from the saved request and revision; refuses export on failed blocking checks; reads approval only from the database; `extra="forbid"` on design schemas; a DRAFT watermark until approved | none | A tampered project, an unknown field, a failing check or an unapproved revision each give a refusal; tests show each | `design_projects`, `approve_revision`, notes catalogue | which checks block (an engineer classifies each note) |
| 2 | **Connectivity model:** devices with typed terminals, conductors with two ends, wire numbering from `CompanyProfile.wire_numbering`, terminal strips with bridges | none | The SATAFI power and control circuits round-trip into a connection list in which every conductor end is a real terminal; a check flags any terminal left unconnected | `designations`, `terminals`, `export_lists` | terminal maps for the chosen devices (manufacturer data) |
| 3 | **Control model plus control schematic pages:** soft starter, Auto/Off/Manual, E-stop, overload and breaker auxiliary interlocks, run and fault lamps, phase-failure relay; schematic pages and coil↔contact cross-references generated from it; PLC program derived from the same model | 2 | The SATAFI control circuit regenerated with the same functions as the reference; a state-table test (mode × input → outputs) passes; cross-references resolve both ways | `plc_program`, `symbols`, `sheet`, `pages` | the sequence of operation signed off by an engineer |
| 4 | **Orderable parts:** connect the PD-001 catalogue to device selection for one manufacturer's soft starter, MCCB/MCB, contactor and overload range | 2 | Every SATAFI part has a manufacturer and an order number; the BOM matches the schematic one-to-one; ranges agree with the motor current | `ingestion/eplan_catalogue.py`, `motors._coordinated` | catalogue licence and coverage; coordination tables |
| 5 | **Dimensioned enclosure and layout:** enclosure from real part dimensions; mounting plate with rails and ducts to scale; external view and dead front | 4 | The layout fits with stated clearances; dimensions printed; the enclosure is chosen from the catalogue, not a list of four | `ai/tools/enclosure_sizing.py`, `din_module_width.py`, `layout.py` | clearance and heat rules (company standard) |
| 6 | **Question loop:** list the facts the design needs (lengths, Ze, fault level, starter, modes) and ask instead of silently defaulting | 1 | No design reaches export with an unconfirmed default on a blocking input | `schedule_writer`, notes | which defaults are acceptable |
| 7 | Extend to POCKET-HALL (contactor or KNX channel mapping), SHANAA-POOL (pool-zone rules, SELV), then KHAMIS-MDB (two sources, ATS controller, interlocks, busbar calculation) | 1–6 | Each reference reproduced and reviewed by an engineer (see §5) | all of the above | KNX and ATS controller data; busbar method |
| — | Fire pump | 1–7 | Only after an engineer defines the scope against a named code edition and the certified controller to use | — | yes, entirely |

---

## F. Time-reduction assessment

**Measured** (frontend trial, one small labelled test pool board, scripted in a headless browser):

| Step | Time |
|---|---|
| Page ready | 1.17 s |
| Data entry (scripted) | 0.96 s |
| Plain-language schedule | 8.07 s |
| Design | 0.20 s |
| Each export | 0.15–0.70 s |

**Estimated, not measured:** 3–6 minutes for an engineer to enter and check that board.

**What this does not measure:** the 2–3-week baseline is spent mostly on what PanelPilot does not produce yet:
- control schematics
- terminal and wiring documentation
- part selection with order numbers
- enclosure and mounting drawings
- reconciliation between them

For each reference family, the remaining manual work is still essentially the whole ECAD drawing job, since PanelPilot covers only the calculations and single-line study. **No time saving can be claimed today for the target families.**

**To approach 4–5 hours,** roadmap items 1–6 must hold for the family in use, so that the engineer's time is spent answering questions and reviewing rather than drawing. Then measure with a stopwatch on real projects: entry, answering questions, correcting outputs and engineering review, each timed separately, on at least 5 projects per family, compared with the same engineers' current time on similar panels.

---

## 5. Accuracy and verification

**What the existing checks establish:**

| Check | What it actually establishes | Evidence |
|---|---|---|
| Unit tests, design (315) and design plus domain subset (359 pass, 9 skip) | Calculation functions follow their encoded tables; exporters produce files | `evidence/backend/pytest-design.txt`, `evidence/verification/` |
| Hand-checked figures (12 sets) | Ib, ΔU (1-phase, 3-phase, star-delta), B1 ampacity (IEC 60364-5-52 Table B.52.4), k²S², Zs max, discrimination, imbalance reproduce | `evidence/verification/verification.md` |
| Refusals outside table ranges (`relay_range`, `market_unknown`) | Out-of-range inputs are refused rather than guessed | backend trace |
| Regeneration | One project object gives consistent outputs after a redesign | frontend, verification |
| PLC review | Syntax of the generated Structured Text only | `ai/plc/validation.py` |
| Approval and locking tests | **Not run** (8 skipped, database-backed) | verification |

**Critical checks missing:**
- connectivity (no terminal model to check)
- control-state behaviour
- drawing↔model consistency on export
- BOM completeness against the schematic
- blocking validation before export
- approval integrity

**Evaluation method for the >99 % target:**
- **Scope.** Fix the supported scope per family in writing. Measure an unsupported family by its correct-refusal rate, kept apart so that refusing cannot raise accuracy.
- **Expected results.** Two engineers produce them independently and blind for held-out projects that development never saw, and resolve disagreements before scoring.
- **Separate measures.** Calculations (per value), connectivity (per conductor end), control logic (per state-table row), document completeness (per required item), cross-output consistency, and usability (time and corrections).
- **Critical-error rate.** Any error that could cause unsafe or unbuildable output, reported separately and required to be zero.
- **Coverage.** Report the share of real requests that fall inside the supported scope.
- **Statistics.** Clopper-Pearson intervals, with a cluster bootstrap by project. With zero failures, a 95 % upper bound below 1 % needs **at least 300 independent items** per measure (rule of three: 3/n). Items within one project are not independent, so many projects are needed, not one large one.
- **No claim of "99 %", "certified" or "execution-ready"** until those numbers exist and an engineer has accepted the outputs.

---

## G. Evidence appendix

**Versions:**
- `main` `48d2a75`
- PR #199 `73ce76c`. It changes 19 web component and lib files plus API diagnostics, search, VFD and calculations, and claims stale-result protection and navigation guards. **Not run in this assessment.**
- PR #87 `2273672` (conflicting)
- No separate dependency-security branch exists; the latest such fix (aiohttp 3.14.5) is on `main` from #192.

**Agent reports:**
- [`evidence/references/reference-analysis.md`](evidence/references/reference-analysis.md)
- [`evidence/backend/backend-trace.md`](evidence/backend/backend-trace.md), with requests and responses, the refusal JSON, all exports and a tampered CSV
- [`evidence/frontend/frontend-assessment.md`](evidence/frontend/frontend-assessment.md), with screenshots 01–09, the v1/v2/v3 exports, the stale PDF, rendered pages and `trial-log.txt`
- [`evidence/verification/verification.md`](evidence/verification/verification.md), with scripts, per-family requests and responses, PDFs, extracted text, regeneration diffs and the hand calculations

**Claims the lead re-checked:**
- Starter set: `models/schemas/design.py:46`
- "changeover" means star-delta contactors, not ATS: `design/pages.py:679-697`
- Export without rebuild or gate: `domain/design.py:export_design`
- `order_number=None`: `design/motors.py:341`
- No `extra="forbid"` in the design schemas
- Approval names taken from the client project: `pages.py:415-416`, `calc_report.py:266`
- Stale PDF byte-identical: SHA-256 `ffef171d…` for both files
- ZALMOUT p7–p8 control rail, inspected visually
- No KNX, fire-pump, jockey, NFPA, dead-front or H-O-A code anywhere in `app/design`, `domain`, `models` or `ai`: grep

**Corrections to the agents' reports:**
- The frontend agent stated PR #199 changes only API files. It changes 19 web component and lib files as well.

**Not verified:**
- PR #199's behaviour
- approval and locking tests (skipped)
- ABB motor and MCCB tables (not hand-checked)
- standard texts: NFPA 20, EN 12845 and IEC 60364-7-702 are cited from general knowledge only
- device datasheets
- 61 of 80 reference pages checked only through extracted text
- Hebrew output

**Left behind:**
- The container `/tmp` of `panelpilot-api-1` holds disposable copies: six verification PDFs, and the lead's copy of ZALMOUT-FIRE used for the p7–p8 check. They could not be deleted, because they were copied in as root and the container runs as `app`, and they are removed on container restart.
- The rendered ZALMOUT p7–p8 images are kept only in the session scratchpad, not the repository.
