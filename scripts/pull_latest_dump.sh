#!/usr/bin/env bash
# Run on macOS. Pulls the newest Odoo DB dump from 3.78.133.72 down into
# ./dumps/ so the local Docker stack can restore it.
#
# Env overrides:
#   KEY        - path to SSH private key (default: ~/Documents/PartyWord/LightsailDefaultKey-eu-central-1.pem)
#   HOST       - remote target           (default: ubuntu@3.78.133.72)
#   REMOTE_DIR - remote backups dir      (default: /home/ubuntu/backups)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${HOST:-ubuntu@3.78.133.72}"
REMOTE_DIR="${REMOTE_DIR:-/home/ubuntu/backups}"
LOCAL_DIR="$REPO_ROOT/dumps"

# Resolve the SSH key. Honor $KEY if set; otherwise probe a few common spots.
if [[ -z "${KEY:-}" ]]; then
    for cand in \
        "$REPO_ROOT/LightsailDefaultKey-eu-central-1.pem" \
        "$HOME/Documents/PartyWord/LightsailDefaultKey-eu-central-1.pem" \
        "$HOME/.ssh/LightsailDefaultKey-eu-central-1.pem" \
        "$HOME/.ssh/lightsail.pem"; do
        if [[ -f "$cand" ]]; then
            KEY="$cand"
            break
        fi
    done
fi

if [[ -z "${KEY:-}" ]] || [[ ! -f "$KEY" ]]; then
    echo "ERROR: SSH key not found." >&2
    echo "Looked in:" >&2
    echo "  $REPO_ROOT/LightsailDefaultKey-eu-central-1.pem" >&2
    echo "  $HOME/Documents/PartyWord/LightsailDefaultKey-eu-central-1.pem" >&2
    echo "  $HOME/.ssh/LightsailDefaultKey-eu-central-1.pem" >&2
    echo "  $HOME/.ssh/lightsail.pem" >&2
    echo "Set KEY=/path/to/key.pem and re-run." >&2
    exit 1
fi
echo "==> Using SSH key: $KEY"

# AWS / SSH refuses world-readable keys
chmod 600 "$KEY" 2>/dev/null || true

mkdir -p "$LOCAL_DIR"

echo "==> Listing newest dumps in $HOST:$REMOTE_DIR ..."
LATEST=$(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST" \
    "ls -1t $REMOTE_DIR/*.dump $REMOTE_DIR/*.sql $REMOTE_DIR/*.sql.gz $REMOTE_DIR/*.backup 2>/dev/null | head -n1" \
    || true)

if [[ -z "$LATEST" ]]; then
    echo "ERROR: no dump found in $REMOTE_DIR on the remote host." >&2
    echo "Check what's actually there:" >&2
    echo "  ssh -i $KEY $HOST 'ls -lah $REMOTE_DIR'" >&2
    exit 1
fi

NAME="$(basename "$LATEST")"
echo "==> Latest remote dump: $LATEST"
echo "==> Downloading to $LOCAL_DIR/$NAME ..."
scp -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST:$LATEST" "$LOCAL_DIR/$NAME"

ls -lh "$LOCAL_DIR/$NAME"
echo "==> Done. Now run: bash scripts/bootstrap_local.sh"
