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
| `app.py` | Main Flask app — all 25+ routes, auth, API endpoints |
| `chat.py` | NLP intent matching → SQL → structured responses |
| `insights.py` | Scored insight generation for dashboard AI panel |
| `anomalies.py` | Z-score anomaly detection |
| `projections.py` | Linear regression revenue forecasting |
| `expense_tiles.py` | Smart recurring expense detection |
| `.env` | Credentials and DB config (APP_USERNAME, APP_PASSWORD, etc.) |
| `ecosystem.config.js` | PM2 config |
| `templates/base.html` | Sidebar, mobile nav, shared JS, AI explain panel |
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

## Meetings

### 2026-04-08 — New System
**Attendees:** ALL
**Notes:** Meeting to show directors system
