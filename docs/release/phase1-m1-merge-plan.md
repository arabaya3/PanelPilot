# Merge plan: Phase 1 (#207) and M1 (#208) to `main`

Status: plan only. This branch is based on `main`; the documents it cites are on #207 and #208. Nothing here has been merged or deployed. Every step needs the owner's go-ahead. Updated on 2026-10-31 for the final heads below; re-check them before step 1 (see "Stop conditions").

## Starting point (verified)

| Ref                                         | SHA                                        | Notes                                                                                                                                                                          |
| ------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `main`                                      | `48d2a75f03bb47f43a7ab71dc87e3e0f3e3960a1` | Alembic head `a7d1e3f5b2c8`                                                                                                                                                    |
| `phase1-base`                               | `daeb143bfc3a93106dab178e5feaffa9287a02f1` | 19 commits ahead of `main`, 0 behind: a fast-forward of `main`. It merges #199 (`73ce76c`) and #202 (`6a96b74`), which already contains #200 (`dfc77dc`) and #201 (`7462755`). |
| #207 `feat/phase1-trustworthy-export-clean` | `041ca1fdbccaaf2b14624137f854c169611e2082` | base `phase1-base`; contains #204 (`b4b9153`) and #205 (`f834e6e`) unchanged                                                                                                   |
| #208 `feat/m1-versioned-design-model-clean` | `0c853572bda13f1a4fb8b0bccab6dd2ae2e69cd6` | base #207's branch                                                                                                                                                             |

**No exposed history on this path.** The commits found only on the affected branches (`feat/phase1-trustworthy-export`, #203, and `feat/m1-versioned-design-model`, #206) are in none of: `main`, `phase1-base`, #207, #208, or the head branches of #199, #200, #201, #202, #204 and #205. This was checked by ancestry for every such commit. No commit on `phase1-base`, #207 or #208 touches `docs/design-readiness/assessment.md` or `docs/design-readiness/evidence/`.

**Simulated end state.** `main` fast-forwarded to `phase1-base`, then #207 and #208 merged with merge commits. The result is tree-identical to #208's head, and after #207 it is tree-identical to #207's head. On that simulated tree:

- single Alembic head `c5d1e8a3f7b2`;
- `alembic upgrade` from `a7d1e3f5b2c8`, `downgrade` back and `upgrade` again, then `alembic check`, all clean;
- black, ruff and mypy clean;
- web typecheck clean, 1,074 tests passing, prettier clean;
- backend suite on Python 3.12 against a disposable database: 4,833 passed, 60 skipped, 0 failed at `d5d8407`; on the final heads, #207 4,168 passed and #208 4,841 passed, 60 skipped each, 0 failed; web 1,072 (#207) and 1,074 (#208).

## What CI does when a PR is retargeted (checked in `.github/workflows/ci.yml`)

- The workflow triggers on `pull_request` with GitHub's default activity types: opened, synchronize and reopened. It also triggers on `push` to `main`.
- **Changing a PR's base is an `edited` event, so it starts no run.** The checks shown after a retarget are the old ones, computed against the old base.
- A `pull_request` run tests GitHub's merge ref, the head merged into the base as it was then. When the base is an ancestor of the head, that merge's tree is the head's tree. That holds here (`main` ⊂ `phase1-base` ⊂ #207 ⊂ #208, checked by ancestry and by a simulated merge that is tree-identical).
- **That identity is a reason to expect the same result, not validation.** The plan therefore requires the full run on `main` after each merge (`push` event) before the next step, and stops if any of them fails.
- No empty commits, close-and-reopen, or force-pushes to trigger runs. If a fresh pre-merge run on the new base is wanted, the clean ways are:
  - push a real change;
  - use "Update branch", only when the base has commits the head lacks;
  - add `workflow_dispatch` (or the `edited` type) to the workflow in its own reviewed PR first.

## Order

1. **Re-verify the starting point.** `main`, `phase1-base` and both PR heads must equal the SHAs above, and CI must be green on both PR heads (the runs on `041ca1f` and `0c85357`).
2. **Bring #199 and #202 to `main` by fast-forwarding `main` to `phase1-base`** (`git push origin daeb143…:main`, a fast-forward only; or the GitHub UI if branch protection requires a PR).
   - Not by merging #199, #200, #201 or #202 one by one: that would create different merge commits, and #207 would then show `phase1-base`'s merge as an extra commit.
   - With their head commits in `main`, GitHub marks #199 and #202 as merged. #200 and #201 are contained in #202 and are marked merged too; if not, close them as superseded by #202.
   - If branch protection forbids a direct push: open a PR from `phase1-base` to `main` and merge it with **"Create a merge commit"** (no squash, no rebase). #207 still diffs cleanly, because `phase1-base` becomes an ancestor of `main`.
3. **Check CI on `main`** after step 2. A push to `main` runs the full workflow. Wait for all 19 checks.
4. **Retarget #207 from `phase1-base` to `main`.** Its diff must be unchanged: the same 510 files as against `phase1-base`.
   - Retargeting does **not** start CI: the workflow runs on `pull_request` opened/synchronize/reopened and on pushes to `main`.
   - The last PR run is valid evidence only if `main` equals `phase1-base` (it was tested against that base) and #207's head is unchanged. If either moved, get a fresh run: push a real fix, or re-run after updating the branch with a merge commit (never a force-push or an empty commit).
5. **Merge #207 with "Create a merge commit".** Not squash: #208 contains #207's commits, and a squash would make #208 re-show all of Phase 1. #204 and #205 are then marked merged, since their exact commits are in #207.
6. **Check CI on `main`** after step 5 (19 checks).
7. **Retarget #208 to `main`.** The same CI note as step 4 applies: its last run was against #207's head, which is now in `main` with an identical tree. Then merge it with "Create a merge commit".
8. **Check CI on `main`** after step 7. This is the final target: all 19 checks must pass on the merge commit.
9. **Close #203 and #206 unmerged**, as superseded by #207 and #208. They hold the affected history. Do not merge or retarget them, and do not delete their branches until the owner's cleanup runbook says so.
10. **#87** is unrelated and stays as it is.

**Do not:** squash or rebase any of these PRs, delete `phase1-base` or any other branch, or merge #203 or #206.

## Required checks on the final targets

On `main` after each of steps 2, 5 and 7, all 19 jobs: `ci`; api lint, format, typecheck, test, coverage and integration; web lint, format, typecheck and test; migration drift; shared types drift; lock drift; deps audit for Python and Node; docker build for api and web; compose smoke test. Integration and migration drift use the disposable `panelpilot_ci_test` database. CI runs on Python 3.12, the runtime version. Do not deploy unless the run on step 7's merge commit is green.

## Deployment (after acceptance; not part of this plan's execution)

From `docs/design-readiness/phase-1-export-integrity.md`, "Deployment steps" and "Legacy approvals: re-approval runbook":

1. Before the release window, run the **read-only counts** on a restored backup of production: `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f apps/api/scripts/legacy-approval-counts.sql`. Tell the affected tenants (suggested text in the runbook).
2. **Back up** the database.
3. Set `WEB_ORIGINS` to the exact HTTPS origins; optionally `REFRESH_TOKEN_KEY`. Keep `AUTH_COOKIE_SECURE` at its default.
4. **`alembic upgrade head`**, from `a7d1e3f5b2c8` through `c5d1e8a3f7b2` (9 migrations). `a7d3e9f1c2b4` briefly locks `users` and `tenants`. On the synthetic dataset (300 tenants, 1,234 revisions) the whole upgrade took 0.92 s.
5. **Mandatory gate:** `python -m app.worker owner-report`. Exit 1 blocks the rollout.
   - Resolve each `needs assign-owner` line with `python -m app.worker assign-owner <email>`, choosing the account with the customer, and re-run until it exits 0.
   - Re-run it once the old pods are drained.
   - After reactivating a tenant or its members, run it again.
6. Roll out. Users sign in once (session migrations).
7. **Legacy re-approval.** Every approval granted before `c5d1e8a3f7b2` no longer counts, and its exports are refused.
   - Per project: open it, press **Save as new revision** (allowed unchanged), review the exports, approve.
   - Re-run the counts until `projects_outstanding` reaches the expected number.
   - Old revisions and their approvals stay in the history unchanged.
8. Rollback: restore the backup. Downgrading `a7d3e9f1c2b4` loses ownership transfers made since; downgrading `c5d1e8a3f7b2` makes approvals count without a renderer check.

## Stop conditions

Stop and re-plan if any of these happens:

- `main` moves before step 2. Re-integrate `phase1-base` with a merge commit, never a rebase, and re-run CI on everything.
- A PR head changes.
- A check fails on a final target.
- Alembic shows more than one head.
- `owner-report` cannot be brought to exit 0.
- Any ref on the path is found to contain affected history.

## Independent acceptance review (2026-10-31, on #208's head `d5d8407`)

A reviewer who wrote none of the code checked five areas: tenant ownership and authorization; snapshot and approval integrity; the renderer-version policy and legacy recovery; M1 conversion and backward compatibility; and the execution-export restrictions. **It found no blockers.** It also found lock ordering acyclic, the server-only snapshot, the hash checked before every read, approval and stamp identity taken from the database, unknown schemas and fields refused, and execution, CSV, QET, AML and PLC refused on every path.

| Finding                                                                                                                                                                                                                                                   | Severity               | Disposition                                                                                                                                                                                                                                                                                   |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A quotation PDF exported from an approved revision carries the REVIEW APPROVED stamp while its prices are the ones sent with that export. The devices and quantities come from the snapshot. Two exports with different price lists carry the same stamp. | should-fix             | **Owner decision before acceptance.** Either (a) state that an approval does not cover prices, or (b) never print the approved label on quotation formats. (b) changes export content, so it needs a renderer-version bump, after which every approval is re-granted. Neither is implemented. |
| `app/tests/design/model/test_compat_db.py` errored on the reviewer's first run against a new database and passed on re-run.                                                                                                                               | should-fix (plausible) | Not reproduced. The full suite passed on a fresh disposable database (simulated final `main`: 4,833 passed, 60 skipped, 0 failed) and in CI. Watch it in the step 3, 6 and 8 runs.                                                                                                            |
| The refusal messages say "only the account's owner" deletes projects or changes settings; admins may too.                                                                                                                                                 | note                   | Wording only; behaviour matches the permission table.                                                                                                                                                                                                                                         |
| `POST /plc/generate` returns model-generated PLC text without a mark. It comes from a text description, not a design.                                                                                                                                     | note                   | Outside the export-mark path. To be documented as such.                                                                                                                                                                                                                                       |
| The stamp's "approved by" is the approver's display name, frozen at approval; an account chooses its own display name.                                                                                                                                    | note                   | The account id and email are recorded for audit. Accepted for this phase.                                                                                                                                                                                                                     |

## Second independent review (2026-10-31, on `fd4420b`)

No blockers. Its should-fix findings:

- a 200-character project name pushed the estimate's disclaimer and price hash out of the stamp's footer, and an unbroken name ran off the page;
- a project name could pose as an approval.

Both are fixed in #207 `041ca1f` (renderer `2026.10.31.4`), with tests.

Notes:

- the member-without-approval test's premise is now asserted;
- `test_compat_db` builds its schema from the models, not the migrations; the migration tests cover migrations;
- the estimate CSV button is relabelled.

The reviewer did not exhaustively re-audit the whole `48d2a75..fd4420b` diff beyond the files and tests it named. The previous review (on `d5d8407`) covered tenant isolation, approval integrity, M1 and the execution-export restrictions.
