#!/usr/bin/env bash
# Pull a fresh dump of the live `odoo18` DB straight from Postgres on the
# server, no SSH required. Saves a custom-format dump to ./dumps/.
#
# Defaults:
#   host    3.78.133.72
#   port    5432
#   db      odoo18
#   user    odoo18
#   password odoo18
#
# Override with PGHOST / PGPORT / PGDATABASE / PGUSER / PGPASSWORD env vars
# if any of those don't match.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DUMP_DIR="$REPO_ROOT/dumps"
mkdir -p "$DUMP_DIR"

# Defaults (can be overridden by exported env)
export PGHOST="${PGHOST:-3.78.133.72}"
export PGPORT="${PGPORT:-5432}"
export PGDATABASE="${PGDATABASE:-odoo18}"
export PGUSER="${PGUSER:-odoo18}"
export PGPASSWORD="${PGPASSWORD:-odoo18}"

bold()   { printf "\033[1m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red()    { printf "\033[31m%s\033[0m\n" "$*" >&2; }

# Prefer pg_dump from postgresql@16 (matches the Docker Postgres version).
# Using a newer client (e.g. pg_dump 17/18 from Homebrew) produces dump
# format 1.15+/1.16 which pg_restore 16 inside the container cannot read.
PG_DUMP_BIN=""
for candidate in \
    /opt/homebrew/opt/postgresql@16/bin/pg_dump \
    /usr/local/opt/postgresql@16/bin/pg_dump \
    "$(command -v pg_dump 2>/dev/null)"; do
    [[ -x "$candidate" ]] && PG_DUMP_BIN="$candidate" && break
done

if [[ -z "$PG_DUMP_BIN" ]]; then
    red "pg_dump not found. Install the pg16 client:"
    red "    brew install postgresql@16"
    exit 1
fi

PG_DUMP_VER=$("$PG_DUMP_BIN" --version | grep -oE '[0-9]+' | head -1)
if [[ "$PG_DUMP_VER" -gt 16 ]]; then
    yellow "WARNING: using pg_dump $PG_DUMP_VER (> 16). The dump may not restore into"
    yellow "         the Postgres 16 Docker container. Install postgresql@16 to avoid this:"
    yellow "         brew install postgresql@16"
fi
bold "Using: $PG_DUMP_BIN  (v$PG_DUMP_VER)"

bold "==> Probing $PGHOST:$PGPORT (user=$PGUSER db=$PGDATABASE) ..."
if ! psql -c 'SELECT version();' -tA -X >/dev/null 2>&1; then
    red "Cannot connect to Postgres at $PGHOST:$PGPORT as $PGUSER."
    red "Common causes:"
    red "  1. Server's firewall/security group doesn't allow your IP on 5432."
    red "  2. Postgres is bound to localhost only (postgresql.conf: listen_addresses)."
    red "  3. pg_hba.conf rejects your client (needs a 'host odoo18 odoo18 <your-ip>/32 md5' line)."
    red ""
    red "Quick reachability test:"
    red "    nc -vz $PGHOST $PGPORT"
    red ""
    red "If it's a firewall issue, open Lightsail console → instance → Networking →"
    red "add a TCP 5432 rule scoped to your public IP."
    exit 1
fi
green "Connection OK."

TS="$(date +%Y%m%d-%H%M%S)"
OUT="$DUMP_DIR/odoo18-live-$TS.dump"

bold "==> Dumping $PGDATABASE → $OUT (custom format, compressed)..."
# -Fc        custom-format dump (works with pg_restore --jobs)
# -Z 5       moderate compression
# --no-owner --no-acl  so it restores cleanly into a fresh local cluster
"$PG_DUMP_BIN" -Fc -Z 5 --no-owner --no-acl -v -f "$OUT" 2>&1 | tail -20 || {
    red "pg_dump failed. Output above should explain why."
    exit 1
}

ls -lh "$OUT"
green "==> Done. Now run:  bash scripts/bootstrap_local.sh"
