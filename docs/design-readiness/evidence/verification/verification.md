# Independent verification: PanelPilot design capability (Agent 4)

Date: 2026-10-23. Repository at `main` 48d2a75 (not modified). Live API `http://127.0.0.1:8000`.
Method: checked behaviour against the running API, its generated files and the source code. Docs, PR text and comments were not relied on.

All loads, lengths and powers in the trials are **TEST VALUES, not from a reference project**; every load description carries "(TEST VALUE)". Fault level (Ik) and Ze were unknown, so they were **omitted**, not invented. No projects were saved, approved or signed up.

## 1. Commands and outcomes

| # | Command (all from this folder unless noted) | Outcome |
|---|---|---|
| 1 | `curl -X POST http://127.0.0.1:8000/api/v1/auth/trial` | 201; the token was moved to a scratchpad file. `trial.json` is stored with its secrets redacted. |
| 2 | `node trials.js <tokenfile>` (7 POSTs to `/api/v1/design/distribution-board`) | See `trials.log`, `requests/`, `responses/` |
| 3 | `node export.js <tok> responses/hall_plain.json hall pdf,calculations_pdf,parts_csv,cables_csv,circuits_csv,terminals_csv,devices_csv` and the same for `pool` (+`plc_st,plc_io_csv`) | All 200 (`exports.log`, `exports/`) |
| 4 | PDF text/geometry: `docker compose cp` into api, then `docker compose exec -T api python -c` with pdfplumber (extract_text, lines/rects/curves, `to_image`) | `pdf_text.txt`, `render/*.png` |
| 5 | Regeneration: socket load 2 kW to 3.5 kW, redesign + export, then diffs | `regen_model.diff`, `regen_csv.diff`, `regen_pdf.diff` |
| 6 | Client-supplied approval: export with `approved_by` edited into `info.revisions` | `forged_approval.txt` |
| 7 | Edited unsafe design: c1 breaker changed 10 A to 63 A on a 1.5 mm² cable, then exported | `exports/hall_unsafe.*` (accepted, HTTP 200) |
| 8 | The mandated isolated pytest command (`docker compose run --rm --no-deps ... env -i ... pytest -p no:cacheprovider -q app/tests/design app/tests/domain -k '...'`) | exit 0; 368 tests collected, **359 passed, 9 skipped** (`pytest_design.log`; counted from the progress dots, because `-q` printed no summary line) |

Design calls: 8 design + 21 export, well under the 240 / 5 min limit. Trial starts: 1.
Housekeeping note: six `/tmp/v4_*.pdf` copies remain in the api container (`rm` was refused with "Operation not permitted"). They are disposable.

## 2. Per-family trial table

The request schema (`apps/api/app/models/schemas/design.py`) has:
- one `Supply` per board;
- `LoadKind` = {lighting, socket, air_conditioning, water_heater, motor, fan, kitchen, lift, sub_board, control, data, spare, other};
- `MotorStarter` = {dol, star_delta, drive};
- no `extra="forbid"`, so unknown fields are **silently dropped**.

`grep` over `app/design` and the design schema for fire, jockey, NFPA, soft starter, ATS, transfer and KNX finds nothing functional. The only hits are a note text suggesting "Consider a soft starter or a drive" and "fire alarm" as a schedule-import keyword that maps to the CONTROL kind. `DeviceKind.BUS_ACTUATOR` exists only as a label and letter code; no design logic creates one.

| Family | Trial | HTTP | Result | Classification |
|---|---|---|---|---|
| Distribution / KNX hall | `hall_plain`: lighting, sockets, AC as plain circuits | 200 | Designed | **Accepted as plain distribution.** KNX is not represented. |
| | `hall_knx_kind`: `load:"knx"` | 422 | `Input should be 'lighting', 'socket', … or 'other'` | **KNX unsupported** (refused by the enum). Sending KNX loads as lighting would be accepted-but-wrong-model: no bus PSU, actuators, line/area or group addresses. |
| Pump with soft starter | `pump_soft_starter`: `starter:"soft_starter"` | 422 | `Input should be 'dol', 'star_delta' or 'drive'` | **Soft starter unsupported** (refused). Not forced through as "drive". |
| Pool | `pool`: pump (DOL), underwater lighting, heater, sockets | 200 | Designed | **Accepted-but-wrong-model.** Nothing pool-specific: no zones, no SELV or separated supply for the underwater lighting (it is fed as a 230 V C10 circuit), no equipotential bonding. The filtration pump `-Q6` sits on the busbar, **not under the 30 mA RCD** (PDF p.3). The only related remark is the generic note that circuits outside an RCD are not checked for disconnection without Ze. |
| MDB with ATS | `mdb_ats_extra_field`: `standby_supply`, `source:"generator"`, `transfer_switch:{type:"ATS"}` | 200 | Designed with a **single** supply and a single incomer. The ATS fields vanished from the response and **no warning** was given. | **Unsupported, and silently accepted.** That is worse than a refusal. A two-board project with two `fed_from`-less boards would just be two unrelated supplies; this was not attempted, by instruction. |
| Fire pump | `fire_pump_kind`: `load:"fire_pump"` | 422 | enum refusal (as above) | **Fire pump unsupported.** |
| | `fire_pump_as_motor`: "Fire pump - main" as a 30 kW star-delta motor, plus a jockey pump DOL | 200 | Designed as a generic motor: MA breaker, Y/D contactors, **thermal overload relay F1 (TA75DU42, set 32.33 A)**, Type 2 coordination note | **Accepted-but-wrong-model**, with no warning that the description says "fire pump". See §7. |

## 3. PDF inspection notes (hall 9 pp., pool 10 pp., calculations reports 2 pp.)

Structure (pool): Title, Contents, Main power, Distribution loads ×2, Design notes, Layout, Terminals, Cable list, Parts list. The page is A3 landscape (1190×842 pt) with a 0–9 / A–F grid.

- **Text and legibility.** All text is extractable and none is garbled. At 110 dpi the symbols are clear: breaker, RCD with test button, contactor, overload. There are no overlaps on the pages rendered (p.3–5). One label is cut short with an ellipsis: in the title block, "TEST APPROVER (client-supp…" (forged-approval export). Long text is cut rather than wrapped.
- **Page numbering and cross-references.** "n of N" is consistent and the Contents page matches the pages present. The cross-references "to /4.0", "to /5.0" and "from busbar /3.0" point at the right pages.
- **Device identifiers.** IEC 81346-style (`=DB-POOL-Q6`, `-W4`, `-X1:12`). They agree across the drawing, cable list, terminal list, parts list and the CSVs.
- **Project, revision and approval data.** These are consistent on every page. "Checked" and "Approved" are blank unless supplied, but **they print whatever the client sends** (see §6). The calculations report drops its "NOT APPROVED" banner when `approved_by` is non-empty.
- **Disagreements between outputs:**
  - The PDF parts list includes terminals (`8WH1000-0AF00` ×11 and so on); `parts_csv` **omits them**.
  - The PDF cable list says `-W4` runs **from `-Q6`**, but the drawing (p.5) shows `-W4` leaving the overload `-F2`. The terminal table puts `-X1:12..15` on `-W4`, yet **no terminal strip is drawn** on the power pages.
  - The PDF cable list has no voltage drop or Zs column; the CSV has voltage drop, and its Zs column is empty without Ze.
- **Materials completeness.** Every MCB, RCD and the incomer in the hall is "not selected": no article or order number. Cables are not in the BOM. The hall enclosure (ABB GDMS332 RX) appears **only as a design note** and is not in the parts list. For the pool, "Enclosure: none named, since 3 devices have no width". The layout page shows "?" for devices without a width. **The BOM is not orderable as issued.**
- **Connectivity: is the drawing a picture?** The model holds a device tree (`upstream_id`, `device_ids`, `cable_id`), plus a terminal table per conductor (L1/L2/L3/N/PE/U/V/W to terminal number to cable). There is **no wire-level net list**: no device terminal numbers (1/2, 3/4, A1/A2), no internal wiring between a breaker and `-X1`, and no control circuit. The motor contactor `-Q7` has no coil, start/stop or overload auxiliary contact drawn anywhere in the PDF; that logic exists only in the separate PLC ST and I/O CSV exports. The single-line is therefore a single-line diagram generated from a tree, not a terminal-level connection diagram. It cannot replace manufacturing or wiring drawings drawn in a CAD tool.

## 4. Regeneration consistency (hall: sockets 2 kW to 3.5 kW)

Every dependent output changed consistently, and nothing unrelated changed:
- **JSON:** `power_kw` 2 → 3.5, `design_current_a` 9.62 → 16.84, `voltage_drop_percent` 1.87 → 3.28, `Q5.rated_current_a` 16 → 20.
- **Notes:** a new `above_company_rating` note; `discrimination_group_raised` now reads "after 20"; `phase_imbalance` 21.8 → 44.5 %. The group breaker stays 32 A (1.6×20 = 32) and the incomer stays 63 A.
- **CSVs:** parts, devices and circuits show the breaker at 20 A, and the cables CSV shows the voltage drop at 3.28 %.
- **PDF:** the distribution page reads "C20 1P" and "Ib 16.84 A"; the notes were renumbered (+1); the parts list reads "C20 1P".
- **Unchanged:** the cable stays 2.5 mm² (Iz 24 A ≥ 20 A). Terminals unchanged.
- **Observation:** the company's 16 A socket rule was exceeded and the software silently up-rated the circuit to 20 A with only a note. It did not refuse the circuit or split it.

Diffs: `regen_model.diff`, `regen_csv.diff`, `regen_pdf.diff`.

## 5. Hand-verified numbers (all reproduce)

U0 = 400/√3 = 230.94 V; cos φ = 0.9 default (as the board notes).

| # | Quantity | Source in code | Hand calculation | Output | OK |
|---|---|---|---|---|---|
| 1 | Ib, lighting 1.2 kW, 1-ph | Ib = P/(U0·cosφ) | 1200/(230.94×0.9) = 5.773 A | 5.77 | ✓ |
| 2 | Ib, AC 5 kW, 3-ph | P/(√3·U·cosφ) | 5000/(1.732×400×0.9) = 8.019 A | 8.02 | ✓ |
| 3 | Ib, sub-board feeder 40 kW | same | 40000/623.5 = 64.15 A | 64.15 | ✓ |
| 4 | Voltage drop c1: 1.5 mm², 30 m | `cable_sizing._VOLTAGE_DROP_MV_PER_A_KM` (Schneider EIG 2010 Fig. G28), 1-ph lighting column 30 V/A/km, worst of the cos 1 and cos 0.8 columns | 30×5.773×0.030 = 5.196 V; /230.94 = 2.25 % | 2.25 | ✓ |
| 5 | Voltage drop c4: 2.5 mm², 20 m, 3-ph | 3-ph lighting column 15 | 15×8.02×0.020 = 2.406 V; /400 = 0.60 % | 0.60 | ✓ |
| 6 | Voltage drop after regeneration: 2.5 mm², 25 m, 16.84 A | 1-ph column 18 | 18×16.84×0.025 = 7.578 V; /230.94 = 3.28 % | 3.28 | ✓ |
| 7 | Star-delta fire-pump test cable: 6 mm², 20 m | Winding current Ir/√3 = 56/1.732 = 32.33 A, single-phase column 7.5, divided by line voltage | 7.5×32.33×0.020 = 4.85 V; /400 = 1.21 % | 1.21 (and OL set 32.33 A) | ✓ |
| 8 | Ampacity B1, PVC | `_AMPACITY_CU[B1]` equals the IEC 60364-5-52 Table B.52.4 values I know (2-loaded 17.5/24/32/41/57/76/101; 3-loaded 15.5/21/28/36/50/68/89) | 10 A ≤ 17.5 (1.5 mm²); 16 A ≤ 24 (2.5); AC 16 A > 15.5, so 2.5 mm² at 21 ✓; feeder 80 A > 68 (16 mm²), so 25 mm² at 89 ✓; Y/D 32.3 A > 28 (4 mm²), so 6 mm² at 36 ✓ | as stated | ✓ |
| 9 | k²S² (PVC, k = 115) | ABB §2.4 Table 1 | (115×1.5)² = 29 756 A²s = 0.030 (kA)²s; (115×25)² = 8.266 (kA)²s; (115×6)² = 0.476 | 0.030 / 8.266 / 0.476 | ✓ |
| 10 | Zs max, calculations report | `disconnection.max_loop_ohm`: Cmin·U0/Ia, with Ia = 10·In for curve C | With U0 rounded to **231 V** (`calc_report.py:143`): 0.95×231/100 = 2.194 Ω; 0.95×231/160 = 1.372 Ω | 2.194 / 1.372 | ✓, with a caveat |
| 11 | Discrimination 1.6× | profile `discrimination_ratio` | 1.6×20 = 32 → group 32 A; 1.6×32 = 51.2 → incomer 63 A | 32 / 63 | ✓ |
| 12 | Phase imbalance | (max − min)/max | before: L1 = 9.62+8.02 = 17.64, L2 = 13.79, so 21.8 %; after: L1 = 24.86, so 44.5 % | 21.8 / 44.5 | ✓ |

Caveat on #10: the calculations report derives U0 from 400/√3 rounded to 231 V, not the nominal 230 V of IEC 60038. This makes Zs max about 0.4 % higher, which is slightly non-conservative. It is minor, but it should be an explicit choice.

Not reproduced by hand: the motor Ir (56 A for 30 kW, 5 A for 2.2 kW) and the Type-2 starter tables are cited to the ABB handbook Vol. 2 §3.3; I did not have the source tables open. The MCCB selection (XT3N 250 TMD 160, Icu 36 kA) was likewise not reproduced.

## 6. Check inventory

| Check | What it actually establishes | Evidence |
|---|---|---|
| Unit tests `app/tests/design` (≈ 250 test functions across 34 files) plus `domain/test_design.py` (15), `test_plc.py` (11) and `test_calculations.py` (6) | Code paths behave as the authors expect, on author-written fixtures: sizing, voltage drop, disconnection, fault level, MCCB, motors, notes, pages, exports, PLC checker, enclosure | `pytest_design.log`: 359 pass, 9 skip |
| `domain/test_design_projects.py` (8: save, revise, approve-locks, tenant isolation) | **Skipped in this run** (module `skipif`, needs a DB). Approval locking is not verified here. | 8 of the 9 skips |
| Runtime: schema enums, Pydantic bounds | Unknown load kinds and starter types are refused (422) | trials |
| Runtime: unknown fields | **Not refused**: silently dropped (the ATS case) | `mdb_ats_extra_field` |
| Runtime: design notes | Missing data is flagged rather than invented (no fault level, no Ze, default cos φ, no lengths, unconfirmed rules, unknown widths) | every response |
| Runtime: approval | The server-side revision approval lock exists in `domain/design_projects.py`. **The stateless export prints client-supplied checker and approver names and removes the "NOT APPROVED" banner.** | `forged_approval.txt` |
| Runtime: export re-validation | **None.** An edited project with a 63 A breaker on 1.5 mm² exports with HTTP 200. | `exports/hall_unsafe.circuits_csv.csv` |
| Drawing/model consistency | Shared designations from one model. **No test or check compares the PDF against the CSVs**, and they already disagree (terminals in the BOM, cable "From"). | §3 |
| Connectivity | Tree plus terminal table only; there is no net list to check | §3 |
| Control-state behaviour | The PLC ST checker test (`test_the_program_passes_the_checker`) covers E-stop dropping all outputs and the hand/auto logic. No control-circuit drawing. | `pool.plc_st.plc_st` |
| Materials completeness | No check. The hall BOM has 0 of 6 protective devices selected, and the enclosure is missing from the BOM. | §3 |

## 7. Fire pump

The code has **no dedicated fire-pump logic**: no load kind, no controller type, no jockey-pump pairing, no NFPA or EN 12845 references (grep, §2). A fire pump entered as `motor` is treated like any other motor:
- it gets a thermal overload relay that trips the motor;
- it gets ordinary Type-2 coordination;
- if `controlled: true`, it gets a PLC program in which "every output drops when EStop_OK is FALSE".

All of this is the opposite of normal fire-pump practice.

Requirements that would apply, cited from memory. **I did not check these against the source texts in this session; verify them against the adopted editions before relying on them.**
- **NFPA 20** (2022 ed.), Ch. 10, requires a fire pump controller **listed for fire pump service** (a factory-assembled, listed controller, e.g. UL 218 / FM 1321-1323), with locked-rotor-rated protection rather than running overload trips.
- **NFPA 70** (NEC 2023), Art. 695: supply reliability, sizing of the overcurrent device to carry locked-rotor current indefinitely, and routing of the supply conductors.
- **EN 12845** (+A1:2019) is the European equivalent for sprinkler pump sets.

A design tool should **refuse** this family, or only draw the feeder up to a listed third-party controller. Its output is not a compliant fire-pump panel.

## 8. Critical missing checks

1. Re-run the design rules on every export, and refuse an edited project that violates In ≤ Iz, Ib ≤ In, breaking capacity or selectivity (§6, test 7).
2. Bind approval and "checked" fields to the server revision record. Stateless exports must not print client-supplied approvals, and must always carry "NOT APPROVED" unless the server-side revision is approved and unchanged (stale/hash check).
3. `extra="forbid"` (or explicit refusal) on design requests, so unsupported requirements such as an ATS are refused rather than dropped.
4. Refuse by keyword or description: "fire pump", "jockey", "ATS", "KNX", "soft starter" and "pool zone" should raise an unsupported-family refusal, not a generic design.
5. A cross-output consistency test that parses the PDF and compares it with every CSV (devices, cables, terminals, BOM quantities).
6. A BOM completeness gate: every device selected with an article number, plus enclosure, cables and accessories, before the document can be "issued for construction".
7. Terminal-level net list and control-circuit drawings, with a connectivity check (no dangling device terminals; every cable has two ends).
8. Pool-specific rules (IEC 60364-7-702) or an explicit refusal; RCD coverage for all pool circuits.
9. Run the 8 DB-backed approval tests in CI against a disposable database.

## 9. Proposed evaluation method for a ">99 % accuracy" claim

1. **Scope first.** State per family what "supported" means: market, standard edition, voltage, earthing, load kinds and starters. Unsupported families (fire pump, ATS, KNX, soft starter, pool zones today) are measured by **refusal rate**, not accuracy.
2. **Ground truth.** For each project, two independent chartered or licensed engineers produce expected results blind, then reconcile them into an expected-results file per item: Ib, In, curve, Icu, section, voltage drop, Zs, selectivity, device list, terminals, cable list, control-state table and drawing set. Record their disagreement rate as the noise floor.
3. **Data split.** Real reference projects per family, split into a development set and a **held-out set** never used for tuning. Freeze the held-out set and the software version before scoring.
4. **Separate measures**, each with its own denominator:
   - (a) calculations: per numeric item, within tolerance, e.g. exact for discrete selections and ±1 % for voltage drop and Zs;
   - (b) connectivity: per net or terminal connection, with precision and recall against the engineer net list;
   - (c) control logic: per state-transition row (hand/auto/E-stop/fault/interlock), checked by simulation;
   - (d) document completeness: checklist per sheet set and BOM line;
   - (e) cross-document consistency;
   - (f) usability: time to an issued document, number of edits, and whether any redraw in another CAD tool was needed (the product goal).
5. **Critical-error rate.** Classify each error by severity: critical means unsafe or non-compliant, such as an undersized cable, Icu below Ik, a missing RCD, a wrong fire-pump treatment, or a forged or stale approval. Report the critical-error rate per project separately. The target is 0 observed, with an upper confidence bound.
6. **Coverage and refusal.** Report the share of real requests that fall in scope (coverage), the correct-refusal rate on out-of-scope requests, and the false-refusal rate on in-scope requests.
7. **Sample sizes and confidence.** Use the Clopper-Pearson exact interval. With zero errors in n trials, the 95 % upper bound is about 3/n (rule of three):
   - showing an error rate below 1 % with 0 failures needs **n ≥ 300 independent items per measure**;
   - with 1 failure, n ≈ 473;
   - for critical errors per *project* at below 1 %, about 300 held-out projects, which is unrealistic early on. Report the bound honestly instead, e.g. "0 critical errors in 40 projects, so the 95 % upper bound is 7.5 %".
   - Items inside one project are correlated, so use a cluster bootstrap by project for item-level confidence intervals.
8. **Reporting.** A per-family, per-measure table with n, errors, point estimate and 95 % CI; the noise floor between engineers; and a list of every critical error.
