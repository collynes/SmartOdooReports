"""
Party World Mobile API
FastAPI + Swagger UI  →  http://localhost:8800/docs
Auth: POST /auth/login  →  Bearer <token>
"""

from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import psycopg2
import psycopg2.extras
import httpx
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from jose import JWTError, jwt
from passlib.hash import pbkdf2_sha512

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# ── Config ──────────────────────────────────────────────────────
SECRET_KEY   = os.getenv('SECRET_KEY', 'changeme')
ALGORITHM    = 'HS256'
TOKEN_EXPIRE = 30  # days

ODOO_URL = 'http://localhost:8069'
ODOO_DB  = os.getenv('DB_NAME', 'odoo18')

# Webapp users from .env — these also work as mobile login (mapped to Odoo admin)
WEBAPP_USERS = {
    os.getenv('APP_USERNAME', ''): os.getenv('APP_PASSWORD', ''),
    'Admin': 'Party@2026!Admin',
}

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
    today      = datetime.today().date()
    month_from = today.replace(day=1)

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

    orders_today = query_one("""
        SELECT COUNT(*) AS total
        FROM sale_order
        WHERE state IN ('sale','done') AND date_order::date = %s
    """, (today,))

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
          AND so.date_order::date BETWEEN %s AND %s
        GROUP BY pt.name
        ORDER BY revenue DESC
        LIMIT 5
    """, (month_from, today))

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
        date_from = str(datetime.today().date().replace(day=1))
    if not date_to:
        date_to = str(datetime.today().date())

    rows = query("""
        SELECT so.id, so.name, so.date_order,
               rp.name AS customer,
               so.amount_total,
               so.amount_tax,
               so.state
        FROM sale_order so
        LEFT JOIN res_partner rp ON rp.id = so.partner_id
        WHERE so.state IN ('sale','done')
          AND so.date_order::date BETWEEN %s AND %s
        ORDER BY so.date_order DESC
        LIMIT %s OFFSET %s
    """, (date_from, date_to, limit, offset))
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
          AND am.state != 'cancel'
        ORDER BY am.invoice_date DESC
    """, (date_from, date_to))

    total = sum(float(r['amount_total'] or 0) for r in rows)
    return {'total': total, 'date_from': date_from, 'date_to': date_to,
            'count': len(rows), 'results': rows}


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
