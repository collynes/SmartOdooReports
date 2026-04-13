"""
Sales & Revenue Projection Engine

Uses linear regression + 7-day moving average on historical data
to project revenue, orders and product demand forward.
"""

import psycopg2
import psycopg2.extras
from datetime import date, timedelta
from scipy import stats
import numpy as np


def _q(conn, sql, params=None):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or [])
    rows = cur.fetchall()
    cur.close()
    return rows


def linear_project(values, horizon_days):
    """
    Returns projected values list of length horizon_days.
    Uses linear regression. Also returns (slope, r_value, std_err).
    """
    if len(values) < 3:
        avg = float(np.mean(values)) if values else 0
        return [avg] * horizon_days, 0, 0, avg

    x = np.arange(len(values), dtype=float)
    y = np.array([float(v) for v in values])
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)

    future_x = np.arange(len(values), len(values) + horizon_days, dtype=float)
    projected = [max(0, intercept + slope * xi) for xi in future_x]
    return projected, slope, r_value, std_err


def confidence_band(values, projected, std_multiplier=1.5):
    """Returns (upper, lower) band lists."""
    if len(values) < 3:
        return projected, projected
    residuals = []
    x = np.arange(len(values), dtype=float)
    y = np.array([float(v) for v in values])
    slope, intercept, *_ = stats.linregress(x, y)
    for i, yi in enumerate(y):
        residuals.append(yi - (intercept + slope * i))
    std = float(np.std(residuals)) if residuals else 0
    upper = [p + std * std_multiplier for p in projected]
    lower = [max(0, p - std * std_multiplier) for p in projected]
    return upper, lower


def get_projections(db_config, horizon_days=30):
    """
    Returns a dict with:
      - revenue: {historical, projected, upper, lower, dates, proj_dates, slope, r2, total_proj}
      - orders:  same structure
      - products: list of {product, avg_daily, projected_30d, velocity_trend, days_stock_left}
      - summary:  human-readable projection statements
    """
    result = {}
    try:
        conn = psycopg2.connect(**db_config)

        # ── Historical daily revenue (last 90 days) ───────────────────
        hist = _q(conn, """
            SELECT DATE(so.date_order) AS day,
                   ROUND(SUM(so.amount_total)::numeric, 0) AS revenue,
                   COUNT(DISTINCT so.id) AS orders
            FROM sale_order so
            WHERE so.state NOT IN ('cancel','draft')
              AND so.date_order >= CURRENT_DATE - 90
              AND so.date_order < CURRENT_DATE
            GROUP BY 1 ORDER BY 1
        """)

        if hist:
            # Fill gaps with zeros
            all_days = {}
            start = hist[0]['day']
            end   = date.today() - timedelta(days=1)
            d = start
            while d <= end:
                all_days[d] = {'revenue': 0, 'orders': 0}
                d += timedelta(days=1)
            for r in hist:
                all_days[r['day']] = {'revenue': float(r['revenue']), 'orders': int(r['orders'])}

            sorted_days  = sorted(all_days.keys())
            rev_values   = [all_days[d]['revenue'] for d in sorted_days]
            ord_values   = [all_days[d]['orders']  for d in sorted_days]

            # Project
            rev_proj, rev_slope, rev_r, rev_se = linear_project(rev_values, horizon_days)
            ord_proj, ord_slope, ord_r, ord_se = linear_project(ord_values, horizon_days)
            rev_upper, rev_lower = confidence_band(rev_values, rev_proj)
            ord_upper, ord_lower = confidence_band(ord_values, ord_proj)

            proj_dates = [date.today() + timedelta(days=i) for i in range(horizon_days)]

            result['revenue'] = {
                'historical':  rev_values,
                'hist_dates':  [str(d) for d in sorted_days],
                'projected':   [round(v) for v in rev_proj],
                'upper':       [round(v) for v in rev_upper],
                'lower':       [round(v) for v in rev_lower],
                'proj_dates':  [str(d) for d in proj_dates],
                'slope':       round(rev_slope, 2),
                'r2':          round(rev_r ** 2, 3),
                'total_proj':  round(sum(rev_proj)),
                'daily_avg':   round(float(np.mean(rev_values[-30:])) if rev_values else 0),
            }
            result['orders'] = {
                'historical':  ord_values,
                'hist_dates':  [str(d) for d in sorted_days],
                'projected':   [round(v, 1) for v in ord_proj],
                'upper':       [round(v, 1) for v in ord_upper],
                'lower':       [round(max(0, v), 1) for v in ord_lower],
                'proj_dates':  [str(d) for d in proj_dates],
                'slope':       round(ord_slope, 3),
                'r2':          round(ord_r ** 2, 3),
                'total_proj':  round(sum(ord_proj)),
                'daily_avg':   round(float(np.mean(ord_values[-30:])), 1) if ord_values else 0,
            }
        else:
            result['revenue'] = result['orders'] = None

        # ── Product-level projections ─────────────────────────────────
        prod_rows = _q(conn, """
            WITH velocity AS (
                SELECT
                    pt.name->>'en_US'            AS product,
                    sol.product_id,
                    COUNT(DISTINCT so.date_order::date) AS active_days,
                    SUM(sol.product_uom_qty)     AS total_qty,
                    SUM(sol.product_uom_qty) / 30.0 AS daily_30d,
                    -- last 7d vs prior 7d trend
                    SUM(CASE WHEN so.date_order >= CURRENT_DATE - 7
                             THEN sol.product_uom_qty ELSE 0 END) AS qty_last7,
                    SUM(CASE WHEN so.date_order >= CURRENT_DATE - 14
                              AND so.date_order < CURRENT_DATE - 7
                             THEN sol.product_uom_qty ELSE 0 END) AS qty_prev7,
                    SUM(CASE WHEN so.date_order >= CURRENT_DATE - 7
                             THEN sol.price_subtotal ELSE 0 END) AS rev_last7,
                    SUM(sol.price_subtotal) / 30.0 AS daily_rev_30d
                FROM sale_order_line sol
                JOIN sale_order so       ON so.id  = sol.order_id
                JOIN product_product pp  ON pp.id  = sol.product_id
                JOIN product_template pt ON pt.id  = pp.product_tmpl_id
                WHERE so.state NOT IN ('cancel','draft')
                  AND sol.display_type IS NULL
                  AND so.date_order >= CURRENT_DATE - 30
                GROUP BY pt.name->>'en_US', sol.product_id
                HAVING SUM(sol.product_uom_qty) / 30.0 > 0
            ),
            stock AS (
                SELECT pp.id AS product_id,
                       SUM(sq.quantity - sq.reserved_quantity) AS available
                FROM stock_quant sq
                JOIN stock_location sl ON sl.id = sq.location_id
                JOIN product_product pp ON pp.id = sq.product_id
                WHERE sl.usage = 'internal'
                GROUP BY pp.id
            )
            SELECT
                v.product,
                ROUND(v.daily_30d::numeric, 1)            AS daily_avg,
                ROUND(v.daily_30d * 30)                   AS proj_30d_qty,
                ROUND(v.daily_30d * 90)                   AS proj_90d_qty,
                ROUND(v.daily_rev_30d * 30)               AS proj_30d_rev,
                ROUND(v.daily_rev_30d * 90)               AS proj_90d_rev,
                CASE
                    WHEN v.qty_prev7 > 0
                    THEN ROUND((v.qty_last7 / v.qty_prev7 - 1) * 100)
                    ELSE 0
                END AS velocity_trend,
                CASE
                    WHEN v.daily_30d > 0
                    THEN ROUND(COALESCE(s.available,0) / v.daily_30d)
                    ELSE NULL
                END AS days_stock_left,
                COALESCE(s.available, 0) AS stock_available
            FROM velocity v
            LEFT JOIN stock s ON s.product_id = v.product_id
            ORDER BY v.daily_rev_30d DESC
            LIMIT 20
        """)
        result['products'] = [dict(r) for r in prod_rows]

        # ── Summary statements ─────────────────────────────────────────
        summaries = []
        r = result.get('revenue')
        if r:
            direction = "grow" if r['slope'] >= 0 else "decline"
            strength  = abs(r['slope'])
            conf      = "strong" if r['r2'] > 0.6 else "moderate" if r['r2'] > 0.3 else "weak"
            summaries.append({
                'text': f"Revenue is projected to {direction} by KSH {strength:,.0f}/day over the next {horizon_days} days.",
                'sub': f"Model confidence: {conf} (R² = {r['r2']}). Projected total: KSH {r['total_proj']:,}.",
                'type': 'positive' if r['slope'] >= 0 else 'warning',
            })

        for p in result.get('products', [])[:3]:
            if p.get('days_stock_left') is not None and float(p['days_stock_left']) < 20:
                summaries.append({
                    'text': f"{p['product']}: only {int(p['days_stock_left'])} days of stock at current sales rate.",
                    'sub': f"Need ~{int(float(p['proj_30d_qty']))} units for next 30 days.",
                    'type': 'alert',
                })

        result['summaries'] = summaries

        conn.close()
    except Exception as e:
        print(f"Projection error: {e}")
        import traceback; traceback.print_exc()
        result['error'] = str(e)

    return result
