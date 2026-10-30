# Handover to a new Claude Code cloud session (2026-10-31)

This repository is **public**. Keep customer material, credentials and details of the exposure below out of it.

## Standing limits

Until the owner says otherwise:

- Do not merge, deploy, force-push, rewrite history, delete branches, change repository visibility or contact GitHub Support.
- Do not start M2. Keep execution export and restricted formats blocked.
- Never run tests against the dev database `panelpilot`, or through `docker compose exec api`.
- Never commit `.env`, customer PDFs, personal data, or `docs/design-readiness/assessment.md` and `docs/design-readiness/evidence/` (held back for privacy review).
- No attribution lines in commits or PRs. Do not call an AI language review a native-speaker review.

## Product goal

PanelPilot is becoming a dynamic, scalable, secure and maintainable electrical design engine. Customer PDFs are test cases, not fixed families: do not hard-code a design per reference document. Do not invent engineering requirements; open engineering decisions belong to the owner. Design: `docs/architecture/dynamic-design-engine.md`.

## PRs

| PR                                                              | Head                                                                               | Base                                               | CI on that head                                                                    |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------- | -------------------------------------------------- | ---------------------------------------------------------------------------------- |
| [#207](https://github.com/arabaya3/PanelPilot/pull/207) Phase 1 | `123d71c42bcffada2ef299c97afe7961de97af17`, `feat/phase1-trustworthy-export-clean` | `phase1-base` @ `daeb143`                          | 19/19 pass, [run](https://github.com/arabaya3/PanelPilot/actions/runs/40717048050) |
| [#208](https://github.com/arabaya3/PanelPilot/pull/208) M1      | `5d2c2435c30c4d8ecc3a688b656132bcc8ed8350`, `feat/m1-versioned-design-model-clean` | `feat/phase1-trustworthy-export-clean` @ `123d71c` | 19/19 pass, [run](https://github.com/arabaya3/PanelPilot/actions/runs/40717039857) |

- `main` is at `48d2a75`. `phase1-base` is 19 commits ahead of `main` and 0 behind.
- Order: get `phase1-base` to `main`, merge #207, retarget #208 and merge it. All of it awaits the owner's acceptance.
- A second CI run on `5d2c243` shows as cancelled (`40717038697`). It is a duplicate; the linked run succeeded.
- M1 is updated by merge commits, not rebases, because a rebase would need a force-push. The M1 commits are unchanged inside it.
- Other open PRs: #199-#202, #204, #205 (dependency and verification fixes, whose changes were brought into the Phase 1 candidate), #87 (not part of this work). #203 and #206 are the superseded originals; see "Public-history exposure".

## Phase 1 (#207): done, awaiting acceptance

Export integrity, sessions, permissions, recorded owner and approval policy. The record is `docs/design-readiness/phase-1-export-integrity.md`, sections "Permissions", "Settings and approval", "Deployment steps".

- **Permissions:** `apps/api/app/domain/permissions.py`.
  - Approve: owner, admin, reviewer.
  - Delete project, save company settings: owner, admin.
  - Transfer ownership: owner only. The owner cannot be removed until ownership is transferred.
  - Another tenant's project is a 404.
- **Owner:** `tenants.owner_user_id`, migration `a7d3e9f1c2b4`. Ownership never passes automatically.
- **Locking:** the acting transaction locks tenant and caller `FOR SHARE`; role and team changes lock them `FOR UPDATE`, so a revocation cannot be bypassed.
- **Approval:** migration `b4e8d2f6a9c1`. It records snapshot, profile hash, rules and catalogue versions, findings fingerprint and frozen approver name. A live company settings change does not affect a saved revision. A change in applicable code policy stops the approval counting, and export is refused with `revision_stale`.

## M1 (#208): done, awaiting acceptance

Read-side versioning in `apps/api/app/design/model/`; see section 13a of the architecture document. A snapshot with no marker is `pp-design/1`. An unknown version is a 409 `snapshot_schema_unknown`. One snapshot read per request, hash checked first. No M2 work exists.

## Outstanding

**Owner decisions**

- Accept #207 and #208; `phase1-base` to `main`.
- Privacy review, then the cleanup runbook and the Support request.
- Resolve any tenants production `owner-report` lists.
- Whether `reviewer` stays one role for operator review and design approval.
- The unresolved engineering decisions in the architecture document.

**Known small issues, not fixed**

- `revision_stale` details show the revision's rules version twice, not the version the approval recorded.
- The unknown-schema message appears under Review & export, not in the overview on open.
- Focus does not move to the field with a settings error.

**Limitations and gaps**

- Renderer wording is not versioned, so a code wording change could alter an approved export undetected.
- A presentational change to the code's default profile counts as stale (strict on purpose).
- The revision list and title-block table check earlier revisions without reading their snapshots, so a findings-classification change appears only when that revision is opened.
- Not verified: byte comparison of PDF and DXF exports; Hebrew in the browser; `tenant_inactive` through the `assign-owner` command; the migrations against a production-like snapshot.
- Arabic and Hebrew strings are AI-written and have had no native-speaker review.

## Last verification

- **Local, M1 head:** backend 4796 passed, 60 skipped (OpenSearch, one architecture test), 0 failed; web 1067 passed. Phase 1 head: backend 4123 passed.
- **Browser, `5d2c243`:** production web server behind Caddy `tls internal` through the real proxy, next 15.5.28, react 19.2.8, fastapi 0.142.0. Passed: sign-in flows and untrusted-origin rejection, the three web fixes, ownership transfer, permissions, approval policy, M1 legacy read, two-tenant isolation. The screenshots are local only.

## Database incident (other project's container)

An agent's command sent database statements to another project's Postgres container on this computer.

- **When:** 2026-10-28 15:10:41 UTC, while recreating the disposable test database.
- **Cause:** the target was chosen with `docker ps --filter name=postgres -q | head -1`, which returned `ms-social-postgres-1`.
- **Statements sent:** `psql -U panelpilot -d postgres` with `DROP DATABASE IF EXISTS panelpilot_phase1_m1perm WITH (FORCE)` and `CREATE DATABASE panelpilot_phase1_m1perm`.
- **Result:** that container's log has one matching line, `2026-10-28 15:10:44.682 UTC [3412] FATAL: role "panelpilot" does not exist`. The connection was refused at login, so neither statement can have run.
- **What this rests on:** the log and the command text. Nobody compared that database's contents before and after. Only one command in the agent transcripts did this.
- **Later contact:** on 2026-10-31 the handover audit read that container's log and ran one needless `echo` inside it. Nothing connected to its database.
- **Evidence, local only:** the container's log (lost if the container is recreated) and the session's agent transcripts.
- **Rule:** always name the container, `panelpilot-postgres-1`. Never select one by filter.

## Public-history exposure

Confidential readiness material was pushed by mistake to an old branch. A later commit removed it from the tree, but it is still in that branch's history. **Treat it as exposed.** This document deliberately gives no identifier for it, because the repository is public.

- **Still affected, kept on purpose:** `feat/phase1-trustworthy-export` and PR #203. PR #206 targets that branch.
- **Clean, verified by ancestry check on 2026-10-31:** `feat/phase1-trustworthy-export-clean`, `feat/m1-versioned-design-model-clean`, `phase1-base`. No tag contains it. The repository had 0 forks when checked on 2026-10-28.
- **Prepared, not sent:** the Support request and cleanup runbook, which exist **only on the owner's computer**. Ask the owner for them; do not commit them here.
- **What GitHub can do:** remove PR refs, clear cached views, garbage-collect, once all other branches are clean and no fork holds the data.
- **What GitHub cannot do:** clean clones, local copies or outside caches, or confirm that nobody saw it.

## Test setup

- **CI:** `.github/workflows`, 19 checks. CI provides OpenSearch; locally those tests skip.
- **Backend:** one-off container from the `panelpilot-api` image on network `panelpilot_panelpilot`, with `apps/api` mounted and `DATABASE_URL` and `TEST_DATABASE_URL` pointing at a disposable `panelpilot_phase1_*` database. Use throwaway values for secrets. Then run `alembic upgrade head`, `black --check app`, `ruff check --no-cache app`, `mypy --cache-dir /tmp/mc app`, `alembic check`, `pytest`.
- **Web, in `apps/web`:** `tsc`, eslint (4 existing `<img>` warnings), `vitest`, `prettier --check`.
- **Shared types:** `npm run generate` needs Python on the host. CI's drift check is authoritative.

## Migration and deployment cautions

- The single head is `b4e8d2f6a9c1`, following `a7d3e9f1c2b4`.
- Back up first, and follow "Deployment steps" in the Phase 1 record.
- **`python -m app.worker owner-report` is a mandatory gate after migrating.** Exit 1 blocks the rollout: a tenant has no owner and no admin. Remedy: `assign-owner`.
- Existing tenants whose oldest account is inactive or shares its creation time get no owner. Members who are neither owner nor admin lose delete and settings, and lose approval unless granted `reviewer`.
- Downgrading `a7d3e9f1c2b4` loses transfers made since. Users sign in once after the session migration.

## Not on GitHub

The owner's computer alone holds these; a cloud session cannot see them:

- **Never upload:** `.env` and its backup; customer reference documents; `docs/design-readiness/assessment.md` and `evidence/`; test account credentials; the sensitivity scan and its extracted files.
- **Local evidence:** browser screenshots and exports from both verification runs; test logs; agent transcripts.
- **Support request draft and cleanup runbook.**
- **Docker:** the dev stack and volumes, the `panelpilot` dev database, leftover `panelpilot_phase1_*` and `panelpilot_ci_sim` test databases, and stopped helper containers.
- **Backups:** the `backups` folder beside the project.
- **Worktrees:** several local worktrees, all with no uncommitted changes, including a stale prunable entry. Keep them until the owner decides.
- **Local-only branches, no unique content needed:**
  - `fix/web-defects-p1`: same changes as commits in #207.
  - `m1-clean-rebased-local`: same tree as `f9cbde3`, an ancestor of #208.
  - `tmp/deps-plus-199`: a temporary integration merge.
- **Untracked in the main checkout, left in place:** the held-back material above, `docs/design-readiness/phase-1/frontend/zz-failure.png` (a stray test screenshot), and `src/` (admin page files that do not look like PanelPilot code; not yet explained, so do not delete or commit them).
