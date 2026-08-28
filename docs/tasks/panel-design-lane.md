# Panel Design lane — PD-001 to PD-008

The eight Panel Design tasks, transcribed from the project spreadsheet (`PanelPilot`, Tasks sheet). **Full Technical Details** and **Acceptance Criteria** are reproduced verbatim — they are the contract, and paraphrasing them would quietly move the goalposts.

These existed only in the sheet until now, which is why an earlier pass of this project reported PD-005 and PD-006 as "not in this repository" when checking AI-005's downstream dependants. That was accurate about the repo and misleading about the project; this file closes that gap.

## Status at a glance

**All eight are `To Do` and assigned to Ayed Rabaya.** None has been started in this repository — there is no PD branch, no PD code, and no PD row in either lane tracker.

**PD-006 is a decision, not a coding task.** The sheet's own overview flags it: _"PD-006 needs Ayed directly (EPLAN license/access is a decision, not a coding task)."_ PD-007 depends on it and PD-008 depends on PD-007, so three of the eight sit behind one access question.

**PD-005 depends on AI-005.** `voltage_drop` is implemented and sourced; `size_conductor` and `derating_factor` are not. See `adan-lane.md` for what that leaves outstanding.

---

## [ ] PD-001 — Rittal enclosure catalog ingestion

| Field                   | Value                                               |
| ----------------------- | --------------------------------------------------- |
| **Task ID**             | PD-001                                              |
| **Category**            | Backend                                             |
| **Epic / Feature Area** | Panel Design                                        |
| **Dependencies**        | _none_                                              |
| **Work Stream**         | Trust & Delivery Systems (Adan)                     |
| **Phase Group**         | Calc Tools & Panel Design                           |
| **Branch Name**         | `feature/pd-001-rittal-enclosure-catalog-ingestion` |
| **Status (sheet)**      | To Do                                               |
| **Assignee (sheet)**    | Ayed Rabaya                                         |

**Full Technical Details**

> Objective: Establishes the real product data (enclosure dimensions/SKUs) every panel-sizing calculation in this feature depends on — without it, PD-003/004 have nothing real to size against.
>
> Approach: Reuses the same staging-then-verification-then-promotion architecture already built for BE-005, applied to structured product records instead of manual-content chunks. Start from System Catalogue 36 (confirmed freely downloadable as PDF/ebook) as the baseline source. Separately check for BMEcat/eCl@ss structured electronic-catalog data via a Rittal account, and check EPLAN Data Portal access (PD-006) as a potentially richer, already-structured alternative source.
>
> Interface: Structured ProductRecord rows (SKU, external W x H x D, internal usable W x H x D, DIN-rail row capacity, mounting type, IP rating) written to staging, promoted only after verification.
>
> Edge cases: A catalog entry with ambiguous or missing dimension data must be flagged uncertain, never silently guessed at — the same cite-or-refuse principle applied to product data instead of troubleshooting content.
>
> Testing: Cross-check a sample of ingested records against the source catalog by hand before trusting the pipeline at scale.

**Acceptance Criteria**

> A sample of enclosure records matches the published catalog exactly on SKU, dimensions, and DIN-rail capacity; no record with missing required dimension data reaches production unflagged.

## [ ] PD-002 — DIN module width reference data

| Field                   | Value                                            |
| ----------------------- | ------------------------------------------------ |
| **Task ID**             | PD-002                                           |
| **Category**            | AI                                               |
| **Epic / Feature Area** | Panel Design                                     |
| **Dependencies**        | _none_                                           |
| **Work Stream**         | Trust & Delivery Systems (Adan)                  |
| **Phase Group**         | Calc Tools & Panel Design                        |
| **Branch Name**         | `feature/pd-002-din-module-width-reference-data` |
| **Status (sheet)**      | To Do                                            |
| **Assignee (sheet)**    | Ayed Rabaya                                      |

**Full Technical Details**

> Objective: The DIN-rail module width is the single number that makes enclosure sizing (PD-003) a real calculation instead of a guess — every breaker, contactor, and terminal block has a manufacturer-specified module width, and it has to be real, not assumed.
>
> Approach: Source per-component-type module widths from the relevant manufacturer datasheets, the same official-documentation sourcing pattern used throughout this project (not estimated or interpolated), structured as a lookup table keyed by component category and, where it varies, by rated current.
>
> Interface: A typed lookup table/function module_width_for(component_type, rating) -\> int (in mm or module units).
>
> Edge cases: A component type with no known module width must raise clearly at lookup time, never silently default to an assumed value.
>
> Testing: Verify a sample of known component widths against their published datasheets exactly.

**Acceptance Criteria**

> Looked-up module widths match published manufacturer datasheets exactly for a representative sample across breaker/contactor/terminal categories.

## [ ] PD-003 — Enclosure sizing calculation

| Field                   | Value                                         |
| ----------------------- | --------------------------------------------- |
| **Task ID**             | PD-003                                        |
| **Category**            | AI                                            |
| **Epic / Feature Area** | Panel Design                                  |
| **Dependencies**        | PD-001,PD-002                                 |
| **Work Stream**         | Trust & Delivery Systems (Adan)               |
| **Phase Group**         | Calc Tools & Panel Design                     |
| **Branch Name**         | `feature/pd-003-enclosure-sizing-calculation` |
| **Status (sheet)**      | To Do                                         |
| **Assignee (sheet)**    | Ayed Rabaya                                   |

**Full Technical Details**

> Objective: The core 'how big a panel do I need' calculation — the deterministic replacement for an engineer manually flipping through a Rittal catalog.
>
> Approach: Given a component list with quantities, sum required DIN modules per functional row/group using PD-002's widths, compute rows needed from standard row-spacing conventions, then match against PD-001's catalog data for the smallest standard Rittal enclosure whose row capacity and dimensions satisfy the requirement. This is a lookup/matching problem against standard catalog sizes, not free-form geometric optimization, since panel design uses standard enclosure sizes by convention rather than custom-fabricated dimensions.
>
> Interface: size_enclosure(components: list[ComponentSpec]) -\> EnclosureSizingResult, returning the row/group breakdown and the matched Rittal SKU.
>
> Edge cases: A component list that doesn't fit any standard catalog enclosure must return a clear 'no standard enclosure fits, needs custom sizing' refusal rather than being force-fit into the closest available option.
>
> Testing: Exact match against hand-calculated reference scenarios covering a range of component counts and mixes, same discipline as AI-005/006/007's worked-example testing.

**Acceptance Criteria**

> Output matches hand-calculated reference enclosure selections for representative component lists; a genuinely oversized request correctly refuses rather than force-fitting.

## [ ] PD-004 — Trunking/wireway sizing calculation

| Field                   | Value                                               |
| ----------------------- | --------------------------------------------------- |
| **Task ID**             | PD-004                                              |
| **Category**            | AI                                                  |
| **Epic / Feature Area** | Panel Design                                        |
| **Dependencies**        | PD-001                                              |
| **Work Stream**         | Trust & Delivery Systems (Adan)                     |
| **Phase Group**         | Calc Tools & Panel Design                           |
| **Branch Name**         | `feature/pd-004-trunkingwireway-sizing-calculation` |
| **Status (sheet)**      | To Do                                               |
| **Assignee (sheet)**    | Ayed Rabaya                                         |

**Full Technical Details**

> Objective: Sizes the cable-routing channels inside the panel — needed for a complete, buildable BOM, not just the components themselves.
>
> Approach: Compute the required wireway cross-section from standard cable fill-ratio conventions (wireway fill should not exceed a defined percentage of its cross-sectional area), then match to the smallest standard trunking size in PD-001's catalog data.
>
> Interface: size_wireway(cables: list[CableSpec]) -\> WirewaySizingResult.
>
> Edge cases: A cable bundle that doesn't fit any standard trunking size returns the same 'no standard size fits' refusal pattern as PD-003, rather than extrapolating.
>
> Testing: Exact match against hand-calculated reference scenarios for representative cable bundles.

**Acceptance Criteria**

> Output matches hand-calculated reference trunking selections for representative cable bundles.

## [ ] PD-005 — Inter-component wiring specification

| Field                   | Value                                                 |
| ----------------------- | ----------------------------------------------------- |
| **Task ID**             | PD-005                                                |
| **Category**            | AI                                                    |
| **Epic / Feature Area** | Panel Design                                          |
| **Dependencies**        | AI-005,PD-003                                         |
| **Work Stream**         | Trust & Delivery Systems (Adan)                       |
| **Phase Group**         | Calc Tools & Panel Design                             |
| **Branch Name**         | `feature/pd-005-inter-component-wiring-specification` |
| **Status (sheet)**      | To Do                                                 |
| **Assignee (sheet)**    | Ayed Rabaya                                           |

**Full Technical Details**

> Objective: Extends the existing cable-sizing calculation (AI-005) from single point-to-point runs to a full internal wiring list for an entire panel — the actual 'how do I wire this' deliverable an engineer needs, not just a parts list.
>
> Approach: For each electrical connection implied by the component list (breaker-to-contactor, contactor-to-terminal, etc.), calls AI-005's existing cable-sizing logic with the specific current/voltage for that connection, producing one complete connection list rather than requiring the caller to invoke AI-005 once per connection manually.
>
> Interface: build_wiring_list(components, connections: list[ConnectionSpec]) -\> list[WireSpec].
>
> Edge cases: An ambiguous or unspecified connection topology must be flagged back to the user for clarification, never guessed at silently.
>
> Testing: Exact match against hand-calculated reference wiring lists for representative panel scenarios.

**Acceptance Criteria**

> Output matches hand-calculated reference wiring lists for representative panel scenarios; an unspecified connection topology is flagged, not guessed.

## [ ] PD-006 — EPLAN access & capability assessment

| Field                   | Value                                               |
| ----------------------- | --------------------------------------------------- |
| **Task ID**             | PD-006                                              |
| **Category**            | Backend                                             |
| **Epic / Feature Area** | Panel Design                                        |
| **Dependencies**        | _none_                                              |
| **Work Stream**         | Trust & Delivery Systems (Adan)                     |
| **Phase Group**         | Calc Tools & Panel Design                           |
| **Branch Name**         | `feature/pd-006-eplan-access-capability-assessment` |
| **Status (sheet)**      | To Do                                               |
| **Assignee (sheet)**    | Ayed Rabaya                                         |

**Full Technical Details**

> Objective: Determines what integration with the team's actual EPLAN license and setup is realistically possible — this is a licensing/access decision, not a coding task, and the entire output/integration layer (PD-007, PD-008) depends on its answer.
>
> Approach: Confirm exactly which EPLAN modules/license the team holds (Electric P8, Pro Panel, eBUILD), whether EPLAN Data Portal access exists, and what the documented EPLAN API actually permits at that license tier — this requires someone with the team's actual EPLAN credentials/account, not something derivable from public documentation alone.
>
> Interface: A findings document, not code: license tier, available modules, Data Portal access status, API access status.
>
> Edge cases: If no programmatic API access exists at all, PD-007/PD-008's scope changes significantly (a structured export file for manual/semi-automated import, rather than live API automation) — this must be reported plainly rather than assumed either way.
>
> Testing: n/a — this is an access/decision task.

**Acceptance Criteria**

> A clear, specific answer exists for: EPLAN license tier held, whether Data Portal access exists, and whether API automation is possible at the current license level.

## [ ] PD-007 — Structured export schema design

| Field                   | Value                                            |
| ----------------------- | ------------------------------------------------ |
| **Task ID**             | PD-007                                           |
| **Category**            | Backend                                          |
| **Epic / Feature Area** | Panel Design                                     |
| **Dependencies**        | PD-006                                           |
| **Work Stream**         | Trust & Delivery Systems (Adan)                  |
| **Phase Group**         | Calc Tools & Panel Design                        |
| **Branch Name**         | `feature/pd-007-structured-export-schema-design` |
| **Status (sheet)**      | To Do                                            |
| **Assignee (sheet)**    | Ayed Rabaya                                      |

**Full Technical Details**

> Objective: Defines exactly what data PD-003/PD-004/PD-005 need to hand off, in a shape the team's actual EPLAN setup (per PD-006's findings) can actually consume.
>
> Approach: Design the schema based on PD-006's confirmed access level: either a rich API-consumable format if programmatic access exists, or a structured file format suited to manual/semi-automated import if it doesn't.
>
> Interface: A documented schema covering: component list, row/position layout, matched Rittal SKUs, and the wiring specification list.
>
> Edge cases: The schema must not assume API access it hasn't confirmed exists — designed to degrade gracefully to a file-based handoff if PD-006 found no API access.
>
> Testing: Validate the schema by running a representative full panel design (from PD-003/004/005) through it end to end.

**Acceptance Criteria**

> The schema produces a complete, correctly-structured handoff for a representative full panel design, validated against PD-006's actual confirmed access level.

## [ ] PD-008 — EPLAN integration implementation

| Field                   | Value                                             |
| ----------------------- | ------------------------------------------------- |
| **Task ID**             | PD-008                                            |
| **Category**            | Backend                                           |
| **Epic / Feature Area** | Panel Design                                      |
| **Dependencies**        | PD-003,PD-004,PD-005,PD-007                       |
| **Work Stream**         | Trust & Delivery Systems (Adan)                   |
| **Phase Group**         | Calc Tools & Panel Design                         |
| **Branch Name**         | `feature/pd-008-eplan-integration-implementation` |
| **Status (sheet)**      | To Do                                             |
| **Assignee (sheet)**    | Ayed Rabaya                                       |

**Full Technical Details**

> Objective: Delivers the actual automated handoff from this system's calculations to the team's EPLAN environment — closing the loop from 'the system selects the right parts and layout' to 'the engineer gets a real, usable drawing start.'
>
> Approach: Implemented per PD-007's schema and PD-006's confirmed access mechanism: API calls if available, eBUILD rule configuration if that's the confirmed path, or structured file export for manual import otherwise.
>
> Interface: An export/integration endpoint or job producing the PD-007 schema in its final, EPLAN-consumable form.
>
> Edge cases: Any integration failure must surface clearly to the user — never silently produce an incomplete or partial handoff that looks complete.
>
> Testing: End-to-end validation producing a real, importable result against the team's actual EPLAN setup — not just a schema-shaped file that was never actually tried against EPLAN itself.

**Acceptance Criteria**

> A representative full panel design (components + layout + wiring) successfully hands off into the team's actual EPLAN environment end to end.
