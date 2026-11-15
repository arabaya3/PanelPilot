# Phase 1 closure in a cloud session (2026-10-30/31)

Evidence for the closure round on #207. Everything below was run in a disposable Linux container with its own Docker daemon and its own Postgres 16, using synthetic data and throwaway secrets. The owner's computer, its containers and its databases were not reachable and were not touched.

## Database-target safeguards

Two throwaway containers were started for this check only: `decoy-postgres-1` (compose project `decoy`, no `panelpilot` role) and `panelpilot-postgres-1` (compose project `panelpilot`). Output of `apps/api/scripts/recreate-test-db.sh`:

```
$ recreate-test-db.sh --container decoy-postgres-1 --database panelpilot_phase1_x
refused: 'decoy-postgres-1' belongs to compose project 'decoy', not 'panelpilot'      exit=3
$ EXPECT_COMPOSE_PROJECT=decoy recreate-test-db.sh --container decoy-postgres-1 --database panelpilot_phase1_x
refused: 'decoy-postgres-1' has no panelpilot role; it is not PanelPilot's server     exit=3
$ recreate-test-db.sh --container panelpilot-postgres --database panelpilot_phase1_x
refused: no container is named 'panelpilot-postgres'                                 exit=3
$ recreate-test-db.sh --container panelpilot-postgres-1 --database panelpilot
refused: 'panelpilot' is not a test database                                         exit=3
$ recreate-test-db.sh --container <first id from docker ps> --database panelpilot_phase1_x
refused: '<id>' resolved to '/panelpilot-postgres-1', not that exact name             exit=3
$ DRY_RUN=1 recreate-test-db.sh --container panelpilot-postgres-1 --database panelpilot_phase1_x
would run in panelpilot-postgres-1: DROP DATABASE IF EXISTS ... ; CREATE DATABASE ...  exit=0
$ recreate-test-db.sh --container panelpilot-postgres-1 --database panelpilot_phase1_x
DROP DATABASE / CREATE DATABASE                                                       exit=0
```

The only contact with the decoy's server was the read-only role probe in the second case, after the project check had been overridden on purpose; its log shows one refused login for role `panelpilot`. pytest with `TEST_DATABASE_URL` naming `panelpilot` stops before collecting: `pytest: refusing the database 'panelpilot': it is not a test database`.

## `tenant_inactive` through the real command

Disposable database `panelpilot_phase1_assign`, migrated to head, one synthetic deactivated tenant with one active account:

```
$ python -m app.worker owner-report
owner-report: 0 without an active owner, 0 need assign-owner          exit=0   (deactivated tenants are skipped)
$ python -m app.worker assign-owner owner@synthetic.test
assign-owner: the tenant is deactivated; reactivate it before recording an owner   exit=1   owner_user_id stays NULL
(tenant reactivated)
$ python -m app.worker assign-owner owner@synthetic.test
assign-owner: tenant 11111111-1111-1111-1111-111111111111 owner recorded           exit=0   owner_user_id set
$ python -m app.worker owner-report
owner-report: 0 without an active owner, 0 need assign-owner          exit=0
```

## PDF and DXF before and after an unrelated settings change

One synthetic revision saved and approved; drawing PDF, calculations PDF and DXF exported; the tenant's company settings changed (name, key, circuits per RCD); exported again.

| Format           | Bytes | SHA-256 before (first 16) | After              | Result    |
| ---------------- | ----- | ------------------------- | ------------------ | --------- |
| Drawing PDF      | 26544 | `b2e76909d55357e4`        | `b2e76909d55357e4` | identical |
| Calculations PDF | 6323  | `facc4420b00b97ad`        | `facc4420b00b97ad` | identical |
| DXF              | 77368 | `6c4dc3b541dd5353`        | `6c4dc3b541dd5353` | identical |

No metadata difference is expected, and none was found: the PDFs are written by ReportLab in invariant mode (fixed `CreationDate`/`ModDate` `D:20000101000000+00'00'`, deterministic `/ID`), their author is the company name from the revision's own stored profile, not the live settings; the DXF is R12 with only `$ACADVER` and `$INSUNITS` in its header (no creation time, no GUID). The same check is a test: `test_an_unrelated_settings_change_leaves_the_approved_exports_unchanged`.

## Not verified here

- The test `app/tests/ingestion/test_robots.py::test_an_end_anchor_matches_only_at_the_end` fails in this container on the unchanged #207 head as well (Python 3.13.17's `robotparser`); CI's result is the reference.
- OpenSearch tests skip locally; CI runs them.

## The local `test_robots` failure (2026-10-31)

`app/tests/ingestion/test_robots.py::test_an_end_anchor_matches_only_at_the_end` fails under this container's Python 3.13.17 and passes under Python 3.12, on the base and on the candidates alike:

| Ref                       | Python 3.13.17 | Python 3.12.3                                              |
| ------------------------- | -------------- | ---------------------------------------------------------- |
| `phase1-base` (`daeb143`) | fails          | passes (32/32 in the file)                                 |
| #207 `c8f2fa4`            | fails          | not run separately; its full suite passed under 3.12 later |
| #208 `de534c3`            | fails          | passes (32/32 in the file)                                 |

**Cause.** Python 3.13.17's `urllib.robotparser.RuleLine` now handles `*` and `$` itself: it strips the trailing `$` from `path` and keeps it as `fullmatch`. Python 3.12's leaves them percent-encoded (`/%2A.pdf%24`). PanelPilot's wildcard shim (`app/ingestion/robots.py`, `_original_pattern`) restores `*` and `$` from the encoded `path`, so under 3.13.17 it gets `/*.pdf` without the anchor, and `/a/manual.pdf.html` is wrongly disallowed.

**Impact.** None for the current deployment: CI (`PYTHON_VERSION: '3.12'`) and the API image (`python:3.12-slim`) use 3.12. It passed in CI's `api (test)` job, for example [#207 job 124381785550](https://github.com/arabaya3/PanelPilot/actions/runs/41149320400/job/124381785550) and [#208 job 124381862905](https://github.com/arabaya3/PanelPilot/actions/runs/41149344371/job/124381862905). **It must be fixed before the runtime moves to a Python with the new `robotparser`**: the shim would then over-block crawls. Not part of Phase 1; not fixed here.

## Production-like dataset (2026-10-31)

Synthetic data only, on a disposable database `panelpilot_phase1_prodlike`. The scripts are in `prodlike/`.

- **Seed at `main`'s schema** (`a7d1e3f5b2c8`), with `prodlike/seed_prodlike.py`:
  - 300 tenants, 50 in each owner case: normal; tied oldest accounts; oldest account inactive, with an admin; oldest inactive, no admin; deactivated tenant; single account without a role;
  - 2–6 accounts each;
  - 600 projects, 1,234 revisions, 484 approvals with typed names (pre-Phase 1).
- **Backup** with `pg_dump -Fc` (310 kB) before migrating.
- **Read-only counts before** (`apps/api/scripts/legacy-approval-counts.sql`): 484 approved, 484 unbound, 0 without renderer, in 365 projects and 257 tenants.
- **Upgrade `a7d1e3f5b2c8` → head** in 0.92 s, exit 0; `alembic check` clean. The migration reported:
  - owners set for 150 tenants;
  - 50 created-time ties and 100 inactive-first-account tenants left without an owner, 50 of them with no active admin;
  - 484 approval policies backfilled; 391 approver names, 93 "a team member".
- **Gate.** `owner-report` exited 1: 150 tenants without an active owner, 50 needing `assign-owner`. `assign-owner` on each tenant's oldest active account succeeded 50/50, and `owner-report` then exited 0.
- **Phase 1-era approvals.** 11 projects (all that matched the selection) were saved again and approved by the current code, then had their renderer cleared, as if approved before `c5d1e8a3f7b2`. With that data in place, `c5d1e8a3f7b2` was downgraded to `b4e8d2f6a9c1` and upgraded again; counts then showed 11 approvals without renderer.
- **Recovery** (`prodlike/recovery.py`) on 20 pre-Phase 1 projects and those 11:

| Check                                                        | Pre-Phase 1 (20)                        | Phase 1-era (11)                        |
| ------------------------------------------------------------ | --------------------------------------- | --------------------------------------- |
| Old revision's export                                        | refused, `revision_not_validated`       | refused, `revision_stale`               |
| Approving the same revision again                            | refused, `revision_already_approved`    | refused, `revision_already_approved`    |
| New revision saved and approved; its export                  | REVIEW APPROVED                         | REVIEW APPROVED                         |
| Renderer named in the identity line                          | yes                                     | yes                                     |
| Execution export                                             | refused, `execution_package_incomplete` | refused, `execution_package_incomplete` |
| Old revision's row unchanged (every column but `updated_at`) | 20/20                                   | 11/11                                   |

A first run stopped part-way because the script leaked database sessions (my script, not the application), after recovering 15 projects; those stayed correctly recovered. Counts after: `projects_outstanding` 319, the 365 affected projects less the 46 recovered.
