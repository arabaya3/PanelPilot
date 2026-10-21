# Graphite Workbench: end-to-end verification report

**Verdict: ready with limitations.** Every confirmed defect in scope is fixed and
covered by tests, and the integrated app was exercised against the real local
backend. Limitations are listed in [Remaining gaps](#remaining-gaps).

- **Tested code commit:** `a8b29dc` on `fix/redesign-e2e-verification` (PR #199).
  This report and its screenshots were committed after it.
- **Stack:** web `next dev` → API container built from the same worktree →
  shared Postgres 16, Redis, OpenSearch (`panelpilot-production`: 6,376 verified
  manual passages), worker; LLM provider Anthropic, embeddings Voyage.
- **Browser:** headless Chromium 156 (Playwright), persistent profiles.

## Defects fixed in this pass

Each was reproduced in the browser or against the API before it was fixed. Every
key regression test was shown to fail with its fix reverted.

| #   | Sev. | Defect and reproduction                                                                                                                                                                                                                                                                                               | Fix                                                                                                                                                                                                                                                                                                                                                         |
| --- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | High | **Real answers rendered as "Not certain".** Ask "ACS880 fault 2310". The card shows `data-variant="uncertain"`. The cause: steps cite passage ids, which were matched against `citations[].document_id` (the PDF URL), so nothing matched.                                                                            | Citations resolve against `evidence[].id`, each with its own page. They fall back to document ids only when no evidence is returned (replayed turns).                                                                                                                                                                                                       |
| 2   | High | **Any equipment model filter returned zero passages.** "ACS880 fault 2310" with `{ABB, ACS880}` gives 0 hits, then "No document in the library covers this". Every indexed chunk has `model="unknown"`, and the chat copies an answer's model into the session, so after one answer every follow-up question refused. | The model filter also keeps chunks with an unknown or missing model, and still excludes chunks recorded for another model. The lexical leg ranks a real model match higher.                                                                                                                                                                                 |
| 3   | Med  | **The brand filter was case-sensitive.** "abb" gives 0 hits.                                                                                                                                                                                                                                                          | Brands match case-insensitively.                                                                                                                                                                                                                                                                                                                            |
| 4   | Med  | **A refusal said "the nearest passage is cited"** while returning `evidence: []`. `_refusal_response` passed `citations=` to a model without that field, and Pydantic silently dropped it.                                                                                                                            | New `refusal_citations` field, and `extra="forbid"` so a misnamed field fails loudly. The templates no longer promise a citation. The UI shows the closest match labelled "does not answer this" only when one is returned (en/ar/he).                                                                                                                      |
| 5   | Med  | **Calculator errors exposed raw API text** ("design_current_a must be positive, got -5"), with no `aria-invalid`. `1e999` caused a **500** in all three calculators.                                                                                                                                                  | Field-level validation of the constraints the API itself enforces, plus coded 422s (`number_too_large`, `drive_altitude`) mapped to localized messages at the field. Values are kept and focus moves to the first bad field. The decimal error handling catches only overflow and invalid operations, so a real division by zero still surfaces as a fault. |
| 6   | Med  | **A valid PLC program showed no verdict.** The API returned `{"status":"valid"}` and the banner rendered `null`.                                                                                                                                                                                                      | Every status renders a banner. "Passed" states its scope (a subset of IEC 61131-3 ST) and that it does not test runtime behaviour or machine safety.                                                                                                                                                                                                        |
| 7   | Med  | **A multi-entry history jump discarded unsaved design work.** Dirty `/design`, then `history.go(-2)`: the page is gone and the form is empty on return. Traversal cannot be cancelled.                                                                                                                                | A tab-scoped recoverable draft (sessionStorage), tied to the trial session id. It holds no token or secret, is offered on return, and is cleared on save, undo, discard or trial end.                                                                                                                                                                       |
| 8   | Low  | Three more guard history bugs: a buffer entry was pushed under a foreign URL; the buffer mark leaked into view entries; an inherited buffer stepped back twice.                                                                                                                                                       | Each fixed with its own test.                                                                                                                                                                                                                                                                                                                               |
| 9   | Low  | **A 429 on trial resume showed "Could not connect"** and fell through. On `/design` it read "service unavailable".                                                                                                                                                                                                    | Mapped to `rate-limited`. The stored trial is kept, and the message says to wait.                                                                                                                                                                                                                                                                           |
| 10  | Low  | **A double click on a rail link dismissed the guard dialog** through the backdrop.                                                                                                                                                                                                                                    | The Dialog ignores backdrop clicks within 400 ms of opening.                                                                                                                                                                                                                                                                                                |
| 11  | Low  | Streamed diagnosis log lines had no correlation id.                                                                                                                                                                                                                                                                   | The id is bound around each stream frame.                                                                                                                                                                                                                                                                                                                   |
| 12  | Low  | **Search showed unrelated passages for gibberish** with no explanation (see the evaluation below).                                                                                                                                                                                                                    | Each result says whether it contains the searched code or words. A notice appears when none does. No score threshold was added.                                                                                                                                                                                                                             |

## Successful diagnosis (real model, real index)

- **Question:** `ACS880 fault 2310`, no equipment set.
- **Path:** browser → `/api/v1/diagnostics/stream` (correlation
  `7653f5466bd84b9f9ce2c00874eb8269`, HTTP 200) → hybrid retrieval → guardrail
  ANSWER → generation (schema valid) → citation check → rendered card,
  `data-variant="diagnosis"`, severity critical.
- **Source:** _ABB ACS880 primary control program firmware manual_, rev A4,
  section "Fault messages", **p.519**: "2310 Overcurrent — Output current has
  exceeded internal fault limit. Check motor load… 95.04… groups 23/26/28…
  motor cable… contactors…". A secondary citation is p.124, the fault-word bit table.
- **Claims checked by hand against p.519:** fault meaning, motor-load check,
  parameter 95.04 for an externally powered control unit, ramp groups 23/26/28,
  motor and encoder cabling. They match the passage. No claim was found
  without support.
- **Unsupported question:** "What brand of grease should I use on the ACS880
  motor bearings?" (correlation `1024e7a6e3304869a5255a71069657f3`) is refused.
  The card shows "No answer given" and the closest match, p.495, labelled as
  not answering.
- **Network failure** (stream aborted in the browser) renders "This answer did
  not finish… Try again". This is distinct from an evidence-based refusal.

> One successful question is an end-to-end smoke test, not proof of general
> reliability.

### Real-model observations (non-deterministic, kept separate)

- During the pass, three unsupported questions refused as `not_in_sources`, and
  one supported question answered across several runs (confidence 0.85–0.9).
- Not observed: schema failure, citation rejection, provider outage.
- Refusal body text is still English in Arabic and Hebrew. Translating it needs
  a reason-code contract change; see the gaps below.

## Deterministic results (`a8b29dc`)

| Check                                                  | Baseline (`cc7be9b`)     | Final                              |
| ------------------------------------------------------ | ------------------------ | ---------------------------------- |
| `tsc --noEmit`                                         | pass                     | pass                               |
| `npm run lint` (eslint, tokens, logical, 360px)        | 0 errors                 | 0 errors (same 4 `<img>` warnings) |
| `prettier --check`                                     | pass                     | pass                               |
| vitest (run twice)                                     | 766/766                  | **853/853** both runs              |
| `ruff check` / `mypy app` (strict)                     | pass / pass              | pass / pass                        |
| pytest                                                 | 3215 passed, 339 skipped | **3230 passed, 339 skipped**       |
| coverage `app.ai.tools` / `app.ai.guardrails` (≥ 95 %) | —                        | 98.53 % / 98.20 %                  |
| `next build`                                           | pass                     | pass                               |

- No timeouts were raised, no tests were skipped and no assertions were weakened.
- `ruff format --check` flags 3 files, all pre-existing and outside this diff.
- `market.test.tsx` failed once at 5.7 s while pytest and a browser run shared
  the machine. It passed on every other run; this is load-sensitive, not a
  logic failure.

## Search evaluation (18 queries, `POST /api/v1/search`, top 10)

| Category                        | Queries                           | Top hit relevant                | "Weak match" notice               |
| ------------------------------- | --------------------------------- | ------------------------------- | --------------------------------- |
| Exact fault codes in corpus     | F30001, F07801, ACS880 fault 2310 | 3/3 (passages contain the code) | 0/3                               |
| Manufacturer/model questions    | 3                                 | 2 yes, 1 partly                 | 0/3                               |
| Paraphrases                     | 3                                 | 2 yes, 1 weak                   | 0/3                               |
| Unrelated text                  | 3                                 | 0/3                             | 2/3 (one passage contains "2018") |
| Gibberish                       | 3                                 | 0/3                             | 3/3                               |
| Arabic / Hebrew                 | 2                                 | 0/2 (manuals are English)       | 2/2                               |
| Code absent from corpus (F0001) | 1                                 | 0/1                             | 1/1                               |

**Finding:** the irrelevant results are arbitrary vector neighbours, not a
filtering bug.

- kNN always returns k results, and fused scores are min-max normalised within
  each query.
- Absolute similarity does not separate noise from real questions:
  "lorem ipsum" scores 0.640, above most real queries.
- The module's own calibration declines to recommend a floor on this sample.

So no threshold was added. Each result now states whether it contains what was
searched. Before and after, the rankings are identical; only the presentation
changed.

## Unsaved-work protection: browser matrix

| Path                                                                     | Result                                                                                                                    |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| Single Back / Forward                                                    | In-app dialog. Stay keeps URL and inputs; Discard reaches the previous page.                                              |
| Multi-entry jumps (`go(-2)`, `go(-3)`, `go(-4)` with views, rapid backs) | The page may leave; the draft is kept and the restore offer appears on return. Restore brings the inputs back as unsaved. |
| Rail / logo links                                                        | Dialog; Stay and Discard both behave as described.                                                                        |
| Open another project / revision                                          | Dialog; Stay keeps the selected revision and text, Discard loads the revision.                                            |
| Back after undoing an edit                                               | One Back leaves the page.                                                                                                 |
| Reload / close tab                                                       | Browser `beforeunload` prompt, then restore offer after reload.                                                           |
| Edit during an in-flight save                                            | Only the submitted snapshot becomes clean; the later edit stays unsaved.                                                  |
| Draft from another session, malformed or old version                     | Not offered; removed.                                                                                                     |

## Fresh screenshots (all taken on the final integrated tree)

All files are in `docs/redesign/evidence/`.

| What                                                  | File                                                                                                       |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Populated Drawings, dark / light                      | `drawings-en-dark-1440.png`, `drawings-en-light-1440.png`                                                  |
| Drawings, Arabic / Hebrew                             | `drawings-ar-dark-1440.png`, `drawings-he-dark-1440.png`                                                   |
| Drawings at 390 / 360 px                              | `drawings-en-dark-390.png`, `drawings-en-dark-360.png`                                                     |
| Load schedule, Arabic, 390 px                         | `schedule-ar-dark-390.png`                                                                                 |
| Current approved revision and exports                 | `review-export-approved-rev2-1440.png`                                                                     |
| Successful cited diagnosis                            | `diagnose-success-1280.png`, `diagnose-success-card.png`                                                   |
| Honest refusal                                        | `diagnose-refusal-1280.png`, `diagnose-refusal-card.png`                                                   |
| Calculator field validation (en 1440, ar 390, he 360) | `calc-validation-*.png`                                                                                    |
| PLC verdicts                                          | `plc-valid-1440.png`, `plc-invalid-1440.png`, `plc-unsupported-1440.png`                                   |
| Search: real query / gibberish                        | `search-real-1440.png`, `search-gibberish-1440.png`                                                        |
| Unsaved work: dialog, restore offer, restored         | `unsaved-guard-dialog-1440.png`, `unsaved-draft-restore-offer-1440.png`, `unsaved-draft-restored-1440.png` |

**Captures checked:**

- Every capture reported zero horizontal page overflow and no console errors.
- RTL captures have `dir="rtl"`.
- Identifiers (`-Q3 C10`, `3G1.5 Cu`, `400 V`) stay left-to-right.
- In dark mode the sheet text is readable on the paper surface (it was 1.01:1).

**Export inspected:** `approved-rev2.pdf`.

- 41 KB, `%PDF-1.3`.
- "E2E Approver", "E2E-A1 Tower R2" and "A1-001" appear in the title block on
  every page.
- The approval panel reads "Revision 2 approved by E2E Approver on 2026-10-21",
  and exports are enabled because the design matches the inputs.

## Remaining gaps

**Not built (out of scope; not relabelled as features):**

- Interactive schematic editor and drawing navigator. The Drawings view is a
  per-board schedule table; schematics are in the PDF and DXF exports only.
- Panel-layout editor. The view is an empty state; layout pages exist in the
  PDF only.
- Draft / Review / Issue state machine.
- Full materials workspace. Materials is the quotation form; the parts list is
  a CSV export.

**Known limitations:**

- Refusal body text is server-rendered English; only the badge, heading and
  closest-match label are translated.
- Server-sent design notes are only partly translated in Arabic and Hebrew.
- Next dev's HMR strips custom history state. This affects only the dev server
  (the app uses no `router.refresh` or server actions), but it was not
  confirmed against a production build.
- Corpus quality, for ingestion: 177 chunks are PDF glyph residue
  (`(cid:NN)`, mostly the S7-1200 manual), and some passages are indexed twice
  (ABB p.35). Nonsense queries land on these.

**Environment:**

- `deps audit (python)` fails in CI on new advisories for `certifi` 2026.7.22
  and `pillow` 12.3.0 (fix: 13.0.0, a major version). These are independent of
  this PR and also fail on `main`; the Pillow major upgrade needs its own PR
  and image-recognition testing.
- The shared trial allowance (60 requests per 5 min per IP) returned 429 for
  long stretches while three agents tested at once. Paths blocked at the time
  were re-run once the window cleared.

## Agent contributions

- **Agent D (diagnosis):** provider and index inspection; stage-by-stage trace;
  the successful cited answer; fixes 1, 4 and 11; found the retrieval defects
  behind 2 and 3.
- **Agent S (calculators, PLC, search):** fixes 2, 3, 5, 6 and 12; the 18-query
  search evaluation.
- **Agent N (data loss, integration):** fixes 7 and 8, and the design-screen
  rate-limit message; independent integrated review of all agents' changes,
  with full checks and browser re-runs.
- **Lead:**
  - Branch API container and environment.
  - Shared-types resolution in the worktree.
  - Fixes 9 and 10; draft clearing on trial end; the decimal-catch narrowing.
  - Final screenshots and export inspection; this report.
