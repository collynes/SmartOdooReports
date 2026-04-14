from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response, stream_with_context
from functools import wraps
import psycopg2
import psycopg2.extras
import os, io, json, sys
from datetime import date, timedelta
from dotenv import load_dotenv
from insights import get_daily_insight
from projections import get_projections
from anomalies import detect_anomalies
from expense_tiles import get_smart_tiles
from chat import answer as chat_answer
from agent import stream_agent

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

app = Flask(__name__)
_secret = os.getenv('SECRET_KEY', '')
if not _secret or _secret == 'changeme':
    import secrets
    _secret = secrets.token_hex(32)
    print("⚠️  WARNING: SECRET_KEY not set or is default. Using a random key (sessions won't persist across restarts). Set SECRET_KEY in .env")
app.secret_key = _secret

# Multi-user credentials
APP_USERS = {
    os.getenv('APP_USERNAME', 'admin'): os.getenv('APP_PASSWORD', 'password'),
    'Admin': 'Party@2026!Admin',
}

DB_CONFIG = {
    'host':     os.getenv('DB_HOST', 'localhost'),
    'port':     int(os.getenv('DB_PORT', 5432)),
    'dbname':   os.getenv('DB_NAME', 'odoo18'),
    'user':     os.getenv('DB_USER', 'odoo18'),
    'password': os.getenv('DB_PASSWORD', 'odoo18'),
}

AVAILABLE_ENVS = {
    'production': {'dbname': os.getenv('DB_NAME', 'odoo18'),     'label': 'Production', 'color': '#059669'},
    'dev':        {'dbname': 'odoo18_dev',                        'label': 'Dev',        'color': '#d97706'},
}

# ── In-memory caches ──────────────────────────────────────────
import time as _time
_insight_cache: dict = {'data': None, 'ts': 0, 'env': None}

def get_cached_insight(db_config):
    env_key = db_config.get('dbname', '')
    now = _time.time()
    if (_insight_cache['data'] is None
            or now - _insight_cache['ts'] > 3600
            or _insight_cache['env'] != env_key):
        _insight_cache['data'] = get_daily_insight(db_config)
        _insight_cache['ts'] = now
        _insight_cache['env'] = env_key
    return _insight_cache['data']

# ── DB ────────────────────────────────────────────────────────

def get_active_env():
    return session.get('active_env', 'production')

def get_active_db_config():
    env = get_active_env()
    return {**DB_CONFIG, 'dbname': AVAILABLE_ENVS[env]['dbname']}

def get_db():
    return psycopg2.connect(**get_active_db_config())

def query(sql, params=None):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"DB Error: {e}")
        return []

# ── CLAUDE.md helpers ─────────────────────────────────────────

CLAUDE_MD_PATH = os.path.join(os.path.dirname(__file__), 'CLAUDE.md')

def read_claude_md():
    with open(CLAUDE_MD_PATH, 'r') as f:
        return f.read()

def write_claude_md(content):
    with open(CLAUDE_MD_PATH, 'w') as f:
        f.write(content)

def get_profit_split_from_claude():
    import re
    try:
        content = read_claude_md()
        m = re.search(r'Profit Split:.*?Shop (\d+)%.*?Kemboi (\d+)%.*?Mutai (\d+)%', content, re.IGNORECASE)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
    except Exception:
        pass
    return 33, 33, 34

def get_monthly_target_from_claude():
    """Returns the monthly revenue target (int KES) or 0 if not set."""
    import re
    try:
        content = read_claude_md()
        m = re.search(r'Monthly Revenue Target:.*?KES\s*([\d,]+)', content, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(',', ''))
    except Exception:
        pass
    return 0

def save_monthly_target_to_claude(target: int):
    """Upsert the monthly revenue target line in CLAUDE.md."""
    import re
    content = read_claude_md()
    new_line = f'**Monthly Revenue Target:** KES {target:,}'
    if re.search(r'Monthly Revenue Target:', content, re.IGNORECASE):
        content = re.sub(r'\*\*Monthly Revenue Target:\*\*[^\n]*', new_line, content)
    else:
        content = re.sub(
            r'(\*\*Profit Split:\*\*[^\n]*\n)',
            f'\\1- {new_line}\n',
            content
        )
    write_claude_md(content)

def get_meetings_from_claude():
    import re
    meetings = []
    try:
        content = read_claude_md()
        if '## Meetings' not in content:
            return meetings
        section = content.split('## Meetings', 1)[1]
        if '\n## ' in section:
            section = section.split('\n## ', 1)[0]
        for entry in re.split(r'\n### ', section):
            entry = entry.strip()
            if not entry:
                continue
            lines = entry.split('\n')
            mtg = {'date': '', 'title': '', 'attendees': '', 'notes': ''}
            header = lines[0]
            if ' — ' in header:
                parts = header.split(' — ', 1)
                mtg['date'] = parts[0].strip()
                mtg['title'] = parts[1].strip()
            else:
                mtg['title'] = header.strip()
            for line in lines[1:]:
                line = line.strip()
                if line.startswith('**Attendees:**'):
                    mtg['attendees'] = line.replace('**Attendees:**', '').strip()
                elif line.startswith('**Notes:**'):
                    mtg['notes'] = line.replace('**Notes:**', '').strip()
            meetings.append(mtg)
    except Exception:
        pass
    return meetings

# ── Date filter helpers ───────────────────────────────────────

def get_date_range():
    """Returns (from_date, to_date, label, period) from request args."""
    period = request.args.get('period', session.get('period', 'month'))
    custom_from = request.args.get('from', '')
    custom_to   = request.args.get('to', '')
    today = date.today()

    if custom_from and custom_to:
        try:
            fd = date.fromisoformat(custom_from)
            td = date.fromisoformat(custom_to)
            session['period'] = 'custom'
            session['custom_from'] = custom_from
            session['custom_to']   = custom_to
            return fd, td + timedelta(days=1), f"{fd.strftime('%d %b')} – {td.strftime('%d %b %Y')}", 'custom'
        except Exception:
            pass

    # Restore custom from session if period=custom
    if period == 'custom' and session.get('custom_from') and session.get('custom_to'):
        try:
            fd = date.fromisoformat(session['custom_from'])
            td = date.fromisoformat(session['custom_to'])
            return fd, td + timedelta(days=1), f"{fd.strftime('%d %b')} – {td.strftime('%d %b %Y')}", 'custom'
        except Exception:
            pass
        period = 'month'

    session['period'] = period

    if period == 'today':
        return today, today + timedelta(days=1), 'Today', period
    elif period == 'week':
        start = today - timedelta(days=today.weekday())
        return start, today + timedelta(days=1), 'This Week', period
    elif period == 'month':
        start = today.replace(day=1)
        return start, today + timedelta(days=1), 'This Month', period
    elif period == 'quarter':
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=q_start_month, day=1)
        return start, today + timedelta(days=1), 'This Quarter', period
    elif period == 'year':
        start = today.replace(month=1, day=1)
        return start, today + timedelta(days=1), 'This Year', period
    elif period == 'last30':
        return today - timedelta(days=30), today + timedelta(days=1), 'Last 30 Days', period
    elif period == 'last90':
        return today - timedelta(days=90), today + timedelta(days=1), 'Last 90 Days', period
    else:
        start = today.replace(day=1)
        return start, today + timedelta(days=1), 'This Month', 'month'


def get_compare_range(main_from, main_to, period):
    """
    Given the main period, compute a sensible comparison period.
    cmp param in URL overrides: prev, lastmonth, lastquarter, lastyear, or custom (cmp_from/cmp_to).
    """
    _period_default = {
        'month': 'lastmonth', 'quarter': 'lastquarter', 'year': 'lastyear',
    }
    cmp = request.args.get('cmp', session.get('cmp', _period_default.get(period, 'prev')))
    cmp_from_str = request.args.get('cmp_from', session.get('cmp_from', ''))
    cmp_to_str   = request.args.get('cmp_to',   session.get('cmp_to',   ''))

    session['cmp'] = cmp
    if cmp_from_str:
        session['cmp_from'] = cmp_from_str
    if cmp_to_str:
        session['cmp_to'] = cmp_to_str

    delta = main_to - main_from

    if cmp == 'custom' and cmp_from_str and cmp_to_str:
        try:
            cf = date.fromisoformat(cmp_from_str)
            ct = date.fromisoformat(cmp_to_str)
            return cf, ct + timedelta(days=1), f"{cf.strftime('%d %b')} – {ct.strftime('%d %b %Y')}", cmp
        except Exception:
            pass

    today = date.today()
    if cmp == 'lastmonth':
        start = (main_from.replace(day=1) - timedelta(days=1)).replace(day=1)
        end   = main_from.replace(day=1)
        return start, end, start.strftime('%B %Y'), cmp
    elif cmp == 'lastquarter':
        qm = ((main_from.month - 1) // 3) * 3 + 1
        start = main_from.replace(month=qm, day=1)
        start = (start - timedelta(days=1)).replace(day=1)
        start = date(start.year, ((start.month - 1) // 3) * 3 + 1, 1)
        end   = main_from.replace(month=((main_from.month - 1) // 3) * 3 + 1, day=1)
        return start, end, f"Q{(start.month-1)//3+1} {start.year}", cmp
    elif cmp == 'lastyear':
        start = main_from.replace(year=main_from.year - 1)
        end   = main_to.replace(year=main_to.year - 1) if main_to.year > main_from.year else main_to - timedelta(days=365)
        return start, end, str(main_from.year - 1), cmp
    else:  # prev (default) — equivalent window immediately before
        return main_from - delta, main_from, f"Prev {delta.days}d", cmp


def date_filter_context():
    # Inject active env info for all templates
    env_key = get_active_env()
    env_info = AVAILABLE_ENVS[env_key]
    from_date, to_date, label, period = get_date_range()
    compare = request.args.get('compare', session.get('compare', '0'))
    session['compare'] = compare

    cmp_from, cmp_to, cmp_label, cmp_type = None, None, None, None
    if compare == '1':
        cmp_from, cmp_to, cmp_label, cmp_type = get_compare_range(from_date, to_date, period)

    return {
        'filter_from':    from_date,
        'filter_to':      to_date - timedelta(days=1),
        'filter_label':   label,
        'filter_period':  period,
        'custom_from':    session.get('custom_from', ''),
        'custom_to':      session.get('custom_to', ''),
        'compare':        compare,
        'cmp_from':       cmp_from,
        'cmp_to':         cmp_to - timedelta(days=1) if cmp_to else None,
        'cmp_label':      cmp_label,
        'cmp_type':       cmp_type,
        'cmp_raw_to':     cmp_to,   # exclusive end for SQL
        'active_env':     env_key,
        'env_label':      env_info['label'],
        'env_color':      env_info['color'],
        'env_options':    AVAILABLE_ENVS,
    }

# ── Auth ──────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('username') in APP_USERS and request.form.get('password') == APP_USERS[request.form.get('username')]:
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        error = 'Invalid username or password'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/switch-env/<env>')
@login_required
def switch_env(env):
    if env in AVAILABLE_ENVS:
        session['active_env'] = env
    return redirect(request.referrer or url_for('dashboard'))

# ── Dashboard ─────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']

    kpis = query("""
        SELECT
            COUNT(DISTINCT so.id)         AS orders,
            COALESCE(SUM(so.amount_total), 0) AS revenue,
            COALESCE(AVG(so.amount_total), 0) AS avg_order
        FROM sale_order so
        WHERE so.state NOT IN ('cancel','draft')
          AND so.date_order >= %s AND so.date_order < %s
    """, (fd, td))
    kpi = kpis[0] if kpis else {}

    # Previous period for comparison
    delta = td - fd
    prev_fd = fd - delta
    prev_kpis = query("""
        SELECT COALESCE(SUM(so.amount_total), 0) AS revenue,
               COUNT(DISTINCT so.id) AS orders
        FROM sale_order so
        WHERE so.state NOT IN ('cancel','draft')
          AND so.date_order >= %s AND so.date_order < %s
    """, (prev_fd, fd))
    prev = prev_kpis[0] if prev_kpis else {}

    # Top 5 products
    top5 = query("""
        SELECT pt.name->>'en_US' AS product,
               ROUND(SUM(sol.price_subtotal)::numeric, 0) AS revenue,
               SUM(sol.product_uom_qty) AS qty
        FROM sale_order_line sol
        JOIN sale_order so       ON so.id  = sol.order_id
        JOIN product_product pp  ON pp.id  = sol.product_id
        JOIN product_template pt ON pt.id  = pp.product_tmpl_id
        WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
          AND so.date_order >= %s AND so.date_order < %s
        GROUP BY 1 ORDER BY 2 DESC LIMIT 5
    """, (fd, td))

    insight = get_cached_insight(get_active_db_config())
    prev_rev = float(prev.get('revenue', 0))
    prev_ord = int(prev.get('orders', 1)) or 1
    prev_avg = prev_rev / prev_ord

    # Monthly revenue target tracking
    monthly_target = get_monthly_target_from_claude()
    today_d = date.today()
    month_start = today_d.replace(day=1)
    mtd_rows = query("""
        SELECT COALESCE(SUM(so.amount_total), 0) AS revenue
        FROM sale_order so
        WHERE so.state NOT IN ('cancel','draft')
          AND so.date_order >= %s AND so.date_order < %s
    """, (month_start, today_d + timedelta(days=1)))
    mtd_revenue = float(mtd_rows[0]['revenue'] if mtd_rows else 0)

    return render_template('dashboard.html', kpi=kpi, prev=prev, prev_avg=prev_avg, top5=top5,
                           insight=insight, monthly_target=monthly_target, mtd_revenue=mtd_revenue,
                           month_label=today_d.strftime('%B %Y'), **ctx)

# ── Products (combined: Best + Momentum + Sleeping) ──────────

@app.route('/products')
@login_required
def products():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']
    delta = td - fd
    prev_fd = fd - delta
    days = max(delta.days, 1)
    lookback = max(delta.days * 3, 90)

    # Best products
    best_rows = query("""
        WITH s AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.product_uom_qty)  AS qty,
                   SUM(sol.price_subtotal)   AS revenue,
                   AVG(sol.price_unit)       AS avg_price,
                   MAX(SUM(sol.product_uom_qty)) OVER() AS max_qty,
                   MAX(SUM(sol.price_subtotal))  OVER() AS max_rev
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id  = sol.order_id
            JOIN product_product pp  ON pp.id  = sol.product_id
            JOIN product_template pt ON pt.id  = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY 1
        )
        SELECT ROW_NUMBER() OVER (ORDER BY (qty/max_qty + revenue/max_rev) DESC) AS rank,
               product, qty, revenue, avg_price,
               ROUND((qty/max_qty + revenue/max_rev)::numeric * 100, 1) AS score
        FROM s ORDER BY score DESC LIMIT 25
    """, (fd, td))

    cmp_rows = {}

    # Momentum
    mom_rows = query("""
        WITH prev AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.price_subtotal) AS revenue, SUM(sol.product_uom_qty) AS qty
            FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
            JOIN product_product pp ON pp.id=sol.product_id JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s GROUP BY 1
        ), curr AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.price_subtotal) AS revenue, SUM(sol.product_uom_qty) AS qty
            FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
            JOIN product_product pp ON pp.id=sol.product_id JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s GROUP BY 1
        )
        SELECT c.product,
               ROUND(p.revenue::numeric,0) AS prev_rev, ROUND(c.revenue::numeric,0) AS curr_rev,
               ROUND((c.revenue/NULLIF(p.revenue,0)-1)*100) AS rev_chg,
               ROUND((c.qty/NULLIF(p.qty,0)-1)*100) AS qty_chg,
               ROUND(((c.revenue/NULLIF(p.revenue,0))+(c.qty/NULLIF(p.qty,0)))/2*100-100) AS score
        FROM curr c JOIN prev p ON p.product=c.product
        WHERE c.revenue>p.revenue AND c.qty>p.qty AND p.revenue>0 AND p.qty>0
        ORDER BY score DESC LIMIT 15
    """, (prev_fd, fd, fd, td))

    # Sleeping giants
    sleep_rows = query("""
        WITH peak AS (
            SELECT pt.name->>'en_US' AS product, SUM(sol.price_subtotal) AS peak_rev
            FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
            JOIN product_product pp ON pp.id=sol.product_id JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= CURRENT_DATE - %s AND so.date_order < %s
            GROUP BY 1 ORDER BY peak_rev DESC LIMIT 30
        ), recent AS (
            SELECT pt.name->>'en_US' AS product, SUM(sol.price_subtotal) AS recent_rev
            FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
            JOIN product_product pp ON pp.id=sol.product_id JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s GROUP BY 1
        )
        SELECT p.product, ROUND(p.peak_rev::numeric,0) AS peak_rev,
               ROUND(COALESCE(r.recent_rev,0)::numeric,0) AS recent_rev,
               ROUND((1-COALESCE(r.recent_rev,0)/NULLIF(p.peak_rev,0))*100) AS decline_pct,
               CASE WHEN COALESCE(r.recent_rev,0)=0 THEN 'GONE SILENT'
                    WHEN COALESCE(r.recent_rev,0)<p.peak_rev*0.3 THEN 'CRITICAL DROP'
                    ELSE 'DECLINING' END AS status
        FROM peak p LEFT JOIN recent r ON r.product=p.product
        WHERE COALESCE(r.recent_rev,0)<p.peak_rev*0.7
        ORDER BY decline_pct DESC
    """, (lookback, fd, fd, td))

    # Underperforming
    under_rows = query("""
        WITH baseline AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.price_subtotal) / 30.0 AS daily_avg_rev
            FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
            JOIN product_product pp ON pp.id=sol.product_id JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= CURRENT_DATE - 90 AND so.date_order < CURRENT_DATE - 7
            GROUP BY 1 HAVING COUNT(so.id) >= 5
        ), recent AS (
            SELECT pt.name->>'en_US' AS product, SUM(sol.price_subtotal) AS rev_period
            FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
            JOIN product_product pp ON pp.id=sol.product_id JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s GROUP BY 1
        )
        SELECT b.product,
               ROUND(b.daily_avg_rev * %s, 2) AS expected_rev,
               ROUND(COALESCE(r.rev_period, 0), 2) AS actual_rev,
               ROUND((1 - COALESCE(r.rev_period,0) / NULLIF(b.daily_avg_rev * %s, 0)) * 100) AS drop_pct,
               CASE WHEN COALESCE(r.rev_period,0) = 0 THEN 'NO SALES' ELSE 'SLOW' END AS status
        FROM baseline b LEFT JOIN recent r ON r.product = b.product
        WHERE COALESCE(r.rev_period,0) < b.daily_avg_rev * %s * 0.5
        ORDER BY drop_pct DESC LIMIT 25
    """, (fd, td, days, days, days))

    return render_template('products.html',
        best_rows=best_rows,
        mom_rows=mom_rows, sleep_rows=sleep_rows, under_rows=under_rows,
        **ctx)

# ── Product Search & Profile APIs ─────────────────────────────

def _jsonify_rows(rows):
    """Convert psycopg2 RealDictRow list to JSON-safe list."""
    import decimal
    def _v(v):
        if isinstance(v, decimal.Decimal): return float(v)
        if hasattr(v, 'isoformat'): return v.isoformat()
        return v
    return [{k: _v(v) for k, v in dict(r).items()} for r in rows]

@app.route('/api/product-search')
@login_required
def api_product_search():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    rows = query("""
        SELECT pp.id,
               pp.default_code AS ref,
               pt.name->>'en_US' AS name,
               ROUND(COALESCE(SUM(sq.quantity), 0)::numeric, 0) AS stock
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN stock_quant sq ON sq.product_id = pp.id
          AND sq.location_id IN (SELECT id FROM stock_location WHERE usage = 'internal')
        WHERE pt.active = true AND pp.active = true
          AND (pt.name->>'en_US' ILIKE %s OR pp.default_code ILIKE %s)
        GROUP BY pp.id, pp.default_code, pt.name
        ORDER BY pt.name->>'en_US' LIMIT 15
    """, (f'%{q}%', f'%{q}%'))
    return jsonify(_jsonify_rows(rows))

@app.route('/api/product-profile')
@login_required
def api_product_profile():
    pid = request.args.get('id', '')
    if not pid:
        return jsonify({})

    info = query("""
        SELECT pp.id, pp.default_code AS ref,
               pt.name->>'en_US' AS name,
               ROUND(COALESCE(SUM(sq.quantity), 0)::numeric, 0) AS stock,
               ROUND(COALESCE((pp.standard_price->>'1')::numeric, 0)::numeric, 2) AS cost
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN stock_quant sq ON sq.product_id = pp.id
          AND sq.location_id IN (SELECT id FROM stock_location WHERE usage = 'internal')
        WHERE pp.id = %s
        GROUP BY pp.id, pp.default_code, pt.name
    """, (pid,))
    if not info:
        return jsonify({})
    product = _jsonify_rows(info)[0]

    # All-time totals
    totals = query("""
        SELECT ROUND(SUM(sol.price_subtotal)::numeric, 0) AS total_rev,
               ROUND(SUM(sol.product_uom_qty)::numeric, 0) AS total_qty,
               COUNT(DISTINCT sol.order_id) AS order_count,
               ROUND(AVG(sol.price_unit)::numeric, 2) AS avg_price
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        WHERE sol.product_id = %s AND so.state NOT IN ('cancel','draft')
          AND sol.display_type IS NULL
    """, (pid,))
    if totals: product.update(_jsonify_rows(totals)[0])

    # This year
    yr = query("""
        SELECT ROUND(SUM(sol.price_subtotal)::numeric, 0) AS year_rev,
               ROUND(SUM(sol.product_uom_qty)::numeric, 0) AS year_qty
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        WHERE sol.product_id = %s AND so.state NOT IN ('cancel','draft')
          AND sol.display_type IS NULL
          AND EXTRACT(YEAR FROM so.date_order) = EXTRACT(YEAR FROM CURRENT_DATE)
    """, (pid,))
    if yr: product.update(_jsonify_rows(yr)[0])

    # Monthly history (last 24 months)
    monthly = query("""
        SELECT TO_CHAR(DATE_TRUNC('month', so.date_order), 'Mon YYYY') AS month,
               TO_CHAR(DATE_TRUNC('month', so.date_order), 'YYYY-MM') AS month_key,
               ROUND(SUM(sol.price_subtotal)::numeric, 0) AS revenue,
               ROUND(SUM(sol.product_uom_qty)::numeric, 0) AS qty
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        WHERE sol.product_id = %s AND so.state NOT IN ('cancel','draft')
          AND sol.display_type IS NULL
          AND so.date_order >= CURRENT_DATE - INTERVAL '24 months'
        GROUP BY DATE_TRUNC('month', so.date_order)
        ORDER BY DATE_TRUNC('month', so.date_order)
    """, (pid,))
    product['monthly'] = _jsonify_rows(monthly)

    # Best / worst month (all time)
    all_monthly = query("""
        SELECT TO_CHAR(DATE_TRUNC('month', so.date_order), 'Mon YYYY') AS month,
               ROUND(SUM(sol.price_subtotal)::numeric, 0) AS revenue,
               ROUND(SUM(sol.product_uom_qty)::numeric, 0) AS qty
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        WHERE sol.product_id = %s AND so.state NOT IN ('cancel','draft')
          AND sol.display_type IS NULL
        GROUP BY DATE_TRUNC('month', so.date_order)
        ORDER BY SUM(sol.price_subtotal) DESC
    """, (pid,))
    am = _jsonify_rows(all_monthly)
    product['best_month']  = am[0]  if am else None
    product['worst_month'] = am[-1] if len(am) > 1 else None

    # Avg monthly revenue (last 12 months)
    avg_m = query("""
        SELECT ROUND(AVG(monthly_rev)::numeric, 0) AS avg_monthly_rev
        FROM (
            SELECT DATE_TRUNC('month', so.date_order) AS m,
                   SUM(sol.price_subtotal) AS monthly_rev
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            WHERE sol.product_id = %s AND so.state NOT IN ('cancel','draft')
              AND sol.display_type IS NULL
              AND so.date_order >= CURRENT_DATE - INTERVAL '12 months'
            GROUP BY 1
        ) sub
    """, (pid,))
    if avg_m: product.update(_jsonify_rows(avg_m)[0])

    # Last restock
    restock = query("""
        SELECT sm.date::date AS restock_date,
               ROUND(sm.product_uom_qty::numeric, 0) AS qty,
               sp.name AS reference
        FROM stock_move sm
        JOIN stock_location sl_dest ON sl_dest.id = sm.location_dest_id
        LEFT JOIN stock_picking sp ON sp.id = sm.picking_id
        WHERE sm.product_id = %s AND sm.state = 'done'
          AND sl_dest.usage = 'internal'
        ORDER BY sm.date DESC LIMIT 1
    """, (pid,))
    product['last_restock'] = _jsonify_rows(restock)[0] if restock else None

    # Period comparison
    p1_from = request.args.get('p1_from')
    p1_to   = request.args.get('p1_to')
    p2_from = request.args.get('p2_from')
    p2_to   = request.args.get('p2_to')

    def _period_stats(pid, d_from, d_to):
        r = query("""
            SELECT ROUND(SUM(sol.price_subtotal)::numeric, 0) AS revenue,
                   ROUND(SUM(sol.product_uom_qty)::numeric, 0) AS qty,
                   COUNT(DISTINCT sol.order_id) AS orders,
                   ROUND(AVG(sol.price_unit)::numeric, 2) AS avg_price
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            WHERE sol.product_id = %s AND so.state NOT IN ('cancel','draft')
              AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s
        """, (pid, d_from, d_to))
        return _jsonify_rows(r)[0] if r else {}

    if p1_from and p1_to:
        product['period1'] = _period_stats(pid, p1_from, p1_to)
    if p2_from and p2_to:
        product['period2'] = _period_stats(pid, p2_from, p2_to)

    return jsonify(product)

# ── Best Products ─────────────────────────────────────────────

@app.route('/best')
@login_required
def best_products():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']

    rows = query("""
        WITH s AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.product_uom_qty)  AS qty,
                   SUM(sol.price_subtotal)   AS revenue,
                   AVG(sol.price_unit)       AS avg_price,
                   MAX(SUM(sol.product_uom_qty)) OVER() AS max_qty,
                   MAX(SUM(sol.price_subtotal))  OVER() AS max_rev
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id  = sol.order_id
            JOIN product_product pp  ON pp.id  = sol.product_id
            JOIN product_template pt ON pt.id  = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY 1
        )
        SELECT ROW_NUMBER() OVER (ORDER BY (qty/max_qty + revenue/max_rev) DESC) AS rank,
               product, qty, revenue, avg_price,
               ROUND((qty/max_qty + revenue/max_rev)::numeric * 100, 1) AS score
        FROM s ORDER BY score DESC LIMIT 25
    """, (fd, td))

    # Compare period
    cmp_rows = {}
    if ctx.get('compare') == '1' and ctx.get('cmp_from'):
        cfd, ctd = ctx['cmp_from'], ctx['cmp_raw_to']
        cmp_data = query("""
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.product_uom_qty) AS qty,
                   SUM(sol.price_subtotal)  AS revenue
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id  = sol.order_id
            JOIN product_product pp  ON pp.id  = sol.product_id
            JOIN product_template pt ON pt.id  = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY 1
        """, (cfd, ctd))
        cmp_rows = {r['product']: r for r in cmp_data}

    return render_template('best.html', rows=rows, cmp_rows=cmp_rows, show_compare=False, **ctx)

# ── Underperforming ───────────────────────────────────────────

@app.route('/underperforming')
@login_required
def underperforming():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']
    days = max((td - fd).days, 1)

    rows = query("""
        WITH baseline AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.price_subtotal) / 30.0 AS daily_avg_rev,
                   COUNT(so.id) AS sale_count
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id  = sol.order_id
            JOIN product_product pp  ON pp.id  = sol.product_id
            JOIN product_template pt ON pt.id  = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= CURRENT_DATE - 90
              AND so.date_order <  CURRENT_DATE - 7
            GROUP BY 1 HAVING COUNT(so.id) >= 5
        ),
        recent AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.price_subtotal) AS rev_period
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id  = sol.order_id
            JOIN product_product pp  ON pp.id  = sol.product_id
            JOIN product_template pt ON pt.id  = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY 1
        )
        SELECT b.product,
               ROUND(b.daily_avg_rev * %s, 2)              AS expected_rev,
               ROUND(COALESCE(r.rev_period, 0), 2)         AS actual_rev,
               ROUND((1 - COALESCE(r.rev_period,0) / NULLIF(b.daily_avg_rev * %s, 0)) * 100) AS drop_pct,
               CASE WHEN COALESCE(r.rev_period,0) = 0 THEN 'NO SALES' ELSE 'SLOW' END AS status
        FROM baseline b
        LEFT JOIN recent r ON r.product = b.product
        WHERE COALESCE(r.rev_period,0) < b.daily_avg_rev * %s * 0.5
        ORDER BY drop_pct DESC LIMIT 25
    """, (fd, td, days, days, days))

    return render_template('underperforming.html', rows=rows, cmp_rows={}, show_compare=False, **ctx)

# ── Momentum ──────────────────────────────────────────────────

@app.route('/momentum')
@login_required
def momentum():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']
    delta = td - fd
    prev_fd = fd - delta

    rows = query("""
        WITH prev AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.price_subtotal)  AS revenue,
                   SUM(sol.product_uom_qty) AS qty
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id  = sol.order_id
            JOIN product_product pp  ON pp.id  = sol.product_id
            JOIN product_template pt ON pt.id  = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY 1
        ),
        curr AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.price_subtotal)  AS revenue,
                   SUM(sol.product_uom_qty) AS qty
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id  = sol.order_id
            JOIN product_product pp  ON pp.id  = sol.product_id
            JOIN product_template pt ON pt.id  = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY 1
        )
        SELECT c.product,
               ROUND(p.revenue::numeric, 0) AS prev_rev,
               ROUND(c.revenue::numeric, 0) AS curr_rev,
               ROUND((c.revenue / NULLIF(p.revenue,0) - 1) * 100) AS rev_chg,
               ROUND((c.qty     / NULLIF(p.qty,    0) - 1) * 100) AS qty_chg,
               ROUND(((c.revenue/NULLIF(p.revenue,0)) + (c.qty/NULLIF(p.qty,0)))/2*100-100) AS score
        FROM curr c
        JOIN prev p ON p.product = c.product
        WHERE c.revenue > p.revenue AND c.qty > p.qty AND p.revenue > 0 AND p.qty > 0
        ORDER BY score DESC LIMIT 15
    """, (prev_fd, fd, fd, td))

    return render_template('momentum.html', rows=rows, cmp_rows={}, show_compare=False, **ctx)

# ── Sleeping Giants ───────────────────────────────────────────

@app.route('/sleeping')
@login_required
def sleeping():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']
    delta = (td - fd).days
    lookback = max(delta * 3, 90)

    rows = query("""
        WITH peak AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.price_subtotal) AS peak_rev
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id  = sol.order_id
            JOIN product_product pp  ON pp.id  = sol.product_id
            JOIN product_template pt ON pt.id  = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= CURRENT_DATE - %s
              AND so.date_order <  %s
            GROUP BY 1 ORDER BY peak_rev DESC LIMIT 30
        ),
        recent AS (
            SELECT pt.name->>'en_US' AS product,
                   SUM(sol.price_subtotal) AS recent_rev
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id  = sol.order_id
            JOIN product_product pp  ON pp.id  = sol.product_id
            JOIN product_template pt ON pt.id  = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY 1
        )
        SELECT p.product,
               ROUND(p.peak_rev::numeric, 0) AS peak_rev,
               ROUND(COALESCE(r.recent_rev, 0)::numeric, 0) AS recent_rev,
               ROUND((1 - COALESCE(r.recent_rev,0)/NULLIF(p.peak_rev,0))*100) AS decline_pct,
               CASE
                 WHEN COALESCE(r.recent_rev,0) = 0 THEN 'GONE SILENT'
                 WHEN COALESCE(r.recent_rev,0) < p.peak_rev * 0.3 THEN 'CRITICAL DROP'
                 ELSE 'DECLINING'
               END AS status
        FROM peak p
        LEFT JOIN recent r ON r.product = p.product
        WHERE COALESCE(r.recent_rev,0) < p.peak_rev * 0.7
        ORDER BY decline_pct DESC
    """, (lookback, fd, fd, td))

    return render_template('sleeping.html', rows=rows, show_compare=False, **ctx)

# ── Cross-sell ────────────────────────────────────────────────

@app.route('/crosssell')
@login_required
def crosssell():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']

    top3 = query("""
        SELECT pt.name->>'en_US' AS product
        FROM sale_order_line sol
        JOIN sale_order so       ON so.id  = sol.order_id
        JOIN product_product pp  ON pp.id  = sol.product_id
        JOIN product_template pt ON pt.id  = pp.product_tmpl_id
        WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
          AND so.date_order >= %s AND so.date_order < %s
        GROUP BY 1 ORDER BY SUM(sol.price_subtotal) DESC LIMIT 5
    """, (fd, td))

    results = []
    for item in top3:
        anchor = item['product']
        pairs = query("""
            WITH anchor_orders AS (
                SELECT so.id AS order_id
                FROM sale_order_line sol
                JOIN sale_order so       ON so.id  = sol.order_id
                JOIN product_product pp  ON pp.id  = sol.product_id
                JOIN product_template pt ON pt.id  = pp.product_tmpl_id
                WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
                  AND pt.name->>'en_US' = %s
                  AND so.date_order >= %s AND so.date_order < %s
            )
            SELECT pt2.name->>'en_US' AS paired_product,
                   COUNT(DISTINCT ao.order_id) AS co_orders,
                   ROUND(SUM(sol2.price_subtotal)::numeric, 0) AS pair_rev,
                   CASE
                     WHEN COUNT(DISTINCT ao.order_id) >= 20 THEN 'Bundle'
                     WHEN COUNT(DISTINCT ao.order_id) >= 10 THEN 'Promote Together'
                     ELSE 'Worth Testing'
                   END AS opportunity
            FROM anchor_orders ao
            JOIN sale_order_line sol2 ON sol2.order_id = ao.order_id
            JOIN product_product pp2  ON pp2.id = sol2.product_id
            JOIN product_template pt2 ON pt2.id = pp2.product_tmpl_id
            WHERE pt2.name->>'en_US' != %s AND sol2.display_type IS NULL
            GROUP BY 1 ORDER BY co_orders DESC LIMIT 6
        """, (anchor, fd, td, anchor))
        results.append({'anchor': anchor, 'pairs': pairs})

    return render_template('crosssell.html', results=results, show_compare=False, **ctx)

# ── Reorder Alerts ────────────────────────────────────────────

@app.route('/reorder')
@login_required
def reorder():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']
    days = max((td - fd).days, 1)

    rows = query("""
        WITH on_hand AS (
            SELECT pp.id AS product_id,
                   SUM(sq.quantity - sq.reserved_quantity) AS available
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            JOIN product_product pp ON pp.id = sq.product_id
            WHERE sl.usage = 'internal'
            GROUP BY pp.id
        ),
        velocity AS (
            SELECT sol.product_id,
                   SUM(sol.product_uom_qty) / %s AS daily_qty
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY sol.product_id
        )
        SELECT pt.name->>'en_US' AS product,
               ROUND(COALESCE(oh.available, 0)::numeric, 0) AS available,
               ROUND(v.daily_qty::numeric, 2) AS daily_sales,
               CASE
                 WHEN v.daily_qty > 0 THEN ROUND(COALESCE(oh.available,0) / v.daily_qty)
                 ELSE NULL
               END AS days_left,
               ROUND(v.daily_qty * 30) AS projected_30d_demand,
               ROUND(v.daily_qty * 60) AS suggested_order_qty,
               ROUND(COALESCE((pp.standard_price->>'1')::numeric, 0)::numeric, 0) AS unit_cost,
               ROUND((ROUND(v.daily_qty * 60) * COALESCE((pp.standard_price->>'1')::numeric, 0))::numeric, 0) AS reorder_cost,
               CASE
                 WHEN v.daily_qty > 0 AND COALESCE(oh.available,0) / v.daily_qty <= 7  THEN 'CRITICAL'
                 WHEN v.daily_qty > 0 AND COALESCE(oh.available,0) / v.daily_qty <= 14 THEN 'ORDER SOON'
                 WHEN v.daily_qty > 0 AND COALESCE(oh.available,0) / v.daily_qty <= 30 THEN 'WATCH'
                 ELSE 'OK'
               END AS alert
        FROM velocity v
        JOIN product_product pp  ON pp.id = v.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN on_hand oh ON oh.product_id = v.product_id
        WHERE v.daily_qty > 0
          AND (COALESCE(oh.available,0) / NULLIF(v.daily_qty,0)) <= 30
        ORDER BY days_left ASC NULLS LAST
        LIMIT 30
    """, (days, fd, td))

    return render_template('reorder.html', rows=rows, show_compare=False, **ctx)

# ── Dead Stock ────────────────────────────────────────────────

@app.route('/deadstock')
@login_required
def deadstock():
    ctx = date_filter_context()

    rows = query("""
        WITH on_hand AS (
            SELECT pp.id AS product_id,
                   SUM(sq.quantity) AS qty,
                   SUM(sq.quantity * COALESCE((pp.standard_price->>'1')::numeric, 0)) AS stock_value
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            JOIN product_product pp ON pp.id = sq.product_id
            WHERE sl.usage = 'internal' AND sq.quantity > 0
            GROUP BY pp.id
        ),
        last_sale AS (
            SELECT sol.product_id,
                   MAX(so.date_order) AS last_sold_at,
                   SUM(sol.price_subtotal) AS total_rev
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
            GROUP BY sol.product_id
        )
        SELECT pt.name->>'en_US' AS product,
               ROUND(oh.qty::numeric, 0) AS qty_on_hand,
               ROUND(oh.stock_value::numeric, 0) AS stock_value,
               ls.last_sold_at::date AS last_sale_date,
               (CURRENT_DATE - ls.last_sold_at::date) AS days_since_sale,
               CASE
                 WHEN ls.last_sold_at IS NULL THEN 'NEVER SOLD'
                 WHEN (CURRENT_DATE - ls.last_sold_at::date) > 90 THEN 'DEAD (90+ days)'
                 WHEN (CURRENT_DATE - ls.last_sold_at::date) > 60 THEN 'STALE (60+ days)'
                 ELSE 'SLOW (30+ days)'
               END AS status
        FROM on_hand oh
        JOIN product_product pp  ON pp.id = oh.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN last_sale ls   ON ls.product_id = oh.product_id
        WHERE oh.qty > 0
          AND (ls.last_sold_at IS NULL OR (CURRENT_DATE - ls.last_sold_at::date) >= 30)
        ORDER BY days_since_sale DESC NULLS FIRST
        LIMIT 30
    """)

    return render_template('deadstock.html', rows=rows, show_compare=False, **ctx)

# ── Top Customers ─────────────────────────────────────────────

@app.route('/customers')
@login_required
def customers():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']

    rows = query("""
        SELECT rp.name AS customer,
               COUNT(DISTINCT so.id) AS orders,
               ROUND(SUM(so.amount_total)::numeric, 0) AS revenue,
               ROUND(AVG(so.amount_total)::numeric, 0) AS avg_order,
               MAX(so.date_order)::date AS last_order,
               (CURRENT_DATE - MAX(so.date_order)::date) AS days_since
        FROM sale_order so
        JOIN res_partner rp ON rp.id = so.partner_id
        WHERE so.state NOT IN ('cancel','draft')
          AND so.date_order >= %s AND so.date_order < %s
        GROUP BY rp.name
        ORDER BY revenue DESC
        LIMIT 30
    """, (fd, td))

    # Compare
    cmp_rows = {}
    if ctx.get('compare') == '1' and ctx.get('cmp_from'):
        cfd, ctd = ctx['cmp_from'], ctx['cmp_raw_to']
        cmp_data = query("""
            SELECT rp.name AS customer,
                   COUNT(DISTINCT so.id) AS orders,
                   ROUND(SUM(so.amount_total)::numeric, 0) AS revenue
            FROM sale_order so
            JOIN res_partner rp ON rp.id = so.partner_id
            WHERE so.state NOT IN ('cancel','draft')
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY rp.name
        """, (cfd, ctd))
        cmp_rows = {r['customer']: r for r in cmp_data}

    return render_template('customers.html', rows=rows, cmp_rows=cmp_rows, show_compare=False, **ctx)

# ── Revenue Trend (full page) ─────────────────────────────────

@app.route('/trend')
@login_required
def trend():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']

    daily = query("""
        SELECT DATE(so.date_order) AS day,
               COUNT(DISTINCT so.id) AS orders,
               ROUND(SUM(so.amount_total)::numeric, 0) AS revenue
        FROM sale_order so
        WHERE so.state NOT IN ('cancel','draft')
          AND so.date_order >= %s AND so.date_order < %s
        GROUP BY 1 ORDER BY 1
    """, (fd, td))

    return render_template('trend.html', daily=daily, show_compare=False, **ctx)

# ── Compare ───────────────────────────────────────────────────

@app.route('/compare')
@login_required
def compare():
    """Side-by-side period comparison for all key metrics."""
    def period_sql(fd, td):
        return query("""
            SELECT
                COUNT(DISTINCT so.id)                        AS orders,
                COALESCE(SUM(so.amount_total), 0)            AS revenue,
                COALESCE(AVG(so.amount_total), 0)            AS avg_order,
                COUNT(DISTINCT so.partner_id)                AS customers
            FROM sale_order so
            WHERE so.state NOT IN ('cancel','draft')
              AND so.date_order >= %s AND so.date_order < %s
        """, (fd, td))

    def top_products_sql(fd, td):
        return query("""
            SELECT pt.name->>'en_US' AS product,
                   ROUND(SUM(sol.price_subtotal)::numeric, 0) AS revenue,
                   SUM(sol.product_uom_qty) AS qty
            FROM sale_order_line sol
            JOIN sale_order so       ON so.id  = sol.order_id
            JOIN product_product pp  ON pp.id  = sol.product_id
            JOIN product_template pt ON pt.id  = pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY 1 ORDER BY revenue DESC LIMIT 10
        """, (fd, td))

    def daily_sql(fd, td):
        return query("""
            SELECT DATE(so.date_order) AS day,
                   ROUND(SUM(so.amount_total)::numeric, 0) AS revenue
            FROM sale_order so
            WHERE so.state NOT IN ('cancel','draft')
              AND so.date_order >= %s AND so.date_order < %s
            GROUP BY 1 ORDER BY 1
        """, (fd, td))

    # Period A — from URL params a_period / a_from / a_to
    a_period  = request.args.get('a_period', 'month')
    b_period  = request.args.get('b_period', 'lastmonth')
    a_from_s  = request.args.get('a_from', '')
    a_to_s    = request.args.get('a_to', '')
    b_from_s  = request.args.get('b_from', '')
    b_to_s    = request.args.get('b_to', '')
    today     = date.today()

    def resolve(period, from_s, to_s):
        if from_s and to_s:
            try:
                return date.fromisoformat(from_s), date.fromisoformat(to_s) + timedelta(days=1)
            except Exception:
                pass
        if period == 'today':
            return today, today + timedelta(days=1)
        elif period == 'yesterday':
            return today - timedelta(days=1), today
        elif period == 'week':
            s = today - timedelta(days=today.weekday())
            return s, today + timedelta(days=1)
        elif period == 'lastweek':
            s = today - timedelta(days=today.weekday() + 7)
            return s, s + timedelta(days=7)
        elif period == 'month':
            return today.replace(day=1), today + timedelta(days=1)
        elif period == 'lastmonth':
            end = today.replace(day=1)
            start = (end - timedelta(days=1)).replace(day=1)
            return start, end
        elif period == 'quarter':
            qm = ((today.month - 1) // 3) * 3 + 1
            return today.replace(month=qm, day=1), today + timedelta(days=1)
        elif period == 'lastquarter':
            qm = ((today.month - 1) // 3) * 3 + 1
            end = today.replace(month=qm, day=1)
            sq = (end - timedelta(days=1))
            start = date(sq.year, ((sq.month - 1) // 3) * 3 + 1, 1)
            return start, end
        elif period == 'year':
            return today.replace(month=1, day=1), today + timedelta(days=1)
        elif period == 'lastyear':
            return date(today.year - 1, 1, 1), date(today.year, 1, 1)
        elif period == 'last30':
            return today - timedelta(days=30), today + timedelta(days=1)
        elif period == 'last90':
            return today - timedelta(days=90), today + timedelta(days=1)
        return today.replace(day=1), today + timedelta(days=1)

    a_fd, a_td = resolve(a_period, a_from_s, a_to_s)
    b_fd, b_td = resolve(b_period, b_from_s, b_to_s)

    a_kpi  = period_sql(a_fd, a_td)
    b_kpi  = period_sql(b_fd, b_td)
    a_prod = top_products_sql(a_fd, a_td)
    b_prod = top_products_sql(b_fd, b_td)
    a_daily = daily_sql(a_fd, a_td)
    b_daily = daily_sql(b_fd, b_td)

    def label(period, from_s, to_s, fd, td):
        if from_s and to_s:
            return f"{fd.strftime('%d %b')} – {(td-timedelta(days=1)).strftime('%d %b %Y')}"
        labels = {
            'today':'Today','yesterday':'Yesterday',
            'week':'This Week','lastweek':'Last Week',
            'month':'This Month','lastmonth':'Last Month',
            'quarter':'This Quarter','lastquarter':'Last Quarter',
            'year':'This Year','lastyear':'Last Year',
            'last30':'Last 30 Days','last90':'Last 90 Days',
        }
        return labels.get(period, period)

    a_label = label(a_period, a_from_s, a_to_s, a_fd, a_td)
    b_label = label(b_period, b_from_s, b_to_s, b_fd, b_td)

    periods = ['today','yesterday','week','lastweek','month','lastmonth','quarter','lastquarter','year','lastyear','last30','last90']

    _env = get_active_env()
    return render_template('compare.html',
        a_kpi=a_kpi[0] if a_kpi else {}, b_kpi=b_kpi[0] if b_kpi else {},
        a_prod=a_prod, b_prod=b_prod,
        a_daily=a_daily, b_daily=b_daily,
        a_label=a_label, b_label=b_label,
        a_period=a_period, b_period=b_period,
        a_from=a_from_s, a_to=a_to_s,
        b_from=b_from_s, b_to=b_to_s,
        periods=periods,
        filter_period='', filter_label='', filter_from='', filter_to='',
        compare='0', cmp_from=None, cmp_to=None, cmp_label=None,
        cmp_type=None, cmp_raw_to=None, custom_from='', custom_to='',
        active_env=_env, env_label=AVAILABLE_ENVS[_env]['label'],
        env_color=AVAILABLE_ENVS[_env]['color'], env_options=AVAILABLE_ENVS,
    )

# ── API: Chart data endpoints ─────────────────────────────────

@app.route('/api/top-products-pie')
@login_required
def api_top_products_pie():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']
    rows = query("""
        SELECT pt.name->>'en_US' AS product,
               ROUND(SUM(sol.price_subtotal)::numeric, 0) AS revenue
        FROM sale_order_line sol
        JOIN sale_order so       ON so.id  = sol.order_id
        JOIN product_product pp  ON pp.id  = sol.product_id
        JOIN product_template pt ON pt.id  = pp.product_tmpl_id
        WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
          AND so.date_order >= %s AND so.date_order < %s
        GROUP BY 1 ORDER BY 2 DESC LIMIT 8
    """, (fd, td))
    return jsonify([{'label': r['product'], 'value': float(r['revenue'])} for r in rows])

@app.route('/api/explain/<page>')
@login_required
def api_explain(page):
    """Return AI-style contextual explanation for a page based on live data."""
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']
    label = ctx['filter_label']

    explanations = {
        'best': lambda: _explain_best(fd, td, label),
        'underperforming': lambda: _explain_underperforming(fd, td, label),
        'momentum': lambda: _explain_momentum(fd, td, label),
        'sleeping': lambda: _explain_sleeping(label),
        'customers': lambda: _explain_customers(fd, td, label),
        'reorder': lambda: _explain_reorder(),
        'deadstock': lambda: _explain_deadstock(),
        'crosssell': lambda: _explain_crosssell(fd, td, label),
        'trend': lambda: _explain_trend(fd, td, label),
        'dashboard': lambda: _explain_dashboard(fd, td, label),
    }
    fn = explanations.get(page)
    if fn:
        try:
            return jsonify(fn())
        except Exception as e:
            return jsonify({'title': 'Insight', 'body': str(e), 'tips': []})
    return jsonify({'title': 'No insight available', 'body': '', 'tips': []})


def _explain_best(fd, td, label):
    rows = query("""
        SELECT pt.name->>'en_US' AS product,
               ROUND(SUM(sol.price_subtotal)::numeric,0) AS rev,
               SUM(sol.product_uom_qty) AS qty,
               COUNT(DISTINCT so.id) AS orders
        FROM sale_order_line sol
        JOIN sale_order so ON so.id=sol.order_id
        JOIN product_product pp ON pp.id=sol.product_id
        JOIN product_template pt ON pt.id=pp.product_tmpl_id
        WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
          AND so.date_order>=%s AND so.date_order<%s
        GROUP BY 1 ORDER BY 2 DESC LIMIT 5
    """, (fd, td))
    if not rows:
        return {'title': 'No data', 'body': 'No sales recorded for this period.', 'tips': []}
    top = rows[0]
    total = sum(float(r['rev']) for r in rows)
    share = round(float(top['rev']) / total * 100) if total else 0
    return {
        'title': f"Best Products — {label}",
        'body': (f"Your top product is **{top['product']}** with KSH {float(top['rev']):,.0f} "
                 f"({int(top['qty'])} units across {top['orders']} orders). "
                 f"It accounts for {share}% of your top-5 revenue."),
        'tips': [
            f"Ensure {top['product']} stays well-stocked — it's your revenue driver.",
            f"Consider bundling {top['product']} with lower-performing items.",
            "The score combines both revenue and quantity — a product can rank high by selling many at lower margins.",
        ]
    }

def _explain_underperforming(fd, td, label):
    rows = query("""
        WITH baseline AS (
            SELECT pt.name->>'en_US' AS product, SUM(sol.price_subtotal)/30.0 AS daily_avg
            FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
            JOIN product_product pp ON pp.id=sol.product_id JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order>=CURRENT_DATE-90 AND so.date_order<CURRENT_DATE-7
            GROUP BY 1 HAVING COUNT(so.id)>=5
        ), recent AS (
            SELECT pt.name->>'en_US' AS product, SUM(sol.price_subtotal) AS rev
            FROM sale_order_line sol JOIN sale_order so ON so.id=sol.order_id
            JOIN product_product pp ON pp.id=sol.product_id JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
              AND so.date_order>=%s AND so.date_order<%s GROUP BY 1
        )
        SELECT COUNT(*) AS cnt, ROUND(SUM(b.daily_avg*7)::numeric,0) AS lost_rev
        FROM baseline b LEFT JOIN recent r ON r.product=b.product
        WHERE COALESCE(r.rev,0)<b.daily_avg*7*0.5
    """, (fd, td))
    cnt = int(rows[0]['cnt']) if rows else 0
    lost = float(rows[0]['lost_rev'] or 0) if rows else 0
    return {
        'title': f"Underperforming Products — {label}",
        'body': (f"{cnt} product(s) are performing below 50% of their historical average. "
                 f"This represents roughly KSH {lost:,.0f} in expected but unrealised revenue."),
        'tips': [
            "Products marked 'No Sales' may have stock issues or have been delisted — verify in Odoo.",
            "For 'Slow' products, consider a short promotion or check if competitors have undercut pricing.",
            "Compare with the Sleeping Giants page to see if this is a longer-term trend.",
        ]
    }

def _explain_momentum(fd, td, label):
    rows = query("""
        SELECT COUNT(*) AS cnt FROM (
            WITH prev AS (SELECT pt.name->>'en_US' AS p, SUM(sol.price_subtotal) AS rev FROM sale_order_line sol
                JOIN sale_order so ON so.id=sol.order_id JOIN product_product pp ON pp.id=sol.product_id
                JOIN product_template pt ON pt.id=pp.product_tmpl_id
                WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
                  AND so.date_order>=%s AND so.date_order<%s GROUP BY 1),
            curr AS (SELECT pt.name->>'en_US' AS p, SUM(sol.price_subtotal) AS rev FROM sale_order_line sol
                JOIN sale_order so ON so.id=sol.order_id JOIN product_product pp ON pp.id=sol.product_id
                JOIN product_template pt ON pt.id=pp.product_tmpl_id
                WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
                  AND so.date_order>=%s AND so.date_order<%s GROUP BY 1)
            SELECT 1 FROM curr c JOIN prev p ON p.p=c.p WHERE c.rev>p.rev
        ) x
    """, (fd - (td - fd), fd, fd, td))
    cnt = int(rows[0]['cnt']) if rows else 0
    return {
        'title': f"Momentum Products — {label}",
        'body': (f"{cnt} product(s) grew in both revenue AND quantity vs the prior equivalent period. "
                 "These are your accelerating products — the market is responding positively."),
        'tips': [
            "Momentum products are ideal candidates for increased marketing spend.",
            "Check if stock levels can support continued growth — visit the Reorder Alerts page.",
            "A product with high momentum but low absolute revenue may be an emerging star — watch it.",
        ]
    }

def _explain_sleeping(label):
    return {
        'title': "Sleeping Giants",
        'body': ("These products were once in your top 20 by revenue but have significantly declined. "
                 "They already have brand recognition and likely an existing customer base — they just need attention."),
        'tips': [
            "Check if pricing has drifted out of market range since their peak.",
            "Consider a targeted re-launch promotion to previous buyers.",
            "Investigate if a stock-out period caused the drop — customers may have switched to alternatives.",
        ]
    }

def _explain_customers(fd, td, label):
    rows = query("""
        SELECT COUNT(DISTINCT partner_id) AS total,
               ROUND(SUM(amount_total)::numeric,0) AS rev,
               ROUND(AVG(amount_total)::numeric,0) AS aov
        FROM sale_order WHERE state NOT IN ('cancel','draft')
          AND date_order>=%s AND date_order<%s
    """, (fd, td))
    r = rows[0] if rows else {}
    return {
        'title': f"Top Customers — {label}",
        'body': (f"You served {r.get('total',0)} unique customers generating KSH {float(r.get('rev',0)):,.0f} "
                 f"with an average order value of KSH {float(r.get('aov',0)):,.0f}."),
        'tips': [
            "Customers marked 'Fading' or 'Inactive' are churn risks — a check-in call or discount voucher can reactivate them.",
            "High AOV customers are your VIPs — consider loyalty benefits or priority service.",
            "A customer with many small orders has high engagement; upsell opportunities are strong.",
        ]
    }

def _explain_reorder():
    rows = query("""
        SELECT COUNT(*) FILTER (WHERE status='CRITICAL') AS crit,
               COUNT(*) FILTER (WHERE status='ORDER SOON') AS soon
        FROM (
            SELECT CASE WHEN (sq.quantity-sq.reserved_quantity)/NULLIF(v.dq,0)<=7 THEN 'CRITICAL'
                        WHEN (sq.quantity-sq.reserved_quantity)/NULLIF(v.dq,0)<=14 THEN 'ORDER SOON'
                        ELSE 'WATCH' END AS status
            FROM (SELECT product_id, SUM(product_uom_qty)/30.0 AS dq FROM sale_order_line sol
                JOIN sale_order so ON so.id=sol.order_id
                WHERE so.state NOT IN ('cancel','draft') AND so.date_order>=CURRENT_DATE-30
                GROUP BY product_id) v
            JOIN stock_quant sq ON sq.product_id=v.product_id
            JOIN stock_location sl ON sl.id=sq.location_id AND sl.usage='internal'
        ) x
    """)
    r = rows[0] if rows else {}
    crit = int(r.get('crit', 0))
    soon = int(r.get('soon', 0))
    return {
        'title': "Reorder Alerts",
        'body': (f"{crit} product(s) are CRITICAL (≤7 days stock) and {soon} need ordering soon (≤14 days). "
                 "Projections are based on your actual sales velocity over the last 30 days."),
        'tips': [
            "Critical items should be ordered today — factor in your supplier lead time.",
            "Use the 30-day demand projection column to determine order quantity.",
            "If a product shows 0 days but has stock, it may have negative reserved quantities — check in Odoo.",
        ]
    }

def _explain_deadstock():
    rows = query("""
        SELECT COUNT(*) AS cnt, ROUND(SUM(sq.quantity*COALESCE((pp.standard_price->>'1')::numeric,0))::numeric,0) AS val
        FROM stock_quant sq JOIN stock_location sl ON sl.id=sq.location_id
        JOIN product_product pp ON pp.id=sq.product_id
        LEFT JOIN (SELECT sol.product_id,MAX(so.date_order) AS ls FROM sale_order_line sol
            JOIN sale_order so ON so.id=sol.order_id WHERE so.state NOT IN ('cancel','draft') GROUP BY 1) l
            ON l.product_id=pp.id
        WHERE sl.usage='internal' AND sq.quantity>0
          AND (l.ls IS NULL OR l.ls<CURRENT_DATE-30)
    """)
    r = rows[0] if rows else {}
    return {
        'title': "Dead / Stale Stock",
        'body': (f"{r.get('cnt',0)} products have stock sitting idle with KSH {float(r.get('val',0)):,.0f} "
                 "in tied-up capital (at cost price). This cash could be deployed more productively."),
        'tips': [
            "Run a clearance promotion — even selling at cost frees up cash and warehouse space.",
            "Check if products have simply been miscategorised or incorrectly received in Odoo.",
            "'Never Sold' items may have been purchased speculatively — review procurement decisions.",
        ]
    }

def _explain_crosssell(fd, td, label):
    rows = query("""
        SELECT pt.name->>'en_US' AS anchor,
               COUNT(DISTINCT so.id) AS orders
        FROM sale_order_line sol
        JOIN sale_order so ON so.id=sol.order_id
        JOIN product_product pp ON pp.id=sol.product_id
        JOIN product_template pt ON pt.id=pp.product_tmpl_id
        WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
          AND so.date_order>=%s AND so.date_order<%s
        GROUP BY 1 ORDER BY 2 DESC LIMIT 3
    """, (fd, td))
    if not rows:
        return {'title': 'Cross-sell Insight', 'body': 'No sales data for this period.', 'tips': []}
    names = ', '.join(r['anchor'] for r in rows[:3])
    return {
        'title': f"Cross-sell Opportunities — {label}",
        'body': (f"Your most frequently ordered products ({names}) are anchor items — customers who buy them "
                 "are likely to need complementary products. The pairs listed show real buying behaviour."),
        'tips': [
            "Products with 3+ co-orders are strong bundle candidates — consider discounted bundles.",
            "Promote paired products on the same shelf or invoice line.",
            "High pair-revenue combinations are ideal for sales rep upsell prompts.",
        ]
    }

def _explain_trend(fd, td, label):
    rows = query("""
        SELECT DATE_TRUNC('week', so.date_order)::date AS wk,
               ROUND(SUM(so.amount_total)::numeric,0) AS rev,
               COUNT(DISTINCT so.id) AS orders
        FROM sale_order so
        WHERE so.state NOT IN ('cancel','draft')
          AND so.date_order>=%s AND so.date_order<%s
        GROUP BY 1 ORDER BY 1
    """, (fd, td))
    if not rows:
        return {'title': 'Revenue Trend', 'body': 'No sales data for this period.', 'tips': []}
    total = sum(float(r['rev']) for r in rows)
    peak_wk = max(rows, key=lambda r: float(r['rev']))
    return {
        'title': f"Revenue Trend — {label}",
        'body': (f"Total revenue across {len(rows)} week(s) was KSH {total:,.0f}. "
                 f"Your strongest week was w/c {peak_wk['wk']} with KSH {float(peak_wk['rev']):,.0f} "
                 f"from {peak_wk['orders']} order(s)."),
        'tips': [
            "Look for day-of-week patterns — most B2B sales peak mid-week.",
            "Dips in the trend often follow weekends or holidays — check if your top customers order cyclically.",
            "Consistent weekly growth is a healthier signal than a single large spike.",
        ]
    }

def _explain_dashboard(fd, td, label):
    kpis = query("""
        SELECT COUNT(DISTINCT so.id) AS orders,
               COALESCE(SUM(so.amount_total),0) AS revenue
        FROM sale_order so
        WHERE so.state NOT IN ('cancel','draft')
          AND so.date_order>=%s AND so.date_order<%s
    """, (fd, td))
    kpi = kpis[0] if kpis else {}
    rev = float(kpi.get('revenue', 0))
    orders = int(kpi.get('orders', 0))
    aov = rev / orders if orders else 0

    top = query("""
        SELECT pt.name->>'en_US' AS product, ROUND(SUM(sol.price_subtotal)::numeric,0) AS rev
        FROM sale_order_line sol
        JOIN sale_order so ON so.id=sol.order_id
        JOIN product_product pp ON pp.id=sol.product_id
        JOIN product_template pt ON pt.id=pp.product_tmpl_id
        WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
          AND so.date_order>=%s AND so.date_order<%s
        GROUP BY 1 ORDER BY 2 DESC LIMIT 3
    """, (fd, td))
    top_names = ', '.join(r['product'] for r in top[:3]) if top else 'N/A'
    return {
        'title': f"Dashboard Summary — {label}",
        'body': (f"**{orders}** orders generated KSH **{rev:,.0f}** in revenue "
                 f"with an average order value of KSH {aov:,.0f}. "
                 f"Top sellers: {top_names}."),
        'tips': [
            "Monitor AOV weekly — small increases in basket size have an outsized revenue impact.",
            "Use the Momentum page to identify which products are accelerating right now.",
            "Check Anomaly Detection if revenue appears unusually high or low.",
        ]
    }

# ── API ───────────────────────────────────────────────────────

@app.route('/api/revenue-trend')
@login_required
def api_revenue_trend():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']

    rows = query("""
        SELECT DATE(so.date_order) AS day,
               ROUND(SUM(so.amount_total)::numeric, 0) AS total,
               COUNT(DISTINCT so.id) AS orders
        FROM sale_order so
        WHERE so.state NOT IN ('cancel','draft')
          AND so.date_order >= %s AND so.date_order < %s
        GROUP BY 1 ORDER BY 1
    """, (fd, td))

    return jsonify([{'day': str(r['day']), 'total': float(r['total']), 'orders': int(r['orders'])} for r in rows])

# ── Projections ───────────────────────────────────────────────

@app.route('/projections')
@login_required
def projections():
    horizon = int(request.args.get('horizon', 30))
    horizon = min(max(horizon, 7), 548)
    data = get_projections(get_active_db_config(), horizon_days=horizon)
    ctx = date_filter_context()
    return render_template('projections.html', data=data, horizon=horizon, show_compare=False, **ctx)

# ── Anomalies ─────────────────────────────────────────────────

DISMISSED_FILE = os.path.join(os.path.dirname(__file__), 'dismissed_anomalies.json')

def _load_dismissed():
    try:
        with open(DISMISSED_FILE) as f:
            return set(json.load(f).get('dismissed', []))
    except Exception:
        return set()

def _save_dismissed(titles):
    with open(DISMISSED_FILE, 'w') as f:
        json.dump({'dismissed': sorted(titles)}, f)

@app.route('/anomalies')
@login_required
def anomalies():
    lookback = int(request.args.get('lookback', 90))
    lookback = min(max(lookback, 14), 365)
    dismissed = _load_dismissed()
    items = [i for i in detect_anomalies(get_active_db_config(), lookback_days=lookback)
             if i['title'] not in dismissed]
    dead_rows = query("""
        WITH on_hand AS (
            SELECT pp.id AS product_id,
                   SUM(sq.quantity) AS qty,
                   SUM(sq.quantity * COALESCE((pp.standard_price->>'1')::numeric, 0)) AS stock_value
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            JOIN product_product pp ON pp.id = sq.product_id
            WHERE sl.usage = 'internal' AND sq.quantity > 0
            GROUP BY pp.id
        ),
        last_sale AS (
            SELECT sol.product_id,
                   MAX(so.date_order) AS last_sold_at
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            WHERE so.state NOT IN ('cancel','draft') AND sol.display_type IS NULL
            GROUP BY sol.product_id
        )
        SELECT pt.name->>'en_US' AS product,
               ROUND(oh.qty::numeric, 0) AS qty_on_hand,
               ROUND(oh.stock_value::numeric, 0) AS stock_value,
               ls.last_sold_at::date AS last_sale_date,
               (CURRENT_DATE - ls.last_sold_at::date) AS days_since_sale,
               CASE
                 WHEN ls.last_sold_at IS NULL THEN 'NEVER SOLD'
                 WHEN (CURRENT_DATE - ls.last_sold_at::date) > 90 THEN 'DEAD (90+ days)'
                 WHEN (CURRENT_DATE - ls.last_sold_at::date) > 60 THEN 'STALE (60+ days)'
                 ELSE 'SLOW (30+ days)'
               END AS status
        FROM on_hand oh
        JOIN product_product pp  ON pp.id = oh.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN last_sale ls   ON ls.product_id = oh.product_id
        WHERE oh.qty > 0
          AND (ls.last_sold_at IS NULL OR (CURRENT_DATE - ls.last_sold_at::date) >= 30)
        ORDER BY days_since_sale DESC NULLS FIRST
        LIMIT 50
    """)
    ctx = date_filter_context()
    return render_template('anomalies.html', items=items, lookback=lookback,
                           dead_rows=dead_rows, show_compare=False, **ctx)

@app.route('/anomalies/dismiss', methods=['POST'])
@login_required
def anomalies_dismiss():
    title = request.json.get('title', '')
    if title:
        dismissed = _load_dismissed()
        dismissed.add(title)
        _save_dismissed(dismissed)
    return jsonify({'ok': True})

# ── Expenses ──────────────────────────────────────────────────

def _get_odoo_bills():
    """Fetch vendor bills from Odoo account_move."""
    return query("""
        SELECT am.name AS ref,
               am.invoice_date AS expense_date,
               rp.name AS vendor,
               aj.name->>'en_US' AS journal,
               ROUND(am.amount_total::numeric, 0) AS amount,
               am.state,
               am.payment_state,
               am.invoice_origin AS origin
        FROM account_move am
        JOIN res_partner rp ON rp.id = am.partner_id
        JOIN account_journal aj ON aj.id = am.journal_id
        WHERE am.move_type = 'in_invoice'
          AND am.state = 'posted'
        ORDER BY am.invoice_date DESC
        LIMIT 100
    """)

def _get_suggestions():
    """Generate smart expense suggestions."""
    suggestions = []

    # Find recurring vendors not seen in 25+ days
    recurring = query("""
        SELECT rp.name AS vendor,
               COUNT(am.id) AS bill_count,
               MAX(am.invoice_date) AS last_bill,
               (CURRENT_DATE - MAX(am.invoice_date)) AS days_since,
               ROUND(AVG(am.amount_total)::numeric, 0) AS avg_amount
        FROM account_move am
        JOIN res_partner rp ON rp.id = am.partner_id
        WHERE am.move_type = 'in_invoice'
          AND am.state = 'posted'
          AND am.invoice_date >= CURRENT_DATE - 180
        GROUP BY rp.name
        HAVING COUNT(am.id) >= 2
           AND (CURRENT_DATE - MAX(am.invoice_date)) > 25
        ORDER BY days_since DESC
        LIMIT 10
    """)

    for r in recurring:
        gap = int(r['days_since'])
        suggestions.append({
            'type': 'overdue_expense',
            'icon': 'clock',
            'title': f"Update expense for {r['vendor']}",
            'detail': f"Last billed {gap} days ago. Avg amount KSH {float(r['avg_amount']):,.0f}. Expected monthly.",
            'action': f"Add bill for {r['vendor']}",
            'priority': 'high' if gap > 35 else 'medium',
            'prefill_vendor': r['vendor'],
            'prefill_amount': float(r['avg_amount']),
        })

    # Products waiting to be received (confirmed purchase orders)
    pending_receipts = query("""
        SELECT pt.name->>'en_US' AS product,
               SUM(pol.product_qty) AS ordered_qty,
               rp.name AS vendor,
               po.name AS po_ref,
               po.date_order::date AS order_date
        FROM purchase_order_line pol
        JOIN purchase_order po ON po.id = pol.order_id
        JOIN product_product pp ON pp.id = pol.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        JOIN res_partner rp ON rp.id = po.partner_id
        WHERE po.state IN ('purchase','done')
          AND pol.qty_received < pol.product_qty
        GROUP BY 1, 3, 4, 5
        ORDER BY 5
        LIMIT 10
    """)

    for r in pending_receipts:
        suggestions.append({
            'type': 'pending_receipt',
            'icon': 'package',
            'title': f"Receive stock — {r['product']}",
            'detail': f"{int(r['ordered_qty'])} units on order {r['po_ref']} from {r['vendor']} (ordered {r['order_date']}).",
            'action': 'Process receipt in Odoo',
            'priority': 'medium',
        })

    return suggestions

@app.route('/expenses', methods=['GET', 'POST'])
@login_required
def expenses():
    ctx = date_filter_context()
    fd, td = ctx['filter_from'], ctx['filter_to']

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            conn = None
            try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO webapp_expenses
                      (expense_date, category, description, vendor, amount, frequency, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    request.form.get('expense_date') or date.today(),
                    request.form.get('category', '').strip(),
                    request.form.get('description', '').strip(),
                    request.form.get('vendor', '').strip(),
                    float(request.form.get('amount', 0)),
                    request.form.get('frequency', 'once'),
                    request.form.get('notes', '').strip(),
                ))
                conn.commit()
            except Exception as e:
                print(f"Expense add error: {e}")
            finally:
                if conn: conn.close()

        elif action == 'delete':
            conn = None
            try:
                exp_id = int(request.form.get('id'))
                conn = get_db()
                cur = conn.cursor()
                cur.execute("DELETE FROM webapp_expenses WHERE id = %s", (exp_id,))
                conn.commit()
            except Exception as e:
                print(f"Expense delete error: {e}")
            finally:
                if conn: conn.close()

        elif action == 'bulk_add':
            conn = None
            try:
                import json
                category      = request.form.get('category', '')
                vendor        = request.form.get('vendor', '')
                amount        = float(request.form.get('amount', 0))
                frequency     = request.form.get('frequency', 'monthly')
                dates_json    = request.form.get('missing_dates', '[]')
                missing_dates = json.loads(dates_json)
                conn = get_db()
                cur  = conn.cursor()
                for d in missing_dates:
                    cur.execute("""
                        INSERT INTO webapp_expenses
                          (expense_date, category, description, vendor, amount, frequency, source)
                        VALUES (%s, %s, %s, %s, %s, %s, 'smart-fill')
                    """, (d, category, "Auto-filled by smart suggestion", vendor, amount, frequency))
                conn.commit()
            except Exception as e:
                print(f"Bulk add error: {e}")
            finally:
                if conn: conn.close()
            return redirect(url_for('expenses'))

        elif action == 'import_xls':
            f = request.files.get('xls_file')
            if f:
                try:
                    import pandas as pd
                    raw = f.read()
                    fname = (f.filename or '').lower()
                    if fname.endswith('.csv'):
                        import io as _io
                        df = pd.read_csv(_io.BytesIO(raw))
                    else:
                        df = pd.read_excel(io.BytesIO(raw))
                    # Normalise columns (case-insensitive)
                    df.columns = [c.lower().strip().replace(' ', '_') for c in df.columns]

                    # Map common column names
                    col_map = {
                        'date': 'expense_date', 'invoice_date': 'expense_date',
                        'bill_date': 'expense_date',
                        'amount_total': 'amount', 'total': 'amount', 'subtotal': 'amount',
                        'partner': 'vendor', 'supplier': 'vendor', 'vendor_name': 'vendor',
                        'account': 'category', 'account_name': 'category',
                        'label': 'description', 'product': 'description',
                        'narration': 'notes', 'note': 'notes',
                    }
                    for old, new in col_map.items():
                        if old in df.columns and new not in df.columns:
                            df.rename(columns={old: new}, inplace=True)

                    imported = 0
                    conn = None
                    try:
                        conn = get_db()
                        cur = conn.cursor()
                        for _, row in df.iterrows():
                            try:
                                cur.execute("""
                                    INSERT INTO webapp_expenses
                                      (expense_date, category, description, vendor, amount, frequency, source)
                                    VALUES (%s, %s, %s, %s, %s, 'once', 'import')
                                """, (
                                    row.get('expense_date', date.today()),
                                    str(row.get('category', 'Imported'))[:100],
                                    str(row.get('description', ''))[:500],
                                    str(row.get('vendor', ''))[:200],
                                    float(row.get('amount', 0)),
                                ))
                                imported += 1
                            except Exception:
                                pass
                        conn.commit()
                    finally:
                        if conn: conn.close()
                except Exception as e:
                    print(f"XLS import error: {e}")

        return redirect(url_for('expenses'))

    # GET
    odoo_bills   = _get_odoo_bills()
    suggestions  = _get_suggestions()
    smart_tiles  = get_smart_tiles(get_active_db_config())

    manual = query("""
        SELECT id, expense_date, category, description, vendor, amount, frequency, source, notes
        FROM webapp_expenses
        WHERE expense_date >= %s AND expense_date <= %s
        ORDER BY expense_date DESC
    """, (fd, td))

    # Summary by category (combined)
    by_category = query("""
        SELECT category, ROUND(SUM(amount)::numeric, 0) AS total, COUNT(*) AS cnt
        FROM webapp_expenses
        WHERE expense_date >= %s AND expense_date <= %s
        GROUP BY category ORDER BY total DESC
    """, (fd, td))

    categories = query("SELECT DISTINCT category FROM webapp_expenses ORDER BY category")

    return render_template('expenses.html',
        odoo_bills=odoo_bills, manual=manual, suggestions=suggestions,
        smart_tiles=smart_tiles, by_category=by_category, categories=categories,
        show_compare=False, **ctx
    )

# ── Chat ─────────────────────────────────────────────────────

@app.route('/chat')
@login_required
def chat():
    ctx = date_filter_context()
    return render_template('chat.html', **ctx)

@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    data = request.get_json(force=True)
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'No question provided'}), 400
    try:
        result = chat_answer(question, query)
        return jsonify(result)
    except Exception as e:
        return jsonify({'text': f'Sorry, I ran into an error: {str(e)}', 'rows': [], 'chart_type': None, 'suggestions': []}), 200


@app.route('/api/chat-stream')
@login_required
def api_chat_stream():
    question = request.args.get('q', '').strip()
    history_raw = request.args.get('history', '[]')
    if not question:
        return jsonify({'error': 'No question'}), 400
    try:
        history = json.loads(history_raw)
    except Exception:
        history = []

    def generate():
        try:
            for chunk in stream_agent(question, history):
                yield chunk
        except Exception as e:
            import json as _json
            yield f"data: {_json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


# ── Analytics Dashboard ───────────────────────────────────────

@app.route('/analytics')
@login_required
def analytics():
    ctx = date_filter_context()
    split_shop, split_kemboi, split_mutai = get_profit_split_from_claude()
    return render_template('analytics.html', split_shop=split_shop,
                           split_kemboi=split_kemboi, split_mutai=split_mutai, **ctx)


@app.route('/profile')
@login_required
def profile():
    ctx = date_filter_context()
    # Live stats for the profile page
    stats = {}
    try:
        r = query("SELECT COUNT(*) AS orders, ROUND(SUM(amount_total)::numeric,0) AS revenue, COUNT(DISTINCT partner_id) AS customers FROM sale_order WHERE state IN ('sale','done')")
        if r: stats.update(r[0])
        r2 = query("SELECT COUNT(*) AS products FROM product_template WHERE active=true")
        if r2: stats['products'] = r2[0]['products']
        r3 = query("""
            SELECT ROUND(SUM(sq.quantity * COALESCE((pp.standard_price->>'1')::numeric,0))::numeric,0) AS stock_value
            FROM stock_quant sq JOIN stock_location sl ON sl.id=sq.location_id
            JOIN product_product pp ON pp.id=sq.product_id
            WHERE sl.usage='internal' AND sq.quantity>0
        """)
        if r3: stats['stock_value'] = r3[0]['stock_value']
        r4 = query("SELECT ROUND(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END)::numeric,0) AS total_opex FROM account_move am JOIN account_move_line aml ON aml.move_id=am.id JOIN res_partner rp ON rp.id=am.partner_id WHERE am.move_type='in_invoice' AND am.state='posted' AND LOWER(rp.name) NOT ILIKE '%chinaland%' AND LOWER(rp.name) NOT ILIKE '%kaboww%'")
        if r4: stats['total_opex'] = r4[0]['total_opex']
        r5 = query("SELECT ROUND(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END)::numeric,0) AS total_imports FROM account_move am JOIN account_move_line aml ON aml.move_id=am.id JOIN res_partner rp ON rp.id=am.partner_id WHERE am.move_type='in_invoice' AND am.state='posted' AND (LOWER(rp.name) ILIKE '%chinaland%' OR LOWER(rp.name) ILIKE '%kaboww%')")
        if r5: stats['total_imports'] = r5[0]['total_imports']
        monthly = query("SELECT TO_CHAR(date_trunc('month',date_order),'YYYY-MM') AS month, ROUND(SUM(amount_total)::numeric,0) AS revenue FROM sale_order WHERE state IN ('sale','done') GROUP BY 1 ORDER BY 1")
        stats['monthly'] = [{'month': r['month'], 'revenue': float(r['revenue'] or 0)} for r in monthly]
        cats = query("SELECT pc.name AS category, COUNT(DISTINCT pt.id) AS products FROM product_template pt JOIN product_category pc ON pc.id=pt.categ_id WHERE pt.active=true AND pc.name NOT IN ('All','Expenses','Deliveries') GROUP BY 1 ORDER BY 2 DESC")
        stats['categories'] = [{'name': r['category'], 'count': int(r['products'])} for r in cats]
        expenses = query("SELECT LOWER(rp.name) AS vendor, ROUND(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END)::numeric,0) AS total FROM account_move am JOIN account_move_line aml ON aml.move_id=am.id JOIN res_partner rp ON rp.id=am.partner_id WHERE am.move_type='in_invoice' AND am.state='posted' AND LOWER(rp.name) NOT ILIKE '%chinaland%' AND LOWER(rp.name) NOT ILIKE '%kaboww%' GROUP BY 1 ORDER BY 2 DESC LIMIT 7")
        stats['expense_breakdown'] = [{'name': r['vendor'].title(), 'total': float(r['total'] or 0)} for r in expenses]
    except Exception:
        pass
    split_shop, split_kemboi, split_mutai = get_profit_split_from_claude()
    meetings = get_meetings_from_claude()
    monthly_target = get_monthly_target_from_claude()
    today = date.today().isoformat()
    return render_template('profile.html', stats=stats,
                           split_shop=split_shop, split_kemboi=split_kemboi, split_mutai=split_mutai,
                           meetings=meetings, today=today, monthly_target=monthly_target, **ctx)


@app.route('/api/profile/save-ownership', methods=['POST'])
@login_required
def save_ownership():
    import re
    data = request.get_json() or {}
    try:
        shop   = int(data.get('shop',   33))
        kemboi = int(data.get('kemboi', 33))
        mutai  = int(data.get('mutai',  34))
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Invalid values'})
    if shop + kemboi + mutai != 100:
        return jsonify({'ok': False, 'error': 'Percentages must sum to 100'})
    content = read_claude_md()
    new_line = f'**Profit Split:** Shop {shop}%, Kemboi {kemboi}%, Mutai {mutai}%'
    if re.search(r'Profit Split:', content, re.IGNORECASE):
        new_content = re.sub(r'\*\*Profit Split:\*\*[^\n]*', new_line, content)
    else:
        new_content = re.sub(
            r'(\*\*Ownership:\*\*[^\n]*\n)',
            f'\\1- {new_line}\n',
            content
        )
    write_claude_md(new_content)
    return jsonify({'ok': True})


@app.route('/api/profile/get-split')
@login_required
def get_split():
    shop, kemboi, mutai = get_profit_split_from_claude()
    return jsonify({'shop': shop, 'kemboi': kemboi, 'mutai': mutai})


@app.route('/api/profile/add-meeting', methods=['POST'])
@login_required
def add_meeting():
    data = request.get_json() or {}
    meeting_date = str(data.get('date', '')).strip()
    title        = str(data.get('title', '')).strip()
    attendees    = str(data.get('attendees', '')).strip()
    notes        = str(data.get('notes', '')).strip()
    if not meeting_date or not title:
        return jsonify({'ok': False, 'error': 'Date and title required'})
    entry = f'\n### {meeting_date} — {title}\n**Attendees:** {attendees}\n**Notes:** {notes}\n'
    content = read_claude_md()
    if '## Meetings' in content:
        parts = content.split('## Meetings', 1)
        new_content = parts[0] + '## Meetings\n' + entry + parts[1].lstrip('\n')
    else:
        new_content = content.rstrip() + '\n\n## Meetings\n' + entry
    write_claude_md(new_content)
    return jsonify({'ok': True})


@app.route('/api/summary')
@login_required
def api_summary():
    """KPI cards — respects ?year= filter."""
    try:
        year = request.args.get('year', '')
        if year.isdigit():
            yr_filter = f"AND EXTRACT(YEAR FROM am.invoice_date) = {int(year)}"
            start = f'{year}-01-01'
        else:
            yr_filter = ''
            start = '2024-01-01'

        monthly_rows = query(f"""
            WITH monthly_sales AS (
                SELECT TO_CHAR(am.invoice_date,'YYYY-MM') AS month,
                    COALESCE(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END),0) AS revenue,
                    COALESCE(SUM(CASE WHEN aml.display_type='product'
                        THEN aml.quantity * COALESCE((pp.standard_price->>'1')::numeric,0) ELSE 0 END),0) AS cogs
                FROM account_move am
                JOIN account_move_line aml ON aml.move_id=am.id
                LEFT JOIN product_product pp ON pp.id=aml.product_id
                WHERE am.move_type IN ('out_invoice','out_receipt') AND am.state='posted'
                  AND am.invoice_date >= '{start}' {yr_filter}
                GROUP BY 1
            ),
            monthly_opex AS (
                SELECT TO_CHAR(am.invoice_date,'YYYY-MM') AS month,
                    COALESCE(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END),0) AS opex
                FROM account_move am
                JOIN account_move_line aml ON aml.move_id=am.id
                JOIN res_partner rp ON rp.id=am.partner_id
                WHERE am.move_type='in_invoice' AND am.state='posted'
                  AND am.invoice_date >= '{start}'
                  AND LOWER(rp.name) NOT ILIKE '%chinaland%'
                  AND LOWER(rp.name) NOT ILIKE '%kaboww%'
                  {yr_filter}
                GROUP BY 1
            )
            SELECT s.month, s.revenue, s.cogs,
                COALESCE(o.opex,0) AS opex,
                s.revenue - s.cogs - COALESCE(o.opex,0) AS net_profit
            FROM monthly_sales s
            LEFT JOIN monthly_opex o ON o.month=s.month
            ORDER BY 1
        """)

        total_revenue = sum(float(r['revenue'] or 0) for r in monthly_rows)
        total_cogs    = sum(float(r['cogs']    or 0) for r in monthly_rows)
        total_opex    = sum(float(r['opex']    or 0) for r in monthly_rows)
        net_profit    = sum(float(r['net_profit'] or 0) for r in monthly_rows)

        best = max(monthly_rows, key=lambda r: float(r['revenue'] or 0), default=None)
        best_month = {'name': best['month'], 'revenue': float(best['revenue'] or 0)} if best else {'name': '—', 'revenue': 0}

        imports_row = query(f"""
            SELECT COALESCE(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END),0) AS total
            FROM account_move am
            JOIN account_move_line aml ON aml.move_id=am.id
            JOIN res_partner rp ON rp.id=am.partner_id
            WHERE am.move_type='in_invoice' AND am.state='posted'
              AND am.invoice_date >= '{start}' {yr_filter}
              AND (LOWER(rp.name) ILIKE '%chinaland%' OR LOWER(rp.name) ILIKE '%kaboww%')
        """)
        total_imports = float((imports_row[0]['total'] if imports_row else 0) or 0)

        # Annual pills always show all-time breakdown (context reference)
        annual_rows = query("""
            SELECT EXTRACT(YEAR FROM am.invoice_date)::int AS year,
                   ROUND(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END)::numeric,2) AS revenue
            FROM account_move am
            JOIN account_move_line aml ON aml.move_id=am.id
            WHERE am.move_type IN ('out_invoice','out_receipt') AND am.state='posted'
              AND am.invoice_date >= '2024-01-01'
            GROUP BY 1 ORDER BY 1
        """)
        annual_revenue = {str(r['year']): float(r['revenue'] or 0) for r in annual_rows}

        return jsonify({
            'total_revenue': total_revenue, 'total_cogs': total_cogs,
            'total_opex': total_opex, 'total_imports': total_imports,
            'net_profit': net_profit, 'best_month': best_month,
            'annual_revenue': annual_revenue,
            'active_year': year or 'all',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/pnl')
@login_required
def api_pnl():
    """Monthly P&L table: [{month, revenue, cogs, opex, net_profit}]"""
    try:
        year = request.args.get('year', '')
        if year.isdigit():
            start = f'{year}-01-01'
            yr_clause = f"AND EXTRACT(YEAR FROM am.invoice_date) = {int(year)}"
            yr_exp    = f"AND EXTRACT(YEAR FROM am.invoice_date) = {int(year)}"
        else:
            start = '2024-01-01'
            yr_clause = ''
            yr_exp = ''

        rows = query(f"""
            WITH s AS (
                SELECT TO_CHAR(am.invoice_date,'YYYY-MM') AS month,
                    COALESCE(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END),0) AS revenue,
                    COALESCE(SUM(CASE WHEN aml.display_type='product'
                        THEN aml.quantity * COALESCE((pp.standard_price->>'1')::numeric,0) ELSE 0 END),0) AS cogs
                FROM account_move am
                JOIN account_move_line aml ON aml.move_id=am.id
                LEFT JOIN product_product pp ON pp.id=aml.product_id
                WHERE am.move_type IN ('out_invoice','out_receipt') AND am.state='posted'
                  AND am.invoice_date >= '{start}' {yr_clause}
                GROUP BY 1
            ),
            o AS (
                SELECT TO_CHAR(am.invoice_date,'YYYY-MM') AS month,
                    COALESCE(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END),0) AS opex
                FROM account_move am
                JOIN account_move_line aml ON aml.move_id=am.id
                JOIN res_partner rp ON rp.id=am.partner_id
                WHERE am.move_type='in_invoice' AND am.state='posted'
                  AND am.invoice_date >= '{start}'
                  AND LOWER(rp.name) NOT ILIKE '%chinaland%'
                  AND LOWER(rp.name) NOT ILIKE '%kaboww%'
                  {yr_exp}
                GROUP BY 1
            )
            SELECT s.month,
                ROUND(s.revenue::numeric,2) AS revenue,
                ROUND(s.cogs::numeric,2) AS cogs,
                ROUND(COALESCE(o.opex,0)::numeric,2) AS opex,
                ROUND((s.revenue - s.cogs - COALESCE(o.opex,0))::numeric,2) AS net_profit
            FROM s LEFT JOIN o ON o.month=s.month ORDER BY 1
        """)
        return jsonify([{k: float(v) if k != 'month' else v for k, v in r.items()} for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/analytics-expenses')
@login_required
def api_analytics_expenses():
    """Expense donut data: [{category, amount}] — respects ?year= filter."""
    try:
        year = request.args.get('year', '')
        if year.isdigit():
            yr_filter = f"AND EXTRACT(YEAR FROM am.invoice_date) = {int(year)}"
            start = f'{year}-01-01'
        else:
            yr_filter = ''
            start = '2024-01-01'

        bill_rows = query(f"""
            SELECT LOWER(rp.name) AS partner_name,
                COALESCE(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END),0) AS amount
            FROM account_move am
            JOIN account_move_line aml ON aml.move_id=am.id
            JOIN res_partner rp ON rp.id=am.partner_id
            WHERE am.move_type='in_invoice' AND am.state='posted'
              AND am.invoice_date >= '{start}' {yr_filter}
              AND LOWER(rp.name) NOT ILIKE '%chinaland%'
              AND LOWER(rp.name) NOT ILIKE '%kaboww%'
            GROUP BY LOWER(rp.name)
        """)

        def categorise(n):
            if any(x in n for x in ['star mall','kamkunji']): return 'Rent'
            if any(x in n for x in ['christine','virginiah']): return 'Christine Salary'
            if any(x in n for x in ['kplc','kenya power']): return 'Electricity'
            if 'collins kemboi' in n: return 'Server Hosting'
            if 'safaricom' in n: return 'Airtime'
            if 'nairobi city' in n: return 'Business Permit'
            return 'Other'

        totals = {}
        for r in bill_rows:
            cat = categorise(r['partner_name'])
            totals[cat] = totals.get(cat, 0) + float(r['amount'] or 0)

        result = [{'category': k, 'amount': round(v, 2)} for k, v in sorted(totals.items(), key=lambda x: -x[1])]
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/top-products-analytics')
@login_required
def api_top_products_analytics():
    """Top 15 products by revenue (from invoices): [{name, revenue, qty_sold}]"""
    try:
        year = request.args.get('year', '')
        if year.isdigit():
            start = f'{year}-01-01'
            yr_clause = f"AND EXTRACT(YEAR FROM am.invoice_date) = {int(year)}"
        else:
            start = '2024-01-01'
            yr_clause = ''

        rows = query(f"""
            SELECT pt.name->>'en_US' AS name,
                ROUND(SUM(aml.price_subtotal)::numeric,2) AS revenue,
                ROUND(SUM(aml.quantity)::numeric,3) AS qty_sold
            FROM account_move am
            JOIN account_move_line aml ON aml.move_id=am.id
            JOIN product_product pp ON pp.id=aml.product_id
            JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE am.move_type IN ('out_invoice','out_receipt') AND am.state='posted'
              AND aml.display_type='product'
              AND am.invoice_date >= '{start}' {yr_clause}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 15
        """)
        return jsonify([{'name': r['name'], 'revenue': float(r['revenue'] or 0), 'qty_sold': float(r['qty_sold'] or 0)} for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock-analytics')
@login_required
def api_stock_analytics():
    """Current stock sorted by value desc: [{name, qty_on_hand, cost, value, list_price}]"""
    try:
        rows = query("""
            SELECT pt.name->>'en_US' AS name,
                ROUND(SUM(sq.quantity)::numeric,3) AS qty_on_hand,
                ROUND(COALESCE((pp.standard_price->>'1')::numeric,0)::numeric,2) AS cost,
                ROUND((SUM(sq.quantity) * COALESCE((pp.standard_price->>'1')::numeric,0))::numeric,2) AS value,
                ROUND(pt.list_price::numeric,2) AS list_price
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id=sq.location_id
            JOIN product_product pp ON pp.id=sq.product_id
            JOIN product_template pt ON pt.id=pp.product_tmpl_id
            WHERE sl.usage='internal'
            GROUP BY pt.name, pp.standard_price, pt.list_price
            HAVING SUM(sq.quantity) * COALESCE((pp.standard_price->>'1')::numeric,0) > 0
            ORDER BY value DESC
        """)
        return jsonify([{
            'name': r['name'], 'qty_on_hand': float(r['qty_on_hand'] or 0),
            'cost': float(r['cost'] or 0), 'value': float(r['value'] or 0),
            'list_price': float(r['list_price'] or 0),
        } for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/monthly-expenses-analytics')
@login_required
def api_monthly_expenses_analytics():
    """Monthly OpEx stacked bar: [{month, rent, salaries, utilities, hosting, other}]"""
    try:
        year = request.args.get('year', '')
        if year.isdigit():
            start = f'{year}-01-01'
            yr_clause = f"AND EXTRACT(YEAR FROM am.invoice_date) = {int(year)}"
        else:
            start = '2024-01-01'
            yr_clause = ''

        rows = query(f"""
            SELECT TO_CHAR(am.invoice_date,'YYYY-MM') AS month,
                LOWER(rp.name) AS partner_name,
                COALESCE(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END),0) AS amount
            FROM account_move am
            JOIN account_move_line aml ON aml.move_id=am.id
            JOIN res_partner rp ON rp.id=am.partner_id
            WHERE am.move_type='in_invoice' AND am.state='posted'
              AND am.invoice_date >= '{start}'
              AND LOWER(rp.name) NOT ILIKE '%chinaland%'
              AND LOWER(rp.name) NOT ILIKE '%kaboww%'
              {yr_clause}
            GROUP BY 1, 2
        """)

        def bucket(n):
            if any(x in n for x in ['star mall','kamkunji']): return 'rent'
            if any(x in n for x in ['christine','virginiah']): return 'salaries'
            if any(x in n for x in ['kplc','kenya power','safaricom']): return 'utilities'
            if 'collins kemboi' in n: return 'hosting'
            return 'other'

        monthly = {}
        for r in rows:
            m = r['month']
            if m not in monthly:
                monthly[m] = {'month': m, 'rent': 0, 'salaries': 0, 'utilities': 0, 'hosting': 0, 'other': 0}
            monthly[m][bucket(r['partner_name'])] += float(r['amount'] or 0)

        return jsonify([monthly[m] for m in sorted(monthly)])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/directors-analytics')
@login_required
def api_directors_analytics():
    """Monthly director profit distribution — respects ?year= filter."""
    try:
        year = request.args.get('year', '')
        if year.isdigit():
            yr_filter = f"AND EXTRACT(YEAR FROM am.invoice_date) = {int(year)}"
            start = f'{year}-01-01'
        else:
            yr_filter = ''
            start = '2024-01-01'

        rows = query(f"""
            WITH s AS (
                SELECT TO_CHAR(am.invoice_date,'YYYY-MM') AS month,
                    COALESCE(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END),0) AS revenue,
                    COALESCE(SUM(CASE WHEN aml.display_type='product'
                        THEN aml.quantity * COALESCE((pp.standard_price->>'1')::numeric,0) ELSE 0 END),0) AS cogs
                FROM account_move am
                JOIN account_move_line aml ON aml.move_id=am.id
                LEFT JOIN product_product pp ON pp.id=aml.product_id
                WHERE am.move_type IN ('out_invoice','out_receipt') AND am.state='posted'
                  AND am.invoice_date >= '{start}' {yr_filter}
                GROUP BY 1
            ),
            o AS (
                SELECT TO_CHAR(am.invoice_date,'YYYY-MM') AS month,
                    COALESCE(SUM(CASE WHEN aml.display_type='product' THEN aml.price_subtotal ELSE 0 END),0) AS opex
                FROM account_move am
                JOIN account_move_line aml ON aml.move_id=am.id
                JOIN res_partner rp ON rp.id=am.partner_id
                WHERE am.move_type='in_invoice' AND am.state='posted'
                  AND am.invoice_date >= '{start}'
                  AND LOWER(rp.name) NOT ILIKE '%chinaland%'
                  AND LOWER(rp.name) NOT ILIKE '%kaboww%'
                  {yr_filter}
                GROUP BY 1
            )
            SELECT s.month,
                ROUND((s.revenue - s.cogs - COALESCE(o.opex,0))::numeric,2) AS net_profit
            FROM s LEFT JOIN o ON o.month=s.month ORDER BY 1
        """)

        shop_pct, kemboi_pct, mutai_pct = get_profit_split_from_claude()
        result = []
        total_net = total_shop = total_kemboi = total_mutai = 0
        for r in rows:
            net = float(r['net_profit'] or 0)
            profitable = net > 0
            if profitable:
                shop_share   = round(net * shop_pct   / 100, 2)
                kemboi_share = round(net * kemboi_pct / 100, 2)
                mutai_share  = round(net - shop_share - kemboi_share, 2)
            else:
                shop_share, kemboi_share, mutai_share = round(net, 2), 0, 0
            total_net += net; total_shop += shop_share; total_kemboi += kemboi_share; total_mutai += mutai_share
            result.append({'month': r['month'], 'net_profit': round(net, 2),
                           'shop': shop_share, 'kemboi': kemboi_share, 'mutai': mutai_share,
                           'profitable': profitable})

        return jsonify({'rows': result, 'totals': {
            'net_profit': round(total_net, 2), 'shop': round(total_shop, 2),
            'kemboi': round(total_kemboi, 2), 'mutai': round(total_mutai, 2),
        }})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Health check ──────────────────────────────────────────────

@app.route('/health')
def health():
    """Lightweight health check — no auth required."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    status = 200 if db_ok else 503
    return jsonify({
        'status': 'ok' if db_ok else 'degraded',
        'db': 'connected' if db_ok else 'error',
        'uptime_since': os.environ.get('PM2_UPTIME', 'unknown'),
    }), status


# ── Revenue target API ─────────────────────────────────────────

@app.route('/api/profile/save-target', methods=['POST'])
@login_required
def save_target():
    data = request.get_json() or {}
    try:
        target = int(data.get('target', 0))
        if target < 0:
            return jsonify({'ok': False, 'error': 'Target must be positive'})
        save_monthly_target_to_claude(target)
        return jsonify({'ok': True, 'target': target})
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Invalid value'})


@app.route('/api/profile/get-target')
@login_required
def get_target():
    return jsonify({'target': get_monthly_target_from_claude()})


# ── Receipt Scanner ───────────────────────────────────────────

import subprocess
import threading
import base64 as _base64
import io as _io
from rapidfuzz import fuzz
from datetime import datetime as _dt

_PRODUCTS_MD_PATH = os.path.join(os.path.dirname(__file__), 'products.md')
_OCR_PY_PATH      = os.path.join(os.path.dirname(__file__), 'receipt_ocr.py')
_PRICE_HIST_PATH  = os.path.join(os.path.dirname(__file__), 'price_history.json')

# ── Price history (learned from Odoo, persisted to JSON) ──────
_price_history: dict = {}

def _load_price_history():
    global _price_history
    try:
        with open(_PRICE_HIST_PATH) as f:
            _price_history = json.load(f)
    except Exception:
        _price_history = {}

_load_price_history()

# ── In-memory catalog — parsed once at startup ─────────────────
_CATALOG: list = []

def _load_catalog():
    global _CATALOG
    products = []
    try:
        with open(_PRODUCTS_MD_PATH) as f:
            md = f.read()
        for line in md.split('\n'):
            if not line.startswith('|') or 'variant_id' in line or line.startswith('| ---'):
                continue
            cols = [c.strip() for c in line.split('|') if c.strip()]
            if len(cols) < 5:
                continue
            try:
                products.append({
                    'id':      int(cols[0]),
                    'name':    cols[1],
                    'code':    cols[2] if cols[2] != '—' else None,
                    'price':   float(cols[3]),
                    'aliases': [a.strip().lower() for a in cols[4].split(',') if a.strip()],
                })
            except Exception:
                continue
    except Exception:
        pass
    _CATALOG = products

_load_catalog()  # parse once at startup


def _catalog_compact() -> str:
    """Pipe-delimited compact catalog string for Gemini prompts (~40% fewer tokens than markdown)."""
    lines = ['id|name|code|price|aliases']
    for p in _CATALOG:
        aliases = ','.join(p['aliases'])
        code    = p['code'] or '—'
        lines.append(f"{p['id']}|{p['name']}|{code}|{p['price']}|{aliases}")
    return '\n'.join(lines)


def _fuzzy_match_item(raw_text: str, qty: int = 1, line_total: float = None) -> dict:
    """
    Match a raw receipt text against the in-memory catalog using rapidfuzz.
    Returns {product, score, confidence, top_candidates}.
    """
    if not raw_text or not _CATALOG:
        return {'product': None, 'score': 0, 'confidence': 'LOW', 'top_candidates': []}

    text = raw_text.lower().strip()
    scored = []

    for p in _CATALOG:
        # Score against product name
        name_score = fuzz.WRatio(text, p['name'].lower())

        # Score against each alias — take best
        alias_score = max(
            (fuzz.WRatio(text, a) for a in p['aliases']),
            default=0
        )

        # Score against product code (prefix bonus — codes are short so exact prefix matters)
        code_score = 0
        if p['code']:
            code = p['code'].lower()
            if text == code:
                code_score = 100
            elif text.startswith(code) or code.startswith(text[:3]):
                code_score = 88

        base_score = max(name_score, alias_score, code_score)

        # Price cross-check boost — use learned historical range if available, else catalog price
        if line_total and qty and p['price']:
            unit      = line_total / qty
            hist      = _price_history.get(str(p['id']))
            if hist and hist.get('count', 0) >= 3:
                # Use learned price range (handles discounts / upsells)
                p_min = hist['min']
                p_max = hist['max']
                p_mean = hist['mean']
                if p_min <= unit <= p_max:
                    base_score = min(base_score + 32, 100)  # within known sold range
                elif abs(unit - p_mean) / max(p_mean, 1) < 0.20:
                    base_score = min(base_score + 16, 100)  # within 20% of mean
            else:
                # Fall back to catalog price
                catalog_p = p['price']
                if catalog_p > 0:
                    diff_pct = abs(unit - catalog_p) / catalog_p
                    if diff_pct == 0:
                        base_score = min(base_score + 35, 100)
                    elif diff_pct < 0.05:
                        base_score = min(base_score + 25, 100)
                    elif diff_pct < 0.10:
                        base_score = min(base_score + 12, 100)

        scored.append((base_score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_product = scored[0]

    # Top 5 candidates for Gemini fallback
    top_candidates = [
        {'id': p['id'], 'name': p['name'], 'code': p['code'], 'price': p['price'],
         'aliases': ','.join(p['aliases']), 'score': s}
        for s, p in scored[:5]
    ]

    if best_score >= 85:
        confidence = 'HIGH'
    elif best_score >= 65:
        confidence = 'MEDIUM'
    else:
        confidence = 'LOW'

    return {
        'product':        best_product if best_score >= 65 else None,
        'score':          best_score,
        'confidence':     confidence,
        'top_candidates': top_candidates,
    }


def _run_ocr(image_b64: str):
    """Run receipt_ocr.py subprocess. Returns dict with is_receipt, reason, raw_text."""
    try:
        proc = subprocess.run(
            [sys.executable, _OCR_PY_PATH],
            input=json.dumps({'image': image_b64}),
            capture_output=True, text=True, timeout=30
        )
        return json.loads(proc.stdout)
    except Exception as e:
        return {'is_receipt': None, 'raw_text': '', 'reason': str(e)}


def _call_gemini(pil_img, prompt: str):
    """Single Gemini call — returns parsed JSON list of receipts."""
    import google.generativeai as genai
    api_key = os.getenv('GEMINI_API_KEY', '')
    if not api_key:
        raise ValueError('GEMINI_API_KEY not configured')
    genai.configure(api_key=api_key)
    model  = genai.GenerativeModel('gemini-2.0-flash')
    result = model.generate_content([pil_img, prompt])
    raw    = (result.text or '').replace('```json', '').replace('```', '').strip()
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, list) else [parsed]


# ── Prompt builders ────────────────────────────────────────────

_RECEIPT_RULES = """Rules:
- Extract EVERY receipt in the image — do not skip any
- Keep each receipt's items strictly within its own table boundaries
- RECEIPT NUMBER: 4-digit serial near the BOTTOM (e.g. 1813, 1850). NOT the date.
- PARTIAL PREFIX MATCHING: match first 2-4 chars against codes/aliases. "LD-910W" → LD Glow. Always prefer partial match over null.
- Use price × qty as confirmation signal — close match → MEDIUM confidence
- HIGH: code or name clearly matches. MEDIUM: partial/prefix/price match. LOW: best guess.
- null matched_product only when truly zero resemblance
- Use receipt price for line_total if different from catalog
- Dates written as D/M/YY"""

def _prompt_gemini_only(catalog_compact: str) -> str:
    return f"""You are an OCR and product matching assistant for Party World Shop, Nairobi.
This image may contain ONE or MULTIPLE cash sale receipts. Extract EVERY receipt.
Catalog (id|name|code|price|aliases):
{catalog_compact}

Return ONLY valid JSON array:
[{{"receipt_no":"string or null","date":"YYYY-MM-DD or null","customer":"string or null",
"total_written":number_or_null,"items":[{{"raw_text":"exactly as written","qty":number,
"unit_price":number_or_null,"line_total":number_or_null,
"matched_product":{{"variant_id":number,"name":"name","code":"code or null","catalog_price":number}} or null,
"confidence":"HIGH or MEDIUM or LOW","confidence_reason":"brief"}}],"notes":"any observations"}}]
{_RECEIPT_RULES}"""


def _prompt_extract_only() -> str:
    return """You are a receipt OCR assistant for Party World Shop, Nairobi.
This image may contain ONE or MULTIPLE cash sale receipts. Extract EVERY receipt.
Do NOT match products — just extract the raw written text.

Return ONLY valid JSON array:
[{"receipt_no":"string or null","date":"YYYY-MM-DD or null","customer":"string or null",
"total_written":number_or_null,
"items":[{"raw_text":"exactly as written in Particulars column","qty":number,
"unit_price":number_or_null,"line_total":number_or_null}],
"notes":"any observations"}]

Rules:
- Extract EVERY receipt — do not skip any
- RECEIPT NUMBER: 4-digit serial near BOTTOM. NOT the date.
- raw_text: copy exactly what is handwritten, do not interpret or clean up
- Dates written as D/M/YY"""


def _prompt_resolve_ambiguous(ambiguous_items: list) -> str:
    lines = []
    for i, it in enumerate(ambiguous_items):
        cands = '\n'.join(
            f"  - {c['id']}|{c['name']}|{c['code'] or '—'}|{c['price']}|{c['aliases']}"
            for c in it['candidates']
        )
        lines.append(
            f"{i+1}. raw_text=\"{it['raw_text']}\", qty={it['qty']}, "
            f"line_total={it['line_total']}\n   candidates:\n{cands}"
        )

    items_block = '\n'.join(lines)
    return f"""You are a product matching assistant for Party World Shop, Nairobi.
Match each item below to its best candidate. Products are Chinese-manufactured party supplies
with short codes (CXF, AQE, LD, YUB etc). Choose the closest match by code prefix, price, or alias.

{items_block}

Return ONLY valid JSON array, one object per item in order:
[{{"variant_id":number or null,"name":"name or null","code":"code or null",
"catalog_price":number or null,"confidence":"HIGH or MEDIUM or LOW",
"confidence_reason":"brief explanation"}}]

If truly no match possible, return null for all fields."""


@app.route('/receipts')
@login_required
def receipts():
    return render_template('receipts.html', **date_filter_context())


@app.route('/api/receipts/training/catalog-list')
@login_required
def training_catalog_list():
    """Lightweight catalog list for inline alias correction autocomplete."""
    return jsonify([{'id': p['id'], 'name': p['name'], 'code': p.get('code') or ''} for p in _CATALOG])


@app.route('/api/receipts/products')
@login_required
def receipts_products():
    # Reload from disk in case file was updated, then return compact format + full md
    _load_catalog()
    try:
        with open(_PRODUCTS_MD_PATH) as f:
            md = f.read()
    except Exception:
        md = ''
    return jsonify({'content': md, 'count': len(_CATALOG)})


@app.route('/api/receipts/scan', methods=['POST'])
@login_required
def receipts_scan():
    data      = request.get_json() or {}
    image_b64 = data.get('image', '')
    mime_type = data.get('mimeType', 'image/jpeg')
    mode      = data.get('mode', 'hybrid')  # 'gemini_only' | 'hybrid'

    if not image_b64:
        return jsonify({'error': 'No image provided'}), 400

    # ── OCR pre-filter ─────────────────────────────────────────
    try:
        ocr = _run_ocr(image_b64)
    except Exception:
        ocr = {'is_receipt': None, 'raw_text': ''}

    if ocr.get('is_receipt') is False:
        return jsonify({'not_a_receipt': True, 'reason': ocr.get('reason', 'Not a receipt')})

    # ── Decode image once ──────────────────────────────────────
    try:
        import PIL.Image
        img_bytes = _base64.b64decode(image_b64)
        pil_img   = PIL.Image.open(_io.BytesIO(img_bytes))
    except Exception as e:
        return jsonify({'error': 'Image decode failed', 'details': str(e)}), 400

    try:
        if mode == 'gemini_only':
            # ── GEMINI ONLY: one call with compact catalog ─────
            receipts_list = _call_gemini(pil_img, _prompt_gemini_only(_catalog_compact()))
            return jsonify({'receipts': receipts_list, 'mode': 'gemini_only'})

        else:
            # ── HYBRID: extract → Python match → Gemini for ambiguous only ──

            # Step 1: Gemini extracts raw line items — no catalog in prompt
            receipts_raw = _call_gemini(pil_img, _prompt_extract_only())

            # Step 2: Python fuzzy match every item
            ambiguous = []  # items needing Gemini resolve
            for receipt in receipts_raw:
                for item in receipt.get('items', []):
                    raw    = item.get('raw_text', '')
                    qty    = item.get('qty', 1)
                    total  = item.get('line_total')
                    match  = _fuzzy_match_item(raw, qty, total)

                    item['_match'] = match  # stash for step 3

                    if match['confidence'] == 'HIGH':
                        # Resolved — fill matched_product directly
                        p = match['product']
                        item['matched_product']   = {
                            'variant_id':   p['id'],
                            'name':         p['name'],
                            'code':         p['code'],
                            'catalog_price': p['price'],
                        }
                        item['confidence']        = 'HIGH'
                        item['confidence_reason'] = f'Python fuzzy match score {match["score"]}'
                    else:
                        # MEDIUM or LOW — queue for Gemini
                        item['matched_product']   = None
                        item['confidence']        = match['confidence']
                        item['confidence_reason'] = f'Python score {match["score"]} — sending to AI'
                        ambiguous.append({
                            'receipt_idx': receipts_raw.index(receipt),
                            'item_idx':    receipt['items'].index(item),
                            'raw_text':    raw,
                            'qty':         qty,
                            'line_total':  total,
                            'candidates':  match['top_candidates'],
                        })

            # Step 3: If any ambiguous items, resolve with Gemini (tiny prompt, no full catalog)
            if ambiguous:
                resolved = _call_gemini(pil_img, _prompt_resolve_ambiguous(ambiguous))
                # resolved is a list aligned with ambiguous[]
                for i, resolution in enumerate(resolved):
                    if i >= len(ambiguous):
                        break
                    ref  = ambiguous[i]
                    item = receipts_raw[ref['receipt_idx']]['items'][ref['item_idx']]
                    if resolution and resolution.get('variant_id'):
                        item['matched_product'] = {
                            'variant_id':    resolution['variant_id'],
                            'name':          resolution.get('name', ''),
                            'code':          resolution.get('code'),
                            'catalog_price': resolution.get('catalog_price', 0),
                        }
                        item['confidence']        = resolution.get('confidence', 'MEDIUM')
                        item['confidence_reason'] = resolution.get('confidence_reason', 'AI resolved')
                    else:
                        item['confidence']        = 'LOW'
                        item['confidence_reason'] = 'No match found'

            # Clean up internal stash
            for receipt in receipts_raw:
                for item in receipt.get('items', []):
                    item.pop('_match', None)

            high  = sum(1 for r in receipts_raw for it in r.get('items',[]) if it.get('confidence')=='HIGH')
            total_items = sum(len(r.get('items',[])) for r in receipts_raw)
            return jsonify({
                'receipts': receipts_raw,
                'mode': 'hybrid',
                'stats': {
                    'total_items':    total_items,
                    'python_resolved': high,
                    'gemini_resolved': len(ambiguous),
                }
            })

    except Exception as e:
        return jsonify({'error': 'Failed to parse receipt', 'details': str(e)}), 500


@app.route('/api/receipts/insert-odoo', methods=['POST'])
@login_required
def receipts_insert_odoo():
    data       = request.get_json() or {}
    receipt_no = data.get('receipt_no')
    order_date = data.get('date') or date.today().isoformat()
    customer   = data.get('customer')
    items      = data.get('items', [])

    try:
        cfg = get_active_db_config()
        conn = psycopg2.connect(**cfg)
        cur  = conn.cursor()

        # Get WalkIn partner
        cur.execute("SELECT id FROM res_partner WHERE name ILIKE '%walk%in%' OR name ILIKE '%walkin%' ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT id FROM res_partner WHERE customer_rank > 0 ORDER BY id LIMIT 1")
            row = cur.fetchone()
        partner_id = row[0] if row else 1

        # Get company and user
        cur.execute("SELECT id FROM res_company ORDER BY id LIMIT 1")
        company_id = (cur.fetchone() or [1])[0]
        cur.execute("SELECT id FROM res_users WHERE active=true ORDER BY id LIMIT 1")
        user_id = (cur.fetchone() or [1])[0]

        # Next order ID
        cur.execute("SELECT nextval('sale_order_id_seq')")
        order_id = cur.fetchone()[0]

        # Build order name from ir.sequence
        cur.execute("SELECT prefix, padding, number_next FROM ir_sequence WHERE code='sale.order' LIMIT 1")
        seq_row = cur.fetchone()
        if seq_row:
            prefix  = (seq_row[0] or 'S').replace('%(year)s', str(date.today().year)).replace('%(month)s', f"{date.today().month:02d}")
            padding = seq_row[1] or 5
            num     = seq_row[2] or order_id
            order_name = f"{prefix}{str(num).zfill(padding)}"
            # Advance sequence
            cur.execute("UPDATE ir_sequence SET number_next=number_next+1 WHERE code='sale.order'")
        else:
            order_name = f"S{str(order_id).zfill(5)}"

        notes  = f"Receipt #{receipt_no}" if receipt_no else ''
        client_ref = str(receipt_no) if receipt_no else None
        cur.execute(
            """INSERT INTO sale_order
               (id, name, date_order, state, partner_id, partner_invoice_id, partner_shipping_id,
                company_id, user_id, note, client_order_ref, picking_policy, create_uid, write_uid, create_date, write_date)
               VALUES (%s,%s,%s,'sale',%s,%s,%s,%s,%s,%s,%s,'direct',%s,%s,NOW(),NOW())""",
            (order_id, order_name, f"{order_date} 12:00:00",
             partner_id, partner_id, partner_id, company_id, user_id, notes, client_ref, user_id, user_id)
        )

        # UOM
        cur.execute("SELECT id FROM uom_uom WHERE name->>'en_US' ILIKE '%unit%' LIMIT 1")
        uom_row = cur.fetchone()
        uom_id  = uom_row[0] if uom_row else None

        for item in items:
            mp = item.get('matched_product') or {}
            variant_id = mp.get('variant_id')
            if not variant_id:
                continue
            cur.execute("SELECT nextval('sale_order_line_id_seq')")
            line_id    = cur.fetchone()[0]
            qty        = item.get('qty') or 1  # null qty (receipt stamp confusion) defaults to 1
            price      = item.get('unit_price') or mp.get('catalog_price', 0)
            name       = mp.get('name', '')
            cur.execute(
                """INSERT INTO sale_order_line
                   (id, order_id, product_id, name, product_uom_qty, price_unit, state,
                    product_uom, customer_lead, create_uid, write_uid, create_date, write_date)
                   VALUES (%s,%s,%s,%s,%s,%s,'sale',%s,0,%s,%s,NOW(),NOW())""",
                (line_id, order_id, variant_id, name, qty, price,
                 uom_id, user_id, user_id)
            )

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'order_name': order_name, 'order_id': order_id})
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({'error': 'Insert failed', 'details': str(e)}), 500


# ── Receipt Scanner Training ──────────────────────────────────

_training_state: dict = {
    'running': False, 'session_id': None,
    'total': 0, 'processed': 0, 'skipped': 0, 'failed': 0,
    'current': '', 'current_image': None,   # b64 thumbnail of current image
    'started_at': None, 'finished_at': None, 'stopped_at': None,
    'folder_path': '',
    # Control flags (set via API, read by background thread)
    'pause_requested': False, 'stop_requested': False, 'paused': False,
    'counters': {'scanned': 0, 'receipt_no': 0, 'date_total': 0,
                 'not_found': 0, 'errors': 0, 'not_receipt': 0},
    'live_feed': [],       # last 10 lightweight result entries
    'results': [], 'alias_suggestions': {},
    # Gemini post-training review
    'gemini_review_status': None,  # None | 'running' | 'done' | 'error: ...' | 'skipped: ...'
    'gemini_aliases': [],          # reviewed alias list from Gemini
}
_training_lock = threading.Lock()
_TRAINING_CACHE_FILE = '.pwtraining_cache.json'
_TRAINING_RUNS_DIR   = os.path.join(os.path.dirname(__file__), 'training_runs')
os.makedirs(_TRAINING_RUNS_DIR, exist_ok=True)


def _training_cache_path(folder_path):
    return os.path.join(folder_path, _TRAINING_CACHE_FILE)


def _load_training_cache(folder_path):
    """Return dict {filename → raw_ocr_entry} from cache file.
    Only stores OCR results (is_receipt, receipts_raw) — NOT comparison results,
    so comparisons are always re-run with the current (updated) catalog/aliases.
    """
    try:
        with open(_training_cache_path(folder_path)) as f:
            data = json.load(f)
        if data.get('v') == 2:
            # v2: ocr-only cache — always safe to use
            return data.get('results', {})
        if data.get('v') == 1:
            # v1: old format stored full comparison results, no receipts_raw.
            # Only keep entries that have receipts_raw (manually converted).
            # Others are silently dropped → will re-run through Gemini.
            return {k: v for k, v in data.get('results', {}).items()
                    if v.get('receipts_raw') is not None}
    except Exception:
        pass
    return {}


def _save_cache_entry(folder_path, filename, entry):
    """Save only the OCR scan result (not comparison) to cache.
    Strips comparison data so the cache is catalog-independent.
    """
    path = _training_cache_path(folder_path)
    try:
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            data = {'v': 2, 'results': {}}
        # Store only what Gemini extracted — strip comparison results
        ocr_entry = {
            'file':        entry.get('file'),
            'status':      entry.get('status'),
            'reason':      entry.get('reason'),     # for not_receipt
            'error':       entry.get('error'),       # for error
            # Store raw receipts with only OCR fields (no comparison)
            'receipts_raw': [
                {
                    'receipt_no':    r.get('receipt_no'),
                    'date':          r.get('date'),
                    'total_written': r.get('total_written'),
                    'items':         r.get('items', []),
                }
                for r in entry.get('receipts_raw', [])
            ],
        }
        data['results'][filename] = ocr_entry
        data['v'] = 2
        with open(path, 'w') as f:
            json.dump(data, f, default=str)
    except Exception:
        pass


def _refresh_price_history(cfg):
    """Query Odoo for actual sold prices per product, save to price_history.json."""
    global _price_history
    try:
        conn = psycopg2.connect(**cfg)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT sol.product_id::text AS vid,
                   MIN(sol.price_unit)  AS p_min,
                   MAX(sol.price_unit)  AS p_max,
                   AVG(sol.price_unit)  AS p_mean,
                   COUNT(*)             AS cnt
            FROM sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            WHERE so.state IN ('sale','done') AND sol.price_unit > 0
            GROUP BY sol.product_id
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        hist = {}
        for r in rows:
            hist[r['vid']] = {
                'min':   round(float(r['p_min']), 2),
                'max':   round(float(r['p_max']), 2),
                'mean':  round(float(r['p_mean']), 2),
                'count': int(r['cnt']),
            }
        _price_history = hist
        with open(_PRICE_HIST_PATH, 'w') as f:
            json.dump(hist, f, indent=2)
    except Exception as e:
        print(f'price_history refresh error: {e}')


def _lookup_odoo_receipt(cfg, receipt_no, receipt_date, receipt_total=None):
    """
    Return (lines, match_quality) for best-matching Odoo order.
    match_quality: 'receipt_no' | 'date_total' | 'date_only' | 'none'
    Priority:
      1. Receipt # in client_order_ref (Customer Reference field)
      2. Date + total amount match ±5%  (historical manually-entered orders)
      3. Date only — returns lines but flags as uncertain
    """
    LINE_SQL = """
        SELECT so.id AS order_id, so.name AS order_name,
               so.client_order_ref AS customer_ref,
               so.date_order::date AS sale_date,
               so.amount_untaxed AS order_total,
               sol.product_id AS variant_id,
               pt.name->>'en_US' AS product_name,
               sol.product_uom_qty AS qty, sol.price_unit
        FROM sale_order so
        JOIN sale_order_line sol ON sol.order_id = so.id
        JOIN product_product pp  ON pp.id = sol.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE {where} AND so.state IN ('sale','done')
        ORDER BY so.date_order DESC
    """
    try:
        conn = psycopg2.connect(**cfg)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # ── 1. Receipt number match via Customer Reference field ──
        if receipt_no:
            cur.execute(LINE_SQL.format(where="so.client_order_ref ILIKE %s"),
                        (f'%{receipt_no}%',))
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                cur.close(); conn.close()
                return rows, 'receipt_no'

        # ── 2. Date + total amount match ───────────────────────
        if receipt_date and receipt_total and receipt_total > 0:
            # Get candidate orders on that date
            cur.execute("""
                SELECT id, name, amount_untaxed
                FROM sale_order
                WHERE date_order::date = %s AND state IN ('sale','done')
            """, (receipt_date,))
            candidates = cur.fetchall()
            best_order_id = None
            best_diff     = float('inf')
            for c in candidates:
                amt = float(c['amount_untaxed'] or 0)
                if amt > 0:
                    diff = abs(amt - receipt_total) / receipt_total
                    if diff < 0.05 and diff < best_diff:  # within 5%
                        best_diff     = diff
                        best_order_id = c['id']
            if best_order_id:
                cur.execute(LINE_SQL.format(where="so.id = %s"),
                            (best_order_id,))
                rows = [dict(r) for r in cur.fetchall()]
                if rows:
                    cur.close(); conn.close()
                    return rows, 'date_total'

        # ── 3. Date only — uncertain, may be multiple orders ───
        if receipt_date:
            cur.execute(LINE_SQL.format(where="so.date_order::date = %s") + " LIMIT 80",
                        (receipt_date,))
            rows = [dict(r) for r in cur.fetchall()]
            cur.close(); conn.close()
            return rows, 'date_only' if rows else 'none'

        cur.close(); conn.close()
    except Exception as e:
        print(f'odoo lookup error: {e}')
    return [], 'none'


def _compare_receipt(scanned_items, odoo_lines):
    """Compare scanner extracted items vs Odoo ground truth. Returns comparison list."""
    comparison = []
    odoo_used  = set()

    for item in scanned_items:
        raw   = item.get('raw_text', '') or ''
        try:
            qty = float(item.get('qty') or 1)
        except (TypeError, ValueError):
            qty = 1.0
        try:
            total = float(item.get('line_total')) if item.get('line_total') is not None else None
        except (TypeError, ValueError):
            total = None
        unit  = (total / qty) if total and qty else None

        fm         = _fuzzy_match_item(raw, qty, total)
        scanner_id = fm['product']['id'] if fm['product'] else None

        # Find best Odoo match — first by product_id, then by price
        odoo_match = None
        match_type = 'none'
        for i, ol in enumerate(odoo_lines):
            if i in odoo_used: continue
            if scanner_id and ol['variant_id'] == scanner_id:
                odoo_match = ol; match_type = 'product'; odoo_used.add(i); break
        if not odoo_match and unit:
            for i, ol in enumerate(odoo_lines):
                if i in odoo_used: continue
                ol_price = float(ol['price_unit'] or 0)
                if ol_price > 0 and abs(ol_price - unit) / ol_price < 0.20:
                    odoo_match = ol; match_type = 'price'; odoo_used.add(i); break

        if odoo_match and scanner_id and scanner_id == odoo_match['variant_id']:
            result = 'correct'
        elif odoo_match:
            result = 'wrong_product'  # alias opportunity
        else:
            result = 'no_odoo'

        comparison.append({
            'raw_text':  raw,
            'qty':       qty,
            'line_total': total,
            'scanner':   {'variant_id': scanner_id,
                          'name': fm['product']['name'] if fm['product'] else None,
                          'confidence': fm['confidence'], 'score': fm['score']},
            'odoo':      {'variant_id': odoo_match['variant_id'] if odoo_match else None,
                          'name': odoo_match['product_name'] if odoo_match else None,
                          'price': float(odoo_match['price_unit']) if odoo_match else None,
                          'match_type': match_type},
            'result':    result,
        })

    # Odoo lines not matched by scanner — missed items
    for i, ol in enumerate(odoo_lines):
        if i not in odoo_used:
            comparison.append({
                'raw_text': None, 'qty': float(ol['qty'] or 1), 'line_total': None,
                'scanner': None,
                'odoo': {'variant_id': ol['variant_id'], 'name': ol['product_name'],
                         'price': float(ol['price_unit'] or 0), 'match_type': 'missed'},
                'result': 'missed',
            })
    return comparison


def _make_thumbnail(img_bytes, max_w=300, max_h=420, quality=55):
    """Return base64 JPEG thumbnail string, or None on failure."""
    try:
        import PIL.Image as _PILImage
        img = _PILImage.open(_io.BytesIO(img_bytes))
        img.thumbnail((max_w, max_h), _PILImage.LANCZOS)
        buf = _io.BytesIO()
        img.convert('RGB').save(buf, format='JPEG', quality=quality)
        return _base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _save_run_record(state, session_id, stopped=False):
    """Save a compact run record to training_runs/ for history comparison."""
    try:
        results  = state.get('results', [])
        all_comp = [c for r in results if r.get('status') == 'scanned'
                      for rec in r.get('receipts', []) for c in rec.get('comparison', [])]
        correct  = sum(1 for c in all_comp if c['result'] == 'correct')
        total_c  = len([c for c in all_comp if c['result'] != 'missed'])
        record = {
            'session_id':      session_id,
            'folder':          state.get('folder_path', ''),
            'started_at':      state.get('started_at'),
            'finished_at':     state.get('finished_at') or state.get('stopped_at'),
            'stopped':         stopped,
            'total_images':    state.get('total', 0),
            'processed':       state.get('processed', 0),
            'skipped':         state.get('skipped', 0),
            'summary': {
                'receipts_scanned': sum(1 for r in results if r.get('status') == 'scanned'),
                'not_receipts':     sum(1 for r in results if r.get('status') == 'not_receipt'),
                'errors':           sum(1 for r in results if r.get('status') == 'error'),
                'total_items':      total_c,
                'correct':          correct,
                'accuracy':         round(correct / total_c * 100, 1) if total_c else 0,
                'alias_suggestions': len(state.get('alias_suggestions', {})),
            },
            'counters': state.get('counters', {}),
        }
        path = os.path.join(_TRAINING_RUNS_DIR, f'{session_id}.json')
        with open(path, 'w') as f:
            json.dump(record, f, default=str, indent=2)
    except Exception as e:
        print(f'_save_run_record error: {e}')


def _run_training(folder_path, session_id, db_cfg, resume=False):
    """Background thread: scan all images, compare to Odoo, collect alias suggestions."""
    import PIL.Image
    supported = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.bmp'}
    images    = sorted([
        os.path.join(folder_path, fn) for fn in os.listdir(folder_path)
        if os.path.splitext(fn.lower())[1] in supported
    ])
    api_key = os.getenv('GEMINI_API_KEY', '')

    # Load cache for resume — cache contains only raw OCR results (no comparison)
    # Comparison is ALWAYS re-run fresh so new aliases take effect every run
    ocr_cache = _load_training_cache(folder_path) if resume else {}

    with _training_lock:
        _training_state.update({
            'total': len(images),
            'processed': 0,
            'skipped': 0,
            'failed': 0,
            'results': [],
            'alias_suggestions': {},
            'counters': {'scanned': 0, 'receipt_no': 0, 'date_total': 0,
                         'not_found': 0, 'errors': 0, 'not_receipt': 0},
            'live_feed': [],
            'current': '', 'current_image': None,
            'folder_path': folder_path,
        })

    def _process_receipts_raw(fn, receipts_raw, db_cfg):
        """Run comparison on raw OCR output, collect alias suggestions. Returns receipt_results list."""
        receipt_results = []
        for receipt in receipts_raw:
            receipt_no    = receipt.get('receipt_no')
            receipt_date  = receipt.get('date')
            receipt_total = receipt.get('total_written')
            items         = receipt.get('items', [])
            odoo_lines, match_quality = _lookup_odoo_receipt(
                db_cfg, receipt_no, receipt_date, receipt_total)
            comparison = _compare_receipt(items, odoo_lines)

            correct = sum(1 for c in comparison if c['result'] == 'correct')
            total_c = len([c for c in comparison if c['result'] != 'missed'])

            # Collect alias suggestions from wrong_product results
            with _training_lock:
                for c in comparison:
                    if c['result'] == 'wrong_product' and c['raw_text'] and c['odoo']['variant_id']:
                        key = f"{c['raw_text'].strip().lower()}|{c['odoo']['variant_id']}"
                        s   = _training_state['alias_suggestions']
                        if key not in s:
                            s[key] = {'raw_text': c['raw_text'].strip(),
                                      'variant_id': c['odoo']['variant_id'],
                                      'product_name': c['odoo']['name'],
                                      'count': 0, 'files': []}
                        s[key]['count'] += 1
                        if fn not in s[key]['files']:
                            s[key]['files'].append(fn)

            receipt_results.append({
                'receipt_no':    receipt_no,
                'date':          receipt_date,
                'odoo_found':    bool(odoo_lines),
                'odoo_order':    odoo_lines[0]['order_name'] if odoo_lines else None,
                'match_quality': match_quality,
                'total_items':   total_c,
                'correct':       correct,
                'accuracy':      round(correct / total_c, 3) if total_c else None,
                'comparison':    comparison,
            })
        return receipt_results

    for img_path in images:
        fn = os.path.basename(img_path)

        # ── If OCR is cached, skip Gemini call but ALWAYS re-run comparison ──
        if fn in ocr_cache:
            cached = ocr_cache[fn]
            cached_status = cached.get('status')

            if cached_status == 'not_receipt':
                entry = {'file': fn, 'status': 'not_receipt',
                         'reason': cached.get('reason', ''), 'receipts': []}
                with _training_lock:
                    _training_state['results'].append(entry)
                    _training_state['processed'] += 1
                    _training_state['skipped'] += 1
                    _training_state['counters']['not_receipt'] += 1
                    _training_state['live_feed'] = [_feed_entry(entry)] + _training_state['live_feed'][:9]
                continue

            if cached_status == 'error':
                entry = {'file': fn, 'status': 'error',
                         'error': cached.get('error', ''), 'receipts': []}
                with _training_lock:
                    _training_state['results'].append(entry)
                    _training_state['failed'] += 1
                    _training_state['processed'] += 1
                    _training_state['skipped'] += 1
                    _training_state['counters']['errors'] += 1
                    _training_state['live_feed'] = [_feed_entry(entry)] + _training_state['live_feed'][:9]
                continue

            if cached_status == 'scanned':
                # Re-run comparison with current catalog (aliases may have changed)
                receipts_raw_cached = cached.get('receipts_raw', [])
                receipt_results = _process_receipts_raw(fn, receipts_raw_cached, db_cfg)
                entry = {'file': fn, 'status': 'scanned', 'receipts': receipt_results}
                with _training_lock:
                    _training_state['results'].append(entry)
                    _training_state['processed'] += 1
                    _training_state['skipped'] += 1
                    _training_state['counters']['scanned'] += 1
                    for rec in receipt_results:
                        mq = rec.get('match_quality', 'none')
                        if mq == 'receipt_no':
                            _training_state['counters']['receipt_no'] += 1
                        elif mq == 'date_total':
                            _training_state['counters']['date_total'] += 1
                        elif not rec.get('odoo_found'):
                            _training_state['counters']['not_found'] += 1
                    _training_state['live_feed'] = [_feed_entry(entry)] + _training_state['live_feed'][:9]
                continue

        # ── Not cached: run Gemini OCR ───────────────────────────

        # Generate thumbnail for UI preview
        try:
            img_bytes = open(img_path, 'rb').read()
        except Exception as e:
            with _training_lock:
                entry = {'file': fn, 'status': 'error', 'error': str(e), 'receipts': []}
                _training_state['results'].append(entry)
                _training_state['failed'] += 1
                _training_state['processed'] += 1
                _training_state['counters']['errors'] += 1
                _training_state['live_feed'] = [_feed_entry(entry)] + _training_state['live_feed'][:9]
            _save_cache_entry(folder_path, fn, {'file': fn, 'status': 'error', 'error': str(e), 'receipts_raw': []})
            continue

        thumb_b64 = _make_thumbnail(img_bytes)

        with _training_lock:
            _training_state['current']       = fn
            _training_state['current_image'] = thumb_b64

        # ── Pause / stop check ──────────────────────────────────
        import time as _time
        while True:
            with _training_lock:
                stop  = _training_state.get('stop_requested', False)
                pause = _training_state.get('pause_requested', False)
            if stop:
                break
            if pause:
                with _training_lock:
                    _training_state['paused'] = True
                _time.sleep(0.5)
                continue
            with _training_lock:
                _training_state['paused'] = False
            break

        with _training_lock:
            if _training_state.get('stop_requested'):
                break

        try:
            image_b64 = _base64.b64encode(img_bytes).decode()
            ocr       = _run_ocr(image_b64)

            if ocr.get('is_receipt') is False:
                entry = {'file': fn, 'status': 'not_receipt',
                         'reason': ocr.get('reason', ''), 'receipts': []}
                with _training_lock:
                    _training_state['results'].append(entry)
                    _training_state['processed'] += 1
                    _training_state['counters']['not_receipt'] += 1
                    _training_state['live_feed'] = [_feed_entry(entry)] + _training_state['live_feed'][:9]
                _save_cache_entry(folder_path, fn, {'file': fn, 'status': 'not_receipt',
                                                    'reason': ocr.get('reason', ''), 'receipts_raw': []})
                continue

            pil_img      = PIL.Image.open(_io.BytesIO(img_bytes))
            receipts_raw = _call_gemini(pil_img, _prompt_extract_only()) if api_key else []

            # Save OCR results to cache (catalog-independent)
            _save_cache_entry(folder_path, fn, {'file': fn, 'status': 'scanned',
                                                 'receipts_raw': receipts_raw})

            receipt_results = _process_receipts_raw(fn, receipts_raw, db_cfg)
            entry = {'file': fn, 'status': 'scanned', 'receipts': receipt_results}
            with _training_lock:
                _training_state['results'].append(entry)
                _training_state['processed'] += 1
                _training_state['counters']['scanned'] += 1
                # Update match quality counters
                for rec in receipt_results:
                    mq = rec.get('match_quality', 'none')
                    if mq == 'receipt_no':
                        _training_state['counters']['receipt_no'] += 1
                    elif mq == 'date_total':
                        _training_state['counters']['date_total'] += 1
                    elif not rec.get('odoo_found'):
                        _training_state['counters']['not_found'] += 1
                _training_state['live_feed'] = [_feed_entry(entry)] + _training_state['live_feed'][:9]

        except Exception as e:
            entry = {'file': fn, 'status': 'error', 'error': str(e), 'receipts': []}
            with _training_lock:
                _training_state['results'].append(entry)
                _training_state['failed'] += 1
                _training_state['processed'] += 1
                _training_state['counters']['errors'] += 1
                _training_state['live_feed'] = [_feed_entry(entry)] + _training_state['live_feed'][:9]
            _save_cache_entry(folder_path, fn, {'file': fn, 'status': 'error',
                                                 'error': str(e), 'receipts_raw': []})

    # Clear current image
    with _training_lock:
        _training_state['current_image']   = None
        _training_state['current']         = ''
        _training_state['paused']          = False
        was_stopped = _training_state.get('stop_requested', False)

    if was_stopped:
        with _training_lock:
            _training_state['running']     = False
            _training_state['stopped_at']  = _dt.now().isoformat()
            _training_state['stop_requested'] = False
        _save_run_record(_training_state, session_id, stopped=True)
        return   # Don't refresh price history on manual stop

    # Refresh price history from Odoo after a full run
    try:
        _refresh_price_history(db_cfg)
    except Exception:
        pass

    with _training_lock:
        _training_state['running']     = False
        _training_state['finished_at'] = _dt.now().isoformat()
        save_path = os.path.join(os.path.dirname(__file__), f'training_{session_id}.json')
        try:
            with open(save_path, 'w') as f:
                json.dump(_training_state, f, default=str, indent=2)
        except Exception:
            pass
        _save_run_record(_training_state, session_id, stopped=False)

    # ── Auto Gemini alias review ──────────────────────────────
    try:
        _gemini_review_aliases()
    except Exception as e:
        print(f'gemini alias review error: {e}')
        with _training_lock:
            _training_state['gemini_review_status'] = f'error: {e}'


def _gemini_review_thread():
    """Wrapper for running _gemini_review_aliases in a background thread."""
    try:
        _gemini_review_aliases()
    except Exception as e:
        print(f'gemini review thread error: {e}')
        with _training_lock:
            _training_state['gemini_review_status'] = f'error: {e}'


def _gemini_review_aliases():
    """
    After training, ask Gemini to review all wrong_product pairs and decide:
    - Which aliases are confident (clear handwriting variation / abbreviation)
    - Which are uncertain (need human judgment)
    Stores results in _training_state['gemini_aliases'].
    """
    import google.generativeai as genai

    api_key = os.getenv('GEMINI_API_KEY', '')
    if not api_key:
        with _training_lock:
            _training_state['gemini_review_status'] = 'skipped: no API key'
        return

    with _training_lock:
        _training_state['gemini_review_status'] = 'running'
        suggestions = dict(_training_state.get('alias_suggestions', {}))

    if not suggestions:
        with _training_lock:
            _training_state['gemini_review_status'] = 'done'
            _training_state['gemini_aliases'] = []
        return

    # Build compact catalog for context
    catalog_compact = _catalog_compact()

    # Build the pairs list — aggregate by raw_text, collect all candidate products
    # Group by raw_text to handle same raw → multiple candidate products
    raw_to_candidates: dict = {}
    for key, s in suggestions.items():
        raw = s['raw_text'].strip()
        if raw not in raw_to_candidates:
            raw_to_candidates[raw] = []
        raw_to_candidates[raw].append({
            'variant_id':   s['variant_id'],
            'product_name': s['product_name'],
            'count':        s['count'],
            'key':          key,
        })

    # Build prompt items (cap at 200 to avoid token limits)
    items = []
    for raw, candidates in list(raw_to_candidates.items())[:200]:
        # Pick highest-count candidate as primary suggestion
        best = max(candidates, key=lambda c: c['count'])
        items.append({
            'raw_text':       raw,
            'suggested_id':   best['variant_id'],
            'suggested_name': best['product_name'],
            'seen_count':     sum(c['count'] for c in candidates),
            'alt_candidates': [
                {'variant_id': c['variant_id'], 'name': c['product_name']}
                for c in candidates if c['variant_id'] != best['variant_id']
            ][:3],
        })

    prompt = f"""You are a product alias expert for Party World Shop, Nairobi — a party supplies retailer.
Staff write product names by hand on receipts using abbreviations, codes, and shorthand.
Your job: review each handwritten text and decide if it should be added as an alias to the catalog.

Catalog (id|name|code|price|existing_aliases):
{catalog_compact}

Review these {len(items)} handwritten texts. For each, decide:
1. The CORRECT product match (variant_id) — may differ from suggested
2. Confidence: HIGH (obvious abbreviation/typo/code) | MEDIUM (likely but not certain) | LOW (guess) | REJECT (wrong match, do not add)

Key context:
- SHORT CODES like "HM-F", "HM-Fx", "HM-F1", "Hn-Fx" → all mean HM-FX product
- "xy", "Xy", "XY", "xly", "Ty", "vy", "LYU" → XY-Florescent Stickers  
- "45-Sm", "45-SM", "45 SM", "4S-Sm", "45-5m", "45-5", "45-2M", "MS-BRA SM" → Contact Paper products
- "baloon", "Baloon", "balooon" → balloon typos (check candidate name)
- "Cartoon Cup", "Cartoon CUPS" → Cartoon Cups product
- "gold Candle", "Gold Candle" → Gold Number Candles
- "FOIL number (32)", "FOLL number (32)" → 32 Inch Number Foil
- REJECT if the suggested product makes no semantic sense (e.g. "baloon tape" → Contact Paper is wrong)
- REJECT if raw_text is too ambiguous to confidently assign (e.g. single letter like "XE", "XF")

Return ONLY valid JSON array, one object per item in the same order:
[{{"raw_text":"exact input text","variant_id":number_or_null,"product_name":"name or null",
"confidence":"HIGH|MEDIUM|LOW|REJECT","reason":"one line explanation"}}]"""

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')

    # Split into batches of 50 to stay within token limits
    batch_size = 50
    all_results = []
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        batch_prompt = prompt.replace(f"these {len(items)} handwritten texts",
                                      f"these {len(batch)} handwritten texts")
        batch_json   = json.dumps(batch, ensure_ascii=False)
        try:
            result   = model.generate_content([batch_json, batch_prompt])
            raw      = (result.text or '').replace('```json', '').replace('```', '').strip()
            parsed   = json.loads(raw)
            all_results.extend(parsed if isinstance(parsed, list) else [parsed])
        except Exception as e:
            # On batch failure, mark all as LOW
            for item in batch:
                all_results.append({
                    'raw_text':    item['raw_text'],
                    'variant_id':  item['suggested_id'],
                    'product_name': item['suggested_name'],
                    'confidence':  'LOW',
                    'reason':      f'batch error: {e}',
                })

    # Merge results back with original suggestion keys
    raw_to_key = {}
    for key, s in suggestions.items():
        raw_to_key[s['raw_text'].strip()] = key

    gemini_aliases = []
    for res in all_results:
        raw  = (res.get('raw_text') or '').strip()
        conf = res.get('confidence', 'LOW')
        vid  = res.get('variant_id')
        if conf == 'REJECT' or not vid:
            gemini_aliases.append({
                'raw_text':    raw,
                'variant_id':  None,
                'product_name': res.get('product_name'),
                'confidence':  'REJECT',
                'reason':      res.get('reason', ''),
                'action':      'reject',   # pre-set action
                'key':         raw_to_key.get(raw, raw),
                'count':       raw_to_candidates.get(raw, [{}])[0].get('count', 1)
                               if raw in raw_to_candidates else 1,
            })
        else:
            # Find the original count
            orig_count = sum(
                c['count'] for c in raw_to_candidates.get(raw, [])
            ) or 1
            gemini_aliases.append({
                'raw_text':    raw,
                'variant_id':  vid,
                'product_name': res.get('product_name', ''),
                'confidence':  conf,
                'reason':      res.get('reason', ''),
                'action':      'add' if conf == 'HIGH' else 'pending',
                'key':         raw_to_key.get(raw, raw),
                'count':       orig_count,
            })

    # Sort: HIGH first, then MEDIUM, then LOW, then REJECT
    order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2, 'REJECT': 3}
    gemini_aliases.sort(key=lambda x: (order.get(x['confidence'], 4), -x.get('count', 0)))

    # ── Auto-apply HIGH and MEDIUM confidence aliases to products.md ──
    auto_added = 0
    auto_errors = []
    try:
        with open(_PRODUCTS_MD_PATH) as f:
            lines = f.readlines()
        new_lines = list(lines)
        for a in gemini_aliases:
            conf = a.get('confidence')
            if conf not in ('HIGH', 'MEDIUM'):
                continue
            raw_text   = (a.get('raw_text') or '').strip()
            variant_id = a.get('variant_id')
            if not raw_text or not variant_id:
                continue
            updated = False
            for idx, line in enumerate(new_lines):
                if line.startswith('|') and '---' not in line and 'variant_id' not in line:
                    cols = line.rstrip('\n').split('|')
                    if len(cols) >= 6:
                        try:
                            if int(cols[1].strip()) == int(variant_id):
                                existing = cols[5].strip()
                                if raw_text.lower() not in existing.lower():
                                    sep     = ', ' if existing else ''
                                    cols[5] = f' {existing}{sep}{raw_text} '
                                new_lines[idx] = '|'.join(cols) + '\n'
                                updated = True
                                break
                        except Exception:
                            pass
            if updated:
                a['action'] = 'added'
                auto_added += 1
            else:
                auto_errors.append(raw_text)
        with open(_PRODUCTS_MD_PATH, 'w') as f:
            f.writelines(new_lines)
        _load_catalog()
    except Exception as e:
        auto_errors.append(str(e))

    with _training_lock:
        _training_state['gemini_aliases']          = gemini_aliases
        _training_state['gemini_review_status']    = 'done'
        _training_state['gemini_auto_added']       = auto_added
        _training_state['gemini_auto_errors']      = auto_errors


def _feed_entry(entry):
    """Lightweight summary of a result entry for the live feed."""
    if entry['status'] == 'scanned' and entry.get('receipts'):
        rec = entry['receipts'][0]
        return {
            'file': entry['file'], 'status': 'scanned',
            'receipt_no': rec.get('receipt_no'), 'date': rec.get('date'),
            'odoo_found': rec.get('odoo_found'), 'odoo_order': rec.get('odoo_order'),
            'match_quality': rec.get('match_quality', 'none'),
            'total_items': rec.get('total_items', 0), 'correct': rec.get('correct', 0),
            'accuracy': rec.get('accuracy'),
        }
    return {'file': entry['file'], 'status': entry['status'],
            'error': entry.get('error', ''), 'reason': entry.get('reason', '')}


@app.route('/receipts/training')
@login_required
def receipts_training():
    return render_template('receipts_training.html', **date_filter_context())


@app.route('/api/receipts/training/start', methods=['POST'])
@login_required
def training_start():
    data        = request.get_json() or {}
    folder_path = data.get('folder', '').strip()
    resume      = bool(data.get('resume', False))

    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({'error': f'Folder not found: {folder_path}'}), 400

    with _training_lock:
        if _training_state['running']:
            return jsonify({'error': 'Training already running'}), 409
        session_id = _dt.now().strftime('%Y%m%d_%H%M%S')
        _training_state.update({
            'running': True, 'session_id': session_id,
            'started_at': _dt.now().isoformat(), 'finished_at': None,
        })

    # Check how many files are already cached
    cached_count = len(_load_training_cache(folder_path)) if resume else 0

    db_cfg = get_active_db_config()
    t = threading.Thread(target=_run_training,
                         args=(folder_path, session_id, db_cfg),
                         kwargs={'resume': resume}, daemon=True)
    t.start()
    return jsonify({'session_id': session_id, 'folder': folder_path,
                    'resume': resume, 'cached': cached_count})


@app.route('/api/receipts/training/pause', methods=['POST'])
@login_required
def training_pause():
    with _training_lock:
        if not _training_state['running']:
            return jsonify({'error': 'Not running'}), 400
        _training_state['pause_requested'] = True
    return jsonify({'ok': True, 'status': 'pausing'})


@app.route('/api/receipts/training/resume-thread', methods=['POST'])
@login_required
def training_resume_thread():
    with _training_lock:
        _training_state['pause_requested'] = False
        _training_state['paused']          = False
    return jsonify({'ok': True, 'status': 'resumed'})


@app.route('/api/receipts/training/stop', methods=['POST'])
@login_required
def training_stop():
    with _training_lock:
        if not _training_state['running']:
            return jsonify({'error': 'Not running'}), 400
        _training_state['stop_requested'] = True
        _training_state['pause_requested'] = False  # unblock if paused
    return jsonify({'ok': True, 'status': 'stopping'})


@app.route('/api/receipts/training/gemini-review')
@login_required
def training_gemini_review():
    """Return Gemini-reviewed alias list + current review status."""
    with _training_lock:
        status     = _training_state.get('gemini_review_status')
        aliases    = list(_training_state.get('gemini_aliases', []))
        auto_added = _training_state.get('gemini_auto_added', 0)
    return jsonify({'status': status, 'aliases': aliases, 'auto_added': auto_added})


@app.route('/api/receipts/training/gemini-review/run', methods=['POST'])
@login_required
def training_gemini_review_run():
    """Manually trigger a Gemini alias review of the current training results."""
    with _training_lock:
        if _training_state.get('running'):
            return jsonify({'error': 'Training is still running'}), 409
        if not _training_state.get('alias_suggestions'):
            return jsonify({'error': 'No alias suggestions found — run training first'}), 400
        if _training_state.get('gemini_review_status') == 'running':
            return jsonify({'ok': True, 'status': 'already running'})
        _training_state['gemini_review_status'] = 'running'
        _training_state['gemini_aliases']       = []
        _training_state['gemini_auto_added']    = 0
        _training_state['gemini_auto_errors']   = []

    t = threading.Thread(target=_gemini_review_thread, daemon=True)
    t.start()
    return jsonify({'ok': True, 'status': 'started'})


@app.route('/api/receipts/training/gemini-review/apply', methods=['POST'])
@login_required
def training_gemini_review_apply():
    """Apply one Gemini alias decision: add or reject."""
    data       = request.get_json() or {}
    raw_text   = (data.get('raw_text') or '').strip()
    variant_id = data.get('variant_id')
    action     = data.get('action', 'add')   # 'add' | 'reject'

    if action == 'reject' or not variant_id:
        # Just mark as actioned in state
        with _training_lock:
            for a in _training_state.get('gemini_aliases', []):
                if a.get('raw_text') == raw_text:
                    a['action'] = 'rejected'
                    break
        return jsonify({'ok': True, 'action': 'rejected'})

    try:
        variant_id = int(variant_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid variant_id'}), 400

    # Reuse the existing add_alias logic
    try:
        with open(_PRODUCTS_MD_PATH) as f:
            lines = f.readlines()
        updated   = False
        new_lines = []
        for line in lines:
            if (line.startswith('|') and '---' not in line and 'variant_id' not in line):
                cols = line.rstrip('\n').split('|')
                if len(cols) >= 6:
                    try:
                        if int(cols[1].strip()) == variant_id:
                            existing = cols[5].strip()
                            if raw_text.lower() not in existing.lower():
                                sep      = ', ' if existing else ''
                                cols[5]  = f' {existing}{sep}{raw_text} '
                            line    = '|'.join(cols) + '\n'
                            updated = True
                    except Exception:
                        pass
            new_lines.append(line)
        if not updated:
            return jsonify({'error': f'Product {variant_id} not found in catalog'}), 404
        with open(_PRODUCTS_MD_PATH, 'w') as f:
            f.writelines(new_lines)
        _load_catalog()
        # Mark as added in state
        with _training_lock:
            for a in _training_state.get('gemini_aliases', []):
                if a.get('raw_text') == raw_text:
                    a['action'] = 'added'
                    break
        return jsonify({'ok': True, 'action': 'added', 'catalog_size': len(_CATALOG)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/receipts/training/gemini-review/apply-all', methods=['POST'])
@login_required
def training_gemini_review_apply_all():
    """Apply all HIGH-confidence Gemini aliases at once."""
    with _training_lock:
        aliases = list(_training_state.get('gemini_aliases', []))

    added = 0; skipped = 0; errors = []
    try:
        with open(_PRODUCTS_MD_PATH) as f:
            lines = f.readlines()
        new_lines = list(lines)

        for a in aliases:
            if a.get('confidence') != 'HIGH' or a.get('action') == 'added':
                skipped += 1
                continue
            raw_text   = (a.get('raw_text') or '').strip()
            variant_id = a.get('variant_id')
            if not raw_text or not variant_id:
                skipped += 1
                continue
            updated = False
            for idx, line in enumerate(new_lines):
                if (line.startswith('|') and '---' not in line and 'variant_id' not in line):
                    cols = line.rstrip('\n').split('|')
                    if len(cols) >= 6:
                        try:
                            if int(cols[1].strip()) == int(variant_id):
                                existing = cols[5].strip()
                                if raw_text.lower() not in existing.lower():
                                    sep      = ', ' if existing else ''
                                    cols[5]  = f' {existing}{sep}{raw_text} '
                                new_lines[idx] = '|'.join(cols) + '\n'
                                updated = True
                                break
                        except Exception:
                            pass
            if updated:
                added += 1
                with _training_lock:
                    for ga in _training_state.get('gemini_aliases', []):
                        if ga.get('raw_text') == raw_text:
                            ga['action'] = 'added'
                            break
            else:
                errors.append(raw_text)
                skipped += 1

        with open(_PRODUCTS_MD_PATH, 'w') as f:
            f.writelines(new_lines)
        _load_catalog()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'ok': True, 'added': added, 'skipped': skipped,
                    'errors': errors, 'catalog_size': len(_CATALOG)})


@app.route('/api/receipts/training/clear-cache', methods=['POST'])
@login_required
def training_clear_cache():
    data        = request.get_json() or {}
    folder_path = data.get('folder', '').strip()
    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({'error': 'Folder not found'}), 400
    path = _training_cache_path(folder_path)
    try:
        if os.path.exists(path):
            os.remove(path)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/receipts/training/cache-info')
@login_required
def training_cache_info():
    folder_path = request.args.get('folder', '').strip()
    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({'count': 0})
    cache = _load_training_cache(folder_path)
    return jsonify({'count': len(cache), 'folder': folder_path})


@app.route('/api/receipts/training/status')
@login_required
def training_status():
    with _training_lock:
        state = dict(_training_state)
    # Compute summary stats from results list
    results  = state.get('results', [])
    all_comp = [c for r in results if r.get('status') == 'scanned'
                  for rec in r.get('receipts', []) for c in rec.get('comparison', [])]
    correct  = sum(1 for c in all_comp if c['result'] == 'correct')
    total_c  = len([c for c in all_comp if c['result'] != 'missed'])

    # ETA calculation
    eta_secs = None
    started  = state.get('started_at')
    if started and state.get('running'):
        try:
            elapsed   = (_dt.now() - _dt.fromisoformat(started)).total_seconds()
            done      = (state.get('processed', 0) - state.get('skipped', 0))
            remaining = state.get('total', 0) - state.get('processed', 0)
            if done > 0 and remaining > 0:
                rate     = done / elapsed          # files per second
                eta_secs = int(remaining / rate)
        except Exception:
            pass

    state['summary'] = {
        'receipts_scanned': sum(1 for r in results if r.get('status') == 'scanned'),
        'not_receipts':     sum(1 for r in results if r.get('status') == 'not_receipt'),
        'errors':           sum(1 for r in results if r.get('status') == 'error'),
        'total_items':      total_c,
        'correct':          correct,
        'accuracy':         round(correct / total_c * 100, 1) if total_c else 0,
        'alias_suggestions': len(state.get('alias_suggestions', {})),
    }
    state['eta_secs'] = eta_secs
    # Strip heavy comparison data — detail endpoint has full data
    state['results'] = [
        {'file': r['file'], 'status': r['status'],
         'receipts': [{'receipt_no': rec['receipt_no'], 'date': rec['date'],
                       'odoo_found': rec['odoo_found'], 'odoo_order': rec.get('odoo_order'),
                       'match_quality': rec.get('match_quality', 'none'),
                       'total_items': rec['total_items'], 'correct': rec['correct'],
                       'accuracy': rec['accuracy']}
                      for rec in r.get('receipts', [])],
         'reason': r.get('reason', ''), 'error': r.get('error', '')}
        for r in results
    ]
    return jsonify(state)


@app.route('/api/receipts/training/detail/<path:filename>')
@login_required
def training_detail(filename):
    """Return full comparison for a single receipt file."""
    with _training_lock:
        for r in _training_state.get('results', []):
            if r.get('file') == filename:
                return jsonify(r)
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/receipts/training/history')
@login_required
def training_history():
    """Return list of all past training run summaries, newest first."""
    runs = []
    try:
        for fname in sorted(os.listdir(_TRAINING_RUNS_DIR), reverse=True):
            if fname.endswith('.json'):
                try:
                    with open(os.path.join(_TRAINING_RUNS_DIR, fname)) as f:
                        runs.append(json.load(f))
                except Exception:
                    pass
    except Exception:
        pass
    return jsonify(runs)


@app.route('/api/receipts/training/history/compare')
@login_required
def training_history_compare():
    """Return last N runs as compact comparison rows."""
    n = min(int(request.args.get('n', 10)), 50)
    runs = []
    try:
        files = sorted(os.listdir(_TRAINING_RUNS_DIR), reverse=True)[:n]
        for fname in files:
            if fname.endswith('.json'):
                try:
                    with open(os.path.join(_TRAINING_RUNS_DIR, fname)) as f:
                        d = json.load(f)
                    runs.append({
                        'session_id': d.get('session_id'),
                        'date':       (d.get('started_at') or '')[:10],
                        'time':       (d.get('started_at') or '')[11:16],
                        'folder':     os.path.basename(d.get('folder', '')),
                        'stopped':    d.get('stopped', False),
                        'images':     d.get('processed', 0),
                        **d.get('summary', {}),
                    })
                except Exception:
                    pass
    except Exception:
        pass
    return jsonify(runs[::-1])  # chronological order


@app.route('/api/receipts/add-alias', methods=['POST'])
@login_required
def add_alias():
    data       = request.get_json() or {}
    raw_text   = (data.get('raw_text') or '').strip()
    variant_id = int(data.get('variant_id', 0))
    if not raw_text or not variant_id:
        return jsonify({'error': 'raw_text and variant_id required'}), 400
    try:
        with open(_PRODUCTS_MD_PATH) as f:
            lines = f.readlines()
        updated   = False
        new_lines = []
        for line in lines:
            if (line.startswith('|') and '---' not in line and 'variant_id' not in line):
                cols = line.rstrip('\n').split('|')
                # cols: ['', id, name, code, price, aliases, '']
                if len(cols) >= 6:
                    try:
                        if int(cols[1].strip()) == variant_id:
                            existing = cols[5].strip()
                            if raw_text.lower() not in existing.lower():
                                sep      = ', ' if existing else ''
                                cols[5]  = f' {existing}{sep}{raw_text} '
                            line    = '|'.join(cols) + '\n'
                            updated = True
                    except Exception:
                        pass
            new_lines.append(line)
        if not updated:
            return jsonify({'error': f'Product {variant_id} not found in catalog'}), 404
        with open(_PRODUCTS_MD_PATH, 'w') as f:
            f.writelines(new_lines)
        _load_catalog()
        return jsonify({'ok': True, 'catalog_size': len(_CATALOG)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/receipts/price-history/refresh', methods=['POST'])
@login_required
def price_history_refresh():
    try:
        _refresh_price_history(get_active_db_config())
        return jsonify({'ok': True, 'products': len(_price_history)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/receipts/price-history')
@login_required
def price_history_get():
    return jsonify(_price_history)


# ── 404 / 500 error handlers ───────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return redirect(url_for('dashboard'))

@app.errorhandler(500)
def server_error(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    _env = session.get('env', 'production')
    env_info = AVAILABLE_ENVS.get(_env, AVAILABLE_ENVS['production'])
    return render_template('dashboard.html', error=str(e),
                           active_env=_env, env_label=env_info['label'],
                           env_color=env_info['color'], env_options=AVAILABLE_ENVS), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 1989))
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.run(host='0.0.0.0', port=port, debug=False)
