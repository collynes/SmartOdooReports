"""
Anomaly Detection Engine

Scans sales, pricing, inventory and customer data for suspicious or
unusual patterns. Each anomaly is assigned a probability score (0-100)
and a severity (low / medium / high).
"""

import psycopg2
import psycopg2.extras
import numpy as np
from datetime import date, timedelta


def _q(conn, sql, params=None):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params or [])
    rows = cur.fetchall()
    cur.close()
    return rows


def z_score(values):
    """Return z-scores for a list of floats."""
    arr = np.array([float(v) for v in values])
    if arr.std() == 0:
        return [0.0] * len(arr)
    return ((arr - arr.mean()) / arr.std()).tolist()


def z_to_prob(z):
    """Convert |z| to suspicion probability (0-100)."""
    z = abs(z)
    if z < 1.5:  return 0
    if z < 2.0:  return int(40 + (z - 1.5) * 60)
    if z < 2.5:  return int(70 + (z - 2.0) * 40)
    if z < 3.0:  return int(90 + (z - 2.5) * 16)
    return 99


def detect_anomalies(db_config, lookback_days=90):
    anomalies = []

    try:
        conn = psycopg2.connect(**db_config)

        # ── 1. Revenue spike / crash days ─────────────────────────────
        daily = _q(conn, """
            SELECT DATE(so.date_order) AS day,
                   ROUND(SUM(so.amount_total)::numeric, 0) AS revenue,
                   COUNT(so.id) AS orders
            FROM sale_order so
            WHERE so.state NOT IN ('cancel','draft')
              AND so.date_order >= CURRENT_DATE - %s
            GROUP BY 1 ORDER BY 1
        """, (lookback_days,))

        if len(daily) >= 7:
            revenues = [float(r['revenue']) for r in daily]
            zs = z_score(revenues)
            for i, (row, z) in enumerate(zip(daily, zs)):
                prob = z_to_prob(z)
                # Only flag crashes (drops), not spikes — revenue spikes may be intentional (marketing, events)
                if prob >= 50 and z < 0:
                    anomalies.append({
                        'category': 'Revenue',
                        'title': f"Unusual revenue drop on {row['day']}",
                        'detail': f"KSH {row['revenue']:,} revenue ({row['orders']} orders) — {abs(z):.1f}× std dev below mean. May indicate a slow day or missed sales.",
                        'probability': prob,
                        'severity': 'high' if prob >= 80 else 'medium',
                        'date': str(row['day']),
                        'type': 'drop',
                    })

        # ── 3. Same product sold at wildly different prices ───────────
        pricing = _q(conn, """
            SELECT
                pt.name->>'en_US' AS product,
                MIN(sol.price_unit) AS min_price,
                MAX(sol.price_unit) AS max_price,
                ROUND(AVG(sol.price_unit)::numeric, 2) AS avg_price,
                COUNT(*) AS line_count,
                ROUND(STDDEV(sol.price_unit)::numeric, 2) AS price_std
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id = sol.order_id
            JOIN product_product pp  ON pp.id = sol.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft')
              AND sol.display_type IS NULL
              AND sol.price_unit > 0
              AND so.date_order >= CURRENT_DATE - %s
            GROUP BY 1
            HAVING COUNT(*) >= 3
               AND MAX(sol.price_unit) > MIN(sol.price_unit) * 1.5
               AND STDDEV(sol.price_unit) > 0
            ORDER BY (MAX(sol.price_unit) - MIN(sol.price_unit)) / NULLIF(AVG(sol.price_unit), 0) DESC
            LIMIT 10
        """, (lookback_days,))

        for r in pricing:
            ratio = float(r['max_price']) / float(r['min_price']) if float(r['min_price']) > 0 else 1
            prob  = min(95, int(40 + (ratio - 1.5) * 30))
            if prob >= 50:
                anomalies.append({
                    'category': 'Pricing',
                    'title': f"Price inconsistency — {r['product']}",
                    'detail': (f"Sold at prices ranging from KSH {float(r['min_price']):,.0f} to "
                               f"KSH {float(r['max_price']):,.0f} (avg KSH {float(r['avg_price']):,.0f}). "
                               f"Max is {ratio:.1f}× the minimum."),
                    'probability': prob,
                    'severity': 'high' if ratio > 3 else 'medium',
                    'date': None,
                    'type': 'pricing',
                })

        # ── 4. Products sold below cost ───────────────────────────────
        below_cost = _q(conn, """
            SELECT
                pt.name->>'en_US' AS product,
                COALESCE((pp.standard_price->>'1')::numeric, 0) AS cost,
                MIN(sol.price_unit) AS min_sold_price,
                COUNT(*) AS occurrences,
                ROUND(SUM(sol.price_subtotal)::numeric, 0) AS total_rev
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id = sol.order_id
            JOIN product_product pp  ON pp.id = sol.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft')
              AND sol.display_type IS NULL
              AND COALESCE((pp.standard_price->>'1')::numeric, 0) > 0
              AND sol.price_unit < COALESCE((pp.standard_price->>'1')::numeric, 0)
              AND so.date_order >= CURRENT_DATE - %s
            GROUP BY 1, (pp.standard_price->>'1')::numeric
            ORDER BY occurrences DESC
            LIMIT 10
        """, (lookback_days,))

        for r in below_cost:
            loss_pct = (1 - float(r['min_sold_price']) / float(r['cost'])) * 100
            prob = min(98, int(60 + loss_pct * 2))
            anomalies.append({
                'category': 'Margin',
                'title': f"Sold below cost — {r['product']}",
                'detail': (f"Cost: KSH {float(r['cost']):,.0f}. "
                           f"Minimum sale price: KSH {float(r['min_sold_price']):,.0f}. "
                           f"{r['occurrences']} occurrence(s). Selling at a loss."),
                'probability': prob,
                'severity': 'high',
                'date': None,
                'type': 'below_cost',
            })

        # ── 5. Top customer suddenly gone quiet ───────────────────────
        customer_gap = _q(conn, """
            WITH past AS (
                SELECT so.partner_id,
                       rp.name AS customer,
                       SUM(so.amount_total) AS past_rev,
                       MAX(so.date_order)::date AS last_order
                FROM sale_order so
                JOIN res_partner rp ON rp.id = so.partner_id
                WHERE so.state NOT IN ('cancel','draft')
                  AND so.date_order >= CURRENT_DATE - %s
                  AND so.date_order <  CURRENT_DATE - 30
                GROUP BY so.partner_id, rp.name
                ORDER BY past_rev DESC
                LIMIT 10
            ),
            recent AS (
                SELECT so.partner_id, SUM(so.amount_total) AS recent_rev
                FROM sale_order so
                WHERE so.state NOT IN ('cancel','draft')
                  AND so.date_order >= CURRENT_DATE - 30
                GROUP BY so.partner_id
            )
            SELECT p.customer, p.past_rev, p.last_order,
                   COALESCE(r.recent_rev, 0) AS recent_rev,
                   (CURRENT_DATE - p.last_order) AS days_since
            FROM past p
            LEFT JOIN recent r ON r.partner_id = p.partner_id
            WHERE COALESCE(r.recent_rev, 0) < p.past_rev * 0.1
              AND (CURRENT_DATE - p.last_order) > 25
            ORDER BY p.past_rev DESC
        """, (lookback_days,))

        for r in customer_gap:
            days = int(r['days_since'])
            prob = min(90, 50 + days)
            anomalies.append({
                'category': 'Customer',
                'title': f"Top customer gone quiet — {r['customer']}",
                'detail': (f"Spent KSH {float(r['past_rev']):,.0f} in prior period, "
                           f"only KSH {float(r['recent_rev']):,.0f} in last 30 days. "
                           f"Last order was {days} days ago."),
                'probability': min(prob, 90),
                'severity': 'medium',
                'date': str(r['last_order']),
                'type': 'churn',
            })

        # ── 6. High cancellation rate ─────────────────────────────────
        cancels = _q(conn, """
            SELECT
                COUNT(*) FILTER (WHERE state = 'cancel') AS cancelled,
                COUNT(*) FILTER (WHERE state NOT IN ('cancel','draft')) AS confirmed,
                rp.name AS customer
            FROM sale_order so
            JOIN res_partner rp ON rp.id = so.partner_id
            WHERE so.date_order >= CURRENT_DATE - %s
            GROUP BY rp.name
            HAVING COUNT(*) FILTER (WHERE state = 'cancel') >= 2
               AND COUNT(*) FILTER (WHERE state = 'cancel')::float /
                   NULLIF(COUNT(*), 0) > 0.3
            ORDER BY cancelled DESC
            LIMIT 5
        """, (lookback_days,))

        for r in cancels:
            total = int(r['cancelled']) + int(r['confirmed'])
            rate  = int(r['cancelled']) / total * 100
            prob  = min(90, int(50 + rate * 0.5))
            anomalies.append({
                'category': 'Cancellations',
                'title': f"High cancellation rate — {r['customer']}",
                'detail': (f"{r['cancelled']} cancellations out of {total} orders "
                           f"({rate:.0f}% cancel rate) in the last {lookback_days} days."),
                'probability': prob,
                'severity': 'medium',
                'date': None,
                'type': 'cancellation',
            })

        # ── 7. Inventory discrepancy — negative stock ─────────────────
        neg_stock = _q(conn, """
            SELECT pt.name->>'en_US' AS product,
                   ROUND(SUM(sq.quantity)::numeric, 0) AS qty
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            JOIN product_product pp ON pp.id = sq.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE sl.usage = 'internal' AND sq.quantity < 0
            GROUP BY 1
            ORDER BY qty ASC
        """)

        for r in neg_stock:
            anomalies.append({
                'category': 'Inventory',
                'title': f"Negative stock — {r['product']}",
                'detail': f"Current stock is {int(r['qty'])} units (below zero). Suggests deliveries were processed without stock.",
                'probability': 95,
                'severity': 'high',
                'date': None,
                'type': 'negative_stock',
            })

        # ── 8. Unusually high discount concentration ──────────────────
        discounts = _q(conn, """
            SELECT so.name AS order_ref,
                   so.date_order::date AS day,
                   rp.name AS customer,
                   ROUND(SUM(sol.price_subtotal)::numeric, 0) AS subtotal,
                   ROUND(SUM(sol.product_uom_qty * sol.price_unit)::numeric, 0) AS list_value,
                   ROUND((1 - SUM(sol.price_subtotal) /
                         NULLIF(SUM(sol.product_uom_qty * sol.price_unit), 0)) * 100, 1) AS disc_pct
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            JOIN res_partner rp ON rp.id = so.partner_id
            WHERE so.state NOT IN ('cancel','draft')
              AND sol.display_type IS NULL
              AND sol.price_unit > 0
              AND so.date_order >= CURRENT_DATE - %s
            GROUP BY so.name, so.date_order, rp.name
            HAVING (1 - SUM(sol.price_subtotal) /
                    NULLIF(SUM(sol.product_uom_qty * sol.price_unit), 0)) > 0.25
               AND SUM(sol.product_uom_qty * sol.price_unit) > 500
            ORDER BY disc_pct DESC
            LIMIT 8
        """, (lookback_days,))

        for r in discounts:
            disc = float(r['disc_pct'])
            prob = min(90, int(50 + (disc - 25) * 2))
            anomalies.append({
                'category': 'Discounts',
                'title': f"{disc:.0f}% discount on order {r['order_ref']}",
                'detail': (f"Customer: {r['customer']}. List value KSH {float(r['list_value']):,.0f} → "
                           f"sold for KSH {float(r['subtotal']):,.0f} on {r['day']}."),
                'probability': prob,
                'severity': 'high' if disc > 40 else 'medium',
                'date': str(r['day']),
                'type': 'discount',
            })

        conn.close()

    except Exception as e:
        print(f"Anomaly error: {e}")
        import traceback; traceback.print_exc()

    # Sort by probability descending
    anomalies.sort(key=lambda x: x['probability'], reverse=True)
    return anomalies
