# Panel Design lane — PD-001 to PD-009

The nine Panel Design tasks, transcribed from the project spreadsheet (`PanelPilot`, Tasks sheet). **Full Technical Details** and **Acceptance Criteria** are reproduced verbatim — they are the contract, and paraphrasing them would quietly move the goalposts.

## Status at a glance

**PD-001, PD-002, PD-003 and PD-006 are done and merged.** PD-006 (PR #70): thirteen IEC 60617-style symbols, 31 tests, five mutants killed. PD-002: DIN module widths sourced from ABB S200, Schneider Acti9 iC60H and WAGO TOPJOB S datasheets, 19 tests, ten mutants killed. The other seven are `To Do`, all assigned to Ayed Rabaya.

**PD-002 found that there is no single module width.** DIN 43880 specifies a band, not a value: ABB's S200 is 17.5 mm per module and Schneider's Acti9 iC60H is 18 mm, both conforming. PD-003 must therefore size rows from per-series millimetres rather than a module count times one constant — an enclosure laid out on 17.5 mm rows and filled with 18 mm devices does not close. **Contactors are absent from the table**: every manufacturer-hosted contactor datasheet reachable from here returned 403, and a lookup for one raises rather than guessing.

**The chain is no longer blocked at the root, but PD-004 now blocks it further down.** PD-001 was unblocked by a structured EPLAN catalogue export rather than by crawling Rittal, and PD-002 and PD-003 followed. What remains:

- **PD-004 is deferred**, not merely unstarted: no normative fill ratio exists to cite. IEC 60364-5-52 was investigated and does not contain one; NEC 376.22 (20%) is the wrong jurisdiction and IET OSG Appendix E (45%) is guidance rather than a standard. See its note — the candidates were rejected on sourcing grounds, not overlooked.
- **PD-005** needs PD-003 (done) and AI-005 (partial — `voltage_drop` only).
- **PD-007** needs PD-003 (done), PD-004 (**deferred**) and PD-005, so it inherits PD-004's block.
- **PD-008** needs PD-007, and so inherits it too.

### Re-synced from the sheet

**PD-006, PD-007 and PD-008 changed scope entirely** after the first transcription. They were an EPLAN integration path — access assessment, export schema, integration implementation. They are now a **native schematic renderer**: a symbol library, a schematic data schema, and an SVG single-line renderer built inside the product, with no external CAD tool involved.

**PD-009 is new**, and is a deferral rather than work: the to-scale physical layout views are explicitly out of scope for this phase.

PD-001 through PD-005 are unchanged from the previous transcription.

### Dependency shape

The renderer chain is now internal, so nothing in it waits on a third-party licence:

- **PD-006** (symbol library) — no dependencies. The entry point.
- **PD-007** (schematic schema) — needs PD-003 ✅, PD-004 (**deferred**), PD-005 and PD-006 ✅. PD-004's deferral is the live blocker here.
- **PD-008** (renderer) — needs PD-006 and PD-007.
- **PD-009** — deferred; nothing depends on it.

PD-005 still depends on **AI-005**, and so inherits what AI-005 leaves outstanding: `voltage_drop` is sourced and implemented, `size_conductor` and `derating_factor` are not. See `adan-lane.md`.

---

## [x] PD-001 — Rittal enclosure catalog ingestion _(external dimensions delivered; internal dims and rail capacity deferred — see below)_

> **A crawler source was attempted and is not viable as-is.** Registering
> `webinfo.rittal.com/en/system_catalogue-36` as a `SourceCrawler` was tried
> the same way the Siemens/ABB/Schneider sources work. Two of the three checks
> pass and the third does not:
>
> - **robots.txt permits it.** The host disallows only `/_hcms/preview/`,
>   `/hs/manage-preferences/`, `/hs/preferences-center/` and two query
>   patterns. Run through this project's own `fetch_policy` /
>   `require_allowed`, the catalogue URL is ALLOWED and a `/_hcms/preview/`
>   URL is correctly REFUSED — so the policy layer works against this host.
> - **The page fetches cleanly.** HTTP 200, ~20 KB of HTML.
> - **It links no documents.** The served HTML contains **zero** `.pdf` hrefs
>   and no download anchors at all; the "Download (PDF)" and "Ebook Version"
>   buttons are rendered client-side. Running the real
>   `_documents_from_links` extractor against the live page discovers **0
>   documents**.
>
> Registering the crawler would therefore add a source that runs green, logs
> success, and stages nothing — which is worse than no source, because a
> pipeline reporting a healthy crawl of an empty result is indistinguishable
> from a manufacturer that published nothing that day. That is the exact
> failure BE-006's source-health monitoring exists to catch, and deliberately
> introducing an instance of it to satisfy a registration step would be
> backwards.
>
> **The Document Center was tried as an alternative, and fails the same way.**
> `rittal.com/us_en/apps/download/` returns HTTP 200 and **5 KB containing zero
> anchors of any kind** — not zero PDF links, zero `<a>` tags at all — with ten
> script tags. Identical response under a browser User-Agent, so this is
> client-side rendering rather than bot-blocking. Its one exposed REST
> endpoint (`/.rest/nav/menu/tree`) returns the site navigation tree and
> contains no `.pdf` reference at all.
>
> So both Rittal entry points are JavaScript-driven. This is not two failures
> but one: the ingestion pipeline fetches HTML and reads links out of it, and
> neither page has links in its HTML.
>
> Unblocking needs a direct PDF URL, a headless browser in the crawler (a real
> change to the ingestion contract, not a new source class), or the
> BMEcat/eCl@ss structured feed the Approach already mentions via a Rittal
> account.
>
> **Ticked on revised scope, with the remainder deliberately deferred.**
> What is delivered: external W x H x D for 266 real catalogue records, plus a
> partial IP rating (19 of 53 enclosures publish one as a structured field).
> That is the dimensional half of the `ProductRecord` the spec describes, and
> it is what PD-003 needs to size against.
>
> **Not delivered, and why:** internal usable dimensions and full DIN-rail row
> capacity. Both were investigated rather than assumed:
>
> - **Internal dimensions are absent from the commercial export.** The 38 rows
>   whose text matches "internal" all say _"Internal set elements"_ — a
>   catalogue group name, not a dimension. Zero rows publish an internal size.
> - **Rail capacity is published on the wrong objects.** 46 rows carry an
>   "N mod." figure, but they are covers (25), mounting plates (15) and rails
>   (6). **Zero of 53 enclosures publish one.**
> - **It is not derivable.** The six rails publish both a module count and a
>   width, and the implied pitch drifts 17.9 -> 19.9 mm per module. Dividing a
>   width by a constant would produce a confident wrong capacity, which is the
>   error PD-002's own finding (DIN 43880 specifies a band, not a value)
>   predicts.
> - **The geometry exists, but only per-part.** An individual part download
>   (`MAC_VX8900100_xD.zip`) does contain a real `dxf/` folder — six Panel
>   layout DXFs, 159 LINE entities, bounds 601.0 x 2061.7 mm against a
>   published 600x2000x800 enclosure. The bulk exports never populate
>   `Relative path of the DXF file`: it is empty in all 510 rows across three
>   exports. So DXF coverage means downloading enclosures one at a time, and
>   extracting usable internal dimensions from them means parsing CAD geometry
>   — closer to PD-009's deferred 2D-layout work than to what PD-003 needs.
>
> Treated the same way as AI-005's `size_conductor`/`derating_factor` stubs and
> PD-009: recorded as a known limitation rather than filled in from general
> knowledge. **PD-003 consumes what exists and refuses what does not** — see its
> own note on why rail capacity is a caller-supplied input there.

**A structured catalogue feed now supplies the data the crawler could not.**

> The Approach names "BMEcat/eCl@ss structured electronic-catalog data" as the
> richer alternative to crawling, and an EPLAN Data Portal export is exactly
> that: structured product records rather than PDFs to parse.
>
> Three exports were filtered (`app/ingestion/eplan_catalogue.py`). The largest
> ran on the search term "enclosure system" and returned **487 rows, of which
> 266 survive** — 190 dropped as not panel components, 31 as publishing no
> dimension. The filter requires a _structured_ dimension (`Width (mm):`,
> `Width/height/depth:`), not the presence of "mm": 41 rows in that export
> contain millimetres that are camera resolutions and working ranges, not object
> sizes.
>
> Of the 266, **107 are sizing candidates** (enclosures, flush enclosures,
> mounting plates, rails). **130 covers are retained and tagged but excluded
> from PD-003's fitting logic** on Ayed's instruction — a cover is chosen to
> match an enclosure already selected, and offering one as somewhere to mount a
> component would propose using a door. `covers_for_width` keeps them
> available for BOM completion.
>
> **Still not complete.** PD-001 asks for internal usable dimensions, DIN-rail
> row capacity, mounting type and IP rating; the export publishes external W/H/D
> and, for ETI, an IP rating in prose. Rittal remains uncrawled. What exists now
> is the dimensional half of the record, which is what PD-003 needs first.
>
> **One useful thing did come out of the attempt.** Rittal's robots.txt puts a
> blank line immediately after `User-agent:*`, which terminates the record per
> the standard — so Python's parser attributed all 96 `Disallow` rules to no
> agent and permitted everything, including the `/products/show/` paths the
> file explicitly forbids. Our policy layer inherited that reading. Fixed in
> `app/ingestion/robots.py` with a regression test; the three live sources do
> not use that format, so nothing was crawled in breach.

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
> Approach: Reuses the same staging-then-verification-then-promotion architecture already built for BE-005, applied to structured product records instead of manual-content chunks. Start from System Catalogue 36 (confirmed freely downloadable as PDF/ebook) as the baseline source. Separately check for BMEcat/eCl@ss structured electronic-catalog data via a Rittal account as a potentially richer, already-structured alternative source.
>
> Interface: Structured ProductRecord rows (SKU, external W x H x D, internal usable W x H x D, DIN-rail row capacity, mounting type, IP rating) written to staging, promoted only after verification.
>
> Edge cases: A catalog entry with ambiguous or missing dimension data must be flagged uncertain, never silently guessed at — the same cite-or-refuse principle applied to product data instead of troubleshooting content.
>
> Testing: Cross-check a sample of ingested records against the source catalog by hand before trusting the pipeline at scale.

**Acceptance Criteria**

> A sample of enclosure records matches the published catalog exactly on SKU, dimensions, and DIN-rail capacity; no record with missing required dimension data reaches production unflagged.

## [x] PD-002 — DIN module width reference data

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

## [x] PD-003 — Enclosure sizing calculation

> **Built on PD-001's catalogue and PD-002's widths.** `size_enclosure` sums
> DIN modules per functional group, rounds each group up to whole rail rows,
> and selects the smallest catalogue enclosure whose width holds the rail and
> whose height holds the rows.
>
> **Rail capacity is a caller-supplied input, not a derived one.** PD-001
> established that zero of 53 ingested enclosures publish a module capacity,
> and that the six rails publishing both a count and a width imply a pitch
> drifting from 17.9 to 19.9 mm — so no constant recovers it from an
> enclosure's width. `usable_rail_mm` and `row_pitch_mm` come from the
> enclosure drawing, and a run without them refuses rather than guessing. A
> version that multiplied width by a fudge factor would always return an
> enclosure, and would be wrong in the direction of a panel that does not
> close.
>
> **A real bug was caught before merge.** The first version used
> `-(-width // rail)` for ceiling division. That is correct for `int` and wrong
> for `Decimal`, whose floor division truncates toward zero: 210 mm on a 465 mm
> rail returned **0 rows** and 500 mm returned **1**. A panel sized a row short
> builds as a component with nowhere to go. Fixed with `math.ceil` and pinned
> by a parametrised boundary test (26 devices = 455 mm = 1 row; 27 = 472.5 mm =
> 2 rows).
>
> Deep-tier: 9 mutants, all killed. One survived the first pass — the width
> check dropped, so a 300 mm cabinet could be selected for a 465 mm rail. The
> tall-enough enclosures in the fixture happened to be wide enough too, which
> is exactly the sort of coincidence a mutation run exists to find.

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

## [ ] PD-004 — Trunking/wireway sizing calculation _(deferred — no normative fill ratio exists to cite)_

> **DEFERRED, on Ayed's decision — same treatment as AI-005's remaining
> stubs.** No fill-ratio number is written without a genuine normative
> citation, and after investigation none exists to cite.
>
> **The real blocker is the fill ratio, not the missing catalogue data.** The
> Approach calls for "standard cable fill-ratio conventions (wireway fill
> should not exceed a defined percentage of its cross-sectional area)". That
> percentage was searched for properly and the candidates were **rejected as
> insufficient sourcing, not overlooked**:
>
> - **IEC 60364-5-52 — investigated and disproven.** This was the leading
>   hypothesis, and a reasonable one: the project already cites this standard
>   in three places (`cable_sizing.py`, `calculations.py`) for ampacity and
>   derating, and the brand list is IEC-oriented. It does **not** contain a
>   fill ratio. Verified against the official IEC preview (Ed. 3.1 2024-11,
>   consolidated, from VDE-Verlag), which carries the complete clause and annex
>   structure: **zero** occurrences of "space factor", "fill", "percentage", or
>   even the `%` character. Every table in the standard is about operating
>   temperature, conductor CSA, installation method or current-carrying
>   capacity. Clause 521.6 ("Conduit systems, cable ducting systems, cable
>   trunking systems…") cross-refers to product standards without giving a
>   figure; 522.8 is "Other mechanical stresses (AJ)".
>
>   **Worth recording as a trap:** web sources confidently assert
>   _"IEC 60364-5-52 clause 522.8.1 — 40%"_ with a specific clause number. That
>   contradicts the document. Treat it as false rather than unverified — it is
>   exactly the plausible-looking citation this project's sourcing rule exists
>   to keep out.
>
> - **NEC / NFPA 70 Article 376.22(A) — 20%. Rejected: wrong jurisdiction.**
>   Genuinely normative and citable ("shall not exceed 20 percent of the
>   interior cross-sectional area of the wireway"), but it is a US code for
>   _wireways_, a different product and convention from IEC/EN panel trunking.
>   Applying it to an IEC-oriented catalogue would be a category error, and a
>   conservative one that sizes trunking more than twice as large as European
>   practice.
>
> - **IET On-Site Guide Appendix E — 45% trunking / 35% conduit. Rejected:
>   guidance, not a standard.** This is the figure the UK/IEC panel-building
>   world actually uses, but it is not a normative requirement. Cable
>   capacities were **removed from the Wiring Regulations in 1991** because the
>   committee considered them guidance; they now live only in the IET On-Site
>   Guide and Guidance Note 1, worded "should not exceed" rather than "shall".
>   The IET's own GN1 later described the earlier space factor as "an arbitrary
>   value, later shown to be inappropriate". No current BS 7671 regulation
>   number carries it.
>
> - **IEC 61537, IEC 61084 / EN 50085 — no fill percentage found.** These are
>   product construction and test standards (dimensions, IP, impact, flame);
>   installation-side fill rules are outside their scope.
>
> **The missing catalogue is the smaller half and is not being pursued.** Of
> PD-001's 266 ingested records, zero match trunking, wireway, duct, cable
> channel or Kabelkanal — the export ran on "enclosure system". A trunking
> export is cheap to obtain by the same EPLAN mechanism with a different search
> term, and would need one class added to `PANEL_CLASSES`, which currently
> holds no trunking class and would filter every such row out. It is left
> undone deliberately: with no citable fill ratio there is nothing to match a
> computed cross-section against, so the catalogue alone would not unblock the
> task.
>
> **What would genuinely unblock it:** a normative fill-ratio clause in a
> standard appropriate to this project's IEC-oriented scope — or an explicit
> decision to adopt the IET guidance figure while labelling it guidance-grade
> in the code, which is a departure from the exact-source discipline every
> other calc tool follows and is Ayed's call to make, not one to slide into.

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

## [x] PD-006 — Schematic symbol library & rendering conventions

| Field                   | Value                                                     |
| ----------------------- | --------------------------------------------------------- |
| **Task ID**             | PD-006                                                    |
| **Category**            | Backend                                                   |
| **Epic / Feature Area** | Panel Design                                              |
| **Dependencies**        | _none_                                                    |
| **Work Stream**         | Trust & Delivery Systems (Adan)                           |
| **Phase Group**         | Calc Tools & Panel Design                                 |
| **Branch Name**         | `feature/pd-006-schematic-symbol-library-rendering-conve` |
| **Status (sheet)**      | To Do                                                     |
| **Assignee (sheet)**    | Ayed Rabaya                                               |

**Full Technical Details**

> Objective: Establishes the visual language the schematic renderer will use — a small, reusable set of standard symbols is what keeps every generated diagram consistent, instead of each render inventing its own look.
>
> Approach: Define standard IEC 60617-style electrical symbols (breaker, contactor, terminal block, relay coil, etc.) as reusable, parameterized SVG components — the same structured-data-to-SVG pattern FE-009 already proved for ladder-logic rendering, applied to panel schematic symbols instead of PLC rungs. Each symbol takes a reference designator and rating label as props rather than being drawn per-instance.
>
> Interface: A symbol library module exporting one component/template per component type, keyed the same way PD-002's DIN-module lookup is keyed.
>
> Edge cases: An unrecognized component type must render a clearly-marked generic placeholder symbol with a visible warning, never silently omit it or invent a plausible-looking symbol for something it doesn't actually have a definition for.
>
> Testing: Visual snapshot tests for each symbol type checked against IEC 60617 reference conventions.

**Acceptance Criteria**

> Every component type PD-001/PD-002 can produce has a corresponding correct symbol; an unrecognized type renders a visible placeholder, never a silent gap or a guessed symbol.

## [ ] PD-007 — Schematic data schema

| Field                   | Value                                  |
| ----------------------- | -------------------------------------- |
| **Task ID**             | PD-007                                 |
| **Category**            | Backend                                |
| **Epic / Feature Area** | Panel Design                           |
| **Dependencies**        | PD-003,PD-004,PD-005,PD-006            |
| **Work Stream**         | Trust & Delivery Systems (Adan)        |
| **Phase Group**         | Calc Tools & Panel Design              |
| **Branch Name**         | `feature/pd-007-schematic-data-schema` |
| **Status (sheet)**      | To Do                                  |
| **Assignee (sheet)**    | Ayed Rabaya                            |

**Full Technical Details**

> Objective: Defines the structured contract between the calc-tools layer (PD-003/004/005) and the renderer (PD-008) — the shape of 'what to draw', kept separate from 'how to draw it' so either side can change independently.
>
> Approach: A schema covering: the component list (mapped to PD-006 symbol types), the electrical connection/topology list (from PD-005), and layout hints (row/grouping assignment from PD-003) — everything the renderer needs and nothing it has to infer.
>
> Interface: A documented, typed schema, e.g. SchematicSpec.
>
> Edge cases: The schema must represent an out-of-range or refused calc-tool result explicitly (not just omit it), so the renderer can show 'not calculated' rather than silently leaving a gap that looks like an oversight.
>
> Testing: Validate the schema by running a representative full panel design's actual PD-003/004/005 output through it end to end.

**Acceptance Criteria**

> The schema produces a complete, correctly structured representation of a representative full panel design, including how it represents any refused/out-of-range calc-tool result.

## [ ] PD-008 — Single-line schematic renderer

| Field                   | Value                                           |
| ----------------------- | ----------------------------------------------- |
| **Task ID**             | PD-008                                          |
| **Category**            | Backend                                         |
| **Epic / Feature Area** | Panel Design                                    |
| **Dependencies**        | PD-006,PD-007                                   |
| **Work Stream**         | Trust & Delivery Systems (Adan)                 |
| **Phase Group**         | Calc Tools & Panel Design                       |
| **Branch Name**         | `feature/pd-008-single-line-schematic-renderer` |
| **Status (sheet)**      | To Do                                           |
| **Assignee (sheet)**    | Ayed Rabaya                                     |

**Full Technical Details**

> Objective: Delivers the actual rendered schematic diagram — extending the same structured-data-to-SVG pattern already proven and tested in FE-009's ladder-logic display, now producing a real single-line power-distribution diagram natively within the product, no external CAD tool involved.
>
> Approach: Consumes PD-007's schema, composes PD-006's symbol library into a full single-line diagram in the same layout convention as the reference example (power distribution shown top-to-bottom, branch circuits fanning out per row/group).
>
> Interface: A rendering function/component producing SVG output, plus a PDF/PNG export path for the resulting diagram.
>
> Edge cases: A panel design too large for one reasonable page must paginate sensibly (matching how the reference example itself splits across multiple numbered pages), never shrink until illegible.
>
> Testing: Render a representative full panel design and visually verify it against the same worked examples PD-003/004/005 already validated — the diagram must show the same components/ratings the calc tools actually computed, not a plausible-looking approximation.

**Acceptance Criteria**

> A representative full panel design renders as a correct, legible single-line schematic matching the underlying calc-tool output exactly; an oversized design paginates rather than shrinking illegibly.

## [ ] PD-009 — Physical panel layout views (deferred)

| Field                   | Value                                                 |
| ----------------------- | ----------------------------------------------------- |
| **Task ID**             | PD-009                                                |
| **Category**            | Backend                                               |
| **Epic / Feature Area** | Panel Design                                          |
| **Dependencies**        | _none_                                                |
| **Work Stream**         | Trust & Delivery Systems (Adan)                       |
| **Phase Group**         | Calc Tools & Panel Design                             |
| **Branch Name**         | `feature/pd-009-physical-panel-layout-views-deferred` |
| **Status (sheet)**      | To Do                                                 |
| **Assignee (sheet)**    | Ayed Rabaya                                           |

**Full Technical Details**

> Objective: The to-scale physical views (front/side/top/dead-front, matching where each component actually sits inside the enclosure) shown in the reference example — deliberately not built in this phase.
>
> Approach: Not attempted yet: this needs real per-component footprint/geometry data (not just DIN-module width) and a genuine 2D layout engine, a substantially larger undertaking than the schematic renderer (PD-008), closer to building CAD-engine functionality than a calc tool or a symbol-based diagram.
>
> Interface: n/a — explicitly out of scope for this phase.
>
> Edge cases: This is not a regression: the feature's mandatory-review design (BE-011 never returns a 'final' flag) already assumes a human finishes the design — for now, that includes turning the verified component list, layout summary, and schematic into the physical drawing, same as they would today without any tool at all.
>
> Testing: n/a.

**Acceptance Criteria**

> Explicitly deferred — not a task to complete, a documented boundary of what this phase delivers.
