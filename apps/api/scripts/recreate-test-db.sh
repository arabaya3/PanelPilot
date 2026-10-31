#!/usr/bin/env bash
# Drop and recreate one disposable test database inside one named container.
#
#   scripts/recreate-test-db.sh --container panelpilot-postgres-1 \
#       --database panelpilot_phase1_m1perm
#
# Both arguments are required and are used exactly as given. The container is
# never chosen by a filter, a pattern or by taking the first of a list: on
# 2026-10-28 `docker ps --filter name=postgres -q | head -1` picked another
# project's Postgres. Before anything is sent, the script checks that:
#
# * the database is a disposable test name (panelpilot_phase1_*,
#   panelpilot_ci_* or panelpilot_test_*), never `panelpilot` or a server
#   database; the same rule as app/tests/db_target.py;
# * a container has exactly that name, is running, belongs to the compose
#   project PanelPilot uses (`panelpilot`, or EXPECT_COMPOSE_PROJECT), and
#   runs a Postgres image;
# * the `panelpilot` role exists there, by a read-only query, before any
#   DROP or CREATE.
#
# DRY_RUN=1 runs every check and prints the statements without sending them.
set -euo pipefail

container=""
database=""
while [ $# -gt 0 ]; do
  case "$1" in
    --container) container="${2:-}"; shift 2 ;;
    --database) database="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

refuse() { echo "refused: $*" >&2; exit 3; }

[ -n "$container" ] || refuse "name the container with --container; none is chosen for you"
[ -n "$database" ] || refuse "name the database with --database"

case "$database" in
  panelpilot|postgres|template0|template1) refuse "'$database' is not a test database" ;;
esac
[[ "$database" =~ ^panelpilot_(phase1|ci|test)_[a-z0-9_]{1,40}$ ]] \
  || refuse "'$database' is not panelpilot_phase1_*, panelpilot_ci_* or panelpilot_test_*"
[[ "$container" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || refuse "'$container' is not a container name"

expected_project="${EXPECT_COMPOSE_PROJECT:-panelpilot}"

# Exact name only: `docker inspect` resolves names and ids, so compare the
# reported name with the one asked for.
name="$(docker inspect --format '{{.Name}}' -- "$container" 2>/dev/null)" \
  || refuse "no container is named '$container'"
[ "$name" = "/$container" ] || refuse "'$container' resolved to '$name', not that exact name"
[ "$(docker inspect --format '{{.State.Running}}' -- "$container")" = "true" ] \
  || refuse "'$container' is not running"
project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' -- "$container")"
[ "$project" = "$expected_project" ] \
  || refuse "'$container' belongs to compose project '$project', not '$expected_project'"
image="$(docker inspect --format '{{.Config.Image}}' -- "$container")"
[[ "$image" == postgres:* || "$image" == */postgres:* ]] \
  || refuse "'$container' runs '$image', not a Postgres image"

role="$(docker exec "$container" psql -U panelpilot -d postgres -tAc \
  "SELECT 1 FROM pg_roles WHERE rolname = 'panelpilot'" 2>/dev/null || true)"
[ "$role" = "1" ] || refuse "'$container' has no panelpilot role; it is not PanelPilot's server"

drop="DROP DATABASE IF EXISTS \"$database\" WITH (FORCE)"
create="CREATE DATABASE \"$database\""
if [ "${DRY_RUN:-0}" = "1" ]; then
  printf 'would run in %s:\n  %s\n  %s\n' "$container" "$drop" "$create"
  exit 0
fi
docker exec "$container" psql -v ON_ERROR_STOP=1 -U panelpilot -d postgres -c "$drop" -c "$create"
