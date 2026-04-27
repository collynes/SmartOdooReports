# Party World Reports — User Guide

A practical handbook for running the webapp day-to-day. For dev/architecture details see `CLAUDE.md`.

---

## 1. Quick Start

**URL:** http://localhost:1989 (local Mac) · http://3.78.133.72:1989 (server)
**Login:** `admin` / `admin`

Before anything else, make sure the DB is up:
- **Local Mac:** Docker Desktop must be running. Container `odoo18_backup-db-1` must be started.
  ```bash
  docker start odoo18_backup-db-1
  ```
- **Server:** `sudo systemctl status odoo18` — Odoo's Postgres should be running on 5432.

If the dashboard shows **500 Internal Server Error** → DB is down. Start it, reload the page.

---

## 2. Receipt Scanner (`/receipts`)

Drag-drop a receipt photo, AI reads it, confirm, insert to Odoo as a sale order.

### Layout
1. **Drop zone** — top of page. Drag photos or click to browse (multiple OK).
2. **Mode toggle** — below drop zone.
   - **Hybrid** (default) — Python handles easy items, Gemini handles ambiguous ones. Fastest.
   - **Gemini Only** — AI does everything. Better for messy handwriting.
3. **Results** — scrollable list at the bottom. Each scanned receipt is a card you can expand/collapse by clicking the header (chevron icon). Keeps the page tidy when scanning many.

### Per-card actions
- **Click any "no match" row** → product search dropdown opens. Type to filter, prices shown on the right.
- **Uncheck items** you don't want to insert.
- **Insert to Odoo** → creates a sale order. Receipt# goes to both `note` AND `client_order_ref`.
- **X button** (top-right of card) → remove from list (doesn't affect Odoo).

### Persistence
Scanned receipts are saved in browser localStorage — refresh the page and they're still there until you hit **Clear all**.

---

## 3. Scanner Training (`/receipts/training`)

Batch-scans a folder of receipts and compares each against Odoo to measure accuracy. This is how we teach the system new aliases.

### Folder layout
```
/Users/collinsk/Documents/com.collins/com.reports/trainingdata/
  3M/   — last 3 months
  6M/   — last 6 months
  9M/   — last 9 months (baseline reference: ~194 files)
  all/  — full history
```

### Start a run
1. Enter folder path.
2. Pick mode:
   - **Extract Only** — Gemini extracts text, Python fuzzy-matches → typically ~40% accuracy. Cheaper.
   - **Gemini Match** — Gemini does product matching too → typically 70–80% accuracy. Use this for serious runs.
3. Optionally tick **Resume** to re-use cached OCR (skip Gemini calls for already-scanned files).

### During a run
- **Live feed** shows each file as it's processed with match quality:
  - `receipt_no` (green) — matched Odoo by receipt number → most reliable.
  - `date_total` (blue) — matched by date + total ±5% → reliable.
  - `date_only` (yellow) — matched by date only → **skipped from accuracy score** (too uncertain).
  - `none` — no Odoo match found.
- **Counters** track scanned / matched / errors.
- **ETA** updates as rate stabilises.
- **Pause / Stop** buttons work at any time. Stopping saves progress to cache.

### After a run
Two things happen automatically:
1. **Gemini alias review** — a second Gemini pass reviews all `wrong_product` mismatches and suggests aliases with HIGH / MEDIUM / LOW confidence. HIGH aliases auto-apply.
2. **Reconciliation page regenerates** — see next section.

### Accuracy benchmarks (9M folder)

| Date | Mode | Accuracy | Notes |
|---|---|---|---|
| 2026-04-14 | Extract only | 47% | Pre-gemini_match baseline |
| 2026-04-16 | Gemini match | 76.9% | Best so far |
| 2026-04-17 | Gemini match | 73.2% | After DB restart mid-run |

**Target:** 90%+. Path to get there: work through reconciliation after every run.

---

## 4. Manual Reconciliation (`/reconciliation`)

**This is the main feedback loop — use it after every training run.**

Shows every item where the scanner either picked the wrong product or found nothing in Odoo. Grouped by `(raw_text, suggested_product)` so resolving one alias fixes all occurrences at once.

### Card layout
- **Raw text** (black badge) — what was written on the receipt.
- **× seen** (purple pill) — how many times this exact text appeared.
- **Wrong product / Not in Odoo** (amber/red) — reason it failed.
- **4-column info:** scanner match · what Odoo says it should be · scanned price · Odoo price (with ✓ match or ✗ N% diff indicator).
- **File chips** — one per occurrence, showing **Receipt# · item position**. Click any chip to preview the image.

### Actions
- **Resolve** — enter/edit the alias text + pick the correct product from the dropdown (prices shown). Adds the alias to `products.md` and marks resolved. All changes are saved immediately — refreshing the app won't lose your progress.
- **Skip** — mark as reviewed, no alias added. Useful for obvious OCR errors you don't want to teach.
- **Delete image** — in the image preview, click Delete to move the photo to a `deleted/` subfolder. Drops it from future training runs. Use for duplicates, non-receipts, or receipts too blurry to read.
- **Undo** — reverts any resolved/skipped back to pending.

### Filter tabs
Pending / Resolved / Skipped / All.

### Bottom bar
- **Re-run Training** — kicks off training on the same folder with `gemini_match` mode. Enabled only when all items are reviewed.
- **Refresh from Last Run** — rebuilds reconciliation from the latest saved training JSON. Use if the app restarted and you want to re-load the last session's items.

### Workflow that pushes accuracy up
1. Run training (9M folder, gemini_match).
2. Open reconciliation — work through all 200+ items.
3. For each: decide whether to **resolve** (add alias), **skip** (noise), or **delete image** (bad data).
4. Re-run training.
5. Repeat until accuracy ≥ 90%.

---

## 5. Dashboard, Products, Customers, etc.

All other pages read from Odoo DB directly. They need the DB up — see section 1.

Top tiles (KPI boxes) support click-to-compare previous period. Date filter top-right applies globally via session.

---

## 6. Things to watch

- **Port 5433 must be up** (local) or Odoo must be running (server). Dashboard 500 = DB down.
- **Don't duplicate recurring expenses.** Rent, salaries, electricity etc. are in Odoo Vendor Bills. `webapp_expenses` table is only for things NOT in Odoo.
- **Training wastes Gemini calls if DB is down.** All comparisons fail silently → 0% accuracy. Always verify DB up before starting.
- **Cache.** First run after clearing `.pwtraining_cache.json` in a folder is slow (full Gemini OCR). Subsequent runs re-use cached OCR and just re-run comparison, so they're fast.

---

## 7. Restart cheat sheet

**Local Mac (this machine):**
```bash
# Kill current app
lsof -ti:1989 | xargs kill -9

# Start DB (if not running)
docker start odoo18_backup-db-1

# Start app
cd /Users/collinsk/Documents/com.collins/com.reports/SmartOdooReports
source venv/bin/activate
python app.py
```

**Server (3.78.133.72):**
```bash
pm2 restart odoo-reports --update-env
pm2 logs odoo-reports --lines 30
```

---

## 8. Key contacts for data questions

| Question | Ask |
|---|---|
| Unknown product alias / MOMO-100 / weird shorthand | Brenda or Olive |
| Expense categorisation | Faith |
| Missing Odoo receipt # | Check if entered under wrong date |
| Shipping / transport costs | Jonathan (transport), Ann (ChinaLand) |
