# Running API test suites in parallel safely

The database-backed tests in `apps/api` truncate tables, and the migration
tests create and drop scratch databases derived from the configured name. Two
runs pointed at the same database corrupt each other. This happened: an env
file in a shared scratch directory was overwritten, one agent's run picked up
another's `TEST_DATABASE_URL`, and its fixtures truncated the other run's
tables mid-suite.

## What the harness enforces

1. **Name guard** (`app/tests/db_target.py`). `TEST_DATABASE_URL` must name a
   `panelpilot_phase1_*`, `panelpilot_ci_*` or `panelpilot_test_*` database;
   `panelpilot`, `postgres` and the templates are refused. Nothing falls back
   to `DATABASE_URL`.
2. **One run per database** (`app/tests/db_lock.py`). Before any test is
   collected, the root `conftest.py` takes a Postgres session-level advisory
   lock keyed on the database name and holds it on a dedicated connection for
   the whole run. A second run against the same database exits at once
   (status 1, about a second) with a message naming the database and the
   holder's pid and working directory; nothing is truncated. The lock dies with
   the holding connection, so a killed run leaves no stale claim. The
   holder shows up in `pg_stat_activity` with an `application_name` starting
   `panelpilot-pytest`.

3. **Owner marker** (`app/tests/db_owner.py`). The lock stops two runs at
   once; it does not stop a run that targets another worktree's *idle*
   database (a copied env file, a mistyped lane). So, under the lock and
   still before collection, the run must own the database:
   - The run's owner token is `PANELPILOT_TEST_DB_OWNER` if set (letters,
     digits, `.`, `_`, `-`, at most 64), else `wt-` plus the first 16 hex
     digits of the SHA-256 of the worktree's absolute path. Each checkout is
     therefore its own owner without any setup.
   - The marker is the database comment,
     `panelpilot-test-owner:<token> path=<worktree path hint>`
     (`COMMENT ON DATABASE`; it needs the database's owner role, which is the
     role that ran `createdb`: `panelpilot` locally and in CI). A comment
     survives `TRUNCATE` and migrations and adds no table for `alembic check`.
   - Matching marker: the run goes ahead. Another token: refused, naming the
     database and the recorded owner and path. No marker: claimed **only if
     the database holds no application data** (no row in `users`, no tenant
     but the migration's `default`, no row in any table with a `tenant_id`;
     an empty or just-migrated database qualifies), otherwise refused. Any
     other comment on the database: refused (never overwritten). Every
     refusal exits before a test is collected (status 1); nothing is
     truncated.

CI runs one suite per job on a fresh, just-migrated database, so the job's
first run claims it and always gets the lock. The suite does not
use pytest-xdist; if it ever does, each worker needs its own database (the lock
would otherwise stop all workers but one).

## Rules for parallel work (several worktrees or agents)

- **One database per worktree.** Create it once:
  `createdb -h localhost -U panelpilot panelpilot_test_<lane>_<worktree>` and
  run `alembic upgrade head` against it.
- **Keep the env file inside the worktree or in a path only you write**
  (e.g. `<worktree>/.env.test`, git-ignored, or `/tmp/<you>/<worktree>-env.sh`).
  Never source a shared file from a common scratch directory: someone else can
  overwrite it between your runs.
- Echo the target before a long run: `echo $TEST_DATABASE_URL`.
- If a run stops with "is in use by another pytest run", do not work around
  it: another run owns that database. Point yours at your own.
- If a run stops with "belongs to another worktree or agent" or "has no owner
  marker but holds application data", the same applies: use your own
  database. A database created before the owner marker existed, and holding
  your own earlier test data, needs one explicit claim (below).
- Drop only databases you created, by exact name. Never use patterns,
  filters or "the first match" to pick a database or server.

## Reassigning a database (claim / release)

Only for a database that really is being handed over (or your own, created
before the marker existed). With `TEST_DATABASE_URL` naming it, from
`apps/api`:

```sh
python -m app.tests.db_owner status   # marker, this run's token, tables holding data
python -m app.tests.db_owner claim    # record this worktree's token (or PANELPILOT_TEST_DB_OWNER)
python -m app.tests.db_owner release  # remove the marker; the next fresh-only claim rules apply
```

`claim` and `release` pass the same name guard, refuse while a pytest run
holds the database's lock, and refuse a database whose comment is not a
PanelPilot marker. `claim` overwrites another owner's marker: tell that owner
first. To share one database between two checkouts deliberately (not
recommended), set the same `PANELPILOT_TEST_DB_OWNER` in both; the lock still
keeps their runs apart.

Checked by hand on disposable databases (2026-11-04): an unowned database
with an extra tenant was refused before collection and its rows were intact;
a database marked for another token was refused (and accepted with that
token); a fresh migrated database was claimed and the run passed; a second
run during a run was refused by the lock, as was `claim`. Automated:
`app/tests/test_db_owner.py`.
