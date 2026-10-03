#!/usr/bin/env bash
# Run one command against NEON instead of the local database:
#
#   tools/on-neon.sh uv run python -m etl.migrate
#
# .env points DATABASE_URL/DIRECT_URL at local Postgres and keeps Neon's endpoints as
# NEON_DATABASE_URL/NEON_DIRECT_URL. This copies the Neon pair over the defaults for one
# process only. load_dotenv() never overrides a variable already set, so the exported
# values win. Use it for migrations and the final write of a change, never for dry
# runs or validation: those read the deliveries table repeatedly and that is what
# used up the 5 GB monthly transfer allowance.
set -euo pipefail
cd "$(dirname "$0")/.."
get() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
NEON_DIRECT="$(get NEON_DIRECT_URL)"
NEON_POOLED="$(get NEON_DATABASE_URL)"
[ -n "$NEON_DIRECT" ] && [ -n "$NEON_POOLED" ] || {
  echo "NEON_DIRECT_URL / NEON_DATABASE_URL missing from .env" >&2; exit 1; }
echo "-> running against NEON" >&2
DIRECT_URL="$NEON_DIRECT" DATABASE_URL="$NEON_POOLED" exec "$@"
