---
name: browser-pdf-verify
description: Verify a PanelPilot change in a real production-build browser and check exported PDFs. Use before claiming a UI or export change works end to end.
---

<!--
Adapted from everything-claude-code (ECC) skills/browser-qa/SKILL.md @ commit d2ef2680594f9a17526a6e1a2e18b3d242c5d17b.
MIT License, Copyright (c) 2026 Affaan Mustafa. See .claude/THIRD_PARTY_NOTICES.md.
Condensed and rewritten for PanelPilot; not the upstream text verbatim.
-->

# Browser and PDF verification

Run read-only by default, against a local stack seeded with synthetic data only. Never use real customer data or credentials; redact tokens from any screenshot.

## Setup

- Build and serve the production web build (`next build` then `next start`), not the dev server.
- Use Playwright with the preinstalled Chromium at `/opt/pw-browsers/chromium` (set `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`); do not download browsers.
- The API must use a disposable `panelpilot_test_*` or lane database, never `panelpilot`.

## Phase 1: Smoke

Load each affected page; record console errors and any 4xx/5xx responses; screenshot at 375, 768 and 1440 px.

## Phase 2: Interaction

Drive the affected flow as a user: valid input reaches the success state, invalid input shows a specific error, save / approve / export behave as documented. Confirm the execution export is still refused.

## Phase 3: Accessibility

Keyboard-only pass through the flow (visible focus, no traps); check labels and landmarks. An automated axe run covers only part of WCAG, so it is necessary, not sufficient.

## Phase 4: PDF

For each exported PDF: it opens; page count and page size are as expected; the title block shows the right project, revision and status; text is extractable (e.g. `pdftotext`); values match the on-screen design exactly, with no invented or placeholder electrical values; draft/blocked watermarks appear where the code says they must.

## Report

```
## Verification - <change> - <timestamp>
Build: production | Browser: Chromium (/opt/pw-browsers) | DB: panelpilot_test_<lane>
Smoke: ... | Interaction: ... | Accessibility: ... | PDF: ...
Not verified: <anything you could not check, and why>
Verdict: PASS / PASS WITH ISSUES / FAIL / INCONCLUSIVE
```

Never report PASS for a phase you did not run.
