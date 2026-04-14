# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Business Context

**Party World** — retail/wholesale party supplies shop at Star Shopping Mall, Kamkunji, Nairobi, Kenya. Business active since **April 2025**.

- **Directors:** Collins Kemboi (NL, system admin), Brenda Chepkemoi (NL, MD/ordering), Faith Chepkoech (Nairobi, CFO), Festus Kipkemoi (Nairobi, operations)
- **Ownership:** Kemboi family 50%, Mutai family 50%
- **Profit Split:** Shop 50%, Kemboi 25%, Mutai 25%
- **Monthly Revenue Target:** KES 400,000
- **Staff:** Christine (sales, KES 10,000 + commission from Feb 2026), Olive (data clerk, KES 3,000)
- **Currency:** KES (Kenyan Shilling)
- **Server IP:** 3.78.133.72 | **Domain:** partyworld.co.ke

---

## Infrastructure

| Service | Port | DB | Config |
|---|---|---|---|
| Odoo 18 Production | 8069 | `odoo18` | `/etc/odoo18.conf` |
| Odoo 18 Dev | 8072 | `odoo18_dev` | `/etc/odoo18-dev.conf` |
| Reports Webapp | 1989 | `odoo18` (read) | `/opt/odoo18/webapp/.env` |
| Odoo 15 (stopped) | — | `partyworld`, `party` | `/etc/odoo15.conf` |

- **Process manager:** PM2 (`odoo-reports` app) — `pm2 restart odoo-reports --update-env`
- **Odoo services:** `sudo systemctl start|stop|restart odoo18` / `odoo18-dev`
- **PostgreSQL:** v16, user `odoo18`, password `odoo18`
- **Backups:** `/home/ubuntu/backups/` — nightly cron syncs prod → dev and keeps last 7 dumps
- **Backup script:** `/home/ubuntu/scripts/nightly_backup_sync.sh` (also syncs filestore)
- **Nightly sync:** Active — runs at midnight, keeps last 7 dumps, syncs prod → dev
- **Expense apply scripts:** `/home/ubuntu/scripts/apply_expenses_dev.py` (dev) and `apply_expenses_prod.py` (prod) — 98 bills applied to both prod and dev (2024-01 → 2026-03)

---

## Webapp (`/opt/odoo18/webapp/`)

Flask analytics dashboard — reads directly from Odoo's PostgreSQL DB. No ORM; pure SQL via psycopg2.

### Run / Restart
```bash
pm2 restart odoo-reports --update-env   # restart
pm2 logs odoo-reports --lines 30        # view logs
pm2 status                              # check status
```

Logs: `/opt/odoo18/webapp/logs/out.log` and `err.log`

### Key Files
| File | Purpose |
|---|---|
| `app.py` | Main Flask app — ~4,150 lines, all routes, auth, receipt scanner, training logic |
| `chat.py` | NLP intent matching → SQL → structured responses |
| `insights.py` | Scored insight generation for dashboard AI panel |
| `anomalies.py` | Z-score anomaly detection |
| `projections.py` | Linear regression revenue forecasting |
| `expense_tiles.py` | Smart recurring expense detection |
| `.env` | Credentials, DB config, GEMINI_API_KEY |
| `ecosystem.config.js` | PM2 config |
| `products.md` | Pipe-delimited product catalog (variant_id\|name\|code\|price\|aliases) — 74 products |
| `price_history.json` | Learned price ranges per product from Odoo (MIN/MAX/AVG/COUNT) — 73 products |
| `training_runs/` | Compact JSON records per training run (for history/compare) |
| `receipt_ocr.py` | Tesseract OCR pre-filter subprocess — rejects non-receipts before Gemini |
| `templates/base.html` | Sidebar, mobile nav, shared JS, AI explain panel, `window.THEME` in `<head>` |
| `templates/receipts.html` | Receipt scanner UI — drag-drop, lightbox, localStorage, hybrid/gemini mode toggle |
| `templates/receipts_training.html` | Training UI — ~1,170 lines, live feed, image preview, alias review |
| `templates/_filter.html` | Date range filter — pass `show_compare=False` to hide compare toggle |
| `templates/_explain.html` | AI Insight button snippet (panel lives in base.html) |

### Architecture Patterns
- **Auth:** Session-based login; `@login_required` decorator; credentials in `.env`
- **DB:** `query(sql, params)` opens a new psycopg2 connection per call, uses `RealDictCursor`
- **Date filtering:** `date_filter_context()` reads URL params + session; returns `filter_from`, `filter_to`, `compare`, etc. Pass `**ctx` to all `render_template()` calls
- **Compare toggle:** Only meaningful on Dashboard, Products, Customers, Trend, Compare pages. Pass `show_compare=False` to crosssell, reorder, deadstock, expenses, projections, anomalies routes
- **Charts:** Client-side Chart.js; data from `/api/*` JSON endpoints
- **AI Explain panel:** Shared in `base.html`; triggered by `loadExplain('pagename')`; data from `/api/explain/<page>`
- **Mobile:** Hamburger sidebar toggle via `toggleSidebar()`; grids collapse to 1-col via JS class assignment on `DOMContentLoaded`
- **Jinja2 warning:** `{% set %}` inside `{% if %}` does NOT propagate to outer scope. `{% %}` tags execute even inside HTML comments — use `{# #}` for Jinja comments

### Odoo DB Conventions
- Product names are JSONB: `pt.name->>'en_US'`
- Cost price: `product_product.standard_price` (JSONB: `{"1": 380.33}`)
- Active stock: `stock_quant` JOIN `stock_location` WHERE `sl.usage = 'internal'`
- Confirmed sales: `sale_order.state IN ('sale', 'done')`
- Receipt number stored in: `sale_order.client_order_ref` (Customer Reference field) — NOT `note`
- psycopg2 returns `price_unit`, `amount_untaxed` etc. as `decimal.Decimal` — always `float()` before arithmetic

---

## Receipt Scanner System

### Overview
Drag-drop receipt photos → OCR pre-filter → Gemini extracts line items → Python fuzzy match → insert to Odoo as `sale.order`.

### Routes
| Route | Purpose |
|---|---|
| `GET /receipts` | Scanner UI |
| `POST /api/receipts/scan` | Scan one image (`mode`: `hybrid`\|`gemini_only`) |
| `POST /api/receipts/insert-odoo` | Insert scanned receipt as sale order |
| `GET /api/receipts/products` | All products for UI autocomplete |
| `GET /receipts/training` | Training UI |
| `POST /api/receipts/training/start` | Start training (`folder`, `resume`) |
| `POST /api/receipts/training/pause` | Pause running thread |
| `POST /api/receipts/training/resume-thread` | Unpause thread |
| `POST /api/receipts/training/stop` | Stop thread (saves cache) |
| `GET /api/receipts/training/status` | Poll status — includes `live_feed`, `counters`, `eta_secs`, `current_image` (b64) |
| `GET /api/receipts/training/detail/<file>` | Full comparison for one file |
| `GET /api/receipts/training/history` | List of past training runs |
| `GET /api/receipts/training/history/compare` | Compare two runs |
| `GET /api/receipts/training/gemini-review` | Get Gemini alias review results |
| `POST /api/receipts/training/gemini-review/run` | Start Gemini alias review thread |
| `POST /api/receipts/training/gemini-review/apply` | Apply one alias from Gemini review |
| `POST /api/receipts/training/gemini-review/apply-all` | Apply all HIGH-confidence Gemini aliases |
| `GET /api/receipts/training/catalog-list` | Product list for alias edit autocomplete |
| `POST /api/receipts/training/clear-cache` | Delete `.pwtraining_cache.json` in folder |
| `GET /api/receipts/training/cache-info` | Count cached files in folder |
| `POST /api/receipts/add-alias` | Append alias to `products.md` + reload `_CATALOG` |
| `POST /api/receipts/price-history/refresh` | Pull MIN/MAX/AVG per product from Odoo → `price_history.json` |
| `GET /api/receipts/price-history` | Return current price history |

### Key Global State (app.py ~line 2487)
```python
_CATALOG: list          # in-memory product list, loaded from products.md at startup
_price_history: dict    # {str(variant_id): {min, max, mean, count}} from price_history.json
_training_state: dict   # live training progress, shared with background thread via _training_lock
_training_lock          # threading.Lock() — always acquire before reading/writing _training_state
```

### products.md Format
```
| variant_id | Name | Code | Price (Shs) | Aliases / Receipt Shorthand |
```
Parsed in `_load_catalog()` — cols[0]=id, cols[1]=name, cols[2]=code, cols[3]=price, cols[4]=aliases.
`add_alias()` rewrites the file line-by-line, then calls `_load_catalog()` to hot-reload.

### Fuzzy Matching (`_fuzzy_match_item`)
- `rapidfuzz.fuzz.WRatio` against name + each alias + code (prefix bonus)
- Price cross-check boost: exact match +35, <5% diff +25, <10% +12; uses `price_history` ranges if count≥3
- Confidence: HIGH ≥85, MEDIUM 65–84, LOW <65
- Hybrid mode: HIGH items resolved locally; MEDIUM/LOW sent to Gemini with top-5 candidates

### Training System
- **Cache:** `.pwtraining_cache.json` (v2 format) inside each training folder
  - Stores only Gemini OCR output (`receipts_raw`) — catalog-independent
  - On resume: OCR skipped (fast), comparison always re-runs with current aliases
  - v1 caches (old format, no `receipts_raw`) are silently dropped → files re-scanned
- **Odoo lookup** (3-tier): `client_order_ref` exact match → date+amount_untaxed ±5% → date only
- **Match quality badges:** `receipt_no` (green), `date_total` (blue), `date_only` (yellow ⚠)
- **Gemini alias review:** After training, optional second Gemini pass reviews all `wrong_product` pairs; returns HIGH/MEDIUM/LOW/REJECT confidence per alias; UI supports per-row pencil edit to correct product before adding
- **Run records:** Compact JSON saved to `training_runs/{session_id}.json` on completion/stop

### Training Accuracy Baseline (2026-04-14)
| Run | Folder | Files | Receipts | Items | Accuracy | Aliases |
|---|---|---|---|---|---|---|
| Best | 9M | 194 | 191 | 1,808 | **47%** | 400 |
| Latest | 6M | 87 | 86 | 931 | 32% | 246 |

**Root cause of low accuracy:** Cache was previously storing full comparison results (v1). New aliases added after a run had zero effect on next run because cached comparisons were reused. **Fixed in v2 cache** — comparison always re-runs. First run after clearing old caches should show real improvement.

### Known Issues / Watch Points
- `sale_order.note` receives `"Receipt #XXXX"` text but Odoo lookup uses `client_order_ref` — the insert endpoint still writes receipt# to `note`, NOT `client_order_ref`. **This should be fixed** so future inserts are findable by the training system.
- Gemini model: `gemini-2.0-flash` via `google.generativeai` package (FutureWarning about deprecation — not urgent)
- OCR pre-filter runs `receipt_ocr.py` as subprocess via `python3` — ensure venv/conda python is used if deps differ

---

## Odoo System

- **Admin login:** collynes@gmail.com / password in Odoo
- **Users:** Administrator (Collins), Fancy Olive (fancyolive04@gmail.com), kipkemoifestus@gmail.com, langatbrendah@gmail.com
- **Modules:** Sales, Inventory, Accounting, Purchase, HR Expenses, POS
- **Custom addons:** `/opt/odoo18/odoo-custom-addons/`
- **report_user** PostgreSQL role must exist (read-only access to odoo18 DB)

---

## Pending Data Tasks (Live Server)

These were completed on a local Docker DB but not yet applied to the live server:

1. **Apply `apply_to_live.py`** — inserts missing vendor bills: rent (KES 20,416/mo), Christine salary, Olive salary (KES 3,000/mo), server hosting, electricity, airtime, business permit, lighting repair
   - Run DRY_RUN=True first against dev (port 8072), then False, then repeat for prod
2. **Delete WH/IN/00004** — empty draft receipt in Odoo UI (0 lines, accidental)
3. **Balloon data integrity check** — data was deleted and re-entered Jul 2025; verify

### Fixed Monthly Expenses (for reference)
| Expense | Amount (KES) |
|---|---|
| Rent (Star Shopping Mall) | 20,416 |
| Christine salary (from Feb 2026) | 10,000 + commission |
| Olive salary | 3,000 |
| Electricity (KPLC prepaid) | ~2,000 |
| Odoo server hosting → Collins | ~1,870 |
| Shop airtime | 500 |

**Commission tiers (Christine, effective Feb 2026):** 0 for ≤250k sales; KES 250 at 251k–300k; +250 per 50k bracket up to KES 7,000 at 951k–1M.

### webapp_expenses table
- Custom table used ONLY for expenses not tracked in Odoo Vendor Bills
- All recurring expenses (rent, salaries, electricity, hosting, airtime, permit, repairs) are now properly in Odoo Vendor Bills — do NOT duplicate them in `webapp_expenses`
- Table was cleared on 2026-03-20 (had 18 duplicate/incorrect rows from early novice usage)
- The webapp expenses page pulls from BOTH Odoo bills AND `webapp_expenses` — adding to both causes duplicates

---

## Key Contacts
| Name | Role | Contact |
|---|---|---|
| Collins Kemboi | Director / Server admin | 254711180014 |
| Faith Chepkoech | CFO | Equity 1*****9466 |
| Brenda Chepkemoi | MD / Ordering | 254711180014 |
| Christine (Virginiah) | Sales | 254716526578 |
| Olive (Alice Chepngetich) | Data clerk | 0705315274 |
| Jonathan Mutune | Transport | 254724539932 |
| Ann (ChinaLand/MCR) | Shipping | 0725601745 |
| KPLC Prepaid Meter | Electricity | 37221316781 |
| Star Shopping Mall (Rent) | Co-op Bank | 01148527203900 |

## Next Up / Known Bugs

> **Update this section every session before hitting context limit.**

### Bugs to Fix
_(none currently known — update this section when new bugs are found)_

### Next Tasks
- Run full training on 9M folder with cleared cache (v2) — expect accuracy improvement now that aliases re-run on cached OCR
- After training: run Gemini alias review, apply HIGH aliases, re-run again to measure improvement
- Refresh price history from Odoo before next training run

### Session Log
| Date | What changed |
|---|---|
| 2026-04-14 | Built receipt scanner (scan, hybrid mode, insert to Odoo) |
| 2026-04-14 | Built training system (batch scan, Odoo compare, alias suggestions) |
| 2026-04-14 | Training UI: live image preview, counter pills, activity feed, ETA, pause/stop/resume |
| 2026-04-14 | Fixed training cache: v2 stores OCR only, comparison always re-runs (aliases now actually improve accuracy) |
| 2026-04-14 | Fixed Odoo receipt lookup: uses `client_order_ref` not `note` |
| 2026-04-14 | Fixed `decimal.Decimal` crash in `_compare_receipt` price arithmetic |
| 2026-04-14 | Added Gemini alias review: reviews wrong_product pairs, confidence tiers, pencil edit to correct product |
| 2026-04-14 | v1 cache entries without `receipts_raw` silently dropped → re-scanned (safe upgrade path) |
| 2026-04-14 | Updated CLAUDE.md with full receipt scanner documentation |
| 2026-04-14 | Fixed: `insert-odoo` now writes receipt# to `client_order_ref` AND `note` |
| 2026-04-14 | Fixed: OCR subprocess now uses `sys.executable` instead of hardcoded `python3` |

---

## Meetings

### 2026-04-08 — New System
**Attendees:** ALL
**Notes:** Meeting to show directors system
