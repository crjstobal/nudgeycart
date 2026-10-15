import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS merchants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    public_key TEXT UNIQUE NOT NULL,
    admin_token TEXT UNIQUE NOT NULL,
    platform TEXT NOT NULL DEFAULT 'woocommerce',
    store_url TEXT NOT NULL,
    woo_user TEXT,
    woo_app_password TEXT,
    payee_email TEXT,            -- merchant's PayPal account (NULL = API caller account)
    billing_email TEXT,          -- where NudgeyCart sends its success-fee invoices
    currency TEXT NOT NULL DEFAULT 'USD',
    min_margin_pct REAL NOT NULL DEFAULT 0.25,
    max_discount_pct REAL NOT NULL DEFAULT 0.15,
    shipping_flat REAL,          -- trusted shipping price; NULL = take it from the cart
    fee_pct REAL NOT NULL DEFAULT 0.05,
    control_pct INTEGER NOT NULL DEFAULT 10,
    test_mode INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nudges (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL,
    visitor_id TEXT NOT NULL,
    session_id TEXT,
    ip_hash TEXT,
    group_name TEXT NOT NULL,         -- treatment | control
    trigger TEXT,
    signals TEXT,
    cart TEXT,
    cart_total REAL,
    currency TEXT,
    state TEXT NOT NULL,              -- control | opened | offered | accepted | paid | dismissed
    transcript TEXT NOT NULL DEFAULT '[]',
    reason_category TEXT,
    lever TEXT,
    discount_amount REAL DEFAULT 0,
    final_total REAL,
    message TEXT,
    rationale TEXT,
    model TEXT,
    decision_ms INTEGER,
    paypal_order_id TEXT,
    capture_id TEXT,
    store_order_id TEXT,
    store_order_url TEXT,
    paid_total REAL,
    fee_amount REAL,
    invoice_id TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS nudges_visitor ON nudges(merchant_id, visitor_id);
CREATE TABLE IF NOT EXISTS conversions (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL,
    visitor_id TEXT,
    order_ref TEXT NOT NULL,
    total REAL,
    source TEXT NOT NULL,             -- natural | nudge
    created_at REAL NOT NULL,
    UNIQUE(merchant_id, order_ref)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant_id TEXT NOT NULL,
    visitor_id TEXT,
    session_id TEXT,
    type TEXT NOT NULL,
    data TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL,
    paypal_invoice_id TEXT,
    amount REAL NOT NULL,
    orders INTEGER NOT NULL,
    status TEXT,
    url TEXT,
    created_at REAL NOT NULL
);
"""


def init() -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)


@contextmanager
def conn():
    c = sqlite3.connect(settings.DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def now() -> float:
    return time.time()


def row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("signals", "cart", "transcript", "data"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def update(table: str, id_: str, **fields) -> None:
    if table == "nudges":
        fields["updated_at"] = now()
    for k, v in list(fields.items()):
        if isinstance(v, (dict, list)):
            fields[k] = json.dumps(v)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE {table} SET {cols} WHERE id = ?", (*fields.values(), id_))


def get(table: str, id_: str) -> dict | None:
    with conn() as c:
        return row(c.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone())
