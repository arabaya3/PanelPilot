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

| Format | Bytes | SHA-256 before (first 16) | After | Result |
|---|---|---|---|---|
| Drawing PDF | 26544 | `b2e76909d55357e4` | `b2e76909d55357e4` | identical |
| Calculations PDF | 6323 | `facc4420b00b97ad` | `facc4420b00b97ad` | identical |
| DXF | 77368 | `6c4dc3b541dd5353` | `6c4dc3b541dd5353` | identical |

No metadata difference is expected, and none was found: the PDFs are written by ReportLab in invariant mode (fixed `CreationDate`/`ModDate` `D:20000101000000+00'00'`, deterministic `/ID`), their author is the company name from the revision's own stored profile, not the live settings; the DXF is R12 with only `$ACADVER` and `$INSUNITS` in its header (no creation time, no GUID). The same check is a test: `test_an_unrelated_settings_change_leaves_the_approved_exports_unchanged`.

## Not verified here

- The test `app/tests/ingestion/test_robots.py::test_an_end_anchor_matches_only_at_the_end` fails in this container on the unchanged #207 head as well (Python 3.13.17's `robotparser`); CI's result is the reference.
- OpenSearch tests skip locally; CI runs them.
