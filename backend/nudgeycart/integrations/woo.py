"""WooCommerce adapter: trusted prices and costs in, paid orders out (REST API v3)."""
from __future__ import annotations

from decimal import Decimal

import httpx

from ..core.policy import Cart, Line, Offer, money

COST_META_KEY = "nudgey_cost"


class StoreError(Exception):
    pass


def _client(m: dict) -> httpx.Client:
    return httpx.Client(base_url=f"{m['store_url'].rstrip('/')}/wp-json/wc/v3",
                        auth=(m["woo_user"], m["woo_app_password"]), timeout=30)


def build_cart(m: dict, items: list[dict], client_shipping: float | None) -> Cart:
    """Re-price the cart from the store itself. The browser only tells us ids and quantities."""
    ids = sorted({int(i["product_id"]) for i in items})
    if not ids:
        raise StoreError("empty cart")
    with _client(m) as c:
        r = c.get("/products", params={"include": ",".join(map(str, ids)), "per_page": 100})
        if r.status_code >= 400:
            raise StoreError(f"store returned {r.status_code}: {r.text[:200]}")
        products = {p["id"]: p for p in r.json()}
    lines = []
    for i in items:
        pid = int(i["product_id"])
        p = products.get(pid)
        if p is None:
            raise StoreError(f"product {pid} not found (variable products are not supported yet)")
        cost = next((md["value"] for md in p.get("meta_data", []) if md["key"] == COST_META_KEY), None)
        lines.append(Line(
            product_id=pid, name=p["name"], qty=max(1, int(i["qty"])),
            unit_price=money(p["price"] or 0),
            unit_cost=money(cost) if cost not in (None, "") else None,
            sku=p.get("sku") or None,
        ))
    if m.get("shipping_flat") is not None:
        shipping = money(m["shipping_flat"])
    else:
        shipping = money(min(max(client_shipping or 0, 0), 100))
    return Cart(lines=lines, shipping=shipping, currency=m["currency"])


def create_paid_order(m: dict, cart: Cart, offer: Offer, payer: dict, nudge: dict) -> dict:
    addr = {**payer["address"], "first_name": payer["first_name"], "last_name": payer["last_name"]}
    payload = {
        "payment_method": "paypal",
        "payment_method_title": "PayPal (NudgeyCart)",
        "set_paid": True,
        "transaction_id": payer["capture_id"],
        "status": "processing",
        "billing": {**addr, "email": payer["email"] or ""},
        "shipping": addr,
        "line_items": [{"product_id": l.product_id, "quantity": l.qty} for l in cart.lines],
        "shipping_lines": [{"method_id": "flat_rate", "method_title": "Shipping",
                            "total": f"{offer.shipping:.2f}"}],
        "meta_data": [
            {"key": "nudgey_nudge_id", "value": nudge["id"]},
            {"key": "nudgey_lever", "value": offer.lever},
            {"key": "nudgey_visitor_id", "value": nudge["visitor_id"]},
        ],
        "customer_note": f"Closed by NudgeyCart ({offer.lever}).",
    }
    if offer.discount_amount > 0:
        payload["fee_lines"] = [{"name": "NudgeyCart offer", "total": f"-{offer.discount_amount:.2f}",
                                 "tax_status": "none"}]
    with _client(m) as c:
        r = c.post("/orders", json=payload)
        if r.status_code >= 400:
            raise StoreError(f"order create failed {r.status_code}: {r.text[:300]}")
        o = r.json()
    total = Decimal(o["total"])
    url = f"{m['store_url'].rstrip('/')}/checkout/order-received/{o['id']}/?key={o['order_key']}&nudgey_done=1"
    # The caller compares `total` with what PayPal captured and flags any mismatch.
    return {"id": o["id"], "url": url, "total": total}
