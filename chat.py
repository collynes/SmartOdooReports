"""
Natural language chat interface for Odoo sales data.
Uses keyword/intent matching to route questions to live DB queries.
"""
import re
from datetime import date, timedelta


def _period_from_text(text):
    """Extract a date range from natural language."""
    t = text.lower()
    today = date.today()

    if 'today' in t:
        return today, today + timedelta(days=1), 'today'
    if 'yesterday' in t:
        d = today - timedelta(days=1)
        return d, today, 'yesterday'
    if 'this week' in t:
        start = today - timedelta(days=today.weekday())
        return start, today + timedelta(days=1), 'this week'
    if 'last week' in t:
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=7)
        return start, end, 'last week'
    if 'this month' in t:
        start = today.replace(day=1)
        return start, today + timedelta(days=1), 'this month'
    if 'last month' in t:
        first = today.replace(day=1)
        last_end = first
        last_start = (first - timedelta(days=1)).replace(day=1)
        return last_start, last_end, 'last month'
    if 'this year' in t:
        start = today.replace(month=1, day=1)
        return start, today + timedelta(days=1), 'this year'
    if 'last 7' in t or 'last seven' in t:
        return today - timedelta(days=7), today + timedelta(days=1), 'last 7 days'
    if 'last 30' in t or 'last thirty' in t:
        return today - timedelta(days=30), today + timedelta(days=1), 'last 30 days'
    if 'last 90' in t or 'last ninety' in t or 'last quarter' in t:
        return today - timedelta(days=90), today + timedelta(days=1), 'last 90 days'
    if 'last year' in t:
        start = (today - timedelta(days=365)).replace(month=1, day=1)
        end = start.replace(month=12, day=31) + timedelta(days=1)
        return start, end, 'last year'
    # default: this month
    start = today.replace(day=1)
    return start, today + timedelta(days=1), 'this month'


def _fmt(v):
    try:
        return f"KSH {float(v):,.0f}"
    except Exception:
        return str(v)


def answer(question, db_query_fn):
    """
    Main entry point. Returns dict: {text, rows, chart_type, chart_labels, chart_values, suggestions}
    db_query_fn(sql, params=None) -> list of dicts
    """
    q = question.strip().lower()
    fd, td, period_label = _period_from_text(question)

    # ── Revenue / Sales total ──────────────────────────────────
    if any(w in q for w in ['revenue', 'sales', 'total', 'how much', 'earned', 'made', 'income']):
        if any(w in q for w in ['product', 'item', 'sell', 'top', 'best', 'which']):
            return _top_products(db_query_fn, fd, td, period_label)
        if any(w in q for w in ['customer', 'client', 'who', 'buyer']):
            return _top_customers(db_query_fn, fd, td, period_label)
        if any(w in q for w in ['categor', 'type', 'group']):
            return _by_category(db_query_fn, fd, td, period_label)
        if any(w in q for w in ['day', 'daily', 'trend', 'over time', 'per day']):
            return _daily_trend(db_query_fn, fd, td, period_label)
        return _revenue_summary(db_query_fn, fd, td, period_label)

    # ── Orders ────────────────────────────────────────────────
    if any(w in q for w in ['order', 'transaction', 'sale count', 'how many order']):
        if any(w in q for w in ['day', 'daily', 'trend', 'per day']):
            return _daily_trend(db_query_fn, fd, td, period_label)
        return _orders_summary(db_query_fn, fd, td, period_label)

    # ── Products ──────────────────────────────────────────────
    if any(w in q for w in ['product', 'item', 'sell', 'selling', 'sold', 'best seller', 'top product']):
        if any(w in q for w in ['slow', 'bad', 'worst', 'under', 'not sell', 'poor']):
            return _underperforming(db_query_fn, fd, td, period_label)
        if any(w in q for w in ['stock', 'inventory', 'reorder', 'low']):
            return _low_stock(db_query_fn)
        return _top_products(db_query_fn, fd, td, period_label)

    # ── Customers ─────────────────────────────────────────────
    if any(w in q for w in ['customer', 'client', 'buyer', 'who buy', 'who bought']):
        if any(w in q for w in ['new', 'first']):
            return _new_customers(db_query_fn, fd, td, period_label)
        return _top_customers(db_query_fn, fd, td, period_label)

    # ── Comparison ────────────────────────────────────────────
    if any(w in q for w in ['compar', 'vs', 'versus', 'differ', 'change', 'grow', 'declin', 'drop', 'increas']):
        return _compare_periods(db_query_fn, fd, td, period_label)

    # ── Average order value ───────────────────────────────────
    if any(w in q for w in ['average', 'avg', 'aov', 'basket', 'per order']):
        return _aov(db_query_fn, fd, td, period_label)

    # ── Stock / inventory ─────────────────────────────────────
    if any(w in q for w in ['stock', 'inventory', 'reorder', 'dead', 'stale']):
        return _low_stock(db_query_fn)

    # ── Category ──────────────────────────────────────────────
    if any(w in q for w in ['categor', 'department', 'group', 'segment']):
        return _by_category(db_query_fn, fd, td, period_label)

    # ── Trend ─────────────────────────────────────────────────
    if any(w in q for w in ['trend', 'over time', 'chart', 'graph', 'plot', 'daily', 'weekly']):
        return _daily_trend(db_query_fn, fd, td, period_label)

    # ── Fallback ──────────────────────────────────────────────
    return _fallback(db_query_fn, fd, td, period_label)


# ── Query helpers ─────────────────────────────────────────────

def _revenue_summary(q, fd, td, lbl):
    rows = q("""
        SELECT COUNT(DISTINCT id) AS orders,
               ROUND(SUM(amount_total)::numeric,0) AS revenue,
               ROUND(AVG(amount_total)::numeric,0) AS avg_order
        FROM sale_order
        WHERE state NOT IN ('cancel','draft')
          AND date_order>=%s AND date_order<%s
    """, (fd, td))
    r = rows[0] if rows else {}
    rev = float(r.get('revenue') or 0)
    orders = int(r.get('orders') or 0)
    aov = float(r.get('avg_order') or 0)
    text = (f"For **{lbl}**, total revenue was **{_fmt(rev)}** across **{orders} orders** "
            f"with an average order value of **{_fmt(aov)}**.")
    return {
        'text': text,
        'rows': [{'Metric': 'Revenue', 'Value': _fmt(rev)},
                 {'Metric': 'Orders', 'Value': str(orders)},
                 {'Metric': 'Avg Order', 'Value': _fmt(aov)}],
        'chart_type': None,
        'suggestions': ['Show me top products', 'Compare to last month', 'Revenue by category']
    }


def _orders_summary(q, fd, td, lbl):
    rows = q("""
        SELECT COUNT(DISTINCT id) AS orders,
               ROUND(AVG(amount_total)::numeric,0) AS avg_order,
               DATE(MIN(date_order)) AS first_day,
               DATE(MAX(date_order)) AS last_day
        FROM sale_order
        WHERE state NOT IN ('cancel','draft')
          AND date_order>=%s AND date_order<%s
    """, (fd, td))
    r = rows[0] if rows else {}
    orders = int(r.get('orders') or 0)
    aov = float(r.get('avg_order') or 0)
    text = (f"There were **{orders} orders** in **{lbl}**, "
            f"averaging **{_fmt(aov)}** per order.")
    return {
        'text': text,
        'rows': [{'Metric': 'Orders', 'Value': str(orders)},
                 {'Metric': 'Avg Order Value', 'Value': _fmt(aov)}],
        'chart_type': None,
        'suggestions': ['Show daily order trend', 'Who placed the most orders?', 'Top products']
    }


def _top_products(q, fd, td, lbl):
    rows = q("""
        SELECT pt.name->>'en_US' AS product,
               ROUND(SUM(sol.price_subtotal)::numeric,0) AS revenue,
               SUM(sol.product_uom_qty)::int AS qty,
               COUNT(DISTINCT so.id) AS orders
        FROM sale_order_line sol
        JOIN sale_order so ON so.id=sol.order_id
        JOIN product_product pp ON pp.id=sol.product_id
        JOIN product_template pt ON pt.id=pp.product_tmpl_id
        WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
          AND so.date_order>=%s AND so.date_order<%s
        GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """, (fd, td))
    if not rows:
        return {'text': f"No product sales found for {lbl}.", 'rows': [], 'chart_type': None, 'suggestions': []}
    top = rows[0]
    text = (f"Top seller in **{lbl}** is **{top['product']}** with "
            f"**{_fmt(top['revenue'])}** revenue ({top['qty']} units).")
    return {
        'text': text,
        'rows': [{'Product': r['product'], 'Revenue': _fmt(r['revenue']),
                  'Qty': str(r['qty']), 'Orders': str(r['orders'])} for r in rows],
        'chart_type': 'bar',
        'chart_labels': [r['product'][:20] for r in rows],
        'chart_values': [float(r['revenue']) for r in rows],
        'chart_label': 'Revenue (KSH)',
        'suggestions': ['Revenue by category', 'Top customers', 'Compare to last month']
    }


def _top_customers(q, fd, td, lbl):
    rows = q("""
        SELECT rp.name AS customer,
               COUNT(DISTINCT so.id) AS orders,
               ROUND(SUM(so.amount_total)::numeric,0) AS revenue,
               ROUND(AVG(so.amount_total)::numeric,0) AS avg_order
        FROM sale_order so
        JOIN res_partner rp ON rp.id=so.partner_id
        WHERE so.state NOT IN ('cancel','draft')
          AND so.date_order>=%s AND so.date_order<%s
        GROUP BY 1 ORDER BY 3 DESC LIMIT 10
    """, (fd, td))
    if not rows:
        return {'text': f"No customer data found for {lbl}.", 'rows': [], 'chart_type': None, 'suggestions': []}
    top = rows[0]
    text = (f"Top customer in **{lbl}** is **{top['customer']}** with "
            f"**{_fmt(top['revenue'])}** across {top['orders']} orders.")
    return {
        'text': text,
        'rows': [{'Customer': r['customer'], 'Revenue': _fmt(r['revenue']),
                  'Orders': str(r['orders']), 'Avg Order': _fmt(r['avg_order'])} for r in rows],
        'chart_type': 'bar',
        'chart_labels': [r['customer'][:20] for r in rows],
        'chart_values': [float(r['revenue']) for r in rows],
        'chart_label': 'Revenue (KSH)',
        'suggestions': ['Top products', 'Revenue summary', 'Daily trend']
    }


def _by_category(q, fd, td, lbl):
    rows = q("""
        SELECT pc.name AS category,
               ROUND(SUM(sol.price_subtotal)::numeric,0) AS revenue,
               SUM(sol.product_uom_qty)::int AS qty
        FROM sale_order_line sol
        JOIN sale_order so ON so.id=sol.order_id
        JOIN product_product pp ON pp.id=sol.product_id
        JOIN product_template pt ON pt.id=pp.product_tmpl_id
        JOIN product_category pc ON pc.id=pt.categ_id
        WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
          AND so.date_order>=%s AND so.date_order<%s
        GROUP BY 1 ORDER BY 2 DESC
    """, (fd, td))
    if not rows:
        return {'text': f"No category data for {lbl}.", 'rows': [], 'chart_type': None, 'suggestions': []}
    total = sum(float(r['revenue']) for r in rows)
    top = rows[0]
    text = (f"In **{lbl}**, **{top['category']}** led with **{_fmt(top['revenue'])}** "
            f"({round(float(top['revenue'])/total*100)}% of total). "
            f"Total across all categories: **{_fmt(total)}**.")
    return {
        'text': text,
        'rows': [{'Category': r['category'], 'Revenue': _fmt(r['revenue']),
                  'Share': f"{round(float(r['revenue'])/total*100)}%",
                  'Qty': str(r['qty'])} for r in rows],
        'chart_type': 'doughnut',
        'chart_labels': [r['category'] for r in rows],
        'chart_values': [float(r['revenue']) for r in rows],
        'chart_label': 'Revenue by Category',
        'suggestions': ['Top products in this category', 'Compare to last month', 'Daily trend']
    }


def _daily_trend(q, fd, td, lbl):
    rows = q("""
        SELECT DATE(date_order) AS day,
               ROUND(SUM(amount_total)::numeric,0) AS revenue,
               COUNT(DISTINCT id) AS orders
        FROM sale_order
        WHERE state NOT IN ('cancel','draft')
          AND date_order>=%s AND date_order<%s
        GROUP BY 1 ORDER BY 1
    """, (fd, td))
    if not rows:
        return {'text': f"No daily data for {lbl}.", 'rows': [], 'chart_type': None, 'suggestions': []}
    total = sum(float(r['revenue']) for r in rows)
    peak = max(rows, key=lambda r: float(r['revenue']))
    text = (f"Daily revenue trend for **{lbl}**: {len(rows)} trading days, "
            f"total **{_fmt(total)}**. "
            f"Best day: **{peak['day']}** with **{_fmt(peak['revenue'])}**.")
    return {
        'text': text,
        'rows': [{'Date': str(r['day']), 'Revenue': _fmt(r['revenue']),
                  'Orders': str(r['orders'])} for r in rows],
        'chart_type': 'line',
        'chart_labels': [str(r['day']) for r in rows],
        'chart_values': [float(r['revenue']) for r in rows],
        'chart_label': 'Daily Revenue (KSH)',
        'suggestions': ['Top products this period', 'Revenue summary', 'Compare to last month']
    }


def _compare_periods(q, fd, td, lbl):
    # Compare current vs equivalent prior
    delta = td - fd
    prev_fd = fd - delta
    rows = q("""
        SELECT
          ROUND(SUM(CASE WHEN date_order>=%s AND date_order<%s THEN amount_total ELSE 0 END)::numeric,0) AS curr_rev,
          COUNT(DISTINCT CASE WHEN date_order>=%s AND date_order<%s THEN id END) AS curr_orders,
          ROUND(SUM(CASE WHEN date_order>=%s AND date_order<%s THEN amount_total ELSE 0 END)::numeric,0) AS prev_rev,
          COUNT(DISTINCT CASE WHEN date_order>=%s AND date_order<%s THEN id END) AS prev_orders
        FROM sale_order WHERE state NOT IN ('cancel','draft')
          AND date_order>=%s AND date_order<%s
    """, (fd, td, fd, td, prev_fd, fd, prev_fd, fd, prev_fd, td))
    r = rows[0] if rows else {}
    curr = float(r.get('curr_rev') or 0)
    prev = float(r.get('prev_rev') or 0)
    curr_o = int(r.get('curr_orders') or 0)
    prev_o = int(r.get('prev_orders') or 0)
    pct = round((curr - prev) / prev * 100, 1) if prev else 0
    arrow = '▲' if pct >= 0 else '▼'
    text = (f"**{lbl}** vs prior equivalent period: "
            f"Revenue **{_fmt(curr)}** vs **{_fmt(prev)}** — "
            f"**{arrow} {abs(pct)}%** {'increase' if pct>=0 else 'decrease'}. "
            f"Orders: {curr_o} vs {prev_o}.")
    return {
        'text': text,
        'rows': [
            {'Period': lbl, 'Revenue': _fmt(curr), 'Orders': str(curr_o)},
            {'Period': 'Prior period', 'Revenue': _fmt(prev), 'Orders': str(prev_o)},
        ],
        'chart_type': 'bar',
        'chart_labels': [lbl, 'Prior period'],
        'chart_values': [curr, prev],
        'chart_label': 'Revenue (KSH)',
        'suggestions': ['Show me top products', 'Revenue by category', 'Daily trend']
    }


def _aov(q, fd, td, lbl):
    rows = q("""
        SELECT ROUND(AVG(amount_total)::numeric,0) AS aov,
               ROUND(MIN(amount_total)::numeric,0) AS min_order,
               ROUND(MAX(amount_total)::numeric,0) AS max_order,
               COUNT(DISTINCT id) AS orders
        FROM sale_order
        WHERE state NOT IN ('cancel','draft')
          AND date_order>=%s AND date_order<%s
    """, (fd, td))
    r = rows[0] if rows else {}
    text = (f"Average order value in **{lbl}**: **{_fmt(r.get('aov',0))}** "
            f"across {r.get('orders',0)} orders. "
            f"Range: {_fmt(r.get('min_order',0))} – {_fmt(r.get('max_order',0))}.")
    return {
        'text': text,
        'rows': [{'Metric': 'Avg Order Value', 'Value': _fmt(r.get('aov',0))},
                 {'Metric': 'Min Order', 'Value': _fmt(r.get('min_order',0))},
                 {'Metric': 'Max Order', 'Value': _fmt(r.get('max_order',0))},
                 {'Metric': 'Total Orders', 'Value': str(r.get('orders',0))}],
        'chart_type': None,
        'suggestions': ['Revenue summary', 'Top products', 'Top customers']
    }


def _underperforming(q, fd, td, lbl):
    rows = q("""
        WITH baseline AS (
            SELECT pt.name->>'en_US' AS product,
                   ROUND(SUM(sol.price_subtotal)/90.0*(%s-%s)::numeric,0) AS expected
            FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
            JOIN product_product pp ON pp.id=sol.product_id
            JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order>=CURRENT_DATE-90 AND so.date_order<CURRENT_DATE-7
            GROUP BY 1 HAVING COUNT(so.id)>=3
        ), recent AS (
            SELECT pt.name->>'en_US' AS product,
                   ROUND(SUM(sol.price_subtotal)::numeric,0) AS actual
            FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
            JOIN product_product pp ON pp.id=sol.product_id
            JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order>=%s AND so.date_order<%s GROUP BY 1
        )
        SELECT b.product, b.expected, COALESCE(r.actual,0) AS actual,
               ROUND(100-(COALESCE(r.actual,0)/b.expected*100)::numeric,0) AS drop_pct
        FROM baseline b LEFT JOIN recent r ON r.product=b.product
        WHERE COALESCE(r.actual,0)<b.expected*0.5
        ORDER BY drop_pct DESC LIMIT 10
    """, ((td-fd).days, 0, fd, td))
    if not rows:
        return {'text': f"No underperforming products found for {lbl} — all within normal range.", 'rows': [], 'chart_type': None, 'suggestions': ['Top products', 'Revenue summary']}
    text = f"**{len(rows)} underperforming products** in {lbl} — selling at less than 50% of historical average."
    return {
        'text': text,
        'rows': [{'Product': r['product'], 'Expected': _fmt(r['expected']),
                  'Actual': _fmt(r['actual']), 'Drop': f"-{r['drop_pct']}%"} for r in rows],
        'chart_type': 'bar',
        'chart_labels': [r['product'][:20] for r in rows],
        'chart_values': [float(r['drop_pct']) for r in rows],
        'chart_label': 'Drop %',
        'suggestions': ['Top products', 'Revenue summary', 'Compare to last month']
    }


def _low_stock(q):
    rows = q("""
        SELECT pt.name->>'en_US' AS product,
               ROUND(sq.quantity::numeric,0) AS on_hand,
               COALESCE(pt.reordering_min_qty,0) AS reorder_point
        FROM stock_quant sq
        JOIN stock_location sl ON sl.id=sq.location_id
        JOIN product_product pp ON pp.id=sq.product_id
        JOIN product_template pt ON pt.id=pp.product_tmpl_id
        WHERE sl.usage='internal' AND sq.quantity>=0
          AND sq.quantity<=COALESCE(pt.reordering_min_qty,0)
        ORDER BY sq.quantity ASC LIMIT 10
    """)
    if not rows:
        rows = q("""
            SELECT pt.name->>'en_US' AS product,
                   ROUND(sq.quantity::numeric,0) AS on_hand
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id=sq.location_id
            JOIN product_product pp ON pp.id=sq.product_id
            JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE sl.usage='internal' AND sq.quantity>=0
            ORDER BY sq.quantity ASC LIMIT 10
        """)
        text = "No products below reorder point. Showing lowest stock items:"
    else:
        text = f"**{len(rows)} products** are at or below their reorder point:"
    return {
        'text': text,
        'rows': [{'Product': r['product'], 'On Hand': str(r.get('on_hand', 0))} for r in rows],
        'chart_type': None,
        'suggestions': ['Top products', 'Revenue summary']
    }


def _new_customers(q, fd, td, lbl):
    rows = q("""
        SELECT rp.name AS customer,
               MIN(so.date_order)::date AS first_order,
               COUNT(DISTINCT so.id) AS orders,
               ROUND(SUM(so.amount_total)::numeric,0) AS revenue
        FROM sale_order so JOIN res_partner rp ON rp.id=so.partner_id
        WHERE so.state NOT IN ('cancel','draft')
          AND so.date_order>=%s AND so.date_order<%s
          AND NOT EXISTS (
            SELECT 1 FROM sale_order s2 WHERE s2.partner_id=so.partner_id
              AND s2.state NOT IN ('cancel','draft') AND s2.date_order<%s
          )
        GROUP BY 1 ORDER BY 4 DESC LIMIT 10
    """, (fd, td, fd))
    if not rows:
        return {'text': f"No new customers found in {lbl}.", 'rows': [], 'chart_type': None, 'suggestions': ['Top customers', 'Revenue summary']}
    text = f"**{len(rows)} new customers** acquired in **{lbl}**."
    return {
        'text': text,
        'rows': [{'Customer': r['customer'], 'First Order': str(r['first_order']),
                  'Orders': str(r['orders']), 'Revenue': _fmt(r['revenue'])} for r in rows],
        'chart_type': None,
        'suggestions': ['Top customers', 'Revenue summary']
    }


def _fallback(q, fd, td, lbl):
    rows = q("""
        SELECT COUNT(DISTINCT id) AS orders,
               ROUND(SUM(amount_total)::numeric,0) AS revenue
        FROM sale_order WHERE state NOT IN ('cancel','draft')
          AND date_order>=%s AND date_order<%s
    """, (fd, td))
    r = rows[0] if rows else {}
    return {
        'text': (f"Here's a quick summary for **{lbl}**: "
                 f"**{_fmt(r.get('revenue',0))}** revenue from **{r.get('orders',0)} orders**. "
                 "Try asking me something more specific!"),
        'rows': [],
        'chart_type': None,
        'suggestions': [
            'What were total sales this month?',
            'Show me top 10 products last month',
            'Revenue by category this week',
            'Compare this month vs last month',
            'Which products are underperforming?',
            'Show me the daily revenue trend',
        ]
    }
