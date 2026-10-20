# Graphite Workbench redesign — implementation plan and contract

Branch: `feat/graphite-workbench-redesign` (worktree `../PanelPilot-redesign`), based on
`77cb97d`, which already carries the design-page fixes open in #192. Unrelated
uncommitted API work in the main checkout is untouched.

## Baseline (before any redesign change)

| Check                                  | Command (in `apps/web`) | Result                                                                         |
| -------------------------------------- | ----------------------- | ------------------------------------------------------------------------------ |
| Types                                  | `npx tsc --noEmit`      | pass                                                                           |
| Lint + token/logical/responsive guards | `npm run lint`          | pass                                                                           |
| Unit tests                             | `npx vitest run`        | 720/723 pass; 3 **pre-existing** 5 s timeouts: `design-boards` ×2, `market` ×1 |
| Build                                  | `npx next build`        | pass, 11 routes                                                                |

Pre-existing defect found: `text-danger` is used in 13 places but no `danger`
colour exists in `tailwind.config.ts`, so those error messages render unstyled.
Fix: use `text-severity-critical`.

## Architectural decisions

1. **Flat routes are kept.** Every existing URL (`/`, `/design`, `/calc`, `/plc`,
   `/search`, `/review`, `/team`, `/pricing`, `/join`, `/tokens`) keeps working.
   Auth tokens live in page memory per screen (by design: no bearer token in
   storage), so a nested `/projects/[id]/…` tree would force a re-auth on every
   sub-route. The project workspace instead uses **in-page contextual tabs**
   synchronised to `?view=` on `/design` (shareable, back-button friendly).
2. **Theme.** Graphite is the dark theme and becomes the **default**. The light
   theme is kept (a user preference exists and is persisted) and retuned to
   the same identity: neutral greys, a deep amber accent that passes AA on
   white. Both are asserted by `src/__tests__/tokens.test.ts`.
3. **No new dependencies.** Icons extend the in-house set in
   `src/components/icons` (Lucide-style outlines). Dialogs use native `<dialog>`.
4. **Real data only.** No screen may show the screenshot's sample values. A
   capability the backend lacks (an interactive drawing editor, a DXF panel
   layout, per-user role in the shell) is shown honestly or not at all, and is
   listed under _Gaps_ below.

## Tokens (owned by Agent A, `src/styles/tokens.css`)

Dark (default) starting points, tuned to pass the existing contrast tests:

| Token                     | Value                              |
| ------------------------- | ---------------------------------- |
| `--color-bg`              | `#10171C`                          |
| `--color-surface`         | `#172127`                          |
| `--color-surface-raised`  | `#1D2931`                          |
| `--color-border-subtle`   | `#304048` (decorative)             |
| `--color-border`          | control boundary, ≥ 3:1 on surface |
| `--color-text`            | `#F1F3F4`                          |
| `--color-text-muted`      | `#ACB8C0`                          |
| `--color-accent`          | `#F5BD45` (gold)                   |
| `--color-accent-contrast` | `#171C20`                          |
| `--color-accent-subtle`   | dark gold tint for active nav      |

New tokens are allowed (e.g. `--color-success`, `--color-chrome` for the rail,
`--color-sheet` / `--color-sheet-ink` for the paper-white drawing frame) but
each must be added to BOTH themes, to `tailwind.config.ts`, and to the contrast
assertions. No hex outside `tokens.css` (`npm run check:tokens`).

Typography: Noto Sans / Noto Sans Arabic / Noto Sans Hebrew stay (already
loaded, metric-matched). Page titles may use a serif display face only if added
through `next/font` and kept to headings; mono stays for identifiers.

## Shared component contract

`src/components/ui/index.tsx` (written by the lead; APIs frozen, styling is A's):
`PageHeader`, `Panel`, `Tabs` + `tabId`/`tabPanelId`, `StatusBadge` (`Tone`),
`EmptyState`, `LoadingState`, `Notice`, `SaveIndicator` (`SaveState`),
`FormField` + `fieldDescribedBy`, `TableFrame`, `Dialog` (dialog / drawer),
`SourceReference`.

CSS vocabulary those components emit, defined by Agent A in `globals.css`
`@layer components`: `page-header page-title page-description page-actions
panel panel-header panel-title panel-body tabs tab tab-badge status status-dot
status-{neutral,accent,info,warning,critical,success} empty-state
empty-state-icon loading-state spinner notice notice-icon
notice-{info,warning,critical,success} save-indicator field field-label
field-control field-unit field-hint field-error table-frame data-table dialog
dialog-{dialog,drawer,drawer-start} dialog-surface dialog-header dialog-body
dialog-footer source-ref source-ref-excerpt toolbar sheet-frame`.
Existing classes (`btn btn-primary btn-secondary btn-ghost btn-danger btn-icon
btn-sm input card chip eyebrow link`) keep their names.

`AppShell` API stays `{ children, actions? }` and gains optional
`{ context?: ReactNode; wide?: boolean }`: `context` renders in the top bar
(project selector / breadcrumb), `wide` drops the max-width for workspaces.

Icons available: existing set plus `FolderIcon FileTextIcon DiagnoseIcon CpuIcon
ShieldCheckIcon SettingsIcon UsersIcon ChevronDownIcon ChevronEndIcon MenuIcon
CloseIcon ZoomInIcon ZoomOutIcon FitIcon DownloadIcon UploadIcon
AlertTriangleIcon InfoIcon CheckIcon HistoryIcon ExternalLinkIcon LayersIcon
ListIcon PackageIcon LogOutIcon`.

## Navigation model

Left rail (≥ md), drawer behind a menu button (< md):
Projects/Design `/design` · Diagnose `/` · Calculate `/calc` · PLC `/plc` ·
Documents `/search` · Review `/review` · (bottom) Team `/team`, Plans `/pricing`.
Language and theme controls sit in the top bar.

## File ownership (no agent edits another's files)

| Agent                                      | Owns                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A — design system & shell                  | `src/styles/**`, `tailwind.config.ts`, `src/components/ui/**` (styling only), `icons/**`, `app-shell.tsx`, `lang-switcher.tsx`, `theme-*.tsx`, `locale-provider.tsx`, `src/app/layout.tsx`, `src/app/page.tsx` (landing/trial frame only), `sign-in-form.tsx`, `join-screen.tsx`, `pricing-screen.tsx`, `team-screen.tsx`, `src/app/tokens/**`, new `not-found.tsx` / `error.tsx`, scripts/check-*.mjs, tests for these |
| B — projects & design                      | `design-screen.tsx`, `src/components/design/**`, `quotation-panel.tsx`, `plc-panel.tsx`, `src/components/schematic/**`, `src/app/design/**`, tests for these                                                                                                                                                                                                                                                            |
| C — diagnose, calc, PLC, documents, review | `chat/**`, `diagnostic-card/**`, `diagnosis-sample.tsx`, `calc-screen.tsx`, `cable-sizing-panel.tsx`, `vfd-selection-panel.tsx`, `panel-bom-panel.tsx`, `plc-review.tsx`, `plc-generate.tsx`, `plc-view/**`, `search-screen.tsx`, `review-screen.tsx`, `verification/**`, `file-picker.tsx`, `state-icon.tsx`, `technical-token.tsx`, `src/app/{calc,plc,search,review}/**`, tests for these                            |
| D — integration review                     | read-only review, then fixes only where the lead assigns                                                                                                                                                                                                                                                                                                                                                                |

Messages (`src/messages/{en,ar,he}.json`): each agent edits only its own
namespaces with small targeted edits (A: `app nav language theme landing
signIn join pricing team tokens` + new `shell` and `ui`; B: `design` + new
`workspace`; C: everything else). Every new key in all three languages.

`Field` exported from `cable-sizing-panel.tsx` is imported by `design-screen`;
C keeps that export's signature.

## Rules for every agent

- No commits (the lead commits). No dependency changes. No backend changes.
- Never change calculation formulas, API calls, request/response handling,
  guardrails, refusal/uncertainty behaviour, or `data-testid`s that tests use.
- Logical CSS only (`ms-`/`pe-`/`start-`), `dir="ltr"` + `<bdi>` around
  identifiers, units, code. Never mirror schematics or code.
- Run `npx tsc --noEmit`, `npm run lint`, and the vitest files you touched.
  Report changed files, validation, and blockers.

## Route coverage

See the table filled in at integration (section below).

## Gaps

Filled in at integration.
