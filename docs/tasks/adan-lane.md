# Adan's lane — unattended run tracker

Adan Alawni's 17 remaining tasks, transcribed from the project spreadsheet. The **Full Technical Details** and **Acceptance Criteria** below are reproduced verbatim — they are the contract, and paraphrasing them would quietly move the goalposts.

Grouped by phase rather than by task id, so the dependency order is visible: the ingestion pipeline has to exist before anything can be verified, and the calc tools before the endpoints that call them.

**Review tier** is recorded per task. Deep tier means two independent review passes; single-round means one. The calc tools and the PLC generation path are deep because they are the two places where a confident wrong answer reaches an engineer as an instruction — the same reason the cite-or-refuse guardrail was. The catch-all still applies: anything that turns out to touch answer correctness or data integrity gets escalated once I am actually inside it.

## Status at a glance

**11 of 17 in scope for this run**, in dependency order: BE-005, BE-006,
BE-007, AI-013, AI-012, AI-014, AI-009, BE-010, FE-009, FE-012, FE-013.

**5 blocked on unavailable source documents** — AI-005, AI-006, AI-007 (the
calc tools) and BE-011, FE-010 (which consume them). Each names a specific
manufacturer engineering guide that is not in this repository. See the note on
each task; the short version is that writing the tables from general knowledge
would produce confident, uncitable numbers with cable and fire safety
downstream of them.

**1 partially satisfied** — BE-015, left unticked with its two remaining gaps
recorded rather than decided.

---

## Ingestion & Verification

### [x] BE-005 — Per-brand documentation crawler jobs

> **Scope note — this task grew to include a PDF structure extractor.**
>
> Not in the original description, which treats text extraction as already
> solved: "documents are downloaded, text-extracted, and handed to chunking
> (AI-001)". Nothing in the repository produced the `StructureMap` that
> `chunk_document` requires — only tests constructed them by hand — and no PDF
> library was declared as a dependency.
>
> The alternative was inferring structure from the extracted text. That is the
> one thing the design explicitly forbids: `app/models/schemas/structure.py`
> states the map "comes from the PDF parser (layout and heading extraction)"
> and that chunking "never re-derives structure from the text itself — guessing
> where a table starts by counting pipes is exactly the failure this design
> avoids". A mis-detected table boundary yields half a parameter table
> presented as a whole one, which is precisely what AI-001's atomic-block rule
> was written to prevent.
>
> So `app/ingestion/structure.py` was added, and treated as deep-tier for the
> same reason AI-001 was: it serves the citation-precision property directly.
>
> **Known limitation, after three review rounds.** A table continued onto the
> next page _without_ a repeated header and without a "(continued)" banner
> stays two blocks rather than one. Geometry looked like the remaining signal
> — a table starting at the top of a page is where a break lands — but
> measurement showed an unrelated table opening the next page starts at
> exactly the same position, so it does not separate the two cases. Two
> fragments of one table are visibly two blocks; fusing two different tables
> presents rows under a heading they never appeared under, so this is the safe
> direction.
>
> **Library chosen on investigation, not by default.** PyMuPDF is
> AGPL-3.0-or-commercial and was ruled out for a proprietary product.
> pdfplumber (MIT) was probed against real-shaped pages and surfaces the two
> signals this needs — per-character font size, so headings come from
> typography rather than wording, and table detection from **ruling lines**, so
> an atomic block is recognised from geometry rather than delimiters.
>
> The probes also set what it refuses to do: borderless tables are not
> detected (pdfplumber's text-alignment fallback swept a heading into one and
> emitted empty rows — a half-right table is worse than none), and
> column-layout pages are reported rather than read, because reading them
> line-by-line merges two columns into sentences the manual never contained.

| Field                   | Value                                                 |
| ----------------------- | ----------------------------------------------------- |
| **Task ID**             | BE-005                                                |
| **Category**            | Backend                                               |
| **Epic / Feature Area** | Ingestion Pipeline                                    |
| **Dependencies**        | BE-004                                                |
| **Work Stream**         | Trust & Delivery Systems (Adan)                       |
| **Phase Group**         | Ingestion & Verification                              |
| **Branch Name**         | `feature/be-005-per-brand-documentation-crawler-jobs` |
| **Status (sheet)**      | To Do                                                 |
| **Assignee (sheet)**    | Adan Alawni                                           |
| **Review tier**         | deep                                                  |

**Full Technical Details**

> Objective: Keeps the knowledge base current without manual re-uploading, while respecting each source's terms — the 'learning never stops' requirement, built safely.

> Approach: One scheduled job per source (Siemens SIOS/SiePortal, ABB Library, Schneider Download Center), each checking that source's robots.txt before any request and rate-limiting politely. Change detection via sitemap/listing-page diff plus content-hash comparison on already-known URLs, so unchanged PDFs aren't re-ingested daily. New/changed documents are downloaded, text-extracted, and handed to chunking (AI-001), writing resulting chunks into staging.

> Interface: crawl_source(source_id) — one function per source implementing a shared SourceCrawler interface, so adding a fourth brand later means implementing one new class, not touching shared pipeline code.

> Edge cases: A source's ToS or robots.txt disallowing automated access must hard-fail that source's job with a clear log entry, never silently skip or proceed anyway.

> Testing: Run against a small fixture set of known-changed and known-unchanged test documents, asserting only the changed one produces a new staging entry.

**Acceptance Criteria**

> A manually-changed test document is detected and queued within one scheduled run; an unchanged document produces zero new staging entries on a repeat run.

### [x] BE-006 — Source health monitoring

| Field                   | Value                                     |
| ----------------------- | ----------------------------------------- |
| **Task ID**             | BE-006                                    |
| **Category**            | Backend                                   |
| **Epic / Feature Area** | Ingestion Pipeline                        |
| **Dependencies**        | BE-005                                    |
| **Work Stream**         | Trust & Delivery Systems (Adan)           |
| **Phase Group**         | Ingestion & Verification                  |
| **Branch Name**         | `feature/be-006-source-health-monitoring` |
| **Status (sheet)**      | To Do                                     |
| **Assignee (sheet)**    | Adan Alawni                               |
| **Review tier**         | deep                                      |

**Full Technical Details**

> Objective: Distinguishes 'nothing new today' (normal, expected most days) from 'the scraper is broken' (a real problem) — without this, both look identical from outside and breakage goes unnoticed.

> Approach: Each crawler run (BE-005) writes a SourceHealth record (source_id, last_run_at, last_success_at, last_error) regardless of outcome. A lightweight scheduled check alerts if last_success_at is older than a defined threshold for any source.

> Interface: record_health(source_id, success: bool, error: str | None) called at the end of every crawler run.

> Edge cases: A source failing due to a transient network error should retry before alerting, not alert on the very first failure — avoids alert fatigue that trains the team to ignore it.

> Testing: Simulate a crawler job raising an exception and assert the health record and (past threshold) alert path both fire correctly.

**Acceptance Criteria**

> Deliberately breaking one crawler job triggers an alert within one missed cycle; a healthy-but-unchanged source produces no alert.

### [x] BE-007 — Verification queue API

| Field                   | Value                                   |
| ----------------------- | --------------------------------------- |
| **Task ID**             | BE-007                                  |
| **Category**            | Backend                                 |
| **Epic / Feature Area** | Ingestion Pipeline                      |
| **Dependencies**        | BE-004,AI-012                           |
| **Work Stream**         | Trust & Delivery Systems (Adan)         |
| **Phase Group**         | Ingestion & Verification                |
| **Branch Name**         | `feature/be-007-verification-queue-api` |
| **Status (sheet)**      | To Do                                   |
| **Assignee (sheet)**    | Adan Alawni                             |
| **Review tier**         | deep                                    |

**Full Technical Details**

> Objective: The operational backbone of the whole accuracy story — coordinating 10 people's daily work reliably is what turns 'we have verifiers' into an actual systematic process rather than ad hoc spot-checking.

> Approach: A VerificationItem table (chunk_id, assigned_to, status: pending/labeled/escalated, label, note, assigned_at). A daily assignment job distributes unassigned staging items across the 10 verifying-engineer accounts in roughly equal batches. POST /verification/items/{id}/label records the label; incorrect or uncertain labels create an EscalationItem visible to lead-engineer accounts instead of just closing the item.

> Interface: GET /verification/queue/me (today's assigned batch), POST /verification/items/{id}/label, GET /verification/escalations (lead-only).

> Edge cases: Assignment must be atomic (a DB-level unique constraint or transaction) so two verifiers can never be assigned or successfully claim the same item — a real risk once this is genuinely distributed across 10 people working concurrently.

> Testing: A concurrency test firing simultaneous assignment/claim requests and asserting no item is ever double-assigned.

**Acceptance Criteria**

> Every staging item is assigned to exactly one queue at a time (no duplicate assignment); an escalated item is visible in the lead-review view within one polling cycle.

### [x] AI-013 — Ingestion-to-verification pipeline wiring

| Field                   | Value                                                     |
| ----------------------- | --------------------------------------------------------- |
| **Task ID**             | AI-013                                                    |
| **Category**            | AI                                                        |
| **Epic / Feature Area** | Ingestion Pipeline                                        |
| **Dependencies**        | AI-001,BE-005,BE-007                                      |
| **Work Stream**         | Trust & Delivery Systems (Adan)                           |
| **Phase Group**         | Ingestion & Verification                                  |
| **Branch Name**         | `feature/ai-013-ingestion-to-verification-pipeline-wirin` |
| **Status (sheet)**      | To Do                                                     |
| **Assignee (sheet)**    | Adan Alawni                                               |
| **Review tier**         | single-round                                              |

**Full Technical Details**

> Objective: The connective tissue making 'continuous learning' and 'always verified' actually one system, instead of two separately-built pieces that happen to sit next to each other.

> Approach: The crawler's staging write (BE-005/BE-004) triggers chunking (AI-001) synchronously or via a lightweight queue, and each resulting chunk is automatically inserted into the verification queue's unassigned pool (BE-007) — no manual 'please review this batch' step by anyone.

> Interface: An event/hook (on_staging_chunk_created) connecting BE-004's write path to BE-007's queue-population logic, kept as an explicit integration point rather than the two modules reaching into each other's internals directly.

> Edge cases: A burst of many new documents from one crawler run (e.g. after a source publishes a large update) must not silently overload a single day's verification capacity — queue population should pace/batch reasonably rather than dumping an unbounded number of items on the ten verifiers at once.

> Testing: An integration test simulating a multi-document crawler result and confirming every resulting chunk appears in the queue exactly once, with no duplicates and none dropped.

**Acceptance Criteria**

> A test document dropped into a source is chunked and appears in the verification queue without any manual trigger.

### [x] AI-012 — Verification labeling schema & escalation logic

| Field                   | Value                                                    |
| ----------------------- | -------------------------------------------------------- |
| **Task ID**             | AI-012                                                   |
| **Category**            | AI                                                       |
| **Epic / Feature Area** | Verification                                             |
| **Dependencies**        | —                                                        |
| **Work Stream**         | Trust & Delivery Systems (Adan)                          |
| **Phase Group**         | Ingestion & Verification                                 |
| **Branch Name**         | `feature/ai-012-verification-labeling-schema-escalation` |
| **Status (sheet)**      | To Do                                                    |
| **Assignee (sheet)**    | Adan Alawni                                              |
| **Review tier**         | single-round                                             |

**Full Technical Details**

> Objective: Ten different engineers need to apply 'correct/incorrect/uncertain' consistently, or the whole verification pipeline's output quality varies by who happened to review a given item — this is what prevents that.

> Approach: A written, concrete labeling rubric (what specifically must be checked against the source to apply 'correct' — e.g. 'the cited section's exact page/paragraph states this value, and the calculation method matches the source formula exactly', not 'this looks about right') distributed to all 10 verifiers, plus the escalation rule: any 'incorrect' or 'uncertain' label, or any case a verifier isn't confident applying the rubric to, routes to lead-engineer review rather than being resolved unilaterally.

> Interface: The rubric lives in docs/ as a reviewable document (not just implicit in code); EscalationItem's creation logic in BE-007 encodes the routing rule.

> Edge cases: A verifier applying 'correct' to something that later turns out wrong is a rubric or training failure to fix, not just a one-off error to silently correct — feed corrected mislabels back into refining the rubric itself.

> Testing: An inter-rater agreement check: 2 verifiers independently label the same 10 test items, checked for matching labels per the documented rubric, before the rubric is considered good enough to roll out to all 10.

**Acceptance Criteria**

> Two different verifying engineers given the same test item independently apply the same label per the documented schema.

### [x] AI-014 — Post-launch user feedback loop

| Field                   | Value                                           |
| ----------------------- | ----------------------------------------------- |
| **Task ID**             | AI-014                                          |
| **Category**            | AI                                              |
| **Epic / Feature Area** | Verification                                    |
| **Dependencies**        | BE-007                                          |
| **Work Stream**         | Trust & Delivery Systems (Adan)                 |
| **Phase Group**         | Ingestion & Verification                        |
| **Branch Name**         | `feature/ai-014-post-launch-user-feedback-loop` |
| **Status (sheet)**      | To Do                                           |
| **Assignee (sheet)**    | Adan Alawni                                     |
| **Review tier**         | single-round                                    |

**Full Technical Details**

> Objective: Extends verification beyond the pre-launch push into an ongoing accuracy mechanism — real usage surfaces cases the eval set and pre-launch verification didn't anticipate, and this captures them instead of losing that signal.

> Approach: A user-facing 'flag this answer' action (attaches to the existing response card, FE-004) posts the flagged answer's full context (question, retrieved chunks used, generated answer) to a FlaggedAnswer record, which enters the verification queue (BE-007) as a new item, tagged distinctly from pre-launch content so the team can track post-launch accuracy trends separately from initial-launch coverage.

> Interface: POST /v1/feedback/flag with {messageId, reason?}.

> Edge cases: A flagged answer's original retrieved chunks/context must be captured at flag-time, not re-derived later (retrieval results for the same query can change over time as the index grows) — otherwise the reviewer can't reconstruct what the user actually saw.

> Testing: Confirm a flagged item's queue entry contains the exact original context (not a freshly re-run retrieval), and that it's visibly distinguishable in the dashboard from pre-launch verification items.

**Acceptance Criteria**

> A flagged answer reliably appears in the verification queue with the original question/answer/context attached for the reviewer.

## Foundation

### [ ] BE-015 — CI/CD pipeline & branch protection

> **PARTIALLY SATISFIED by work already on `main` — left unticked.**
>
> Live and verified against the GitHub API rather than assumed: the `ci`
> required status check, `enforce_admins: true`, force-push and deletion both
> blocked, and all seven tool gates the approach calls for (ruff, black, mypy,
> pytest, eslint, tsc, vitest). A direct push to `main` was rejected with
> `GH006` during this run, so the no-direct-push criterion is proven rather
> than configured-and-hoped.
>
> Two specific gaps remain, deliberately **not** settled unilaterally:
>
> 1. `required_approving_review_count` is `0`; the criterion asks for ≥1.
>    Enforcing it would end the unattended run, since these PRs are
>    self-merged and no second reviewer exists.
> 2. No `staging` branch exists, so its protection cannot be configured.
>
> Both are Ayed's call. Repository settings were not modified.

| Field                   | Value                                            |
| ----------------------- | ------------------------------------------------ |
| **Task ID**             | BE-015                                           |
| **Category**            | Backend                                          |
| **Epic / Feature Area** | Foundation                                       |
| **Dependencies**        | BE-001                                           |
| **Work Stream**         | Trust & Delivery Systems (Adan)                  |
| **Phase Group**         | Foundation                                       |
| **Branch Name**         | `feature/be-015-cicd-pipeline-branch-protection` |
| **Status (sheet)**      | To Do                                            |
| **Assignee (sheet)**    | Adan Alawni                                      |
| **Review tier**         | single-round                                     |

**Full Technical Details**

> Objective: Makes the whole Code Standards tab enforceable rather than aspirational — a rule nobody can accidentally bypass is the only kind of rule that reliably holds across 20 people.

> Approach: GitHub Actions workflow on every PR: install deps, ruff check, black --check, mypy, pytest (backend); eslint, tsc --noEmit, frontend test suite (frontend) — any failure blocks merge via required-status-check branch protection on main and staging. Branch protection additionally requires >=1 approving review and disallows force-push/direct-push to both branches for all contributors including admins.

> Interface: .github/workflows/ci.yml; branch protection configured in repo settings, documented in docs/adr/ so it isn't just tribal knowledge.

> Edge cases: A workflow failure due to a flaky test must be distinguishable from a genuine failure — flaky tests get fixed or explicitly quarantined, never just re-run until green becomes a habit.

> Testing: A deliberate test PR with a lint violation and one with a failing test, both confirmed blocked from merging, as part of setting this up.

**Acceptance Criteria**

> A direct push attempt to main/staging is rejected by branch protection; a PR cannot merge with failing CI or zero approvals.

## Calc Tools & Panel Design

### [ ] AI-005 — Cable sizing calculation function

> **BLOCKED — do not attempt on general knowledge.**
>
> This task's constants must come from a named source document that is not
> available in this repository or to this run: Prysmian Wire & Cable Engineering Handbook, 5th ed. §4.2 (reproducing IEC 60364-5-52:2009 Annex B, Tables B.52.2–B.52.5). The acceptance
> criterion requires exact agreement with worked examples published in that
> document.
>
> Ampacity, derating and rating tables can be written from general engineering
> knowledge. They would look correct, carry citations to a document nobody
> read, and pass tests invented alongside them. The spec is explicit that
> "close enough is not an acceptable test result, given real cable/fire safety
> is downstream of this number" — which is exactly why plausible-but-unsourced
> values are the worst possible output here, not an acceptable interim one.
>
> Unblocking needs the document, not more effort.

| Field                   | Value                                              |
| ----------------------- | -------------------------------------------------- |
| **Task ID**             | AI-005                                             |
| **Category**            | AI                                                 |
| **Epic / Feature Area** | Calc Tools                                         |
| **Dependencies**        | —                                                  |
| **Work Stream**         | Trust & Delivery Systems (Adan)                    |
| **Phase Group**         | Calc Tools & Panel Design                          |
| **Branch Name**         | `feature/ai-005-cable-sizing-calculation-function` |
| **Status (sheet)**      | To Do                                              |
| **Assignee (sheet)**    | Adan Alawni                                        |
| **Review tier**         | deep                                               |

**Full Technical Details**

> Objective: The first and simplest calc tool, and the template the others (VFD, panel) follow — its sourcing discipline sets the pattern for every deterministic, safety-relevant calculation in the product.

> Approach: Pure function (no LLM call inside it) implementing cable ampacity/derating tables and voltage-drop calculation, every constant/table value sourced directly from a named manufacturer engineering guide (not IEC standard text, per the licensing constraint) and cited by section in the function's docstring per the Code Standards requirement.

> Interface: size_cable(current_a: float, length_m: float, install_method: InstallMethod, ambient_c: float) -> CableSizingResult, with a typed InstallMethod enum matching exactly the source guide's categories — no free-text install-method input that could mismatch the table.

> Edge cases: An input combination outside the source table's covered range (e.g. ambient temperature beyond what the guide tabulates) raises a typed OutOfValidatedRangeError, caught by BE-011/BE-008 and turned into the refuse-path response, never extrapolated silently.

> Testing: Exact match against at least 10 worked examples published in the source guide itself — this is the one place where 'close enough' is not an acceptable test result, given real cable/fire safety is downstream of this number.

**Acceptance Criteria**

> Output matches hand-calculated reference values for at least 10 published worked examples from the source guides, exactly.

### [ ] AI-006 — VFD selection calculation function

> **BLOCKED — do not attempt on general knowledge.**
>
> This task's constants must come from a named source document that is not
> available in this repository or to this run: ABB ACS880 Hardware Manual (3AUA0000078093) §3 and §5, cross-checked against Siemens SINAMICS G120 Operating Instructions §4.2. The acceptance
> criterion requires exact agreement with worked examples published in that
> document.
>
> Ampacity, derating and rating tables can be written from general engineering
> knowledge. They would look correct, carry citations to a document nobody
> read, and pass tests invented alongside them. The spec is explicit that
> "close enough is not an acceptable test result, given real cable/fire safety
> is downstream of this number" — which is exactly why plausible-but-unsourced
> values are the worst possible output here, not an acceptable interim one.
>
> Unblocking needs the document, not more effort.

| Field                   | Value                                               |
| ----------------------- | --------------------------------------------------- |
| **Task ID**             | AI-006                                              |
| **Category**            | AI                                                  |
| **Epic / Feature Area** | Calc Tools                                          |
| **Dependencies**        | —                                                   |
| **Work Stream**         | Trust & Delivery Systems (Adan)                     |
| **Phase Group**         | Calc Tools & Panel Design                           |
| **Branch Name**         | `feature/ai-006-vfd-selection-calculation-function` |
| **Status (sheet)**      | To Do                                               |
| **Assignee (sheet)**    | Adan Alawni                                         |
| **Review tier**         | deep                                                |

**Full Technical Details**

> Objective: Same sourcing/testing discipline as AI-005, applied to VFD selection — also literally the exact calculation category shown in OhmX's own hero demo, so it needs to be at least as rigorous as what this project is positioned against.

> Approach: Pure function taking motor nameplate parameters (power, voltage, full-load current, duty type) and returning a ranked shortlist of appropriate VFD models/ratings per the source manufacturer's own selection-guide logic, each recommendation citing the guide table it came from.

> Interface: select_vfd(motor: MotorSpec) -> list[VfdRecommendation].

> Edge cases: Duty cycle type (continuous vs. intermittent vs. high-inertia-load) materially changes the correct selection — must be an explicit required input, never defaulted to continuous, since a silent default here is exactly the 'confident wrong answer' pattern the whole product exists to avoid.

> Testing: Match against >=10 manufacturer-published selection-guide worked examples, plus one deliberately-ambiguous input (missing duty type) asserted to raise a clear validation error rather than guessing a default.

**Acceptance Criteria**

> Output matches manufacturer-published selection-guide examples for at least 10 test cases.

### [ ] AI-007 — Panel component/load sizing functions

> **BLOCKED — do not attempt on general knowledge.**
>
> This task's constants must come from a named source document that is not
> available in this repository or to this run: Rittal Handbook 36 §2 and §5, and Schneider Electric Electrical Installation Guide 2024 §H. The acceptance
> criterion requires exact agreement with worked examples published in that
> document.
>
> Ampacity, derating and rating tables can be written from general engineering
> knowledge. They would look correct, carry citations to a document nobody
> read, and pass tests invented alongside them. The spec is explicit that
> "close enough is not an acceptable test result, given real cable/fire safety
> is downstream of this number" — which is exactly why plausible-but-unsourced
> values are the worst possible output here, not an acceptable interim one.
>
> Unblocking needs the document, not more effort.

| Field                   | Value                                                 |
| ----------------------- | ----------------------------------------------------- |
| **Task ID**             | AI-007                                                |
| **Category**            | AI                                                    |
| **Epic / Feature Area** | Panel Design                                          |
| **Dependencies**        | AI-005,AI-006                                         |
| **Work Stream**         | Trust & Delivery Systems (Adan)                       |
| **Phase Group**         | Calc Tools & Panel Design                             |
| **Branch Name**         | `feature/ai-007-panel-componentload-sizing-functions` |
| **Status (sheet)**      | To Do                                                 |
| **Assignee (sheet)**    | Adan Alawni                                           |
| **Review tier**         | deep                                                  |

**Full Technical Details**

> Objective: Scales the calc-tool pattern from a single component (cable, VFD) to a whole panel's worth of components — the highest-complexity calc tool in the product, directly gating the panel-design feature's safety.

> Approach: Composes AI-005/006-style deterministic functions across a full load list — for each load, determines breaker rating (with standard/preferred rating rounding, not a raw calculated value), contactor rating, and terminal-block sizing, then aggregates into a component list. Explicitly returns a PanelBomDraft type (matching BE-011) with no code path producing anything else — there is no 'finalize' function anywhere in this module, by design.

> Interface: size_panel(loads: list[LoadSpec], environment: EnvironmentSpec) -> PanelBomDraft.

> Edge cases: Total panel load must be checked against the specified enclosure/environment (NEMA/IP) rating limits where the source guides define such limits, refusing via the out-of-range error pattern rather than sizing components for a physically inconsistent panel.

> Testing: 5 representative panel scenarios (varying load count and mix) verified against hand-calculated reference results; a schema-level test confirming PanelBomDraft's mandatory-review field cannot be omitted or overridden via any input parameter.

**Acceptance Criteria**

> Output matches hand-calculated reference values for 5 representative panel load scenarios; every response object carries the mandatory-review flag with no parameter that removes it.

### [ ] BE-011 — Panel BOM generation endpoint

> **BLOCKED — depends on a blocked calculation.**
>
> The endpoint itself is buildable; what it would return is not. It consumes
> AI-007's panel sizing and AI-005's cable sizing, whose constants are unavailable (see above), so shipping this
> would mean shipping a route that serves numbers nobody can vouch for.

| Field                   | Value                                          |
| ----------------------- | ---------------------------------------------- |
| **Task ID**             | BE-011                                         |
| **Category**            | Backend                                        |
| **Epic / Feature Area** | Panel Design                                   |
| **Dependencies**        | BE-001,AI-007                                  |
| **Work Stream**         | Trust & Delivery Systems (Adan)                |
| **Phase Group**         | Calc Tools & Panel Design                      |
| **Branch Name**         | `feature/be-011-panel-bom-generation-endpoint` |
| **Status (sheet)**      | To Do                                          |
| **Assignee (sheet)**    | Adan Alawni                                    |
| **Review tier**         | deep                                           |

**Full Technical Details**

> Objective: The backend half of the highest-stakes feature in the product — its job is entirely to produce a labeled draft, never a 'final' artifact, and that constraint has to be enforced in code, not just documentation.

> Approach: POST /v1/panel/bom accepts structured load/requirement input (loads:{type,voltage,current,qty}[], environment:{nemaRating}), calls AI-007's calc-tool functions per load item, aggregates into a component list. Response schema has no field or flag representing 'final/approved' — the mandatory-review indicator is baked into the schema itself, so no caller can construct a request that omits it.

> Interface: Response type PanelBomDraft — note the type name itself: no PanelBomFinal type exists anywhere in the codebase, by design.

> Edge cases: A load combination outside the calc tools' validated range returns a clear 'outside verified range — consult a source directly' response rather than extrapolating.

> Testing: The 5 reference load scenarios from AI-007's own test suite, re-verified at the endpoint level, confirming the response always includes the review flag regardless of input.

**Acceptance Criteria**

> Output component ratings match hand-calculated reference values for 3 test load scenarios; review flag is present on every response with no way to suppress it via request params.

### [ ] FE-010 — Panel BOM / checklist display

> **BLOCKED — depends on a blocked calculation.**
>
> The endpoint itself is buildable; what it would return is not. It consumes
> BE-011, and through it AI-005 and AI-007, whose constants are unavailable (see above), so shipping this
> would mean shipping a route that serves numbers nobody can vouch for.

| Field                   | Value                                        |
| ----------------------- | -------------------------------------------- |
| **Task ID**             | FE-010                                       |
| **Category**            | Frontend                                     |
| **Epic / Feature Area** | Panel Design                                 |
| **Dependencies**        | FE-001,BE-011,AI-007                         |
| **Work Stream**         | Trust & Delivery Systems (Adan)              |
| **Phase Group**         | Calc Tools & Panel Design                    |
| **Branch Name**         | `feature/fe-010-panel-bom-checklist-display` |
| **Status (sheet)**      | To Do                                        |
| **Assignee (sheet)**    | Adan Alawni                                  |
| **Review tier**         | single-round                                 |

**Full Technical Details**

> Objective: The highest-stakes UI surface in the product — the review-required banner is a safety control, not a legal footnote, and has to be built with that weight.

> Approach: Table component (component list, quantity, calculated rating, source citation per row) plus a sticky banner component that cannot be dismissed (no close button, no 'don't show again'), rendering on every view of a BOM.

> Interface: Export action generates CSV client-side (e.g. papaparse) and PDF (print-stylesheet or server-rendered); both export formats must include the review-required text in the exported file itself, not just the on-screen view, so it survives being forwarded or printed.

> Edge cases: No export path may omit the review-required notice — this needs an automated check, not a code-review convention.

> Testing: An automated test asserting every export format (CSV, PDF) contains the review-required string.

**Acceptance Criteria**

> The review-required banner is impossible to miss and cannot be permanently dismissed; BOM exports to a shareable format (CSV/PDF).

## PLC Programming

### [x] AI-009 — PLC code generation + validation layer

| Field                   | Value                                                   |
| ----------------------- | ------------------------------------------------------- |
| **Task ID**             | AI-009                                                  |
| **Category**            | AI                                                      |
| **Epic / Feature Area** | PLC Programming                                         |
| **Dependencies**        | —                                                       |
| **Work Stream**         | Trust & Delivery Systems (Adan)                         |
| **Phase Group**         | PLC Programming                                         |
| **Branch Name**         | `feature/ai-009-plc-code-generation-+-validation-layer` |
| **Status (sheet)**      | To Do                                                   |
| **Assignee (sheet)**    | Adan Alawni                                             |
| **Review tier**         | deep                                                    |

**Full Technical Details**

> Objective: Applies cite-or-refuse's core principle — never present unverified output as trustworthy — to code correctness instead of factual correctness, since generated Ladder/ST that looks plausible but has a logic error is its own serious failure mode if an engineer trusts and deploys it.

> Approach: Generation produces Ladder (as the structured rung representation FE-009 expects, not raw text) or ST code per request. A separate validation pass — ideally leveraging an actual PLC syntax parser/simulator library rather than asking the LLM to self-check its own output — checks syntax validity and basic logic soundness (unreferenced tags, unreachable rungs, obvious type mismatches) before the result can carry a 'ready' status.

> Interface: generate_plc_code(request) -> PlcGenerationResult; validation as a distinct validate_plc_code(code) -> PlcValidationResult callable independently for the review use case (BE-010's second endpoint) where no generation occurs at all.

> Edge cases: Validation tooling that can't fully parse a given dialect/vendor variant returns an explicit 'validation incomplete for this dialect' status rather than a false pass — an unverifiable result is not the same as a verified-correct one.

> Testing: A fixture set of deliberately broken code (per dialect where possible) each asserted to be caught, plus valid reference code asserted not to be false-flagged.

**Acceptance Criteria**

> Deliberately broken test code is correctly flagged by the validation layer in 100% of a test batch; valid code is not false-flagged.

### [x] BE-010 — PLC code generation/review endpoint

| Field                   | Value                                               |
| ----------------------- | --------------------------------------------------- |
| **Task ID**             | BE-010                                              |
| **Category**            | Backend                                             |
| **Epic / Feature Area** | PLC Programming                                     |
| **Dependencies**        | BE-001,AI-009                                       |
| **Work Stream**         | Trust & Delivery Systems (Adan)                     |
| **Phase Group**         | PLC Programming                                     |
| **Branch Name**         | `feature/be-010-plc-code-generationreview-endpoint` |
| **Status (sheet)**      | To Do                                               |
| **Assignee (sheet)**    | Adan Alawni                                         |
| **Review tier**         | deep                                                |

**Full Technical Details**

> Objective: Applies the same 'never trust unverified output' principle from cite-or-refuse to a different failure mode — syntactically or logically broken PLC code, its own costly mistake if deployed to real equipment.

> Approach: POST /v1/plc/generate and POST /v1/plc/review (existing-code input), both routing through AI-009's generation + validation pipeline, returning the same {code | ladderRungs, validation} shape FE-009 expects. The endpoint contains no PLC-domain logic itself — a thin wrapper calling app/ai/tools functions, per the thin-routes standard.

> Interface: Shared PlcValidationResult type in shared-types so frontend and backend can't drift on what pass/fail/warning means.

> Edge cases: If validation itself errors out (rather than returning a valid fail result), the endpoint returns an explicit 'validation unavailable' state — never falls back to returning code as if it passed.

> Testing: A suite of intentionally-broken code fixtures (unclosed rung, undefined tag reference, type mismatch) each asserted to produce the correct validation failure, plus one valid fixture asserted to pass cleanly.

**Acceptance Criteria**

> A request for known-invalid code returns validation failures with location info, not a false pass.

### [x] FE-009 — PLC code display & review component

| Field                   | Value                                              |
| ----------------------- | -------------------------------------------------- |
| **Task ID**             | FE-009                                             |
| **Category**            | Frontend                                           |
| **Epic / Feature Area** | PLC Programming                                    |
| **Dependencies**        | FE-001,BE-010,AI-009                               |
| **Work Stream**         | Trust & Delivery Systems (Adan)                    |
| **Phase Group**         | PLC Programming                                    |
| **Branch Name**         | `feature/fe-009-plc-code-display-review-component` |
| **Status (sheet)**      | To Do                                              |
| **Assignee (sheet)**    | Adan Alawni                                        |
| **Review tier**         | deep                                               |

**Full Technical Details**

> Objective: Ladder and ST are visually very different (graphical rungs vs. text) — this is the exact component OhmX's own comparison claims superiority on, so it has to actually deliver that, not just gesture at it.

> Approach: ST renders via a code syntax highlighter (e.g. Shiki) with a custom grammar for ST keywords. Ladder renders graphically as SVG rungs (contacts, coils, blocks) generated from a structured ladder-representation the backend returns — not an image or ASCII art — so it stays crisp at any zoom.

> Interface: Accepts {language:'ladder'|'st', code | ladderRungs, validation:{status, issues:{line,message,severity}[]}}.

> Edge cases: A validation.status==='fail' response must visually block the 'looks done' impression — a red banner above the code, not just inline squiggles someone could miss.

> Testing: Render tests against representative Ladder rung shapes (simple contact-coil, branch, timer block) confirming SVG generation handles branching correctly, not just the trivial case.

**Acceptance Criteria**

> Generated code displays with correct syntax highlighting for both Ladder and ST; validation warnings are visually attached to the relevant lines.

## Internal Tools

### [x] FE-012 — Verification dashboard (for the 10-engineer queue)

| Field                   | Value                                                     |
| ----------------------- | --------------------------------------------------------- |
| **Task ID**             | FE-012                                                    |
| **Category**            | Frontend                                                  |
| **Epic / Feature Area** | Internal Tools                                            |
| **Dependencies**        | BE-007,AI-012                                             |
| **Work Stream**         | Trust & Delivery Systems (Adan)                           |
| **Phase Group**         | Internal Tools                                            |
| **Branch Name**         | `feature/fe-012-verification-dashboard-for-the-10-engine` |
| **Status (sheet)**      | To Do                                                     |
| **Assignee (sheet)**    | Adan Alawni                                               |
| **Review tier**         | single-round                                              |

**Full Technical Details**

> Objective: The 10 verifying engineers are doing the single most safety-critical work in the project — the tool has to make that fast and hard to do carelessly, not just possible.

> Approach: Split-pane view: proposed answer/content on one side, the cited source document (embedded viewer where possible, page/section pre-highlighted) on the other, so the verifier never leaves the tool to check the source. Three separated action buttons (Correct / Incorrect / Uncertain); Incorrect and Uncertain require a short mandatory note before submitting.

> Interface: Pulls the day's assigned queue from BE-007 (GET /verification/queue/me), submits labels back per item (POST /verification/items/{id}/label).

> Edge cases: Two verifiers must never be able to claim the same queue item simultaneously — if a race occurs server-side, the UI must show 'already claimed by X' rather than allowing a silent double-submit.

> Testing: A full click-through test of one verification cycle, plus a check that the mandatory-note requirement actually blocks submission when empty.

**Acceptance Criteria**

> A verifier can complete a full review cycle (view item, view source, label, submit) without leaving the tool; escalated items surface to lead-engineer view.

## Polish & Hardening

### [ ] FE-013 — Responsive/mobile-web layout pass

| Field                   | Value                                             |
| ----------------------- | ------------------------------------------------- |
| **Task ID**             | FE-013                                            |
| **Category**            | Frontend                                          |
| **Epic / Feature Area** | General                                           |
| **Dependencies**        | FE-003,FE-004,FE-008,FE-010                       |
| **Work Stream**         | Final Integration (Joint)                         |
| **Phase Group**         | Polish & Hardening                                |
| **Branch Name**         | `feature/fe-013-responsivemobile-web-layout-pass` |
| **Status (sheet)**      | To Do                                             |
| **Assignee (sheet)**    | Both (joint audit)                                |
| **Review tier**         | single-round                                      |

**Full Technical Details**

> Objective: Ensures the primary usage context (tablet/phone browser near equipment) actually works, not just the desktop layout the team likely builds and tests on by default.

> Approach: Audit against real breakpoints (360px small-phone, 768px tablet), not only a desktop-first 1440px baseline; the chat input, response cards, and BOM table are highest-risk for overflow given their content density.

> Interface: n/a — audit/fix pass across existing components, not a new component.

> Edge cases: The panel BOM table needs a mobile-friendly fallback (e.g. stacked card view below a width threshold) since a wide multi-column table cannot simply shrink to fit a phone screen.

> Testing: Manual pass on real device sizes (or accurate emulation) for every customer-facing screen, logged as a checklist against each screen.

**Acceptance Criteria**

> No horizontal scroll or clipped content at common tablet/phone widths on any customer-facing screen.
