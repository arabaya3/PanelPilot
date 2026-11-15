# PanelPilot - rules for Claude

Monorepo: `apps/api` (FastAPI, SQLAlchemy, Alembic, Postgres), `apps/web` (Next.js, vitest), `packages/shared-types`.

- **Synthetic data only.** Never commit or paste real customer, project or personal data; fixtures and screenshots use synthetic values.
- **Execution export stays blocked.** Do not unlock, bypass or weaken the execution-export refusal or the approval / revision-stale export gates.
- **No invented electrical values.** Ratings, cable sizes, derating factors and similar values come from the code, the catalogue or a cited standard. If one is missing, say so; do not make one up.
- **No weakened tests.** Never delete, skip, loosen or rewrite a test to make a change pass. A behaviour change needs a test that fails without it.
- **Test databases.** API tests run only against a disposable `panelpilot_test_*` database guarded by `apps/api/app/tests/db_owner.py`. Never run tests or destructive SQL against the `panelpilot` database; never `claim --force` another worktree's database.
- **Browser checks** use the production build and Chromium at `/opt/pw-browsers/chromium`; report anything not verified.
- Before claiming done: `npm run lint`, `npm run typecheck`, `npm test`, and the relevant pytest run.

Project agents: `architect`, `a11y-reviewer`, `tdd-guide`, `security-reviewer` (`.claude/agents/`). Skill: `browser-pdf-verify` (`.claude/skills/`).
