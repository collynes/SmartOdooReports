# Party World — Master Reference

Everything about the business, system, infrastructure, and AI tooling in one place.  
For dev/architecture patterns see `CLAUDE.md`. For day-to-day webapp usage see `USER.md`.

---

## 1. Business Overview

**Party World** — retail and wholesale party supplies shop.  
**Location:** Star Shopping Mall, Kamkunji, Nairobi, Kenya  
**Active since:** April 2025  
**Currency:** KES (Kenyan Shilling)  
**Monthly revenue target:** KES 400,000  
**Domain:** partyworld.co.ke  
**Server IP:** 3.78.133.72

### Ownership
| Party | Share |
|---|---|
| Kemboi family | 50% |
| Mutai family | 50% |

### Profit split
| Recipient | Share |
|---|---|
| Shop (reinvestment/costs) | 50% |
| Collins Kemboi | 25% |
| Mutai family | 25% |

---

## 2. People

### Directors / Principals
| Name | Role | Location | Contact |
|---|---|---|---|
| Collins Kemboi | Director, system admin | Netherlands | 254711180014 |
| Brenda Chepkemoi | Managing Director, ordering | Nairobi | 254711180014 |
| Faith Chepkoech | CFO | Nairobi | Equity 1*****9466 |
| Festus Kipkemoi | Operations | Nairobi | kipkemoifestus@gmail.com |

### Staff
| Name | Role | Pay | Notes |
|---|---|---|---|
| Christine (Virginiah) | Sales | KES 10,000 + commission | From Feb 2026 |
| Olive (Alice Chepngetich) | Data clerk | KES 3,000/mo | 0705315274 |

### Christine's Commission Tiers (from Feb 2026)
| Monthly Sales | Commission |
|---|---|
| ≤ KES 250,000 | 0 |
| 251,000 – 300,000 | KES 250 |
| 301,000 – 350,000 | KES 500 |
| 351,000 – 400,000 | KES 750 |
| +50,000 per bracket | +KES 250 |
| 951,000 – 1,000,000 | KES 7,000 (cap) |

### External Contacts
| Name | Role | Contact |
|---|---|---|
| Jonathan Mutune | Transport | 254724539932 |
| Ann (ChinaLand / MCR) | Shipping agent | 0725601745 |
| KPLC Prepaid Meter | Electricity | 37221316781 |
| Star Shopping Mall (Rent) | Co-op Bank account | 01148527203900 |

### Data Questions — Who to Ask
| Topic | Ask |
|---|---|
| Unknown product alias, MOMO-100, shorthand | Brenda or Olive |
| Expense categorisation | Faith |
| Missing Odoo receipt number | Check if entered under wrong date |
| Shipping / transport costs | Jonathan (transport), Ann (ChinaLand) |

---

## 3. Fixed Monthly Expenses

| Expense | Amount (KES) | Source in Odoo |
|---|---|---|
| Rent (Star Shopping Mall) | 20,416 | Vendor Bills |
| Christine salary (from Feb 2026) | 10,000 + commission | Vendor Bills |
| Olive salary | 3,000 | Vendor Bills |
| Electricity (KPLC prepaid) | ~2,000 | Vendor Bills |
| Odoo server hosting → Collins | ~1,870 | Vendor Bills |
| Shop airtime | 500 | Vendor Bills |

> **Rule:** All recurring expenses above are in Odoo Vendor Bills. Do NOT also add them to `webapp_expenses` — that causes duplicates. `webapp_expenses` is only for one-off items not tracked in Odoo.

---

## 4. Infrastructure

### Services
| Service | Port | Database | Config |
|---|---|---|---|
| Odoo 18 Production | 8069 | `odoo18` | `/etc/odoo18.conf` |
| Odoo 18 Dev | 8072 | `odoo18_dev` | `/etc/odoo18-dev.conf` |
| Reports Webapp | 1989 | `odoo18` (read-only) | `/opt/odoo18/webapp/.env` |
| Odoo 15 (stopped) | — | `partyworld`, `party` | `/etc/odoo15.conf` |
| PostgreSQL | 5432 (server) / 5433 (local Docker) | — | user: `odoo18` / pw: `odoo18` |

### Server
- **OS:** Ubuntu
- **Process manager:** PM2 (`odoo-reports` app)
- **Odoo services:** `sudo systemctl start|stop|restart odoo18` / `odoo18-dev`
- **PostgreSQL version:** 16
- **Backups:** `/home/ubuntu/backups/` — nightly cron, last 7 dumps, syncs prod → dev
- **Backup script:** `/home/ubuntu/scripts/nightly_backup_sync.sh` (also syncs filestore)

### Local Mac (Collins)
- Docker Desktop must be running
- Container: `odoo18_backup-db-1` (port 5433)
- Start: `docker start odoo18_backup-db-1`

---

## 5. Webapp (SmartOdooReports)

### Quick Access
| Environment | URL | Login |
|---|---|---|
| Local | http://localhost:1989 | admin / admin |
| Server | http://3.78.133.72:1989 | admin / admin |

### Stack
| Layer | Technology |
|---|---|
| Backend | Python / Flask 3.1 |
| DB | PostgreSQL 16 via psycopg2 (no ORM) |
| AI | Google Gemini 2.0 Flash (`google.generativeai`) |
| Fuzzy match | rapidfuzz |
| Analytics | numpy, scipy, pandas |
| Templates | Jinja2 |
| Charts | Chart.js (client-side) |
| Process manager | PM2 |

### Key Files
| File | Purpose |
|---|---|
| `app.py` | Main Flask app — ~4,150 lines. All routes, auth, receipt scanner, training |
| `chat.py` | NLP intent matching → SQL → structured responses |
| `insights.py` | Scored insight generation for dashboard AI panel |
| `anomalies.py` | Z-score anomaly detection |
| `projections.py` | Linear regression revenue forecasting |
| `expense_tiles.py` | Smart recurring expense detection |
| `mobile_api.py` | Mobile API layer |
| `agent.py` | Agent helper |
| `receipt_ocr.py` | Tesseract OCR pre-filter — rejects non-receipts before Gemini |
| `products.md` | Pipe-delimited product catalog — 74 products |
| `price_history.json` | MIN/MAX/AVG/COUNT price ranges per product from Odoo — 73 products |
| `reconciliation.json` | Persisted reconciliation state (pending/resolved/skipped items) |
| `training_runs/` | Compact JSON records per training session |
| `.env` | Credentials and config |
| `requirements.txt` | Python dependencies |
| `ecosystem.config.js` | PM2 configuration |

### Pages / Routes
| Page | Route | What it shows |
|---|---|---|
| Login | `/login` | Auth |
| Dashboard | `/` | KPI tiles, AI insights, anomalies, revenue trend |
| Products | `/products` | Per-product revenue, margin, top sellers |
| Customers | `/customers` | Customer revenue, frequency, lifetime value |
| Trend | `/trend` | Revenue trend over time |
| Compare | `/compare` | Period-over-period comparison |
| Cross-sell | `/crosssell` | Products commonly bought together |
| Reorder | `/reorder` | Low-stock / reorder suggestions |
| Deadstock | `/deadstock` | Slow-moving products |
| Best sellers | `/best` | Top performing products |
| Sleeping | `/sleeping` | Products with no recent sales |
| Underperforming | `/underperforming` | Products below expected performance |
| Momentum | `/momentum` | Sales velocity changes |
| Analytics | `/analytics` | Advanced analytics view |
| Expenses | `/expenses` | Expense breakdown |
| Projections | `/projections` | Revenue forecast |
| Anomalies | `/anomalies` | Z-score anomaly detection |
| Chat | `/chat` | NLP query interface |
| Profile | `/profile` | User profile |
| Receipt Scanner | `/receipts` | Scan receipts → insert to Odoo |
| Scanner Training | `/receipts/training` | Batch scan + accuracy measurement |
| Reconciliation | `/reconciliation` | Review and fix scanner mismatches |

### Start / Restart Commands
```bash
# --- LOCAL MAC ---
lsof -ti:1989 | xargs kill -9          # kill current process
docker start odoo18_backup-db-1        # ensure DB is up
cd /Users/collinsk/Documents/com.collins/com.reports/SmartOdooReports
source venv/bin/activate
python app.py

# --- SERVER ---
pm2 restart odoo-reports --update-env
pm2 logs odoo-reports --lines 30
pm2 status
```

### Health Check
- **500 Internal Server Error** on dashboard → DB is down. Start Docker (local) or Odoo (server).
- **Training shows 0% accuracy** → DB was down during run. Stop, start DB, re-run.
- **Port already in use:** `lsof -ti:1989 | xargs kill -9`

---

## 6. Odoo System

### Access
| Environment | URL | Admin login |
|---|---|---|
| Production | http://3.78.133.72:8069 | collynes@gmail.com |
| Dev | http://3.78.133.72:8072 | collynes@gmail.com |

### Users
| User | Email |
|---|---|
| Administrator (Collins) | collynes@gmail.com |
| Fancy Olive | fancyolive04@gmail.com |
| Festus | kipkemoifestus@gmail.com |
| Brenda | langatbrendah@gmail.com |

### Modules Active
Sales · Inventory · Accounting · Purchase · HR Expenses · POS

### DB Conventions
- Product names are JSONB: `pt.name->>'en_US'`
- Cost price: `product_product.standard_price` (JSONB: `{"1": 380.33}`)
- Active stock: `stock_quant JOIN stock_location WHERE sl.usage = 'internal'`
- Confirmed sales: `sale_order.state IN ('sale', 'done')`
- Receipt number: stored in `sale_order.client_order_ref` (Customer Reference)
- psycopg2 returns decimals as `decimal.Decimal` — always `float()` before arithmetic

---

## 7. Receipt Scanner

### How It Works
1. Drop receipt photos on `/receipts`
2. `receipt_ocr.py` pre-filters (Tesseract) — rejects non-receipts
3. Gemini 2.0 Flash reads line items
4. Python fuzzy-matches against `products.md` catalog
5. User confirms, fixes any mismatches
6. **Insert to Odoo** → creates `sale.order`, receipt# → `client_order_ref` AND `note`

### Scan Modes
| Mode | Who matches | Best for |
|---|---|---|
| Hybrid (default) | Python for HIGH confidence, Gemini for ambiguous | Speed |
| Gemini Only | Gemini does everything | Messy handwriting |

### Fuzzy Match Logic
- Uses `rapidfuzz.fuzz.WRatio` against name + aliases + code
- Price boost: exact match +35pts · <5% diff +25pts · <10% diff +12pts
- Confidence: HIGH ≥85 · MEDIUM 65–84 · LOW <65
- Hybrid: HIGH resolved locally; MEDIUM/LOW → Gemini with top-5 candidates

### Persistence
Scanned receipts saved in browser `localStorage` — survive refresh until **Clear all**.

---

## 8. Product Catalog (`products.md`)

### Format
```
| variant_id | Name | Code | Price (Shs) | Aliases / Receipt Shorthand |
```

- **74 products** (as of Apr 2026)
- Pipe-delimited, loaded at startup into `_CATALOG`
- Aliases are comma-separated in column 5
- Adding an alias: Reconciliation → Resolve → auto-appended + hot-reloaded (no restart needed)

### Price History (`price_history.json`)
- **73 products** tracked
- Stores `{min, max, mean, count}` per `variant_id` from real Odoo sales
- Used in fuzzy match price boost when `count ≥ 3`
- Refresh via: Webapp → `/receipts` → Price History → Refresh button

---

## 9. Scanner Training System

### Training Data Folders
```
/Users/collinsk/Documents/com.collins/com.reports/trainingdata/
  3M/   — last 3 months
  6M/   — last 6 months
  9M/   — last 9 months (~194 files, used as benchmark)
  all/  — full history
```

### Training Modes
| Mode | Who matches | Typical accuracy | Cost |
|---|---|---|---|
| Extract Only | Python fuzzy only | ~40–47% | Cheap |
| Gemini Match | Gemini does product matching | 70–80% | Higher |

### Match Quality Tiers
| Badge | Colour | Method | Reliability |
|---|---|---|---|
| `receipt_no` | Green | Matched by receipt number in `client_order_ref` | Most reliable |
| `date_total` | Blue | Matched by date + total ±5% | Reliable |
| `date_only` | Yellow | Date only | Skipped from accuracy score |
| `none` | — | No Odoo match | Not counted |

### Accuracy Score Formula
```
accuracy = correct / (correct + wrong_product + no_odoo)
```
`missed` items (Odoo has it, scanner didn't see it) are excluded from denominator.

### Cache (`<folder>/.pwtraining_cache.json`)
- **v2 format:** stores Gemini OCR output only (`receipts_raw`)
- Resume mode: skips Gemini calls, re-runs comparison with current aliases (fast)
- v1 caches (old format, no `receipts_raw`) are silently dropped → files re-scanned
- Clear cache button available in Training UI

### Accuracy Benchmarks (9M folder, ~194 files)
| Date | Mode | Accuracy | Notes |
|---|---|---|---|
| 2026-04-14 | Extract only | 47% | Pre-Gemini-match baseline |
| 2026-04-16 | Gemini match | **76.9%** | Best to date |
| 2026-04-17 | Gemini match | 73.2% | DB restart mid-run |

**Target: 90%+.** Path: work reconciliation after every run → re-run → repeat.

### After a Training Run
1. **Gemini alias review** runs automatically — reviews all `wrong_product` pairs
   - HIGH confidence aliases auto-applied
   - MEDIUM/LOW shown for manual review (pencil-edit to correct product)
2. **Reconciliation page regenerates** — shows all items to review

---

## 10. Reconciliation (`/reconciliation`)

The main feedback loop. Use it after every training run.

### What It Shows
Every item where the scanner picked the wrong product or found nothing in Odoo — grouped by `(raw_text, suggested_product)` so resolving one alias fixes all occurrences at once.

### Card Information
- **Raw text** — what was written on the receipt
- **× seen** — how many times this text appeared
- **Failure reason** — Wrong product / Not in Odoo
- **4 columns:** scanner match · Odoo correct answer · scanned price · Odoo price (with % diff)
- **File chips** — one per occurrence: `#receipt_no · item N` — click to preview image

### Actions
| Action | Effect |
|---|---|
| Resolve | Enter alias + pick correct product → saves to `products.md` immediately |
| Skip | Mark reviewed, no alias added (for OCR noise) |
| Delete image | Moves photo to `deleted/` subfolder, drops from future runs |
| Undo | Reverts resolved/skipped → pending |

### Persistence
All changes write through to `reconciliation.json` on disk immediately. App restart loses nothing.

### Workflow That Pushes Accuracy Up
```
1. Run training (9M folder, gemini_match)
2. Open /reconciliation — work through all items
3. Resolve → add alias | Skip → noise | Delete image → bad data
4. Re-run training
5. Repeat until accuracy ≥ 90%
```

---

## 11. Pending Tasks (Live Server)

> Apply these to the production server — they were done on local Docker but not pushed live.

1. **`apply_to_live.py`** — inserts missing vendor bills (rent, salaries, hosting, electricity, airtime, permit, repairs)
   - Run `DRY_RUN=True` first against dev (port 8072), then `False`, then repeat for prod
2. **Delete WH/IN/00004** — empty draft receipt in Odoo UI (0 lines, accidental)
3. **Balloon data integrity check** — data was deleted and re-entered Jul 2025; verify no gaps

---

## 12. Known Issues / Watch Points

- **Don't duplicate recurring expenses.** Rent, salaries, electricity etc. are Odoo Vendor Bills. `webapp_expenses` is only for things NOT in Odoo.
- **Training with DB down** — Gemini runs fine but all Odoo comparisons return `none` silently → 0% accuracy wasted. Always verify DB is up before starting.
- **Cache cold start** — first run after clearing `.pwtraining_cache.json` is slow (full Gemini OCR per file). Subsequent resume runs re-use cached OCR → fast.
- **Gemini deprecation warning** — `google.generativeai` package shows `FutureWarning`. Not urgent; will need migration to `google-genai` SDK eventually.
- **Odoo 15 data** — databases `partyworld` and `party` still on server (stopped). Don't delete.
- **`webapp_expenses` table was cleared** on 2026-03-20 (had 18 duplicate rows). Don't re-add recurring items.

---

## 13. Git & Deployment

### `.gitignore` (current — needs update)
Currently ignores: `__pycache__/`, `venv/`, `.env`, `*.log`, `dismissed_anomalies.json`, `.DS_Store`

**Should also ignore** (not yet added):
```
training_*.json
training_runs/
reconciliation.json
price_history.json
```

### Untracked Files That Should Be Committed
- `USER.md`
- `app.py` (modified)
- `products.md` (modified — new aliases)
- `templates/receipts.html` (modified)
- `templates/reconciliation.html` (modified)

### Server Logs
```
/opt/odoo18/webapp/logs/out.log
/opt/odoo18/webapp/logs/err.log
```

---

## 14. Session History

| Date | What was built / changed |
|---|---|
| 2026-04-14 | Built receipt scanner (scan, hybrid mode, insert to Odoo) |
| 2026-04-14 | Built training system (batch scan, Odoo compare, alias suggestions) |
| 2026-04-14 | Training UI: live image preview, counter pills, activity feed, ETA, pause/stop/resume |
| 2026-04-14 | Fixed training cache: v2 stores OCR only — comparison re-runs with current aliases |
| 2026-04-14 | Fixed Odoo receipt lookup: uses `client_order_ref` not `note` |
| 2026-04-14 | Fixed `decimal.Decimal` crash in `_compare_receipt` price arithmetic |
| 2026-04-14 | Added Gemini alias review: confidence tiers, pencil edit to correct product |
| 2026-04-14 | v1 cache entries silently dropped → re-scanned (safe upgrade path) |
| 2026-04-14 | `insert-odoo` writes receipt# to both `client_order_ref` AND `note` |
| 2026-04-14 | OCR subprocess uses `sys.executable` instead of hardcoded `python3` |
| 2026-04-16 | Reconciliation: receipt# + item position tracking in file chips |
| 2026-04-16 | Reconciliation: product price shown in resolve dropdown |
| 2026-04-16 | Reconciliation: dropdown onfocus bug fixed (pre-fill vs search conflict) |
| 2026-04-16 | Receipt scanner: drop zone moved to top, results collapsible with scroll |
| 2026-04-16 | Reconciliation changes persist to disk immediately (no memory-only state) |
| 2026-04-17 | Training run 20260417_153142: 73.2% accuracy, 239 reconciliation items pending |
| 2026-04-18 | Created USER.md (user-facing guide) and partyworld.md (this file) |
| 2026-04-18 | Created COLLINS.md (personal AI operating profile) |

---

## 15. Next Steps

1. **Work through 239 pending reconciliation items** — resolve aliases, skip noise, delete bad images
2. **Re-run training** (9M folder, gemini_match) after reconciliation complete
3. **Update .gitignore** to exclude training JSONs and commit real code changes
4. **Apply `apply_to_live.py`** to live server for missing vendor bills
5. **Target:** push accuracy from 73.2% → 90%+

---

*Last updated: 2026-04-27*
