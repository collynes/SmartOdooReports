"""
Claude-powered data agent for Party World analytics.
Uses the Anthropic SDK with tool_use so Claude can query the live DB.
Yields SSE-formatted chunks for streaming to the browser.
"""
import json
import os
import anthropic
import psycopg2
import psycopg2.extras
from datetime import date, datetime
from decimal import Decimal

SYSTEM_PROMPT = """You are a smart data analyst for **Party World**, a party supplies retail shop in Nairobi, Kenya.

## Business context
- Currency: **KES (Kenyan Shillings)**
- Two directors: Kemboi Family and Mutai Family (50/50 owners). Net profit from profitable months is split equally in thirds: 1/3 Shop reinvestment, 1/3 Kemboi, 1/3 Mutai.
- Main stock suppliers: ChinaLand and Kaboww (imports)
- Staff: Christine (manager/salary ~KES 10,000–12,162/month), Olive/Fancy/Belsoi/Valentine (part-time, KES 3,000/month)
- Hosting: Collins Kemboi (server)
- Rent: Star Mall Kamkunji (~KES 20,416/month)
- Operating since January 2024

## Database (PostgreSQL / Odoo 18)
Key tables you can query:
- **sale_order** (so): id, name, state, date_order, amount_total, partner_id — state NOT IN ('cancel','draft') for confirmed orders
- **sale_order_line** (sol): order_id, product_id, product_uom_qty, price_unit, price_subtotal, display_type (filter display_type IS NULL for real lines)
- **account_move** (am): id, move_type, state, invoice_date, amount_untaxed, amount_total, partner_id — move_type IN ('out_invoice','out_receipt') for sales, 'in_invoice' for vendor bills; state='posted' for confirmed
- **account_move_line** (aml): move_id, account_id, product_id, quantity, price_unit, price_subtotal, display_type, debit, credit
- **product_product** (pp): id, product_tmpl_id, standard_price (JSONB, use ->>'1' for cost)
- **product_template** (pt): id, name (JSONB, use ->>'en_US'), list_price, categ_id
- **res_partner** (rp): id, name
- **stock_quant** (sq): product_id, location_id, quantity — join stock_location sl WHERE sl.usage='internal' for on-hand stock
- **stock_location** (sl): id, usage

## Rules
- Always format monetary values as **KES X,XXX** (no decimals unless meaningful)
- For revenue, use account_move with move_type IN ('out_invoice','out_receipt') AND state='posted'
- For opex (expenses), use account_move with move_type='in_invoice' AND state='posted', excluding ChinaLand and Kaboww
- For imports (COGS stock purchases), filter partner name ILIKE '%chinaland%' OR '%kaboww%'
- Be concise, insightful, and business-focused in your answers
- When you get data back, interpret it — don't just repeat numbers, give context and insights
- Use **bold** for key numbers and product/customer names
- If a question is ambiguous about period, default to "this month" (current calendar month)
"""

DB_CONFIG = {
    'host':     os.getenv('DB_HOST', 'localhost'),
    'dbname':   os.getenv('DB_NAME', 'odoo18'),
    'user':     os.getenv('DB_USER', 'odoo18'),
    'password': os.getenv('DB_PASSWORD', 'odoo18'),
}

TOOLS = [
    {
        "name": "query_database",
        "description": (
            "Run a SELECT SQL query against the Odoo PostgreSQL database to get live business data. "
            "Use this to answer questions about sales, products, expenses, stock, customers, and P&L. "
            "Always use SELECT only — never INSERT, UPDATE, DELETE."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL SELECT query to execute. Use $1, $2... style params or inline safe values."
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what this query fetches (shown to user while loading)"
                }
            },
            "required": ["sql", "description"]
        }
    }
]


def _serialize(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


def _run_query(sql):
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def stream_agent(question, history=None):
    """
    Generator that yields SSE lines.
    Handles multi-turn: history is list of {role, content} dicts.
    """
    client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

    messages = list(history or [])
    messages.append({"role": "user", "content": question})

    while True:
        # Stream from Claude
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:

            full_text = ""
            tool_calls = []
            current_tool = None

            for event in stream:
                etype = event.type

                # Text token
                if etype == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        chunk = event.delta.text
                        full_text += chunk
                        yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"

                    elif hasattr(event.delta, "partial_json"):
                        if current_tool:
                            current_tool["partial"] = current_tool.get("partial", "") + event.delta.partial_json

                elif etype == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool = {"id": block.id, "name": block.name, "partial": ""}

                elif etype == "content_block_stop":
                    if current_tool:
                        try:
                            current_tool["input"] = json.loads(current_tool.get("partial", "{}"))
                        except Exception:
                            current_tool["input"] = {}
                        tool_calls.append(current_tool)
                        current_tool = None

            # Get final message for stop_reason
            final = stream.get_final_message()
            stop_reason = final.stop_reason

        # If Claude wants to use tools
        if stop_reason == "tool_use" and tool_calls:
            # Append Claude's response to messages
            messages.append({"role": "assistant", "content": final.content})

            tool_results = []
            for tc in tool_calls:
                desc = tc["input"].get("description", "Querying database…")
                sql  = tc["input"].get("sql", "")

                yield f"data: {json.dumps({'type': 'tool_start', 'desc': desc})}\n\n"

                try:
                    rows = _run_query(sql)
                    # Limit rows for context size
                    rows_preview = rows[:100]
                    result_str = json.dumps(rows_preview, default=_serialize)
                    if len(rows) > 100:
                        result_str = result_str[:-1] + f', {{"note": "...{len(rows)-100} more rows truncated"}}]'
                    yield f"data: {json.dumps({'type': 'tool_done', 'rows': len(rows)})}\n\n"
                except Exception as e:
                    result_str = json.dumps({"error": str(e)})
                    yield f"data: {json.dumps({'type': 'tool_error', 'error': str(e)})}\n\n"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})
            tool_calls = []
            # Loop — Claude will now respond with final answer
            continue

        # Done
        break

    yield f"data: {json.dumps({'type': 'done'})}\n\n"
