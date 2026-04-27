# Dockerized PartyWord Stack — Runbook

This stack runs Odoo 18 + Postgres 16 + the Reports webapp + the Mobile API
in containers, with two supported targets:

- **Local on macOS** (your Mac with Docker Desktop) — see [Local](#local-on-macos) below.
- **The Lightsail server** (`3.78.133.72`) — see [Server](#on-the-server) below. Uses
  alt ports so it can co-exist with the live native stack already running there.

| Service        | Live (native) | Docker (this stack) |
| -------------- | ------------- | ------------------- |
| Postgres       | 5432          | **5532**            |
| Odoo 18        | 8069          | **8169**            |
| Reports webapp | 1989          | **2989**            |
| Mobile API     | 8800          | **8900**            |

The Docker Postgres is a **separate** instance with its own volume — restoring
into it does **not** touch the live `odoo18` DB.

---

## Local on macOS

Prerequisites: Docker Desktop installed and running.

```bash
# 1. Clone (or pull) the repo on your Mac
cd ~/Documents/PartyWord
git clone https://github.com/collynes/SmartOdooReports.git    # or `git pull` if already cloned
cd SmartOdooReports

# 2. Make sure the Lightsail key is at the default path the script expects:
#    ~/Documents/PartyWord/LightsailDefaultKey-eu-central-1.pem
# (override with KEY=/path/to/key.pem if it lives elsewhere)

# 3. Pull the latest dump from the live server into ./dumps/
bash scripts/pull_latest_dump.sh

# 4. Bring the stack up locally
bash scripts/bootstrap_local.sh
```

That's it. Open:

- **Odoo**: http://localhost:8169 (login with the same Odoo creds as live — this is a copy of that DB)
- **Reports webapp**: http://localhost:2989
- **Mobile API docs**: http://localhost:8900/mobileapi/docs

Re-running `bootstrap_local.sh` is safe — it drops + recreates the Docker DB from the dump on every run. Re-pull the dump (`pull_latest_dump.sh`) any time you want fresher data; then re-run bootstrap.

---

## On the server

```bash
# 1. Get the repo (or `git pull` if it's already there)
cd /opt/odoo18 && sudo -u ubuntu git clone https://github.com/collynes/SmartOdooReports.git docker-stack
cd docker-stack

# 2. (Optional) Edit secrets — auto-generated on first bootstrap if missing
cp docker/.env.example docker/.env
$EDITOR docker/.env   # add ANTHROPIC_API_KEY / GEMINI_API_KEY if you want chat/agent/receipts working

# 3. Run the bootstrap. It installs Docker if missing, finds the latest
#    dump in /home/ubuntu/backups/, builds the image, restores the DB,
#    and brings the stack up.
bash scripts/bootstrap_docker.sh
```

If the dump lives somewhere else:

```bash
DUMP_FILE=/path/to/odoo18-2026-04-26.dump bash scripts/bootstrap_docker.sh
# or
BACKUP_DIR=/srv/dumps bash scripts/bootstrap_docker.sh
```

The bootstrap is **idempotent** — re-running it will rebuild the image and
re-restore the DB (drops + recreates `odoo18` in the Docker Postgres each
time).

---

## Day-to-day commands

```bash
cd /opt/odoo18/docker-stack/docker

docker compose ps                    # status
docker compose logs -f webapp        # tail Flask logs
docker compose logs -f odoo          # tail Odoo logs
docker compose logs -f mobile-api    # tail Mobile API logs
docker compose restart webapp        # bounce one service
docker compose down                  # stop everything (volumes preserved)
docker compose down -v               # stop + WIPE the Docker Postgres volume
```

Restore a fresh dump without rebuilding:

```bash
bash docker/restore_db.sh /home/ubuntu/backups/odoo18-2026-04-26.dump
```

---

## URLs

Replace `<host>` with the server's public IP (`3.78.133.72`) or domain.

- **Odoo**: http://&lt;host&gt;:8169 — login with the same credentials as the live DB (this is a copy of it).
- **Reports webapp**: http://&lt;host&gt;:2989 — uses `APP_USERNAME` / `APP_PASSWORD` from `docker/.env`.
- **Mobile API docs (Swagger)**: http://&lt;host&gt;:8900/mobileapi/docs
- **Mobile API health**: http://&lt;host&gt;:8900/mobileapi/health
- **Postgres** (psql): `psql -h <host> -p 5532 -U odoo18 -d odoo18` (password `odoo18`)

If the host's firewall blocks those ports externally, that's fine — you can
SSH-tunnel: `ssh -L 8169:localhost:8169 -L 2989:localhost:2989 -L 8900:localhost:8900 ubuntu@3.78.133.72`.

---

## Architecture notes

- **One image, two services**: `webapp` and `mobile-api` both use the same
  `partyworld/app` image (built from `docker/Dockerfile.app`). Compose just
  swaps the `command`. Build once, run twice.
- **Why merged requirements**: The repo's top-level `requirements.txt` only
  pins the Flask app's deps. The mobile API needs FastAPI / uvicorn / jose /
  passlib, the receipt scanner needs `google-generativeai` + tesseract +
  Pillow + opencv. All merged into `docker/requirements.txt`.
- **One env-var change to source**: `mobile_api.py`'s `ODOO_URL` is now
  `os.getenv('ODOO_URL', 'http://localhost:8069')` — fully backward
  compatible (live server gets the same default), but lets the Docker
  mobile-api point at `http://odoo:8069` (the compose service name).
- **Odoo config**: `docker/odoo.conf` pins `db_name = odoo18` and
  `dbfilter = ^odoo18$` so Odoo's UI never offers to create / list other DBs.
- **DB roles**: `init-postgres.sql` creates `report_user` (the read-only
  role the live webapp uses) before any restore runs, so dumps that grant to
  it don't fail.

---

## Troubleshooting

**`pg_restore` prints lots of red warnings about missing roles or
ownership.** Usually harmless — `restore_db.sh` passes `--no-owner
--no-privileges`, but Odoo's dumps still reference roles that don't exist in
a fresh cluster. The data lands fine. Verify with the row-count summary the
script prints at the end.

**Odoo container loops with `FATAL: role "odoo" does not exist`.** That
means the `odoo.conf` mount didn't take effect. Check that
`/etc/odoo/odoo.conf` inside the container shows our config:
`docker exec pw_odoo cat /etc/odoo/odoo.conf`.

**Webapp `OperationalError: could not translate host name "postgres"`.**
The webapp container is using stale env. Force-recreate:
`docker compose up -d --force-recreate webapp`.

**Port already in use.** Something on the host is holding 8169/2989/8900/5532.
`sudo ss -tlnp | grep -E '8169|2989|8900|5532'` will tell you who. Edit the
left side of the `ports:` mapping in `docker-compose.yml` and bring the
stack back up.

**Receipt scanner OCR errors with `tesseract not found`.** The image already
installs `tesseract-ocr` via apt; this only happens if you bind-mounted
`/app` over the built image. Don't.

---

## Tearing it all down

```bash
cd /opt/odoo18/docker-stack/docker
docker compose down -v        # stops + removes volumes (Docker Postgres + Odoo data)
docker image rm partyworld/app:latest odoo:18 postgres:16   # optional
```

The live native stack is untouched.
