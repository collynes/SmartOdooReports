"""
Party World Mobile API
FastAPI + Swagger UI  →  http://localhost:8800/docs
Auth: POST /auth/login  →  Bearer <token>
"""

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import psycopg2
import psycopg2.extras
import os
import secrets
from datetime import datetime, timedelta
import calendar
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from jose import JWTError, jwt
from passlib.hash import pbkdf2_sha512

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# ── Config ──────────────────────────────────────────────────────
SECRET_KEY   = os.getenv('SECRET_KEY', '')
if not SECRET_KEY or SECRET_KEY == 'changeme':
    SECRET_KEY = secrets.token_urlsafe(48)
    print('WARNING: SECRET_KEY is not configured; mobile tokens will reset on restart')
ALGORITHM    = 'HS256'
TOKEN_EXPIRE = 30  # days

ODOO_URL = os.getenv('ODOO_URL', 'http://localhost:8069')
ODOO_DB  = os.getenv('DB_NAME', 'odoo18')
MONTHLY_REVENUE_TARGET = float(os.getenv('MONTHLY_REVENUE_TARGET', '800000'))
SHOP_TIMEZONE = ZoneInfo(os.getenv('SHOP_TIMEZONE', 'Africa/Nairobi'))
SHOP_OPEN_HOUR = int(os.getenv('SHOP_OPEN_HOUR', '9'))
SHOP_CLOSE_HOUR = int(os.getenv('SHOP_CLOSE_HOUR', '19'))

# Webapp users from .env — these also work as mobile login (mapped to Odoo admin)
WEBAPP_USERS = {}
if os.getenv('APP_USERNAME') and os.getenv('APP_PASSWORD'):
    WEBAPP_USERS[os.environ['APP_USERNAME']] = os.environ['APP_PASSWORD']

DB_CONFIG = {
    'host':     os.getenv('DB_HOST', 'localhost'),
    'dbname':   ODOO_DB,
    'user':     os.getenv('DB_USER', 'odoo18'),
    'password': os.getenv('DB_PASSWORD', 'odoo18'),
}

# ── App ─────────────────────────────────────────────────────────
ROOT_PATH = os.getenv('MOBILE_API_ROOT_PATH', '/mobileapi')

app = FastAPI(
    title='Party World API',
    description=(
        '## Party World Mobile API\n\n'
        'REST API for the Party World mobile app backed by Odoo 18.\n\n'
        '### Authentication\n'
        '1. `POST /auth/login` with your Odoo credentials\n'
        '2. Copy the `access_token` from the response\n'
        '3. Click **Authorize** above and enter: `Bearer <token>`\n'
        '4. All `/api/v1/*` endpoints are now unlocked'
    ),
    version='1.0.0',
    contact={'name': 'Party World', 'url': 'https://partyworld.co.ke'},
    root_path=ROOT_PATH,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

security = HTTPBearer()

# ── DB ──────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(**DB_CONFIG)

def query(sql: str, params=None):
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def query_one(sql: str, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None

# ── JWT ─────────────────────────────────────────────────────────
def create_token(data: dict) -> str:
    payload = data.copy()
    payload['exp'] = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail='Invalid or expired token')

def current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    return decode_token(creds.credentials)

# ── Odoo auth ────────────────────────────────────────────────────
def odoo_authenticate(login: str, password: str) -> dict:
    """Validate credentials — tries Odoo DB hash first, then webapp fallback users."""

    # 1. Try Odoo DB hash (works for all Odoo users who know their Odoo password)
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT ru.id, rp.name, ru.login, ru.password
            FROM res_users ru
            JOIN res_partner rp ON rp.id = ru.partner_id
            WHERE ru.login = %s AND ru.active = true
        """, (login,))
        odoo_row = cur.fetchone()
        # Also fetch the Odoo admin for fallback mapping
        cur.execute("""
            SELECT ru.id, rp.name, ru.login
            FROM res_users ru
            JOIN res_partner rp ON rp.id = ru.partner_id
            WHERE ru.login = 'collynes@gmail.com' AND ru.active = true
            LIMIT 1
        """)
        admin_row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if odoo_row and odoo_row['password']:
        try:
            if pbkdf2_sha512.verify(password, odoo_row['password']):
                return {'uid': odoo_row['id'], 'name': odoo_row['name'], 'login': odoo_row['login']}
        except Exception:
            pass

    # 2. Fallback: webapp credentials from .env (mapped to Odoo admin user)
    expected = WEBAPP_USERS.get(login)
    if expected and expected == password and admin_row:
        return {'uid': admin_row['id'], 'name': admin_row['name'], 'login': admin_row['login']}

    raise HTTPException(status_code=401, detail='Invalid credentials')

# ── Schemas ─────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    expires_in_days: int = TOKEN_EXPIRE
    user_id: int
    name: str

class OwnerNotification(BaseModel):
    id: str
    category: str
    priority: str
    title: str
    body: str
    metric_label: Optional[str] = None
    metric_value: Optional[float] = None
    action_label: Optional[str] = None
    route: Optional[str] = None
    created_at: str

class OwnerNotificationsResponse(BaseModel):
    date: str
    count: int
    critical_count: int
    warning_count: int
    results: list[OwnerNotification]

# ════════════════════════════════════════════════════════════════
# AUTH
# ════════════════════════════════════════════════════════════════
@app.post('/auth/login', response_model=LoginResponse, tags=['Auth'],
          summary='Login with Odoo credentials → JWT token')
def login(body: LoginRequest):
    user = odoo_authenticate(body.username, body.password)
    token = create_token({'uid': user['uid'], 'name': user['name'], 'login': user['login']})
    return LoginResponse(access_token=token, user_id=user['uid'], name=user['name'])


@app.get('/auth/me', tags=['Auth'], summary='Current token info')
def me(user=Depends(current_user)):
    return user


# ════════════════════════════════════════════════════════════════
# DASHBOARD
# ════════════════════════════════════════════════════════════════
@app.get('/api/v1/dashboard', tags=['Dashboard'], summary='KPI snapshot')
def dashboard(user=Depends(current_user)):
    today      = datetime.now(SHOP_TIMEZONE).date()
    month_from = today.replace(day=1)

    revenue_today = query_one("""
        SELECT COALESCE(SUM(sol.price_subtotal), 0) AS revenue
        FROM sale_order so
        JOIN sale_order_line sol ON sol.order_id = so.id
        WHERE so.state IN ('sale','done')
          AND (so.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s)::date = %s
    """, (str(SHOP_TIMEZONE), today))

    revenue_month = query_one("""
        SELECT COALESCE(SUM(sol.price_subtotal), 0) AS revenue
        FROM sale_order so
        JOIN sale_order_line sol ON sol.order_id = so.id
        WHERE so.state IN ('sale','done')
          AND (so.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s)::date BETWEEN %s AND %s
    """, (str(SHOP_TIMEZONE), month_from, today))

    orders_today = query_one("""
        SELECT COUNT(*) AS total
        FROM sale_order
        WHERE state IN ('sale','done')
          AND (date_order AT TIME ZONE 'UTC' AT TIME ZONE %s)::date = %s
    """, (str(SHOP_TIMEZONE), today))

    stock_value = query_one("""
        SELECT COALESCE(SUM(sq.quantity * (pp.standard_price->>'1')::numeric), 0) AS value
        FROM stock_quant sq
        JOIN product_product pp ON pp.id = sq.product_id
        JOIN stock_location sl ON sl.id = sq.location_id
        WHERE sl.usage = 'internal'
    """)

    top_products = query("""
        SELECT pt.name->>'en_US' AS product, SUM(sol.product_uom_qty) AS qty_sold,
               SUM(sol.price_subtotal) AS revenue
        FROM sale_order so
        JOIN sale_order_line sol ON sol.order_id = so.id
        JOIN product_product pp ON pp.id = sol.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE so.state IN ('sale','done')
          AND (so.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s)::date BETWEEN %s AND %s
        GROUP BY pt.name
        ORDER BY revenue DESC
        LIMIT 5
    """, (str(SHOP_TIMEZONE), month_from, today))

    low_stock_count = query_one("""
        SELECT COUNT(DISTINCT pp.id) AS count
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN (
            SELECT sq.product_id, SUM(sq.quantity) AS qty
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            WHERE sl.usage = 'internal'
            GROUP BY sq.product_id
        ) s ON s.product_id = pp.id
        WHERE pt.type = 'consu' AND pt.active = true
          AND COALESCE(s.qty, 0) <= 5
    """)

    return {
        'date': str(today),
        'monthly_revenue_target': MONTHLY_REVENUE_TARGET,
        'revenue_today':    float(revenue_today['revenue']),
        'revenue_month':    float(revenue_month['revenue']),
        'orders_today':     int(orders_today['total']),
        'stock_value':      float(stock_value['value']),
        'low_stock_alerts': int(low_stock_count['count']),
        'top_products_month': top_products,
    }


# ════════════════════════════════════════════════════════════════
# PRODUCTS
# ════════════════════════════════════════════════════════════════
@app.get('/api/v1/products', tags=['Products'], summary='Product list with stock')
def products(
    search: Optional[str] = Query(None, description='Search by name'),
    limit:  int = Query(50, le=200),
    offset: int = Query(0),
    user=Depends(current_user),
):
    like = f'%{search}%' if search else '%'
    rows = query("""
        SELECT pp.id,
               pt.name->>'en_US'            AS name,
               pt.list_price                AS sale_price,
               (pp.standard_price->>'1')::numeric AS cost_price,
               COALESCE(s.qty, 0)           AS qty_on_hand,
               pt.categ_id,
               pc.name                      AS category
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN product_category pc ON pc.id = pt.categ_id
        LEFT JOIN (
            SELECT sq.product_id, SUM(sq.quantity) AS qty
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            WHERE sl.usage = 'internal'
            GROUP BY sq.product_id
        ) s ON s.product_id = pp.id
        WHERE pt.active = true
          AND pt.type = 'consu'
          AND (pt.name->>'en_US') ILIKE %s
        ORDER BY pt.name->>'en_US'
        LIMIT %s OFFSET %s
    """, (like, limit, offset))
    return {'count': len(rows), 'offset': offset, 'results': rows}


@app.get('/api/v1/products/{product_id}', tags=['Products'], summary='Single product detail')
def product_detail(product_id: int, user=Depends(current_user)):
    row = query_one("""
        SELECT pp.id,
               pt.name->>'en_US'            AS name,
               pt.description_sale          AS description,
               pt.list_price                AS sale_price,
               (pp.standard_price->>'1')::numeric AS cost_price,
               COALESCE(s.qty, 0)           AS qty_on_hand,
               pc.name                      AS category,
               pt.uom_id,
               uom.name->>'en_US'           AS uom_name
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN product_category pc ON pc.id = pt.categ_id
        LEFT JOIN uom_uom uom ON uom.id = pt.uom_id
        LEFT JOIN (
            SELECT sq.product_id, SUM(sq.quantity) AS qty
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            WHERE sl.usage = 'internal'
            GROUP BY sq.product_id
        ) s ON s.product_id = pp.id
        WHERE pp.id = %s
    """, (product_id,))
    if not row:
        raise HTTPException(status_code=404, detail='Product not found')
    return row


# ════════════════════════════════════════════════════════════════
# STOCK
# ════════════════════════════════════════════════════════════════
@app.get('/api/v1/stock/low', tags=['Stock'], summary='Low stock alerts (qty ≤ 5)')
def low_stock(threshold: int = Query(5), user=Depends(current_user)):
    rows = query("""
        SELECT pp.id,
               pt.name->>'en_US'  AS name,
               COALESCE(s.qty, 0) AS qty_on_hand,
               pt.list_price      AS sale_price
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN (
            SELECT sq.product_id, SUM(sq.quantity) AS qty
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            WHERE sl.usage = 'internal'
            GROUP BY sq.product_id
        ) s ON s.product_id = pp.id
        WHERE pt.type = 'consu' AND pt.active = true
          AND COALESCE(s.qty, 0) <= %s
        ORDER BY COALESCE(s.qty, 0) ASC
    """, (threshold,))
    return {'threshold': threshold, 'count': len(rows), 'results': rows}


@app.get('/api/v1/stock/movements', tags=['Stock'], summary='Recent stock moves')
def stock_movements(
    limit:  int = Query(30, le=100),
    offset: int = Query(0),
    user=Depends(current_user),
):
    rows = query("""
        SELECT sm.id, sm.date, sm.name,
               pt.name->>'en_US' AS product,
               sm.product_qty,
               sl_src.name AS from_location,
               sl_dst.name AS to_location,
               sm.state
        FROM stock_move sm
        JOIN product_product pp ON pp.id = sm.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        JOIN stock_location sl_src ON sl_src.id = sm.location_id
        JOIN stock_location sl_dst ON sl_dst.id = sm.location_dest_id
        WHERE sm.state = 'done'
        ORDER BY sm.date DESC
        LIMIT %s OFFSET %s
    """, (limit, offset))
    return {'count': len(rows), 'offset': offset, 'results': rows}


# ════════════════════════════════════════════════════════════════
# SALES
# ════════════════════════════════════════════════════════════════
@app.get('/api/v1/sales', tags=['Sales'], summary='Sales orders list')
def sales(
    date_from: Optional[str] = Query(None, description='YYYY-MM-DD'),
    date_to:   Optional[str] = Query(None, description='YYYY-MM-DD'),
    limit:     int = Query(30, le=100),
    offset:    int = Query(0),
    user=Depends(current_user),
):
    if not date_from:
        date_from = str(datetime.now(SHOP_TIMEZONE).date().replace(day=1))
    if not date_to:
        date_to = str(datetime.now(SHOP_TIMEZONE).date())

    rows = query("""
        SELECT so.id, so.name, so.date_order,
               rp.name AS customer,
               so.amount_total,
               so.amount_tax,
               so.state
        FROM sale_order so
        LEFT JOIN res_partner rp ON rp.id = so.partner_id
        WHERE so.state IN ('sale','done')
          AND (so.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s)::date BETWEEN %s AND %s
        ORDER BY so.date_order DESC
        LIMIT %s OFFSET %s
    """, (str(SHOP_TIMEZONE), date_from, date_to, limit, offset))
    return {'count': len(rows), 'date_from': date_from, 'date_to': date_to,
            'offset': offset, 'results': rows}


@app.get('/api/v1/sales/{order_id}', tags=['Sales'], summary='Sale order detail with lines')
def sale_detail(order_id: int, user=Depends(current_user)):
    order = query_one("""
        SELECT so.id, so.name, so.date_order, so.state,
               rp.name AS customer, rp.phone, rp.email,
               so.amount_untaxed, so.amount_tax, so.amount_total,
               so.note
        FROM sale_order so
        LEFT JOIN res_partner rp ON rp.id = so.partner_id
        WHERE so.id = %s
    """, (order_id,))
    if not order:
        raise HTTPException(status_code=404, detail='Order not found')

    lines = query("""
        SELECT sol.id,
               pt.name->>'en_US' AS product,
               sol.product_uom_qty AS qty,
               sol.price_unit,
               sol.discount,
               sol.price_subtotal
        FROM sale_order_line sol
        JOIN product_product pp ON pp.id = sol.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        WHERE sol.order_id = %s
        ORDER BY sol.id
    """, (order_id,))

    return {**order, 'lines': lines}


# ════════════════════════════════════════════════════════════════
# CUSTOMERS
# ════════════════════════════════════════════════════════════════
@app.get('/api/v1/customers', tags=['Customers'], summary='Customer list')
def customers(
    search: Optional[str] = Query(None),
    limit:  int = Query(50, le=200),
    offset: int = Query(0),
    user=Depends(current_user),
):
    like = f'%{search}%' if search else '%'
    rows = query("""
        SELECT rp.id, rp.name, rp.phone, rp.email, rp.street,
               COUNT(so.id)            AS total_orders,
               COALESCE(SUM(so.amount_total), 0) AS total_spent
        FROM res_partner rp
        LEFT JOIN sale_order so ON so.partner_id = rp.id AND so.state IN ('sale','done')
        WHERE rp.customer_rank > 0
          AND rp.name ILIKE %s
        GROUP BY rp.id
        ORDER BY total_spent DESC
        LIMIT %s OFFSET %s
    """, (like, limit, offset))
    return {'count': len(rows), 'offset': offset, 'results': rows}


@app.get('/api/v1/customers/{customer_id}', tags=['Customers'],
         summary='Customer detail + recent orders')
def customer_detail(customer_id: int, user=Depends(current_user)):
    customer = query_one("""
        SELECT rp.id, rp.name, rp.phone, rp.email,
               rp.street, rp.city, rp.customer_rank,
               COUNT(so.id)                        AS total_orders,
               COALESCE(SUM(so.amount_total), 0)   AS total_spent
        FROM res_partner rp
        LEFT JOIN sale_order so ON so.partner_id = rp.id AND so.state IN ('sale','done')
        WHERE rp.id = %s
        GROUP BY rp.id
    """, (customer_id,))
    if not customer:
        raise HTTPException(status_code=404, detail='Customer not found')

    recent_orders = query("""
        SELECT so.id, so.name, so.date_order, so.amount_total, so.state
        FROM sale_order so
        WHERE so.partner_id = %s AND so.state IN ('sale','done')
        ORDER BY so.date_order DESC
        LIMIT 10
    """, (customer_id,))

    return {**customer, 'recent_orders': recent_orders}


# ════════════════════════════════════════════════════════════════
# INVOICES
# ════════════════════════════════════════════════════════════════
@app.get('/api/v1/invoices', tags=['Invoices'], summary='Customer invoices')
def invoices(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    state:     Optional[str] = Query(None, description='draft|posted|cancel'),
    limit:     int = Query(30, le=100),
    offset:    int = Query(0),
    user=Depends(current_user),
):
    if not date_from:
        date_from = str(datetime.today().date().replace(day=1))
    if not date_to:
        date_to = str(datetime.today().date())

    state_filter = "AND am.state = %(state)s" if state else ""
    rows = query(f"""
        SELECT am.id, am.name, am.invoice_date,
               rp.name         AS customer,
               am.amount_untaxed,
               am.amount_tax,
               am.amount_total,
               am.amount_residual AS amount_due,
               am.state,
               am.payment_state
        FROM account_move am
        LEFT JOIN res_partner rp ON rp.id = am.partner_id
        WHERE am.move_type = 'out_invoice'
          AND am.invoice_date BETWEEN %(from)s AND %(to)s
          {state_filter}
        ORDER BY am.invoice_date DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """, {'from': date_from, 'to': date_to, 'state': state,
          'limit': limit, 'offset': offset})
    return {'count': len(rows), 'date_from': date_from, 'date_to': date_to,
            'offset': offset, 'results': rows}


# ════════════════════════════════════════════════════════════════
# EXPENSES / VENDOR BILLS
# ════════════════════════════════════════════════════════════════
@app.get('/api/v1/expenses', tags=['Expenses'], summary='Vendor bills / expenses summary')
def expenses(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    user=Depends(current_user),
):
    if not date_from:
        date_from = str(datetime.today().date().replace(day=1))
    if not date_to:
        date_to = str(datetime.today().date())

    rows = query("""
        SELECT am.id, am.name, am.invoice_date,
               rp.name         AS vendor,
               am.amount_total,
               am.amount_residual AS amount_due,
               am.state,
               am.payment_state
        FROM account_move am
        LEFT JOIN res_partner rp ON rp.id = am.partner_id
        WHERE am.move_type = 'in_invoice'
          AND am.invoice_date BETWEEN %s AND %s
          AND am.state = 'posted'
        ORDER BY am.invoice_date DESC
    """, (date_from, date_to))

    total = sum(float(r['amount_total'] or 0) for r in rows)
    return {'total': total, 'date_from': date_from, 'date_to': date_to,
            'count': len(rows), 'results': rows}


# ════════════════════════════════════════════════════════════════
# OWNER NOTIFICATIONS
# ════════════════════════════════════════════════════════════════
def _owner_notification(
    *,
    notification_id: str,
    category: str,
    priority: str,
    title: str,
    body: str,
    metric_label: Optional[str] = None,
    metric_value: Optional[float] = None,
    action_label: Optional[str] = None,
    route: Optional[str] = None,
) -> OwnerNotification:
    return OwnerNotification(
        id=notification_id,
        category=category,
        priority=priority,
        title=title,
        body=body,
        metric_label=metric_label,
        metric_value=metric_value,
        action_label=action_label,
        route=route,
        created_at=datetime.utcnow().isoformat(timespec='seconds') + 'Z',
    )


@app.get('/api/v1/owner/notifications', response_model=OwnerNotificationsResponse,
         tags=['Owner'], summary='Owner alerts: stock, target pace, invoices, expenses')
def owner_notifications(user=Depends(current_user)):
    now = datetime.now(SHOP_TIMEZONE)
    today = now.date()
    month_from = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    daily_target = MONTHLY_REVENUE_TARGET / days_in_month if days_in_month else 0
    business_hours = max(SHOP_CLOSE_HOUR - SHOP_OPEN_HOUR, 1)
    elapsed_hours = min(max(now.hour + now.minute / 60 - SHOP_OPEN_HOUR, 0), business_hours)
    elapsed_share = elapsed_hours / business_hours
    expected_now = daily_target * elapsed_share
    expected_mtd = daily_target * ((today.day - 1) + elapsed_share)

    revenue_today = query_one("""
        SELECT COALESCE(SUM(sol.price_subtotal), 0) AS revenue
        FROM sale_order so
        JOIN sale_order_line sol ON sol.order_id = so.id
        WHERE so.state IN ('sale','done')
          AND so.date_order::date = %s
    """, (today,))

    revenue_month = query_one("""
        SELECT COALESCE(SUM(sol.price_subtotal), 0) AS revenue
        FROM sale_order so
        JOIN sale_order_line sol ON sol.order_id = so.id
        WHERE so.state IN ('sale','done')
          AND so.date_order::date BETWEEN %s AND %s
    """, (month_from, today))

    zero_stock = query_one("""
        SELECT COUNT(DISTINCT pp.id) AS count
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN (
            SELECT sq.product_id, SUM(sq.quantity) AS qty
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            WHERE sl.usage = 'internal'
            GROUP BY sq.product_id
        ) s ON s.product_id = pp.id
        WHERE pt.type = 'consu' AND pt.active = true
          AND COALESCE(s.qty, 0) <= 0
    """)

    low_stock = query_one("""
        SELECT COUNT(DISTINCT pp.id) AS count
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        LEFT JOIN (
            SELECT sq.product_id, SUM(sq.quantity) AS qty
            FROM stock_quant sq
            JOIN stock_location sl ON sl.id = sq.location_id
            WHERE sl.usage = 'internal'
            GROUP BY sq.product_id
        ) s ON s.product_id = pp.id
        WHERE pt.type = 'consu' AND pt.active = true
          AND COALESCE(s.qty, 0) > 0
          AND COALESCE(s.qty, 0) <= 5
    """)

    unpaid = query_one("""
        SELECT COUNT(*) AS count, COALESCE(SUM(am.amount_residual), 0) AS amount_due
        FROM account_move am
        WHERE am.move_type = 'out_invoice'
          AND am.state = 'posted'
          AND am.payment_state IN ('not_paid', 'partial')
          AND am.amount_residual > 0
          AND am.invoice_date_due < %s
    """, (today,))

    month_expenses = query_one("""
        SELECT COALESCE(SUM(am.amount_total), 0) AS total
        FROM account_move am
        WHERE am.move_type = 'in_invoice'
          AND am.state = 'posted'
          AND am.invoice_date BETWEEN %s AND %s
    """, (month_from, today))

    alerts: list[OwnerNotification] = []
    today_revenue = float(revenue_today['revenue'] or 0)
    month_revenue = float(revenue_month['revenue'] or 0)
    zero_count = int(zero_stock['count'] or 0)
    low_count = int(low_stock['count'] or 0)
    unpaid_count = int(unpaid['count'] or 0)
    unpaid_total = float(unpaid['amount_due'] or 0)
    expense_total = float(month_expenses['total'] or 0)

    if zero_count:
        alerts.append(_owner_notification(
            notification_id=f'stock-zero-{today}',
            category='stock',
            priority='critical',
            title=f'{zero_count} products are out of stock',
            body='These can block sales today. Review the reorder list before checking slower items.',
            metric_label='Out of stock',
            metric_value=zero_count,
            action_label='Open stock',
            route='stock',
        ))

    if low_count:
        alerts.append(_owner_notification(
            notification_id=f'stock-low-{today}',
            category='stock',
            priority='warning',
            title=f'{low_count} products are running low',
            body='Keep fast-moving party items available before weekend and event demand picks up.',
            metric_label='Low stock',
            metric_value=low_count,
            action_label='Review stock',
            route='stock',
        ))

    if elapsed_share >= 0.4 and today_revenue < expected_now * 0.65:
        alerts.append(_owner_notification(
            notification_id=f'target-daily-{today}',
            category='sales',
            priority='warning',
            title='Today is behind the sales pace',
            body=f'Today is at KES {today_revenue:,.0f} against an expected KES {expected_now:,.0f} by now.',
            metric_label='Today revenue',
            metric_value=today_revenue,
            action_label='Check sales',
            route='sales',
        ))
    elif daily_target and today_revenue >= daily_target:
        alerts.append(_owner_notification(
            notification_id=f'target-daily-hit-{today}',
            category='sales',
            priority='info',
            title='Today is on target',
            body=f'Today has reached KES {today_revenue:,.0f}; keep the best sellers visible.',
            metric_label='Today revenue',
            metric_value=today_revenue,
            action_label='View sales',
            route='sales',
        ))

    if expected_mtd and month_revenue < expected_mtd * 0.85:
        gap = expected_mtd - month_revenue
        alerts.append(_owner_notification(
            notification_id=f'target-month-{today}',
            category='target',
            priority='warning',
            title='Month-to-date revenue is behind pace',
            body=f'The shop is about KES {gap:,.0f} behind the monthly target pace.',
            metric_label='Target gap',
            metric_value=gap,
            action_label='Open dashboard',
            route='dashboard',
        ))

    if unpaid_count:
        priority = 'critical' if unpaid_total >= 50000 else 'warning'
        alerts.append(_owner_notification(
            notification_id=f'invoices-unpaid-{today}',
            category='cashflow',
            priority=priority,
            title=f'{unpaid_count} customer invoices need follow-up',
            body=f'Outstanding customer balance is KES {unpaid_total:,.0f}.',
            metric_label='Amount due',
            metric_value=unpaid_total,
            action_label='Review customers',
            route='customers',
        ))

    if month_revenue > 0 and expense_total > month_revenue * 0.45:
        alerts.append(_owner_notification(
            notification_id=f'expenses-pressure-{today}',
            category='expenses',
            priority='info',
            title='Expenses are taking a large share this month',
            body=f'Vendor bills total KES {expense_total:,.0f}, about {(expense_total / month_revenue) * 100:.0f}% of sales.',
            metric_label='Expenses',
            metric_value=expense_total,
            action_label='Review expenses',
            route='expenses',
        ))

    priority_order = {'critical': 0, 'warning': 1, 'info': 2}
    alerts.sort(key=lambda n: (priority_order.get(n.priority, 9), n.category, n.title))

    return OwnerNotificationsResponse(
        date=str(today),
        count=len(alerts),
        critical_count=sum(1 for n in alerts if n.priority == 'critical'),
        warning_count=sum(1 for n in alerts if n.priority == 'warning'),
        results=alerts,
    )


@app.get('/api/v1/mobile/summary', tags=['Mobile'], summary='Single-call mobile owner summary')
def mobile_summary(user=Depends(current_user)):
    return {
        'dashboard': dashboard(user=user),
        'low_stock': low_stock(user=user),
        'sales': sales(limit=30, user=user),
        'customers': customers(limit=50, user=user),
        'owner_notifications': owner_notifications(user=user),
    }


# ════════════════════════════════════════════════════════════════
# POS
# ════════════════════════════════════════════════════════════════
def _pos_installed() -> bool:
    row = query_one("SELECT 1 FROM information_schema.tables WHERE table_name='pos_session' LIMIT 1")
    return row is not None

@app.get('/api/v1/pos/sessions', tags=['POS'], summary='POS sessions summary')
def pos_sessions(limit: int = Query(10, le=50), user=Depends(current_user)):
    if not _pos_installed():
        return {'count': 0, 'results': [], 'note': 'POS module not installed'}
    rows = query("""
        SELECT ps.id, ps.name, ps.start_at, ps.stop_at, ps.state,
               COUNT(po.id)                      AS order_count,
               COALESCE(SUM(po.amount_total), 0) AS total_sales
        FROM pos_session ps
        LEFT JOIN pos_order po ON po.session_id = ps.id
        GROUP BY ps.id
        ORDER BY ps.start_at DESC
        LIMIT %s
    """, (limit,))
    return {'count': len(rows), 'results': rows}


@app.get('/api/v1/pos/orders', tags=['POS'], summary='POS orders')
def pos_orders(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    limit:     int = Query(30, le=100),
    offset:    int = Query(0),
    user=Depends(current_user),
):
    if not _pos_installed():
        return {'count': 0, 'results': [], 'note': 'POS module not installed'}
    if not date_from:
        date_from = str(datetime.today().date())
    if not date_to:
        date_to = str(datetime.today().date())

    rows = query("""
        SELECT po.id, po.name, po.date_order,
               rp.name          AS customer,
               po.amount_total,
               po.amount_tax,
               po.state
        FROM pos_order po
        LEFT JOIN res_partner rp ON rp.id = po.partner_id
        WHERE po.date_order::date BETWEEN %s AND %s
          AND po.state NOT IN ('cancel')
        ORDER BY po.date_order DESC
        LIMIT %s OFFSET %s
    """, (date_from, date_to, limit, offset))
    return {'count': len(rows), 'date_from': date_from, 'date_to': date_to,
            'offset': offset, 'results': rows}


# ════════════════════════════════════════════════════════════════
# HEALTH
# ════════════════════════════════════════════════════════════════
@app.get('/health', tags=['System'], summary='Health check (no auth)')
def health():
    try:
        conn = get_db()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    return {'status': 'ok' if db_ok else 'degraded', 'db': db_ok, 'odoo': ODOO_URL}


# ── Entry point ─────────────────────────────────────────────────
if __name__ == '__main__':
    import uvicorn
    port = int(os.getenv('MOBILE_API_PORT', 8800))
    print(f'Party World Mobile API  →  http://localhost:{port}/docs')
    uvicorn.run('mobile_api:app', host='0.0.0.0', port=port, reload=False)
