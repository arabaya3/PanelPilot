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

CI runs one suite per database, so it always gets the lock. The suite does not
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
- Drop only databases you created, by exact name. Never use patterns,
  filters or "the first match" to pick a database or server.
