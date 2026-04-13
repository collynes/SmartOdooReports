"""
Smart insight algorithm for the dashboard.

Runs several independent queries, scores each potential insight by
"interestingness" (magnitude of change, risk level, revenue impact),
then deterministically rotates through the top candidates so the
dashboard shows something different each day — no hardcoding.
"""

import psycopg2
import psycopg2.extras
from datetime import date


def _q(conn, sql, params=None):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or [])
    rows = cur.fetchall()
    cur.close()
    return rows


def fmt(n):
    """Format number as KSH with commas."""
    try:
        return f"KSH {float(n):,.0f}"
    except Exception:
        return str(n)


def pct_change(curr, prev):
    try:
        curr, prev = float(curr), float(prev)
        if prev == 0:
            return None
        return round((curr - prev) / prev * 100, 1)
    except Exception:
        return None


def generate_insights(db_config, limit=5):
    """
    Returns a list of insight dicts sorted by score descending.
    Each dict: {text, subtext, type, score, icon}
    Types: 'positive', 'warning', 'info', 'alert'
    """
    candidates = []

    try:
        conn = psycopg2.connect(**db_config)

        # ── 1. Best growing product this month vs last ─────────────────
        rows = _q(conn, """
            SELECT
                pt.name->>'en_US' AS product,
                SUM(CASE WHEN so.date_order >= date_trunc('month', CURRENT_DATE)
                         THEN sol.price_subtotal ELSE 0 END) AS curr_rev,
                SUM(CASE WHEN so.date_order >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
                         AND so.date_order <  date_trunc('month', CURRENT_DATE)
                         THEN sol.price_subtotal ELSE 0 END) AS prev_rev,
                SUM(CASE WHEN so.date_order >= date_trunc('month', CURRENT_DATE)
                         THEN sol.product_uom_qty ELSE 0 END) AS curr_qty
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id = sol.order_id
            JOIN product_product pp  ON pp.id = sol.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
            GROUP BY 1
            HAVING SUM(CASE WHEN so.date_order >= date_trunc('month', CURRENT_DATE)
                            THEN sol.price_subtotal ELSE 0 END) > 0
            ORDER BY curr_rev DESC
        """)

        if rows:
            # Best overall this month
            top = rows[0]
            candidates.append({
                'text': f"{top['product']} is your top product this month with {fmt(top['curr_rev'])} in revenue.",
                'subtext': f"{int(float(top['curr_qty'])):,} units sold so far this month.",
                'type': 'positive', 'icon': 'star',
                'score': float(top['curr_rev']) / 1000,
            })

            # Best % growth vs last month
            for r in rows:
                chg = pct_change(r['curr_rev'], r['prev_rev'])
                if chg and chg >= 20 and float(r['curr_rev']) > 500:
                    candidates.append({
                        'text': f"{r['product']} revenue is up {chg:+.0f}% vs last month.",
                        'subtext': f"From {fmt(r['prev_rev'])} last month to {fmt(r['curr_rev'])} this month.",
                        'type': 'positive', 'icon': 'trend-up',
                        'score': abs(chg) * float(r['curr_rev']) / 50000,
                    })
                    break  # only one growth insight

        # ── 2. Revenue trajectory — MTD vs same days last month ────────
        # Compare day 1..N of this month vs day 1..N of last month (apples-to-apples)
        rev_row = _q(conn, """
            SELECT
                SUM(CASE WHEN so.date_order >= date_trunc('month', CURRENT_DATE)
                              AND so.date_order <  CURRENT_DATE + INTERVAL '1 day'
                         THEN so.amount_total ELSE 0 END) AS curr,
                SUM(CASE WHEN so.date_order >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
                              AND so.date_order <  date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
                                                   + (EXTRACT(day FROM CURRENT_DATE)::int) * INTERVAL '1 day'
                         THEN so.amount_total ELSE 0 END) AS prev,
                EXTRACT(day FROM CURRENT_DATE)::int AS days_in
            FROM sale_order so
            WHERE so.state NOT IN ('cancel','draft')
              AND so.date_order >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
        """)
        if rev_row:
            r = rev_row[0]
            days_in = int(r.get('days_in') or 1)
            chg = pct_change(r['curr'], r['prev'])
            if chg is not None and abs(chg) >= 5:
                day_note = f"(first {days_in} days of each month)"
                if chg > 0:
                    candidates.append({
                        'text': f"Sales are up {chg:+.0f}% vs the same point last month — great momentum.",
                        'subtext': f"This month so far: {fmt(r['curr'])} vs last month same days: {fmt(r['prev'])} {day_note}.",
                        'type': 'positive', 'icon': 'trend-up',
                        'score': abs(chg) * 3,
                    })
                else:
                    candidates.append({
                        'text': f"Sales are down {chg:.0f}% vs the same point last month.",
                        'subtext': f"This month so far: {fmt(r['curr'])} vs last month same days: {fmt(r['prev'])} {day_note}.",
                        'type': 'warning', 'icon': 'trend-down',
                        'score': abs(chg) * 4,
                    })

        # ── 3. Revenue concentration risk ──────────────────────────────
        conc = _q(conn, """
            WITH total AS (
                SELECT SUM(sol.price_subtotal) AS t
                FROM sale_order_line sol
                JOIN sale_order so ON so.id = sol.order_id
                WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
                  AND so.date_order >= date_trunc('month', CURRENT_DATE)
            ),
            by_product AS (
                SELECT pt.name->>'en_US' AS product, SUM(sol.price_subtotal) AS rev
                FROM sale_order_line sol
                JOIN sale_order so       ON so.id = sol.order_id
                JOIN product_product pp  ON pp.id = sol.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
                  AND so.date_order >= date_trunc('month', CURRENT_DATE)
                GROUP BY 1
                ORDER BY rev DESC LIMIT 1
            )
            SELECT b.product, ROUND(b.rev / NULLIF(t.t,0) * 100) AS share, b.rev
            FROM by_product b, total t
        """)
        if conc and float(conc[0].get('share') or 0) >= 35:
            c = conc[0]
            candidates.append({
                'text': f"{c['product']} accounts for {c['share']}% of this month's revenue.",
                'subtext': "High concentration — consider diversifying or ensuring steady supply.",
                'type': 'info', 'icon': 'alert',
                'score': float(c['share']) * 1.5,
            })

        # ── 4. Critical stock alert ─────────────────────────────────────
        stock = _q(conn, """
            WITH velocity AS (
                SELECT sol.product_id,
                       SUM(sol.product_uom_qty) / 30.0 AS daily_qty
                FROM sale_order_line sol
                JOIN sale_order so ON so.id = sol.order_id
                WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
                  AND so.date_order >= CURRENT_DATE - 30
                GROUP BY sol.product_id
            ),
            avail AS (
                SELECT pp.id AS product_id,
                       SUM(sq.quantity - sq.reserved_quantity) AS available
                FROM stock_quant sq
                JOIN stock_location sl ON sl.id = sq.location_id
                JOIN product_product pp ON pp.id = sq.product_id
                WHERE sl.usage = 'internal'
                GROUP BY pp.id
            )
            SELECT pt.name->>'en_US' AS product,
                   ROUND(COALESCE(a.available,0)::numeric,0) AS available,
                   ROUND((COALESCE(a.available,0) / NULLIF(v.daily_qty,0))::numeric,0) AS days_left
            FROM velocity v
            JOIN product_product pp  ON pp.id = v.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            LEFT JOIN avail a ON a.product_id = v.product_id
            WHERE COALESCE(a.available,0) / NULLIF(v.daily_qty,0) <= 10
            ORDER BY days_left ASC NULLS LAST
            LIMIT 1
        """)
        if stock:
            s = stock[0]
            days = int(float(s['days_left'])) if s['days_left'] else 0
            candidates.append({
                'text': f"{s['product']} will run out in {days} day{'s' if days != 1 else ''} at current sales rate.",
                'subtext': f"Only {int(float(s['available'])):,} units left in stock. Consider reordering.",
                'type': 'alert', 'icon': 'alert',
                'score': max(0, (10 - days)) * 20,
            })

        # ── 5. Best customer this month ─────────────────────────────────
        cust = _q(conn, """
            SELECT rp.name AS customer,
                   COUNT(so.id) AS orders,
                   ROUND(SUM(so.amount_total)::numeric, 0) AS revenue
            FROM sale_order so
            JOIN res_partner rp ON rp.id = so.partner_id
            WHERE so.state NOT IN ('cancel','draft')
              AND so.date_order >= date_trunc('month', CURRENT_DATE)
            GROUP BY rp.name
            ORDER BY revenue DESC LIMIT 1
        """)
        if cust:
            c = cust[0]
            candidates.append({
                'text': f"{c['customer']} is your top customer this month.",
                'subtext': f"{c['orders']} orders totalling {fmt(c['revenue'])}.",
                'type': 'positive', 'icon': 'user',
                'score': float(c['revenue']) / 2000,
            })

        # ── 6. Biggest underperformer ───────────────────────────────────
        under = _q(conn, """
            WITH baseline AS (
                SELECT pt.name->>'en_US' AS product,
                       SUM(sol.price_subtotal) / 30.0 AS daily_avg
                FROM sale_order_line sol
                JOIN sale_order so       ON so.id = sol.order_id
                JOIN product_product pp  ON pp.id = sol.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
                  AND so.date_order >= CURRENT_DATE - 60
                  AND so.date_order <  CURRENT_DATE - 7
                GROUP BY 1 HAVING COUNT(so.id) >= 5
            ),
            recent AS (
                SELECT pt.name->>'en_US' AS product, SUM(sol.price_subtotal) AS rev_7d
                FROM sale_order_line sol
                JOIN sale_order so       ON so.id = sol.order_id
                JOIN product_product pp  ON pp.id = sol.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
                  AND so.date_order >= CURRENT_DATE - 7
                GROUP BY 1
            )
            SELECT b.product,
                   ROUND(b.daily_avg * 7, 0) AS expected,
                   ROUND(COALESCE(r.rev_7d, 0), 0) AS actual,
                   ROUND((1 - COALESCE(r.rev_7d,0) / NULLIF(b.daily_avg * 7,0)) * 100) AS drop_pct
            FROM baseline b
            LEFT JOIN recent r ON r.product = b.product
            WHERE COALESCE(r.rev_7d, 0) < b.daily_avg * 7 * 0.3
              AND b.daily_avg * 7 > 1000
            ORDER BY drop_pct DESC LIMIT 1
        """)
        if under:
            u = under[0]
            candidates.append({
                'text': f"{u['product']} sales dropped {u['drop_pct']}% in the last 7 days.",
                'subtext': f"Expected {fmt(u['expected'])} but only made {fmt(u['actual'])}. Investigate pricing or stock.",
                'type': 'warning', 'icon': 'trend-down',
                'score': float(u['drop_pct']) * float(u['expected']) / 5000,
            })

        # ── 7. Year-to-date vs same period last year ────────────────────
        ytd = _q(conn, """
            SELECT
                SUM(CASE WHEN EXTRACT(year FROM so.date_order) = EXTRACT(year FROM CURRENT_DATE)
                         THEN so.amount_total ELSE 0 END) AS this_year,
                SUM(CASE WHEN EXTRACT(year FROM so.date_order) = EXTRACT(year FROM CURRENT_DATE) - 1
                         AND so.date_order::date <= (CURRENT_DATE - INTERVAL '1 year')::date
                         THEN so.amount_total ELSE 0 END) AS last_year
            FROM sale_order so
            WHERE so.state NOT IN ('cancel','draft')
              AND EXTRACT(year FROM so.date_order) >= EXTRACT(year FROM CURRENT_DATE) - 1
        """)
        if ytd:
            y = ytd[0]
            chg = pct_change(y['this_year'], y['last_year'])
            if chg is not None and float(y.get('last_year') or 0) > 0 and abs(chg) >= 10:
                direction = "up" if chg > 0 else "down"
                candidates.append({
                    'text': f"Year-to-date revenue is {direction} {abs(chg):.0f}% vs the same period last year.",
                    'subtext': f"This year: {fmt(y['this_year'])} — last year same point: {fmt(y['last_year'])}.",
                    'type': 'positive' if chg > 0 else 'warning', 'icon': 'calendar',
                    'score': abs(chg) * 2.5,
                })

        # ── 8. Dead stock capital at risk ───────────────────────────────
        dead = _q(conn, """
            SELECT COUNT(*) AS cnt,
                   ROUND(SUM(sq.quantity * COALESCE((pp.standard_price->>'1')::numeric, 0))::numeric, 0) AS value
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            JOIN product_product pp ON pp.id = sq.product_id
            LEFT JOIN (
                SELECT sol.product_id, MAX(so.date_order) AS last_sale
                FROM sale_order_line sol
                JOIN sale_order so ON so.id = sol.order_id
                WHERE so.state NOT IN ('cancel','draft')
                GROUP BY sol.product_id
            ) ls ON ls.product_id = pp.id
            WHERE sl.usage = 'internal' AND sq.quantity > 0
              AND (ls.last_sale IS NULL OR ls.last_sale < CURRENT_DATE - 60)
        """)
        if dead and float(dead[0].get('value') or 0) > 5000:
            d = dead[0]
            candidates.append({
                'text': f"{d['cnt']} products with {fmt(d['value'])} in stock haven't sold in 60+ days.",
                'subtext': "That capital could be freed up — consider promotions or clearance.",
                'type': 'warning', 'icon': 'box',
                'score': float(d['value']) / 1000,
            })

        conn.close()

    except Exception as e:
        print(f"Insight error: {e}")
        return []

    # Sort by score, return top candidates for rotation
    candidates.sort(key=lambda x: x['score'], reverse=True)
    return candidates[:limit]


def get_daily_insight(db_config):
    """
    Returns a single insight, rotated daily among the top candidates.
    Same insight shows all day, different one each day.
    """
    candidates = generate_insights(db_config, limit=5)
    if not candidates:
        return None

    # Deterministic daily rotation: use day-of-year mod number of candidates
    from datetime import date
    day_seed = date.today().timetuple().tm_yday
    idx = day_seed % len(candidates)
    return candidates[idx]
