#!/usr/bin/env bash
# Server-side bootstrap for the Dockerized PartyWord stack.
#
# Run this on the Odoo server (3.78.133.72) once you've git-pulled this repo
# (or scp'd the docker/ + scripts/ folders over).
#
#   curl-pipe-bash style is intentional NOT supported here — please review
#   the script then run:    bash scripts/bootstrap_docker.sh
#
# What it does:
#   1. Installs Docker Engine + compose plugin if missing.
#   2. Locates the latest Odoo DB dump under /home/ubuntu/backups/.
#   3. Builds the app image and starts the stack on alt ports.
#   4. Restores the dump into the Dockerized Postgres.
#   5. Prints final URLs.
#
# It does NOT touch the live native Odoo / Flask / Postgres on this host.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER_DIR="$REPO_ROOT/docker"
BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/backups}"
DUMP_OVERRIDE="${DUMP_FILE:-}"

bold()   { printf "\033[1m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red()    { printf "\033[31m%s\033[0m\n" "$*" >&2; }
step()   { echo; bold "==> $*"; }

# ──────────────────────────────────────────────────────────────────────────
step "1/5  Checking Docker"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    green "Docker $(docker --version) and compose plugin are already installed."
else
    yellow "Docker not found — installing via the official convenience script."
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER" || true
    yellow "You may need to log out / back in for the docker group to apply."
    yellow "Continuing with sudo for this run."
    DOCKER="sudo docker"
fi
DOCKER="${DOCKER:-docker}"
COMPOSE="$DOCKER compose"

# ──────────────────────────────────────────────────────────────────────────
step "2/5  Preparing docker/.env"
if [[ ! -f "$DOCKER_DIR/.env" ]]; then
    cp "$DOCKER_DIR/.env.example" "$DOCKER_DIR/.env"
    SECRET="$(openssl rand -hex 32)"
    sed -i "s|SECRET_KEY=.*|SECRET_KEY=$SECRET|" "$DOCKER_DIR/.env"
    yellow "Created $DOCKER_DIR/.env with a fresh SECRET_KEY."
    yellow "Edit it to add ANTHROPIC_API_KEY / GEMINI_API_KEY before features that need them work."
else
    green "Reusing existing $DOCKER_DIR/.env"
fi

# ──────────────────────────────────────────────────────────────────────────
step "3/5  Locating latest DB dump"
if [[ -n "$DUMP_OVERRIDE" ]]; then
    DUMP="$DUMP_OVERRIDE"
elif [[ -d "$BACKUP_DIR" ]]; then
    # Pick the newest file matching common Odoo dump shapes.
    DUMP=$(ls -1t "$BACKUP_DIR"/*odoo18*.{dump,sql,sql.gz,backup,pgdump} 2>/dev/null | head -n1 || true)
    if [[ -z "$DUMP" ]]; then
        DUMP=$(ls -1t "$BACKUP_DIR"/*.{dump,sql,sql.gz,backup,pgdump} 2>/dev/null | head -n1 || true)
    fi
fi

if [[ -z "${DUMP:-}" ]] || [[ ! -f "$DUMP" ]]; then
    red "No dump file found. Set BACKUP_DIR or DUMP_FILE env var. Examples:"
    red "  DUMP_FILE=/home/ubuntu/backups/odoo18-2026-04-26.dump bash $0"
    red "  BACKUP_DIR=/some/other/dir bash $0"
    exit 1
fi
green "Using dump: $DUMP  ($(du -h "$DUMP" | cut -f1))"

# ──────────────────────────────────────────────────────────────────────────
step "4/5  Building image and starting stack"
cd "$DOCKER_DIR"
$COMPOSE build webapp
$COMPOSE up -d postgres
# Wait until healthcheck flips to healthy.
for i in $(seq 1 30); do
    state=$($DOCKER inspect -f '{{.State.Health.Status}}' pw_postgres 2>/dev/null || echo starting)
    [[ "$state" == "healthy" ]] && break
    sleep 2
done
green "Postgres container is $state."

bash "$DOCKER_DIR/restore_db.sh" "$DUMP"

$COMPOSE up -d odoo webapp mobile-api

# ──────────────────────────────────────────────────────────────────────────
step "5/5  Smoke test"
sleep 5
echo
echo "Container status:"
$COMPOSE ps
echo
echo "Quick port checks (expect HTTP 200/302/401, not connection refused):"
for url in \
    "http://localhost:8169/web/database/selector  Odoo" \
    "http://localhost:2989/login                  Reports webapp" \
    "http://localhost:8900/mobileapi/health       Mobile API"; do
    set -- $url
    code=$(curl -s -o /dev/null -m 5 -w "%{http_code}" "$1" || echo "ERR")
    printf "  %-50s %s -> %s\n" "$2" "$1" "$code"
done

echo
green "Done. URLs (replace localhost with this server's IP / domain):"
echo "  Odoo 18           : http://<host>:8169"
echo "  Reports webapp    : http://<host>:2989"
echo "  Mobile API docs   : http://<host>:8900/mobileapi/docs"
echo "  Postgres          : <host>:5532  (user odoo18 / db odoo18)"
echo
yellow "Native live stack on 8069 / 1989 / 8800 / 5432 was NOT touched."
