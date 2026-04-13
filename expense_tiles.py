"""
Smart Expense Tiles Engine

Detects recurring expenses (rent, salary, utilities, etc.) from both
webapp_expenses and Odoo vendor bills, finds gaps in the sequence,
and returns actionable tiles with one-click bulk-add data.
"""

import psycopg2
import psycopg2.extras
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import calendar


def _q(conn, sql, params=None):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or [])
    rows = cur.fetchall()
    cur.close()
    return rows


def _months_between(d1, d2):
    """Number of full months between two dates."""
    return (d2.year - d1.year) * 12 + d2.month - d1.month


def _missing_monthly_dates(last_date, today):
    """Return list of (year, month) tuples that are missing between last_date and today."""
    missing = []
    d = last_date + relativedelta(months=1)
    while d.replace(day=1) < today.replace(day=1):
        missing.append(date(d.year, d.month, min(last_date.day, calendar.monthrange(d.year, d.month)[1])))
        d += relativedelta(months=1)
    return missing


def _missing_annual_dates(last_date, today):
    missing = []
    d = last_date + relativedelta(years=1)
    while d.year <= today.year:
        try:
            missing.append(date(d.year, last_date.month, last_date.day))
        except ValueError:
            missing.append(date(d.year, last_date.month, 28))
        d += relativedelta(years=1)
    return missing


def get_smart_tiles(db_config):
    """
    Returns a list of smart suggestion tiles.
    Each tile:
    {
        id: unique key,
        title: str,
        subtitle: str,
        category: str,
        vendor: str,
        amount: float,
        frequency: str,
        missing_count: int,
        missing_dates: [date, ...],
        total_amount: float,
        source: 'webapp' | 'odoo',
        priority: 'high' | 'medium',
        color: str,
    }
    """
    tiles = []
    today = date.today()

    try:
        conn = psycopg2.connect(**db_config)

        # ── 1. Detect from webapp_expenses (explicit frequency) ────────
        recurring = _q(conn, """
            SELECT category, vendor, frequency,
                   ROUND(AVG(amount)::numeric, 0) AS avg_amount,
                   MAX(expense_date) AS last_date,
                   COUNT(*) AS entry_count
            FROM webapp_expenses
            WHERE frequency IN ('monthly', 'weekly', 'annual')
            GROUP BY category, vendor, frequency
            HAVING COUNT(*) >= 1
            ORDER BY last_date ASC
        """)

        for r in recurring:
            last_date = r['last_date']
            if isinstance(last_date, str):
                last_date = date.fromisoformat(last_date)
            freq      = r['frequency']
            amount    = float(r['avg_amount'])
            category  = r['category']
            vendor    = r['vendor'] or category

            if freq == 'monthly':
                missing_dates = _missing_monthly_dates(last_date, today)
                gap_label = f"{len(missing_dates)} month(s)"
            elif freq == 'annual':
                missing_dates = _missing_annual_dates(last_date, today)
                gap_label = f"{len(missing_dates)} year(s)"
            elif freq == 'weekly':
                weeks_since = (today - last_date).days // 7
                missing_dates = [last_date + timedelta(weeks=i+1) for i in range(weeks_since)]
                gap_label = f"{len(missing_dates)} week(s)"
            else:
                missing_dates = []

            if not missing_dates:
                continue

            count = len(missing_dates)
            total = round(amount * count, 0)
            days_since = (today - last_date).days

            tiles.append({
                'id': f"webapp_{category}_{vendor}".replace(' ', '_').lower(),
                'title': f"{category} — {vendor}" if vendor != category else category,
                'subtitle': f"Last recorded: {last_date.strftime('%d %b %Y')} · {gap_label} missing",
                'category': category,
                'vendor': vendor,
                'amount': amount,
                'frequency': freq,
                'missing_count': count,
                'missing_dates': [str(d) for d in missing_dates],
                'total_amount': total,
                'source': 'webapp',
                'priority': 'high' if days_since > 45 else 'medium',
                'color': '#dc2626' if days_since > 45 else '#d97706',
            })

        # ── 2. Detect from Odoo vendor bills (pattern-based) ──────────
        odoo_recurring = _q(conn, """
            SELECT rp.name AS vendor,
                   COUNT(am.id) AS bill_count,
                   MAX(am.invoice_date) AS last_date,
                   MIN(am.invoice_date) AS first_date,
                   ROUND(AVG(am.amount_total)::numeric, 0) AS avg_amount,
                   ROUND(STDDEV(am.amount_total)::numeric, 0) AS std_amount
            FROM account_move am
            JOIN res_partner rp ON rp.id = am.partner_id
            WHERE am.move_type = 'in_invoice'
              AND am.state = 'posted'
              AND am.invoice_date IS NOT NULL
            GROUP BY rp.name
            HAVING COUNT(am.id) >= 2
               AND MAX(am.invoice_date) < CURRENT_DATE - 25
            ORDER BY MAX(am.invoice_date) ASC
        """)

        for r in odoo_recurring:
            last_date  = r['last_date']
            first_date = r['first_date']
            if isinstance(last_date, str):
                last_date  = date.fromisoformat(last_date)
            if isinstance(first_date, str):
                first_date = date.fromisoformat(first_date)

            bill_count = int(r['bill_count'])
            amount     = float(r['avg_amount'])
            std        = float(r['std_amount'] or 0)
            vendor     = r['vendor']
            days_since = (today - last_date).days

            # Determine frequency by average gap between bills
            total_days = (last_date - first_date).days
            avg_gap    = total_days / max(bill_count - 1, 1) if bill_count > 1 else 30

            if avg_gap < 10:       continue   # too frequent, not recurring fixed cost
            elif avg_gap <= 10:    freq = 'weekly'
            elif avg_gap <= 45:    freq = 'monthly'
            elif avg_gap <= 100:   freq = 'quarterly'
            else:                  freq = 'annual'

            # Only suggest if overdue and amount is relatively consistent (std < 30% of avg)
            if std > amount * 0.4:
                continue  # too variable — not a fixed expense

            if freq == 'monthly' and days_since > 30:
                missing_dates = _missing_monthly_dates(last_date, today)
            elif freq == 'annual' and days_since > 300:
                missing_dates = _missing_annual_dates(last_date, today)
            else:
                missing_dates = []

            if not missing_dates:
                continue

            # Skip if already have a webapp_expenses tile for same vendor
            existing = any(t['vendor'] == vendor for t in tiles)
            if existing:
                continue

            count = len(missing_dates)
            tiles.append({
                'id': f"odoo_{vendor}".replace(' ', '_').lower(),
                'title': vendor,
                'subtitle': f"Last Odoo bill: {last_date.strftime('%d %b %Y')} · {count} month(s) missing",
                'category': 'Vendor Bill',
                'vendor': vendor,
                'amount': amount,
                'frequency': freq,
                'missing_count': count,
                'missing_dates': [str(d) for d in missing_dates],
                'total_amount': round(amount * count, 0),
                'source': 'odoo',
                'priority': 'high' if days_since > 60 else 'medium',
                'color': '#dc2626' if days_since > 60 else '#d97706',
            })

        conn.close()

    except Exception as e:
        print(f"Smart tiles error: {e}")
        import traceback; traceback.print_exc()

    # Sort: high priority first, then by missing count
    tiles.sort(key=lambda x: (0 if x['priority'] == 'high' else 1, -x['missing_count']))
    return tiles
