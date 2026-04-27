#!/usr/bin/env bash
# Restore an Odoo Postgres dump into the Dockerized Postgres (pw_postgres).
#
# Usage:
#   ./restore_db.sh /path/to/odoo18.dump   # custom-format (.dump or .pgdump)
#   ./restore_db.sh /path/to/odoo18.sql    # plain SQL
#   ./restore_db.sh /path/to/odoo18.sql.gz # gzipped plain SQL
#
# Assumes `docker compose up -d postgres` has already brought up the
# postgres service (container name pw_postgres) on host port 5532.

set -euo pipefail

DUMP_PATH="${1:-}"
if [[ -z "$DUMP_PATH" ]]; then
    echo "ERROR: pass the dump file path as the first arg." >&2
    exit 1
fi
if [[ ! -f "$DUMP_PATH" ]]; then
    echo "ERROR: file not found: $DUMP_PATH" >&2
    exit 1
fi

CONTAINER="pw_postgres"
DBNAME="odoo18"
DBUSER="odoo18"

# Wait for the container to be healthy (compose healthcheck handles this on `up`,
# but guard anyway in case the script is invoked manually).
echo "==> Waiting for $CONTAINER to be ready..."
for i in $(seq 1 30); do
    if docker exec "$CONTAINER" pg_isready -U "$DBUSER" -d postgres >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

# Drop & recreate the target DB so we get a clean slate every restore.
echo "==> Dropping and recreating database $DBNAME..."
docker exec -i "$CONTAINER" psql -U "$DBUSER" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid)
  FROM pg_stat_activity
 WHERE datname = '$DBNAME' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $DBNAME;
CREATE DATABASE $DBNAME OWNER $DBUSER;
SQL

# Detect dump format by magic bytes / extension.
case "$DUMP_PATH" in
    *.gz)
        echo "==> Restoring gzipped SQL dump..."
        gunzip -c "$DUMP_PATH" | docker exec -i "$CONTAINER" psql -U "$DBUSER" -d "$DBNAME" -v ON_ERROR_STOP=0
        ;;
    *.sql)
        echo "==> Restoring plain SQL dump..."
        docker exec -i "$CONTAINER" psql -U "$DBUSER" -d "$DBNAME" -v ON_ERROR_STOP=0 < "$DUMP_PATH"
        ;;
    *.dump|*.pgdump|*.backup|*.tar)
        echo "==> Restoring custom/tar pg_dump archive..."
        # --jobs requires a file path, not stdin — copy into container first
        docker cp "$DUMP_PATH" "$CONTAINER:/tmp/restore.dump"
        docker exec "$CONTAINER" pg_restore -U "$DBUSER" -d "$DBNAME" \
            --no-owner --no-privileges --jobs=2 --verbose /tmp/restore.dump \
            || echo "(pg_restore reported some warnings — usually harmless ownership/privilege noise)"
        docker exec "$CONTAINER" rm -f /tmp/restore.dump
        ;;
    *)
        # Sniff the first bytes; pg_dump custom format starts with "PGDMP"
        head -c 5 "$DUMP_PATH" | grep -q "PGDMP" && {
            echo "==> Detected custom-format dump (no recognized extension)..."
            docker cp "$DUMP_PATH" "$CONTAINER:/tmp/restore.dump"
            docker exec "$CONTAINER" pg_restore -U "$DBUSER" -d "$DBNAME" \
                --no-owner --no-privileges --jobs=2 --verbose /tmp/restore.dump \
                || echo "(pg_restore reported some warnings — usually harmless)"
            docker exec "$CONTAINER" rm -f /tmp/restore.dump
        } || {
            echo "==> Falling back to plain SQL restore..."
            docker exec -i "$CONTAINER" psql -U "$DBUSER" -d "$DBNAME" -v ON_ERROR_STOP=0 < "$DUMP_PATH"
        }
        ;;
esac

echo "==> Verifying restore..."
docker exec "$CONTAINER" psql -U "$DBUSER" -d "$DBNAME" -c "
SELECT
    (SELECT count(*) FROM information_schema.tables WHERE table_schema='public') AS tables,
    (SELECT count(*) FROM sale_order) AS sale_orders,
    (SELECT count(*) FROM account_move) AS account_moves,
    (SELECT count(*) FROM product_product) AS products;
" || echo "(verification query failed — DB may be partial; check pg_restore output above)"

echo "==> Restore complete."
