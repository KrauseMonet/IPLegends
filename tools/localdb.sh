#!/usr/bin/env bash
# The local development Postgres. Runs the ETL, validation and the dev server without
# touching Neon, whose free tier caps data sent out at 5 GB a month.
#
# Binaries come from the `pgserver` PyPI package (PostgreSQL 16), installed in its own
# venv outside the repo, so no Homebrew, Docker or GUI installer is needed:
#
#   uv venv ~/.local/share/iplegends-pg/venv --python 3.11
#   uv pip install --python ~/.local/share/iplegends-pg/venv/bin/python pgserver
#   tools/localdb.sh init
#
# The data directory runs with fsync off: everything in it is rebuildable from the
# archives in data/ (tools/localdb.sh rebuild), so speed beats crash safety here.
#
#   tools/localdb.sh start | stop | status | psql | init | rebuild
set -euo pipefail

BASE="${IPLEGENDS_PG_HOME:-$HOME/.local/share/iplegends-pg}"
BIN="$BASE/venv/lib/python3.11/site-packages/pgserver/pginstall/bin"
DATA="$BASE/data"
PORT=5433
DB=iplegends

pg() { "$BIN/$1" "${@:2}"; }

case "${1:-status}" in
  init)
    [ -d "$DATA" ] && { echo "already initialised: $DATA"; exit 0; }
    pg initdb -D "$DATA" -U postgres --auth=trust -E UTF8 --locale=C >/dev/null
    cat >> "$DATA/postgresql.conf" <<EOF
port = $PORT
listen_addresses = 'localhost'
max_connections = 50
shared_buffers = 256MB
work_mem = 32MB
maintenance_work_mem = 256MB
fsync = off
synchronous_commit = off
EOF
    pg pg_ctl -D "$DATA" -l "$BASE/postgres.log" -w start
    pg createdb -h localhost -p $PORT -U postgres $DB
    ;;
  start)
    pg pg_isready -h localhost -p $PORT -q && { echo "already running"; exit 0; }
    pg pg_ctl -D "$DATA" -l "$BASE/postgres.log" -w start ;;
  stop)   pg pg_ctl -D "$DATA" -w stop ;;
  status) pg pg_isready -h localhost -p $PORT ;;
  psql)   pg psql -h localhost -p $PORT -U postgres -d $DB "${@:2}" ;;
  rebuild)
    # The whole refresh chain from CLAUDE.md, against the local database. Needs only
    # the archives already in data/ -- nothing is fetched from Neon.
    cd "$(dirname "$0")/.."
    export PATH="$HOME/.local/bin:$PATH"
    uv run python -m etl.migrate
    uv run python -m etl.load --all
    # Neon's franchise_season ids, then a reload that keeps them (see the module).
    uv run python -m tools.localdb_align_ids
    uv run python -m etl.load --all
    uv run python -m etl.derive_people --all
    uv run python -m etl.derive_squads
    uv run python -m etl.state_model --write
    uv run python -m etl.impact --write
    uv run python -m etl.career_positions --write
    ;;
  *) echo "usage: $0 start|stop|status|psql|init|rebuild" >&2; exit 2 ;;
esac
