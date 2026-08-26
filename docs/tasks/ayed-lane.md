# Ayed's lane — unattended run tracker

Generated verbatim from the project task spreadsheet (`PanelPilot`, Tasks sheet). Every field below is copied as-is; nothing is summarised or reworded.

A box is checked **only** when that task's PR has been merged to `main` with CI green. See `ayed-lane-log.md` for the running log.

---

## Borrowed from Adan's lane (bootstrap, unblocks the rest)

Selected by Task ID regardless of the Assignee cell — BE-001's assignee is blank/unreliable in the sheet. These are implemented by the unattended run purely because the rest of Ayed's lane cannot start without them; their PRs are prefixed accordingly.

### [x] BE-001 — FastAPI service scaffold

| Field                   | Value                                     |
| ----------------------- | ----------------------------------------- |
| **Task ID**             | BE-001                                    |
| **Category**            | Backend                                   |
| **Epic / Feature Area** | Foundation                                |
| **Dependencies**        | _none_                                    |
| **Work Stream**         | Bootstrap (Shared)                        |
| **Phase Group**         | Foundation                                |
| **Branch Name**         | `feature/be-001-fastapi-service-scaffold` |
| **Status (sheet)**      | To Do                                     |
| **Assignee (sheet)**    | Adan Alawni                               |

**Full Technical Details**

> Objective: The foundation every other backend/AI task builds on — getting module boundaries right here is what makes the 'thin routes, logic in domain/' standard actually enforceable later, not just aspirational. Approach: pyproject.toml-managed project (Poetry or uv). app/core/config.py using pydantic-settings BaseSettings reading from environment, no defaults for secrets (fails fast if unset in prod). Environment resolved via a single ENV var (dev|staging|prod) read once at startup, not scattered if-checks through the codebase. /health endpoint checks DB and OpenSearch connectivity, not just an unconditional 200. Interface: app/main.py wires routers from app/api/v1/* only — no route logic defined inline in main.py. Edge cases: Startup must fail loudly (non-zero exit, clear error) on missing required config, never start half-configured. Testing: A smoke test that boots the app against a test config and hits /health.

**Acceptance Criteria**

> Service boots in all three environments from env vars only, no hardcoded config; module layout matches the agreed structure exactly.

### [x] BE-002 — Auth, sessions & multi-tenant foundation

| Field                   | Value                                                  |
| ----------------------- | ------------------------------------------------------ |
| **Task ID**             | BE-002                                                 |
| **Category**            | Backend                                                |
| **Epic / Feature Area** | Auth & Tenancy                                         |
| **Dependencies**        | BE-001,BE-014                                          |
| **Work Stream**         | Bootstrap (Shared)                                     |
| **Phase Group**         | Foundation                                             |
| **Branch Name**         | `feature/be-002-auth-sessions-multi-tenant-foundation` |
| **Status (sheet)**      | To Do                                                  |
| **Assignee (sheet)**    | Adan Alawni                                            |

**Full Technical Details**

> Objective: Getting tenancy right from the start avoids the classic 'retrofit multi-tenancy later' rewrite — every table carries a tenant reference from day one, even while the only tenant type is a single free-trial user. Approach: JWT-based session auth (short-lived access token + refresh token). Tenant model where a User belongs to exactly one Tenant (a trial user gets an implicit single-user tenant created at signup, so the schema never special-cases 'no tenant'). Usage counter (Tenant.free_questions_used) incremented server-side on every completed diagnosis response, checked before allowing a new one — never trust a client-reported count. Interface: POST /auth/signup, POST /auth/login, POST /auth/refresh; get_current_user() FastAPI dependency used by every protected route. Edge cases: An anonymous trial session (FE-008) must upgrade to an authenticated one without losing its sessionId's conversation history — needs an explicit 'claim this anonymous session' step at signup, not a fresh start. Testing: A test asserting the (N+1)th free question is correctly rejected, and that a claimed anonymous session's history is visible post-signup.

**Acceptance Criteria**

> A new user can sign up and get a scoped, working session; usage counter correctly blocks further free questions once the limit is hit.

### [x] BE-003 — OpenSearch index schema & hybrid search

| Field                   | Value                                                  |
| ----------------------- | ------------------------------------------------------ |
| **Task ID**             | BE-003                                                 |
| **Category**            | Backend                                                |
| **Epic / Feature Area** | Retrieval                                              |
| **Dependencies**        | BE-001                                                 |
| **Work Stream**         | Bootstrap (Shared)                                     |
| **Phase Group**         | Foundation                                             |
| **Branch Name**         | `feature/be-003-opensearch-index-schema-hybrid-search` |
| **Status (sheet)**      | To Do                                                  |
| **Assignee (sheet)**    | Adan Alawni                                            |

**Full Technical Details**

> Objective: This schema is the backbone of every citation the product ever shows — wrong fields here means cite-or-refuse has nothing precise to cite. Approach: Index mapping: brand (keyword), model (keyword), doc_type (keyword: manual/datasheet/guide), page, section (text), source_url (keyword), verification_status (keyword: unverified/verified), content (text, BM25-analyzed), content_vector (dense_vector, kNN-indexed). Hybrid query combines BM25 match on content with a knn clause on content_vector, blended via weighted score combination — starting from the boost-tuning approach used on the SWR OpenSearch setup, then re-tuned for this domain's query patterns in AI-002. Interface: A single search(query, brand=None, model=None, index='production') function in app/ai/retrieval/ — no route or domain code constructs OpenSearch queries directly. Edge cases: index defaults to production; staging must never be queryable through this shared function without an explicit, separately-named staging-only path (ties directly to BE-004's isolation requirement). Testing: A fixture of 5 known query→expected-chunk pairs asserting the expected chunk appears in the top-3 hybrid results.

**Acceptance Criteria**

> A test query against a known-good chunk returns it in the top 3 results via hybrid search; schema fields are all populated on ingest, none left null.

### [x] BE-004 — Staging vs production index separation

| Field                   | Value                                                   |
| ----------------------- | ------------------------------------------------------- |
| **Task ID**             | BE-004                                                  |
| **Category**            | Backend                                                 |
| **Epic / Feature Area** | Ingestion Pipeline                                      |
| **Dependencies**        | BE-003                                                  |
| **Work Stream**         | Trust & Delivery Systems (Adan)                         |
| **Phase Group**         | Ingestion & Verification                                |
| **Branch Name**         | `feature/be-004-staging-vs-production-index-separation` |
| **Status (sheet)**      | To Do                                                   |
| **Assignee (sheet)**    | Adan Alawni                                             |

**Full Technical Details**

> Objective: This is the single load-bearing safety mechanism in the whole ingestion architecture — everything else (crawler, verification queue) exists to feed this boundary correctly. Approach: Two OpenSearch indices with identical mappings (content-staging, content-production). All crawler writes (BE-005) target staging exclusively — the crawler code has no credentials/client configured for production at all, so this is structural, not just policy. A promote_chunk(chunk_id) function, callable only from the verification queue's 'correct' label handler (BE-007), copies a chunk from staging to production and marks it verification_status=verified. Interface: promote_chunk() lives in app/ingestion/promotion.py; nowhere else in the codebase writes to the production index. Edge cases: A chunk already in production that gets re-crawled with changed content goes back through staging + re-verification as a new pending item — it is never silently overwritten in production. Testing: A code-level test asserting the crawler module has no reference to the production index client at all (import-level check), plus an integration test that only a 'correct'-labeled item results in a production-index write.

**Acceptance Criteria**

> It is structurally impossible for the chat endpoint to retrieve from staging; promotion only occurs via the verification-clearance path, provable by code review of the retrieval query scope.

### [x] BE-014 — Database schema & migrations

| Field                   | Value                                       |
| ----------------------- | ------------------------------------------- |
| **Task ID**             | BE-014                                      |
| **Category**            | Backend                                     |
| **Epic / Feature Area** | Foundation                                  |
| **Dependencies**        | BE-001                                      |
| **Work Stream**         | Bootstrap (Shared)                          |
| **Phase Group**         | Foundation                                  |
| **Branch Name**         | `feature/be-014-database-schema-migrations` |
| **Status (sheet)**      | To Do                                       |
| **Assignee (sheet)**    | Adan Alawni                                 |

**Full Technical Details**

> Objective: The relational backbone tying together every other backend task — sessions, verification records, and source metadata all depend on this being right first. Approach: Core tables: tenants, users, sessions, messages, verification_items, escalation_items, source_health, all created and evolved exclusively through Alembic revision files — alembic revision --autogenerate reviewed by hand before commit, never a raw CREATE/ALTER TABLE run against any environment. Interface: alembic upgrade head is the only sanctioned way any environment's schema changes. Edge cases: A migration must be reversible (a working downgrade()) unless there's a documented reason it can't be (e.g. destructive data cleanup) — undocumented one-way migrations are a standards violation. Testing: CI runs alembic upgrade head then downgrade -1 then upgrade head again against a throwaway test DB, catching irreversible or broken migrations before merge.

**Acceptance Criteria**

> A fresh environment can be brought up to current schema from migrations alone; no environment's schema was hand-edited.

---

## Ayed Rabaya's lane

All 21 rows where Assignee = "Ayed Rabaya".

### [x] FE-001 — Design system & app scaffold

| Field                   | Value                                       |
| ----------------------- | ------------------------------------------- |
| **Task ID**             | FE-001                                      |
| **Category**            | Frontend                                    |
| **Epic / Feature Area** | Foundation                                  |
| **Dependencies**        | _none_                                      |
| **Work Stream**         | Bootstrap (Shared)                          |
| **Phase Group**         | Foundation                                  |
| **Branch Name**         | `feature/fe-001-design-system-app-scaffold` |
| **Status (sheet)**      | To Do                                       |
| **Assignee (sheet)**    | Ayed Rabaya                                 |

**Full Technical Details**

> Objective: Establish the single source of truth for all visual tokens so no component ever hardcodes a color, spacing value, or font — this is what keeps 20 people building consistent UI without a design review on every PR. Approach: Next.js (App Router) bootstrap. Define tokens as CSS custom properties in one tokens.css (colors: --color-bg, --color-surface, --color-text, --color-severity-critical/warning/info, --color-accent; spacing --space-1..8 on a 4px base; type scale --font-size-xs..2xl; --font-mono for technical values). Map these into tailwind.config.ts via theme.extend referencing the CSS vars, so utility classes resolve to tokens, never Tailwind defaults. Dark mode via Tailwind's class strategy (not media), toggled by a root data-theme attribute. Interface: Exports <ThemeProvider> wrapping the app, and useTheme() returning {theme, setTheme}. Edge cases: Token file must have zero hardcoded hex values outside itself — enforce via an ESLint rule or CI grep check that fails the build on a hex code found in any .tsx file. Testing: Storybook (or equivalent) story rendering every token as a swatch in both light and dark mode, used as the visual-regression baseline for later component work.

**Acceptance Criteria**

> Storybook or equivalent shows all base tokens/components; dark mode toggle works app-wide; no hardcoded hex colors outside the token file.

### [x] FE-002 — AR/EN/HE i18n infrastructure

| Field                   | Value                                       |
| ----------------------- | ------------------------------------------- |
| **Task ID**             | FE-002                                      |
| **Category**            | Frontend                                    |
| **Epic / Feature Area** | Localization                                |
| **Dependencies**        | FE-001                                      |
| **Work Stream**         | Diagnostic Core (Ayed)                      |
| **Phase Group**         | Foundation                                  |
| **Branch Name**         | `feature/fe-002-arenhe-i18n-infrastructure` |
| **Status (sheet)**      | To Do                                       |
| **Assignee (sheet)**    | Ayed Rabaya                                 |

**Full Technical Details**

> Objective: Make language a first-class, correctly-behaving dimension of the UI from day one — retrofitting RTL support later typically breaks half the component library. Approach: next-intl (App Router-native) with three bundles: messages/ar.json, en.json, he.json. Direction resolved at the root layout: dir={locale==='en' ? 'ltr' : 'rtl'} on <html>. All spacing/positioning uses CSS logical properties (margin-inline-start, not margin-left) so components flip automatically with dir instead of needing per-locale overrides. Font loading via next/font: an Arabic-supporting family (e.g. Noto Sans Arabic) for ar, a Hebrew-supporting family (Noto Sans Hebrew) for he, sharing the same x-height/weight scale as the Latin font so switching languages doesn't visibly shift UI rhythm. Interface: useTranslations() hook per next-intl convention; a <LangSwitcher> component in the app shell. Edge cases: Technical tokens embedded in translated strings (fault codes, parameter names) must be wrapped in a <bdi dir="ltr"> span so they aren't mirrored/misordered inside RTL sentences — this is the actual mechanism behind 'LTR islands inside RTL prose'. Testing: A snapshot test per locale on a screen containing a mixed RTL-prose + LTR-token card, checked visually, not just that the string loaded.

**Acceptance Criteria**

> Switching language flips full-page direction correctly; technical tokens (fault codes, parameter names, units) stay LTR embedded inside RTL prose in all three locales; no layout breakage at the direction boundary.

### [ ] FE-003 — Core chat interface

| Field                   | Value                                |
| ----------------------- | ------------------------------------ |
| **Task ID**             | FE-003                               |
| **Category**            | Frontend                             |
| **Epic / Feature Area** | Fault Diagnosis                      |
| **Dependencies**        | FE-001,FE-002,BE-008                 |
| **Work Stream**         | Diagnostic Core (Ayed)               |
| **Phase Group**         | Core Diagnostic Flow                 |
| **Branch Name**         | `feature/fe-003-core-chat-interface` |
| **Status (sheet)**      | To Do                                |
| **Assignee (sheet)**    | Ayed Rabaya                          |

**Full Technical Details**

> Objective: The primary interaction surface — every other feature (image input, checklist, session context) attaches to this. Approach: Message list as a virtualized/windowed list (e.g. @tanstack/react-virtual) since long troubleshooting sessions accumulate many turns. Input as a controlled component with a stop/submit affordance. Consume BE-008's streamed response via ReadableStream (or EventSource for SSE), appending tokens to the in-progress assistant message as they arrive rather than buffering client-side. Interface: Session state held in React state/context ({sessionId, messages[], activeContext}); the session itself persists server-side (BE-014) and is fetched by sessionId on load, not stored in the browser. Edge cases: A network interruption mid-stream must leave a resumable/clearly-failed state, not a silently truncated answer; RTL scroll behavior needs explicit cross-browser testing. Testing: Streaming render tested against a mocked slow-stream backend response to confirm incremental rendering, not just full-response display.

**Acceptance Criteria**

> A response begins rendering within \~1s of submission and streams smoothly; chat scrolls correctly with RTL content.

### [ ] FE-004 — Structured diagnostic response card

| Field                   | Value                                                |
| ----------------------- | ---------------------------------------------------- |
| **Task ID**             | FE-004                                               |
| **Category**            | Frontend                                             |
| **Epic / Feature Area** | Fault Diagnosis                                      |
| **Dependencies**        | FE-001,BE-008,AI-003,AI-004                          |
| **Work Stream**         | Diagnostic Core (Ayed)                               |
| **Phase Group**         | Core Diagnostic Flow                                 |
| **Branch Name**         | `feature/fe-004-structured-diagnostic-response-card` |
| **Status (sheet)**      | To Do                                                |
| **Assignee (sheet)**    | Ayed Rabaya                                          |

**Full Technical Details**

> Objective: This card is the product's primary trust signal — its structure is the 'cite-or-refuse, always show your source' promise made visible. Approach: A <DiagnosticCard> component consuming AI-004's schema: {severity, cause, measurement:{label,value,unit}[], solutionSteps:{text,done}[], reference:{docName,section,url}}. Severity maps to exactly one of three token colors (critical/warning/info), never an arbitrary color. Measurement values render in the monospace token font. An \`uncertain\` variant (triggered when confidence is below threshold) replaces cause/measurement/solution with a single 'not certain — check {reference.docName}' message, using the info-severity styling so it never resembles a confident critical/warning card. Interface: Props strictly typed against the shared backend response schema via the shared-types package, so a backend schema change breaks the frontend build instead of silently rendering wrong. Edge cases: A missing/null reference must never render an empty citation — falls back to the uncertain variant entirely, since an uncited claim is exactly what the guardrail exists to prevent. Testing: Component tests with a matrix of payload variants (each severity, the uncertain variant, missing optional fields) asserting the correct visual branch renders.

**Acceptance Criteria**

> All five zones render correctly from the API's structured payload; the uncertain-state variant is visually distinct at a glance, not just a text difference.

### [ ] FE-005 — Fault-code image capture

| Field                   | Value                                     |
| ----------------------- | ----------------------------------------- |
| **Task ID**             | FE-005                                    |
| **Category**            | Frontend                                  |
| **Epic / Feature Area** | Fault Diagnosis                           |
| **Dependencies**        | FE-003,BE-009,AI-008                      |
| **Work Stream**         | Diagnostic Core (Ayed)                    |
| **Phase Group**         | Core Diagnostic Flow                      |
| **Branch Name**         | `feature/fe-005-fault-code-image-capture` |
| **Status (sheet)**      | To Do                                     |
| **Assignee (sheet)**    | Ayed Rabaya                               |

**Full Technical Details**

> Objective: Field engineers describing a fault code from memory mistype it; capturing the actual display removes that error source entirely. Approach: <input type="file" accept="image/*" capture="environment"> for the mobile camera path plus a standard drag-and-drop zone for desktop, both feeding the same upload handler. Client-side downscale/compress before upload (e.g. to a max 1600px edge via canvas) to keep upload fast on factory-floor mobile connections. Interface: On send, POSTs to BE-009's upload endpoint, receives {recognizedCode, brand, model, confidence}; high confidence pre-fills the chat message for one-tap confirm, low confidence surfaces AI-008's 'please confirm what this shows' prompt instead. Edge cases: No-camera-permission and slow-upload states need explicit UI, not a silent spinner with no timeout messaging. Testing: Manual test matrix across at least 2 real device/browser combinations (camera capture behaves inconsistently across mobile browsers) plus a unit test on the compress-before-upload step.

**Acceptance Criteria**

> A photo can be captured/dropped, previewed, and sent; recognized fault code and brand/model are shown to the user for confirmation before the diagnosis proceeds.

### [ ] FE-006 — Interactive solution checklist

| Field                   | Value                                           |
| ----------------------- | ----------------------------------------------- |
| **Task ID**             | FE-006                                          |
| **Category**            | Frontend                                        |
| **Epic / Feature Area** | Fault Diagnosis                                 |
| **Dependencies**        | FE-004                                          |
| **Work Stream**         | Diagnostic Core (Ayed)                          |
| **Phase Group**         | Core Diagnostic Flow                            |
| **Branch Name**         | `feature/fe-006-interactive-solution-checklist` |
| **Status (sheet)**      | To Do                                           |
| **Assignee (sheet)**    | Ayed Rabaya                                     |

**Full Technical Details**

> Objective: An engineer working a multi-step fix on-site needs to track progress without re-reading the whole card each time they glance back at their phone. Approach: Each solutionSteps[i] renders as a checkbox row; checked state lives in session client state keyed by {sessionId, messageId, stepIndex}, not global state, so multiple diagnostic cards in one session track independently. Interface: No backend persistence required for this scope — state is session-lifetime only unless a later task adds server-side persistence. Edge cases: Checking a step must not be mistaken for 'this step is verified correct' — it's a to-do checkbox, not a validation checkmark; keep the visual language distinct from any verification-status indicator elsewhere in the app. Testing: Interaction test confirming state persists across a re-render (e.g. new message arriving) without resetting existing checks.

**Acceptance Criteria**

> Checking a step persists on scroll/re-render within the session; state resets cleanly on a new question.

### [ ] FE-007 — Session context indicator

| Field                   | Value                                      |
| ----------------------- | ------------------------------------------ |
| **Task ID**             | FE-007                                     |
| **Category**            | Frontend                                   |
| **Epic / Feature Area** | Fault Diagnosis                            |
| **Dependencies**        | FE-003,BE-008                              |
| **Work Stream**         | Diagnostic Core (Ayed)                     |
| **Phase Group**         | Core Diagnostic Flow                       |
| **Branch Name**         | `feature/fe-007-session-context-indicator` |
| **Status (sheet)**      | To Do                                      |
| **Assignee (sheet)**    | Ayed Rabaya                                |

**Full Technical Details**

> Objective: Removes the single biggest repeated-friction point in a multi-question troubleshooting session — re-stating the same brand/model every message. Approach: A chip in the chat header bound to the session's activeContext field (set server-side by BE-008 from the first message's extracted entities, returned in the response payload). Clicking the chip opens an editable state (dropdown or text field) for the case the engineer is now working on different equipment. Interface: activeContext: {brand, model, powerRating?} — optional fields render conditionally, not as empty placeholders. Edge cases: If the backend can't confidently extract brand/model from the first message, the chip shows a neutral 'no equipment set' state rather than guessing and displaying something wrong. Testing: Verify the chip updates the moment the first response returns context, and that editing it correctly changes what's sent as context on the next message.

**Acceptance Criteria**

> Indicator updates correctly when context changes; subsequent questions in the session use it without the user repeating brand/model.

### [ ] FE-008 — Self-serve trial & landing flow

| Field                   | Value                                          |
| ----------------------- | ---------------------------------------------- |
| **Task ID**             | FE-008                                         |
| **Category**            | Frontend                                       |
| **Epic / Feature Area** | Onboarding                                     |
| **Dependencies**        | FE-001,BE-002                                  |
| **Work Stream**         | Diagnostic Core (Ayed)                         |
| **Phase Group**         | Core Diagnostic Flow                           |
| **Branch Name**         | `feature/fe-008-self-serve-trial-landing-flow` |
| **Status (sheet)**      | To Do                                          |
| **Assignee (sheet)**    | Ayed Rabaya                                    |

**Full Technical Details**

> Objective: This is the direct, deliberate contrast with OhmX's invite-only/WhatsApp funnel — it has to actually remove every manual step, not just look like it does. Approach: Landing page with a chat input visible above the fold, no 'request access' form gating it. First N messages (limit enforced server-side by BE-002/BE-012, never just hidden client-side) work with zero auth. On hitting the limit, a lightweight modal offers continued use via email/OAuth signup — one step, no approval wait. Interface: Anonymous sessions get a client-generated/server-issued temporary session ID that upgrades to an authenticated session ID on signup, without losing the in-progress conversation. Edge cases: The trial-limit modal must not interrupt mid-response — it triggers only after a response completes, never cutting off an answer in progress. Testing: Full flow test: anonymous question → real answer → limit hit → signup → conversation history preserved post-signup.

**Acceptance Criteria**

> A new visitor can ask a real question and get a real answer with zero manual steps; trial-limit and signup-prompt states are clear, not abrupt.

### [ ] FE-011 — Conversation history sidebar

| Field                   | Value                                         |
| ----------------------- | --------------------------------------------- |
| **Task ID**             | FE-011                                        |
| **Category**            | Frontend                                      |
| **Epic / Feature Area** | General                                       |
| **Dependencies**        | FE-003,BE-014                                 |
| **Work Stream**         | Diagnostic Core (Ayed)                        |
| **Phase Group**         | Core Diagnostic Flow                          |
| **Branch Name**         | `feature/fe-011-conversation-history-sidebar` |
| **Status (sheet)**      | To Do                                         |
| **Assignee (sheet)**    | Ayed Rabaya                                   |

**Full Technical Details**

> Objective: Lets an engineer return to yesterday's session on the same machine without re-describing the problem. Approach: List fetched from BE-014's session store, sorted by most recent, showing brand/model/date per entry; selecting one hydrates the chat view via the same session-fetch path used on page load. Interface: Paginated fetch (GET /sessions?cursor=) rather than loading full history at once, since a heavy user could accumulate hundreds of sessions. Edge cases: RTL layout puts the sidebar on the visual right, not hardcoded left — driven by the same logical-properties approach as FE-002, not a separate RTL-specific stylesheet. Testing: Verify sidebar ordering and that selecting an old session restores its exact context-indicator state, not just its messages.

**Acceptance Criteria**

> Selecting a past session restores its context indicator and message history correctly.

### [ ] FE-014 — Uncertainty & error states UI pass

| Field                   | Value                                             |
| ----------------------- | ------------------------------------------------- |
| **Task ID**             | FE-014                                            |
| **Category**            | Frontend                                          |
| **Epic / Feature Area** | General                                           |
| **Dependencies**        | FE-004,AI-003                                     |
| **Work Stream**         | Diagnostic Core (Ayed)                            |
| **Phase Group**         | Polish & Hardening                                |
| **Branch Name**         | `feature/fe-014-uncertainty-error-states-ui-pass` |
| **Status (sheet)**      | To Do                                             |
| **Assignee (sheet)**    | Ayed Rabaya                                       |

**Full Technical Details**

> Objective: Directly implements the product's core promise — a low-confidence answer must be impossible to mistake for a confident one, even glancing at a phone screen in bad factory lighting. Approach: Design review confirming the three states (confident / uncertain / hard error) use genuinely distinct color, icon, and layout — not just different text with the same visual chrome. Hard error (timeout, backend failure) gets its own distinct state, separate from 'uncertain', since they mean different things. Interface: n/a — audit/fix pass. Edge cases: Colorblind-safe distinction between states — don't rely on color alone; pair each state with a distinct icon shape. Testing: A design/QA sign-off checklist confirming all three states pass a 'recognizable from a glance' check, plus a basic color-contrast/colorblind-simulation check.

**Acceptance Criteria**

> A design/QA review confirms the three states are distinguishable without reading the text.

### [x] BE-008 — Chat / diagnosis orchestration endpoint

| Field                   | Value                                                  |
| ----------------------- | ------------------------------------------------------ |
| **Task ID**             | BE-008                                                 |
| **Category**            | Backend                                                |
| **Epic / Feature Area** | Fault Diagnosis                                        |
| **Dependencies**        | BE-003,BE-004,AI-002,AI-003,AI-004                     |
| **Work Stream**         | Diagnostic Core (Ayed)                                 |
| **Phase Group**         | Core Diagnostic Flow                                   |
| **Branch Name**         | `feature/be-008-chat-diagnosis-orchestration-endpoint` |
| **Status (sheet)**      | To Do                                                  |
| **Assignee (sheet)**    | Ayed Rabaya                                            |

**Full Technical Details**

> Objective: The single endpoint tying together retrieval, calc tools, guardrails, and generation — every other safety mechanism upstream is worthless if this endpoint doesn't enforce 'production index only' and 'cite-or-refuse'. Approach: POST /v1/chat accepts {sessionId, message, imageId?}, streams a response. Sequence: (1) resolve session context, (2) if imageId present, fetch its recognition result from BE-009/AI-008, (3) run hybrid retrieval (BE-003) against production only, (4) if confidence is below AI-003's threshold, short-circuit straight to the uncertain-response template without calling generation at all — cheaper and strictly safer than generating then discarding, (5) otherwise call calc tools as needed (AI-005/006/007) and generation (AI-004) with retrieved context, (6) stream the structured response. Interface: Server-Sent Events; final event carries the full structured JSON payload matching FE-004's expected shape (from shared-types). Edge cases: A calc-tool exception (e.g. out-of-range input) must degrade to a clear 'can't calculate this — here's why' message, never a generic 500 or a silently wrong number. Testing: Integration tests covering (a) high-confidence known query → correct structured response with citation, (b) no-match query → uncertain response with zero generation calls made (assert via mock call count), (c) calc-tool error path.

**Acceptance Criteria**

> End-to-end request for a known verified fault code returns the correct structured card with a valid citation, streamed.

### [x] BE-009 — Image handling service

| Field                   | Value                                   |
| ----------------------- | --------------------------------------- |
| **Task ID**             | BE-009                                  |
| **Category**            | Backend                                 |
| **Epic / Feature Area** | Fault Diagnosis                         |
| **Dependencies**        | BE-002                                  |
| **Work Stream**         | Diagnostic Core (Ayed)                  |
| **Phase Group**         | Core Diagnostic Flow                    |
| **Branch Name**         | `feature/be-009-image-handling-service` |
| **Status (sheet)**      | To Do                                   |
| **Assignee (sheet)**    | Ayed Rabaya                             |

**Full Technical Details**

> Objective: Handles the fault-code photo path end to end, cleanly separated from recognition logic itself (AI-008) so storage/security concerns don't tangle with ML concerns. Approach: POST /v1/images accepts multipart upload, validates MIME type and a size ceiling (e.g. 8MB) server-side (never trust client-side validation alone), stores to object storage (S3-compatible) under a tenant-scoped path ({tenantId}/{imageId}.jpg), returns an imageId the chat endpoint can reference. Interface: POST /v1/images → {imageId}; internal get_image(imageId, tenantId) used by AI-008, raising if the tenant doesn't match. Edge cases: Reject non-image MIME types even if the filename extension looks like an image — sniff actual content, don't trust the extension or client-declared type. Testing: A test asserting cross-tenant image access is rejected, plus oversized/wrong-type upload rejection.

**Acceptance Criteria**

> Upload, storage, and retrieval work correctly with access restricted to the uploading tenant; oversized/invalid files are rejected with a clear error.

### [x] BE-012 — Rate limiting & free-tier abuse protection

| Field                   | Value                                                     |
| ----------------------- | --------------------------------------------------------- |
| **Task ID**             | BE-012                                                    |
| **Category**            | Backend                                                   |
| **Epic / Feature Area** | Onboarding                                                |
| **Dependencies**        | BE-002                                                    |
| **Work Stream**         | Diagnostic Core (Ayed)                                    |
| **Phase Group**         | Core Diagnostic Flow                                      |
| **Branch Name**         | `feature/be-012-rate-limiting-free-tier-abuse-protection` |
| **Status (sheet)**      | To Do                                                     |
| **Assignee (sheet)**    | Ayed Rabaya                                               |

**Full Technical Details**

> Objective: Protects the free trial (the core self-serve differentiator vs. OhmX) from being trivially abused into unlimited free usage, without adding friction that undermines the reason it exists. Approach: Sliding-window rate limit per account (from BE-002's usage counter) and a secondary per-IP limit (e.g. via Redis) to catch multi-account abuse from a single source, tuned generously enough that no legitimate first-time user hits the IP-level limit under normal use. Interface: FastAPI middleware/dependency applied specifically to trial-path routes, not globally — authenticated paying usage isn't subject to the same IP-level ceiling. Edge cases: Shared-IP scenarios (an office/factory behind one NAT'd IP with multiple legitimate engineers) must not be blocked — set the threshold based on realistic concurrent-user counts for that scenario, not a single-user assumption. Testing: A burst-request test confirming throttling kicks in past the threshold and a normal single-session flow never triggers it.

**Acceptance Criteria**

> Automated burst requests from one source are throttled; a normal user's trial flow is unaffected.

### [x] BE-013 — Logging, tracing & latency metrics

| Field                   | Value                                            |
| ----------------------- | ------------------------------------------------ |
| **Task ID**             | BE-013                                           |
| **Category**            | Backend                                          |
| **Epic / Feature Area** | Foundation                                       |
| **Dependencies**        | BE-001                                           |
| **Work Stream**         | Diagnostic Core (Ayed)                           |
| **Phase Group**         | Foundation                                       |
| **Branch Name**         | `feature/be-013-logging-tracing-latency-metrics` |
| **Status (sheet)**      | To Do                                            |
| **Assignee (sheet)**    | Ayed Rabaya                                      |

**Full Technical Details**

> Objective: Makes the 'faster than OhmX' claim measurable and debuggable in production, not just true in a demo. Approach: Structured JSON logging with a correlation_id generated per request and threaded through every downstream call (retrieval, calc tools, generation) via context propagation. Latency instrumentation specifically captures time-to-first-token on the streamed chat endpoint as its own tracked metric, separate from total-response-time, since perceived speed is driven by the former. Interface: A with_correlation_id() context manager/middleware wrapping every request; a record_latency(stage, ms) helper called at each pipeline stage. Edge cases: Logging must never include full user message content or image data at info level (privacy) — log metadata (lengths, IDs, timings), with content-level logging gated behind an explicit debug flag if ever needed. Testing: Assert a single request's correlation ID appears across all expected log lines (retrieval, generation, response) in a test run.

**Acceptance Criteria**

> A single request's full lifecycle is traceable via its correlation ID across logs; time-to-first-token is visible as a tracked metric.

### [x] AI-001 — Chunking & metadata strategy

| Field                   | Value                                       |
| ----------------------- | ------------------------------------------- |
| **Task ID**             | AI-001                                      |
| **Category**            | AI                                          |
| **Epic / Feature Area** | Retrieval                                   |
| **Dependencies**        | _none_                                      |
| **Work Stream**         | Bootstrap (Shared)                          |
| **Phase Group**         | Foundation                                  |
| **Branch Name**         | `feature/ai-001-chunking-metadata-strategy` |
| **Status (sheet)**      | To Do                                       |
| **Assignee (sheet)**    | Ayed Rabaya                                 |

**Full Technical Details**

> Objective: This single decision determines whether every citation the product ever makes is genuinely precise or just approximately right — upstream of retrieval quality, guardrail reliability, and the verification workflow all at once. Approach: Chunk technical manuals by structural unit (section/subsection boundaries from the source PDF's own headings, not a fixed token-count sliding window, since a fixed window can split a parameter table or procedure mid-step) with a target size band (e.g. 200-500 tokens) and overlap only at true structural continuations. Every chunk carries the full metadata schema from BE-003's index mapping — no chunk is written without brand/model/doc_type/page/section populated. Interface: chunk_document(text, structure_map) -> list[Chunk], where Chunk is a typed object, not a bare string. Edge cases: Tables and numbered procedures must never split mid-table/mid-step — detect structural boundaries (via the source PDF's layout/heading info, not naive character counting) and treat them as atomic chunking units even if that means exceeding the target size band. Testing: Run against 3 real sample manuals with manually-verified expected chunk boundaries around at least one table and one numbered procedure each, confirming no split occurs mid-structure.

**Acceptance Criteria**

> Chunking a sample manual produces chunks that each resolve to one verifiable page/section reference, with no metadata field left empty.

### [x] AI-002 — Hybrid retrieval tuning

| Field                   | Value                                    |
| ----------------------- | ---------------------------------------- |
| **Task ID**             | AI-002                                   |
| **Category**            | AI                                       |
| **Epic / Feature Area** | Retrieval                                |
| **Dependencies**        | AI-001,AI-011,BE-003                     |
| **Work Stream**         | Diagnostic Core (Ayed)                   |
| **Phase Group**         | Foundation                               |
| **Branch Name**         | `feature/ai-002-hybrid-retrieval-tuning` |
| **Status (sheet)**      | To Do                                    |
| **Assignee (sheet)**    | Ayed Rabaya                              |

**Full Technical Details**

> Objective: Retrieval quality directly determines whether cite-or-refuse fires correctly — under-retrieving triggers false 'uncertain' responses (frustrating but safe), over-retrieving irrelevant chunks risks generation citing something that doesn't actually support the answer. Approach: Iterative tuning of the BM25/kNN blend weight and score thresholds against AI-011's eval set, tracking precision (retrieved chunk actually supports the expected answer) and recall (the right chunk was retrievable at all) separately — fault-code lookups (near-exact match, BM25-favoring) and natural-language symptom descriptions (semantic, kNN-favoring) likely need a query-type-conditional blend weight rather than one fixed global value. Interface: Tuning parameters live in a single config object (RetrievalConfig), not scattered constants, so re-tuning doesn't require a code change per parameter. Edge cases: Tuning against the eval set risks overfitting to it — hold out a portion purely for final validation, not used during the tuning loop itself. Testing: Precision/recall reported per query-type category (fault-code lookup vs. symptom description vs. parameter lookup) against the held-out split, not just an aggregate score that could hide a category doing badly.

**Acceptance Criteria**

> Retrieval precision on the eval set's known-answer queries meets an agreed threshold before this is considered done, not just 'looks reasonable'.

### [x] AI-003 — Cite-or-refuse guardrail logic

| Field                   | Value                                           |
| ----------------------- | ----------------------------------------------- |
| **Task ID**             | AI-003                                          |
| **Category**            | AI                                              |
| **Epic / Feature Area** | Guardrails                                      |
| **Dependencies**        | AI-001                                          |
| **Work Stream**         | Diagnostic Core (Ayed)                          |
| **Phase Group**         | Core Diagnostic Flow                            |
| **Branch Name**         | `feature/ai-003-cite-or-refuse-guardrail-logic` |
| **Status (sheet)**      | To Do                                           |
| **Assignee (sheet)**    | Ayed Rabaya                                     |

**Full Technical Details**

> Objective: The single mechanism the entire accuracy claim rests on — everything else (verification pipeline, staging/production separation, calc tools) exists to give this good material, but this is what actually enforces 'never guess' at answer time. Approach: A confidence score derived from the retrieval step's top result score (not a separate LLM self-reported confidence, which is unreliable) — below a tuned threshold, the pipeline short-circuits to the fixed uncertain-response template (referencing the closest-matching document above a lower threshold, or a generic 'no verified source found' if not) without invoking generation at all. Applies uniformly across the diagnostic flow, calc-tool results (an out-of-validated-range input triggers the same refuse path), and PLC validation (AI-009's 'never mark ready without validation' is this same principle applied to code). Interface: evaluate_confidence(retrieval_result) -> ConfidenceDecision — a single function every response-generating code path calls before proceeding, rather than each path implementing its own ad hoc check. Edge cases: The threshold must be validated against real ambiguous cases, not just clear-cut 'nothing found' vs. 'exact match' cases — the hard part is the middle ground, which is what AI-011's eval set needs to stress-test. Testing: The eval set's deliberately-ambiguous and deliberately-no-match query categories both verified to trigger the uncertain path consistently across repeated runs — guardrail behavior should be deterministic given the same retrieval result.

**Acceptance Criteria**

> A query with no matching verified content in the index reliably returns the uncertain-state response, never a fabricated answer, across repeated tests.

### [x] AI-004 — Structured response formatting

| Field                   | Value                                           |
| ----------------------- | ----------------------------------------------- |
| **Task ID**             | AI-004                                          |
| **Category**            | AI                                              |
| **Epic / Feature Area** | Fault Diagnosis                                 |
| **Dependencies**        | AI-003                                          |
| **Work Stream**         | Diagnostic Core (Ayed)                          |
| **Phase Group**         | Core Diagnostic Flow                            |
| **Branch Name**         | `feature/ai-004-structured-response-formatting` |
| **Status (sheet)**      | To Do                                           |
| **Assignee (sheet)**    | Ayed Rabaya                                     |

**Full Technical Details**

> Objective: Turns free-form LLM output into a contract the frontend can render reliably — this is what makes FE-004's component trust its input shape instead of defensively parsing text. Approach: Enforce structured output via the model API's native structured-output/tool-calling mechanism (JSON schema-constrained generation) rather than prompting for JSON and hoping — eliminates the whole class of 'the model added a stray sentence before the JSON' failures. Interface: Schema matches DiagnosticResponse in shared-types exactly — defined once, with both the generation constraint and the frontend type deriving from it so they can't drift apart. Edge cases: A schema validation failure on generation output (rare given constrained generation, not impossible) falls back to the uncertain-response template per AI-003 — treated as a confidence failure, never shown to the user broken. Testing: 100 varied test queries run through the full pipeline, asserting 100% schema-valid output — any failure is a bug to fix, not an acceptable error rate, given the fallback exists specifically to make zero-tolerance achievable.

**Acceptance Criteria**

> 100 varied test queries all return schema-valid structured output with no parsing failures on the frontend.

### [x] AI-008 — Multimodal fault-code image recognition

| Field                   | Value                                                    |
| ----------------------- | -------------------------------------------------------- |
| **Task ID**             | AI-008                                                   |
| **Category**            | AI                                                       |
| **Epic / Feature Area** | Fault Diagnosis                                          |
| **Dependencies**        | AI-001                                                   |
| **Work Stream**         | Diagnostic Core (Ayed)                                   |
| **Phase Group**         | Core Diagnostic Flow                                     |
| **Branch Name**         | `feature/ai-008-multimodal-fault-code-image-recognition` |
| **Status (sheet)**      | To Do                                                    |
| **Assignee (sheet)**    | Ayed Rabaya                                              |

**Full Technical Details**

> Objective: Removes the specific, real error source of a stressed field engineer mistyping a fault code from a small, glare-affected screen. Approach: A vision-capable model call constrained to extract exactly three fields (fault code, brand if visibly identifiable, model if visibly identifiable) with a confidence score per field, rather than a free-form 'describe this image' prompt — narrow, structured extraction is both more reliable and easier to gate on confidence. Interface: recognize_fault_display(image_id: str) -> FaultRecognitionResult with per-field confidence, called by BE-009's flow. Edge cases: A photo of the wrong thing entirely (not an equipment display) must be detected and return a clear 'doesn't look like a fault-code display' result rather than hallucinating a plausible-looking but fabricated code — a real, likely failure mode given real-world photo quality/framing variance. Testing: A test set of >=20 real photos spanning lighting/angle/glare variation and including at least 2 deliberately off-topic images, asserting correct extraction on the good photos and correct rejection on the off-topic ones.

**Acceptance Criteria**

> Correctly reads fault code + brand/model from at least 20 real test photos across lighting/angle variation; low-confidence cases correctly trigger the confirm-back flow instead of a guess.

### [x] AI-010 — Multi-language response generation

| Field                   | Value                                               |
| ----------------------- | --------------------------------------------------- |
| **Task ID**             | AI-010                                              |
| **Category**            | AI                                                  |
| **Epic / Feature Area** | Localization                                        |
| **Dependencies**        | AI-004                                              |
| **Work Stream**         | Diagnostic Core (Ayed)                              |
| **Phase Group**         | Multi-language & Localization                       |
| **Branch Name**         | `feature/ai-010-multi-language-response-generation` |
| **Status (sheet)**      | To Do                                               |
| **Assignee (sheet)**    | Ayed Rabaya                                         |

**Full Technical Details**

> Objective: Makes the product genuinely trilingual rather than 'translated UI chrome around English-only AI output' — a common shortcut that would undercut the Arabic-first positioning established from the start of this project. Approach: The response-generation prompt (AI-004) is parameterized by the session's active locale, instructing generation directly in that language rather than generating in English and machine-translating after — direct generation avoids a translation layer's errors compounding on top of retrieval/generation errors. Technical tokens (fault codes, parameter names, units, part numbers) are explicitly instructed to remain untranslated/untransliterated in all three locales, matching FE-002's <bdi> isolation handling on the display side. Interface: locale is a required field on the generation call, no implicit default. Edge cases: Arabic and Hebrew both being RTL doesn't mean identical technical/engineering terminology conventions — Hebrew technical vocabulary needs its own review, not an assumption that 'RTL handling' covers correctness for both languages equally. Testing: The same underlying diagnosis run through all three locale settings, verified for correct language output and, critically, identical (unchanged) technical tokens across all three.

**Acceptance Criteria**

> The same underlying diagnosis produces correctly-translated prose in all three languages with identical technical tokens preserved unchanged in each.

### [x] AI-011 — Eval set construction

| Field                   | Value                                  |
| ----------------------- | -------------------------------------- |
| **Task ID**             | AI-011                                 |
| **Category**            | AI                                     |
| **Epic / Feature Area** | Verification                           |
| **Dependencies**        | AI-001                                 |
| **Work Stream**         | Diagnostic Core (Ayed)                 |
| **Phase Group**         | Ingestion & Verification               |
| **Branch Name**         | `feature/ai-011-eval-set-construction` |
| **Status (sheet)**      | To Do                                  |
| **Assignee (sheet)**    | Ayed Rabaya                            |

**Full Technical Details**

> Objective: Without this, 'verified' has no reference point — retrieval and prompt changes could silently regress previously-working answers with nobody noticing until a user hits it. Approach: Built directly from the verification pipeline's own output — every item clearing verification with a 'correct' label is a candidate eval-set entry (query + expected answer + expected citation), reviewed by the QA/verification-coordination pod specifically for eval-set inclusion (not every verified item needs to be in the smaller regression set). Deliberately includes edge-case and ambiguous queries, not only clean/easy ones, since those are where regressions most likely hide. Interface: Eval entries stored as {query, expectedAnswerSummary, expectedCitation, category}, runnable as an automated batch against the current pipeline with pass/fail scored per entry. Edge cases: As brand coverage expands, the eval set must expand alongside it — a brand with zero eval coverage is a brand where a regression could ship silently. Testing: The eval runner itself is tested against a fixture with a known-should-pass and known-should-fail entry, confirming the scoring logic itself is correct before it's trusted to gate real changes.

**Acceptance Criteria**

> Eval set covers all in-scope brands and includes both common and edge-case queries; a deliberately-introduced regression is caught by a full eval run.
