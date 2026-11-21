# Protecting the security-exception workflow

Status: proposed, 2026-11-18. Requires a repository admin to apply. Nothing here changes
repository settings by itself.

## What needs protecting

The audit exception for GHSA-fc5q-39xc-vjpf (`security/exceptions/GHSA-fc5q-39xc-vjpf.json`)
is enforced by `scripts/security/npm-audit-gate.mjs` and
`scripts/security/check-runner-image.sh`, run from `.github/workflows/ci.yml`. Its owner
approval is a field in the record. Anyone who can push a reviewed-by-nobody change to these
files could approve, broaden or extend an exception, or weaken the gate. `.github/CODEOWNERS`
assigns these paths to the repository owner; branch protection must make that review
**required** for it to mean anything.

## Observed state (2026-11-18)

| Item                          | State                                                                               |
| ----------------------------- | ----------------------------------------------------------------------------------- |
| Repository owner / admins     | `arabaya3` (admin, the only admin)                                                  |
| Other collaborators           | `ayedrabaya2000` (write), `AdanALalawni` (write)                                    |
| `main` branch protection      | Enabled. Required status check `ci` (enforcement: everyone).                        |
| Review requirements on `main` | Not readable with write access (the API returns 403); to be confirmed by the admin. |
| Rulesets                      | None (repository and branch rules are empty).                                       |
| CODEOWNERS before this change | None.                                                                               |

## Proposed `main` protection (keep everything that exists; add, never remove)

Keep:

- Require status checks to pass: `ci` (strict/up to date as currently configured).

Add:

- Require a pull request before merging.
- Required approvals: **1**.
- **Require review from Code Owners.**
- Dismiss stale pull request approvals when new commits are pushed.
- Require approval of the most recent reviewable push.
- Require conversation resolution before merging.
- **Do not allow bypassing the above settings** ("Include administrators" / no bypass
  list). Grant no bypass to any user, team or app.
- Restrict force pushes and deletions (keep both disabled).

Equivalent as a ruleset (target: default branch): "Require a pull request before merging"
with 1 approval, "Require review from Code Owners", "Dismiss stale approvals",
"Require approval of the most recent push", "Require conversation resolution", required
status check `ci`, "Block force pushes", "Restrict deletions", empty bypass list.

## How to verify it enforces the review

1. Open a test PR that changes only `scripts/security/README-test` (or any path above) from
   a non-owner account. The PR must show "Review required — waiting on code owner
   `@arabaya3`", and the merge button must stay blocked with `ci` green.
2. A non-owner approval must not satisfy it; the owner's approval must.
3. Push another commit after approval: the approval must be dismissed.
4. Confirm the admin cannot merge without it ("bypass" not offered), then close the test PR
   without merging and delete its branch.

## Limitations (honest)

- **Sole owner.** `arabaya3` is the only code owner and the only admin. GitHub does not let a
  pull-request author approve their own pull request, so a change **authored by the owner**
  to these paths cannot get a code-owner approval from anyone. With no bypass, such a change
  is blocked until a second code owner exists. Options: add a second trusted code owner (a
  person, not this automation), or accept that owner-authored security changes need the
  owner to temporarily allow a bypass, which defeats the control and must be recorded.
- **Same person, two accounts.** If the owner and a collaborator account belong to the same
  person, a code-owner review is self-review in substance even though GitHub accepts it.
  The control then proves only that the owner account looked at the change.
- **Automation accounts.** Changes authored by automation acting under a collaborator
  account (as this one is) still need the owner's review; the automation must never be a
  code owner or be granted bypass.
- **CODEOWNERS on the base branch governs.** A PR that edits CODEOWNERS is reviewed under the
  base branch's file; protecting `/.github/CODEOWNERS` itself prevents removing the rule
  without the owner's review once this file is on `main`.
- **Scope.** This protects the exception workflow and the workflows. It does not change the
  approved exception (GHSA-fc5q-39xc-vjpf, exact path, expiry 2026-12-15) and does not
  suppress any advisory.
