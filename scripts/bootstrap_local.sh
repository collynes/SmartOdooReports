#!/usr/bin/env bash
# Run on macOS (or any machine with Docker Desktop). Brings up the
# Dockerized PartyWord stack locally and restores the newest dump
# found in ./dumps/.
#
# Idempotent — re-run any time. The DB is dropped + recreated on each run.
#
# Env overrides:
#   DUMP_FILE  - path to a specific dump to restore (skips auto-pick)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER_DIR="$REPO_ROOT/docker"
LOCAL_DUMP_DIR="$REPO_ROOT/dumps"

bold()   { printf "\033[1m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red()    { printf "\033[31m%s\033[0m\n" "$*" >&2; }
step()   { echo; bold "==> $*"; }

# ──────────────────────────────────────────────────────────────────────────
step "1/5  Checking Docker"
if ! command -v docker >/dev/null 2>&1; then
    red "docker not found. Install Docker Desktop:"
    red "  https://docs.docker.com/desktop/install/mac-install/"
    red "Then restart this terminal and re-run."
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    red "Docker daemon isn't responding. Start Docker Desktop and wait for the whale icon to settle, then re-run."
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    red "docker compose v2 plugin missing. Update Docker Desktop."
    exit 1
fi
green "Docker $(docker --version | sed 's/Docker version //;s/,.*//')  +  $(docker compose version | head -n1)"

# ──────────────────────────────────────────────────────────────────────────
step "2/5  Preparing docker/.env"
if [[ ! -f "$DOCKER_DIR/.env" ]]; then
    cp "$DOCKER_DIR/.env.example" "$DOCKER_DIR/.env"
    SECRET="$(openssl rand -hex 32)"
    # BSD sed (macOS) needs an explicit '' after -i
    if sed --version >/dev/null 2>&1; then
        sed -i "s|SECRET_KEY=.*|SECRET_KEY=$SECRET|" "$DOCKER_DIR/.env"
    else
        sed -i '' "s|SECRET_KEY=.*|SECRET_KEY=$SECRET|" "$DOCKER_DIR/.env"
    fi
    yellow "Created $DOCKER_DIR/.env with a fresh SECRET_KEY."
    yellow "Add ANTHROPIC_API_KEY / GEMINI_API_KEY there if you want chat / agent / receipt features."
else
    green "Reusing existing $DOCKER_DIR/.env"
fi

# ──────────────────────────────────────────────────────────────────────────
step "3/5  Locating local dump"
DUMP="${DUMP_FILE:-}"
if [[ -z "$DUMP" ]]; then
    if [[ -d "$LOCAL_DUMP_DIR" ]]; then
        DUMP=$(ls -1t "$LOCAL_DUMP_DIR"/*.dump "$LOCAL_DUMP_DIR"/*.sql \
                          "$LOCAL_DUMP_DIR"/*.sql.gz "$LOCAL_DUMP_DIR"/*.backup \
                          2>/dev/null | head -n1 || true)
    fi
fi
if [[ -z "${DUMP:-}" ]] || [[ ! -f "$DUMP" ]]; then
    red "No dump file found locally."
    red "Pull one from the server first:"
    red "    bash scripts/pull_latest_dump.sh"
    red "or set DUMP_FILE=/path/to/dump and re-run."
    exit 1
fi
green "Using dump: $DUMP  ($(du -h "$DUMP" | cut -f1))"

# ──────────────────────────────────────────────────────────────────────────
step "4/5  Building image and starting stack"
cd "$DOCKER_DIR"
docker compose build webapp
docker compose up -d postgres
for i in $(seq 1 30); do
    state=$(docker inspect -f '{{.State.Health.Status}}' pw_postgres 2>/dev/null || echo starting)
    [[ "$state" == "healthy" ]] && break
    sleep 2
done
green "Postgres container is $state."

bash "$DOCKER_DIR/restore_db.sh" "$DUMP"

docker compose up -d odoo webapp mobile-api

# ──────────────────────────────────────────────────────────────────────────
step "5/5  Smoke test"
sleep 5
echo
echo "Container status:"
docker compose ps
echo
echo "Quick HTTP checks (expect 200/302/401, not connection refused):"
for entry in \
    "Odoo|http://localhost:8169/web/database/selector" \
    "Reports webapp|http://localhost:2989/login" \
    "Mobile API|http://localhost:8900/mobileapi/health"; do
    label="${entry%%|*}"
    url="${entry#*|}"
    code=$(curl -s -o /dev/null -m 5 -w "%{http_code}" "$url" || echo "ERR")
    printf "  %-18s %-50s -> %s\n" "$label" "$url" "$code"
done

echo
green "Done. Open these in your browser:"
echo "  Odoo 18           http://localhost:8169"
echo "  Reports webapp    http://localhost:2989"
echo "  Mobile API docs   http://localhost:8900/mobileapi/docs"
echo "  Postgres          localhost:5532  (user odoo18 / db odoo18 / pw odoo18)"
