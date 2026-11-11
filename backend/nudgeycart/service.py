"""Use cases: decide on a nudge, take payment, record the outcome, bill the fee.

The caller is always the store plugin (server side, authenticated with the merchant's secret
key). The plugin reads prices and unit costs from its own catalogue, so the shopper's browser
never gets to state a price, and creates the store order itself after a capture.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import agent, db
from .core import policy
from .integrations import channel3, paypal

log = logging.getLogger("nudgeycart")

DISCOUNT_COOLDOWN_DAYS = 30
# Many shoppers share an IP (offices, mobile carriers), so the IP rule needs several hits.
IP_DISCOUNT_LIMIT = 3
MONEY_LEVERS = ("discount", "free_shipping", "price_match")

REASON_LABEL = {
    "price": "Price", "shipping_cost": "Shipping cost", "comparison_shopping": "Comparing prices",
    "trust": "Trust", "checkout_friction": "Checkout friction", "just_browsing": "Just browsing",
    "unclear": "Unclear",
}


class NudgeError(Exception):
    def __init__(self, status: int, msg: str):
        self.status = status
        super().__init__(msg)


# ---------- helpers ----------

def merchant_by_secret(authorization: str) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    with db.conn() as c:
        m = db.row(c.execute("SELECT * FROM merchants WHERE secret_key = ?", (token,)).fetchone()) if token else None
    if not m:
        raise NudgeError(401, "unknown merchant secret key")
    return m


def guardrails(m: dict) -> policy.Guardrails:
    return policy.Guardrails(m["min_margin_pct"], m["max_discount_pct"], m["fee_pct"])


def _dec(v, what: str, lo: Decimal = Decimal("0"), hi: Decimal = Decimal("100000")) -> Decimal:
    try:
        d = policy.money(v)
    except (InvalidOperation, TypeError, ValueError):
        raise NudgeError(400, f"invalid {what}")
    if not lo <= d <= hi:
        raise NudgeError(400, f"{what} out of range")
    return d


def cart_from_plugin(m: dict, d: dict) -> policy.Cart:
    """Validate the cart the store plugin built from its own catalogue."""
    if (d.get("currency") or m["currency"]) != m["currency"]:
        raise NudgeError(400, f"cart currency must be {m['currency']}")
    lines = []
    for l in (d.get("lines") or [])[:50]:
        cost = l.get("unit_cost")
        lines.append(policy.Line(
            product_id=int(l["product_id"]), name=str(l.get("name") or "")[:200],
            qty=max(1, min(int(l.get("qty") or 1), 999)),
            unit_price=_dec(l.get("unit_price"), "unit_price"),
            unit_cost=_dec(cost, "unit_cost") if cost not in (None, "") else None,
            sku=(str(l["sku"])[:64] if l.get("sku") else None)))
    return policy.Cart(lines=lines, shipping=_dec(d.get("shipping") or 0, "shipping", hi=Decimal("1000")),
                       currency=m["currency"])


def cart_to_store(cart: policy.Cart) -> dict:
    d = cart.to_dict()
    for line, l in zip(d["lines"], cart.lines):
        line["unit_cost"] = str(l.unit_cost) if l.unit_cost is not None else None
    return d


def cart_from_store(d: dict) -> policy.Cart:
    return policy.Cart(
        lines=[policy.Line(product_id=l["product_id"], name=l["name"], qty=l["qty"],
                           unit_price=Decimal(l["unit_price"]),
                           unit_cost=Decimal(l["unit_cost"]) if l.get("unit_cost") else None,
                           sku=l.get("sku")) for l in d["lines"]],
        shipping=Decimal(d["shipping"]), currency=d["currency"])


def ip_hash(ip: str | None) -> str | None:
    return hashlib.sha256(ip.encode()).hexdigest()[:16] if ip else None


def visitor_history(m: dict, visitor_id: str, iph: str | None, exclude: str | None = None) -> dict:
    since = db.now() - DISCOUNT_COOLDOWN_DAYS * 86400
    with db.conn() as c:
        rows = c.execute(
            """SELECT lever, state, visitor_id FROM nudges WHERE merchant_id = ? AND id != ? AND created_at > ?
               AND (visitor_id = ? OR (ip_hash IS NOT NULL AND ip_hash = ?))""",
            (m["id"], exclude or "", since, visitor_id, iph)).fetchall()
    money = [r for r in rows if r["lever"] in MONEY_LEVERS]
    mine = [r for r in money if r["visitor_id"] == visitor_id]
    return {
        "previous_nudges_30d": len(rows),
        "previous_money_offers_30d": len(mine),
        "money_offers_same_ip_30d": len(money) - len(mine),
        "previous_saved_orders_30d": sum(1 for r in rows if r["state"] == "paid"),
    }


def offer_view(nid: str, offer: policy.Offer, d: agent.Decision, cart: policy.Cart) -> dict:
    out = {
        "nudge_id": nid,
        "lever": offer.lever,
        "headline": d.headline if offer.lever == d.lever else "Checkout in one tap",
        "message": d.message,
        "cta_label": d.cta_label if offer.lever == d.lever else "Checkout with PayPal",
        "currency": cart.currency,
        "item_total": f"{offer.item_total:.2f}",
        "shipping": f"{offer.shipping:.2f}",
        "discount": f"{offer.discount_amount:.2f}",
        "final_total": f"{offer.final_total:.2f}",
        "original_total": f"{cart.total:.2f}",
        # The competitor link is deliberately not sent: the point is to keep the sale here.
        "matches": [{k: m[k] for k in ("name", "competitor", "competitor_price", "our_price")}
                    for m in offer.matches],
        # Disclosure for personalised pricing (NY Algorithmic Pricing Disclosure Act, EU Omnibus).
        "disclosure": "Personalized offer, chosen by an automated assistant based on your cart and this visit."
                      if offer.lever in MONEY_LEVERS else None,
    }
    if policy.money(30) <= offer.final_total <= policy.money(1500):
        out["installment"] = f"{policy.money(offer.final_total / 4):.2f}"
    return out


# ---------- store setup ----------

def config(m: dict) -> dict:
    from . import settings
    return {"paypal_client_id": settings.paypal_client_id(), "currency": m["currency"],
            "sandbox": settings.paypal_env() != "live", "merchant": m["name"], "merchant_id": m["id"],
            "test_mode": bool(m["test_mode"]), "demo": bool(m["demo"]),
            "price_match": bool(settings.channel3_api_key()),
            "guardrails": {"min_margin_pct": m["min_margin_pct"], "max_discount_pct": m["max_discount_pct"],
                           "fee_pct": m["fee_pct"], "control_pct": m["control_pct"]},
            "billing_email": m["billing_email"]}


def update_settings(m: dict, body: dict) -> dict:
    fields = {}
    for k, lo, hi in (("min_margin_pct", 0, 0.9), ("max_discount_pct", 0, 0.9), ("control_pct", 0, 50)):
        if k in body:
            v = float(body[k])
            if not lo <= v <= hi:
                raise NudgeError(400, f"{k} must be between {lo} and {hi}")
            fields[k] = int(v) if k == "control_pct" else v
    if "billing_email" in body:
        fields["billing_email"] = str(body["billing_email"] or "")[:200] or None
    if "name" in body and body["name"]:
        fields["name"] = str(body["name"])[:120]
    if fields:
        db.update("merchants", m["id"], **fields)
    return config(db.get("merchants", m["id"]))


def warm_market(m: dict, body: dict) -> dict:
    lines = [{"product_id": int(l["product_id"]), "name": str(l["name"])[:200]}
             for l in (body.get("lines") or []) if l.get("name")]
    return {"lookups": channel3.warm(lines, m["currency"])}


# ---------- the nudge ----------

def open_nudge(m: dict, body: dict) -> dict:
    visitor_id = str(body.get("visitor_id") or "")[:64]
    if not visitor_id:
        raise NudgeError(400, "visitor_id required")
    session_id = str(body.get("session_id") or "")[:64]
    with db.conn() as c:
        prior = c.execute("SELECT id, group_name FROM nudges WHERE merchant_id = ? AND session_id = ?",
                          (m["id"], session_id)).fetchone()
    if prior and not (m["test_mode"] and body.get("force")):
        return {"show": False, "why": "already nudged this session", "group": prior["group_name"]}
    signals = body.get("signals") or {}
    if not m["test_mode"] and float(signals.get("seconds_on_site", 0)) < 15:
        return {"show": False, "why": "not engaged enough yet"}

    cart = cart_from_plugin(m, body.get("cart") or {})
    if not cart.lines:
        return {"show": False, "why": "empty cart"}

    force = body.get("force") if m["test_mode"] else None
    group = policy.assign_group(m["id"], visitor_id, m["control_pct"], force)
    nid = db.new_id("ndg")
    iph = ip_hash(body.get("ip"))
    with db.conn() as c:
        c.execute(
            """INSERT INTO nudges (id, merchant_id, visitor_id, session_id, ip_hash, group_name, trigger,
               signals, cart, cart_total, currency, state, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (nid, m["id"], visitor_id, session_id, iph, group, str(body.get("trigger") or "")[:32],
             json.dumps(signals)[:4000], json.dumps(cart_to_store(cart)), float(cart.total), cart.currency,
             "control" if group == "control" else "deciding", db.now(), db.now()))
    if group == "control":
        return {"show": False, "why": "holdout group: measured, never nudged", "group": "control", "nudge_id": nid}

    g = guardrails(m)
    history = visitor_history(m, visitor_id, iph, exclude=nid)
    blocked = (history["previous_money_offers_30d"] > 0
               or history["money_offers_same_ip_30d"] >= IP_DISCOUNT_LIMIT)
    # Demo scenarios (test mode, forced) replay many exits from one browser. Each one is judged as a
    # first-time visitor: otherwise the agent, rightly, grows wary of a shopper nudged ten times.
    if force:
        blocked = False
        history = {k: 0 for k in history}
        history["note"] = "test mode demo scenario: judged as a first-time visitor"
    market = channel3.cached_offers([{"product_id": l.product_id, "name": l.name} for l in cart.lines],
                                    cart.currency)
    levers = policy.available_levers(cart, g, blocked, market)
    decision, meta = agent.decide(cart, levers, signals, history, body.get("locale"))
    offer = policy.clamp(decision.lever, decision.discount_pct, cart, g, levers)

    rationale = decision.rationale + (f" [guardrail: {'; '.join(offer.notes)}]" if offer.notes else "")
    if blocked:
        rationale += " [money levers blocked: this visitor/IP already got one in the last 30 days]"
    view = offer_view(nid, offer, decision, cart) if offer.lever != "none" else None
    db.update("nudges", nid, state="offered" if view else "skipped",
              reason_category=decision.reason_category, confidence=decision.confidence,
              lever=offer.lever, offer=view or {"evidence": decision.evidence},
              discount_amount=float(offer.discount_amount), final_total=float(offer.final_total),
              message=decision.message, rationale=rationale, model=meta["model"], decision_ms=meta["ms"])
    base = {"group": "treatment", "nudge_id": nid, "reason": decision.reason_category,
            "evidence": decision.evidence, "rationale": rationale, "model": meta["model"], "ms": meta["ms"]}
    if not view:
        return {"show": False, "why": "agent chose not to interrupt", **base}
    return {"show": True, **base, **view}


def current_offer(n: dict) -> tuple[policy.Cart, policy.Offer]:
    cart = cart_from_store(n["cart"])
    offer = policy.Offer(lever=n["lever"], item_total=cart.item_total, shipping=cart.shipping,
                         discount_amount=policy.money(n["discount_amount"] or 0))
    if n["lever"] == "free_shipping":
        offer.shipping = Decimal("0")
    return cart, offer


def _nudge(m: dict, nid: str) -> dict:
    n = db.get("nudges", nid)
    if not n or n["merchant_id"] != m["id"]:
        raise NudgeError(404, "nudge not found")
    return n


def create_paypal_order(m: dict, nid: str) -> dict:
    n = _nudge(m, nid)
    if n["state"] not in ("offered", "accepted"):
        raise NudgeError(409, "no open offer to pay")
    cart, offer = current_offer(n)
    if offer.final_total != policy.money(n["final_total"]):
        raise NudgeError(500, "offer integrity check failed")
    try:
        o = paypal.create_order(cart, offer, nid, m["name"], m["payee_email"])
    except paypal.PayPalError as e:
        log.error("create order failed: %s", e)
        raise NudgeError(502, "PayPal order could not be created")
    db.update("nudges", nid, state="accepted", paypal_order_id=o["id"])
    return {"id": o["id"]}


def capture(m: dict, nid: str, order_id: str) -> dict:
    """Capture the payment and hand the plugin everything it needs to create the store order."""
    n = _nudge(m, nid)
    if n["paypal_order_id"] != order_id:
        raise NudgeError(404, "order does not belong to this nudge")
    cart, offer = current_offer(n)
    if n["state"] == "paid":
        return {"status": "COMPLETED", "already_recorded": bool(n["store_order_id"]),
                "store_order_id": n["store_order_id"]}
    try:
        cap = paypal.capture_order(order_id)
    except paypal.PayPalError as e:
        # INSTRUMENT_DECLINED means the buyer should pick another funding source.
        issue = (e.body.get("details") or [{}])[0].get("issue")
        raise NudgeError(422 if issue == "INSTRUMENT_DECLINED" else 502, issue or "capture failed")
    s = paypal.capture_summary(cap)
    if s["status"] != "COMPLETED":
        db.update("nudges", nid, error=f"capture status {s['status']}", capture_id=s["capture_id"])
        return {"status": s["status"]}
    fee = policy.fee_for(s["amount"], guardrails(m))
    db.update("nudges", nid, state="paid", capture_id=s["capture_id"], paid_total=float(s["amount"]),
              fee_amount=float(fee))
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO conversions VALUES (?,?,?,?,?,?,?)",
                  (db.new_id("cv"), m["id"], n["visitor_id"], f"paypal:{s['capture_id']}",
                   float(s["amount"]), "nudge", db.now()))
    return {
        "status": "COMPLETED",
        "capture": {**{k: v for k, v in s.items() if k != "amount"}, "amount": f"{s['amount']:.2f}"},
        "offer": {"lever": offer.lever, "item_total": f"{offer.item_total:.2f}", "shipping": f"{offer.shipping:.2f}",
                  "discount": f"{offer.discount_amount:.2f}", "final_total": f"{offer.final_total:.2f}"},
        "lines": [{"product_id": l.product_id, "qty": l.qty, "unit_price": f"{l.unit_price:.2f}"} for l in cart.lines],
        "fee": f"{fee:.2f}",
    }


def record_store_order(m: dict, nid: str, body: dict) -> dict:
    n = _nudge(m, nid)
    error = None
    if body.get("error"):
        error = f"paid but store order failed: {str(body['error'])[:300]}"
    elif body.get("total") is not None and policy.money(body["total"]) != policy.money(n["paid_total"] or 0):
        error = f"store total {body['total']} differs from captured {n['paid_total']}"
    db.update("nudges", nid, store_order_id=str(body.get("store_order_id") or "")[:32] or None,
              store_order_url=str(body.get("url") or "")[:500] or None, error=error)
    return {"ok": True, "warning": error}


def dismiss(m: dict, nid: str) -> dict:
    n = db.get("nudges", nid)
    if n and n["merchant_id"] == m["id"] and n["state"] == "offered":
        db.update("nudges", nid, state="dismissed")
    return {"ok": True}


def record_conversion(m: dict, body: dict) -> dict:
    ref = str(body.get("order_ref") or "")[:64]
    if not ref:
        raise NudgeError(400, "order_ref required")
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO conversions VALUES (?,?,?,?,?,?,?)",
                  (db.new_id("cv"), m["id"], str(body.get("visitor_id") or "")[:64], f"store:{ref}",
                   float(body.get("total") or 0), "natural", db.now()))
    return {"ok": True}


def record_events(m: dict, body: dict) -> dict:
    evs = (body.get("events") or [])[:50]
    with db.conn() as c:
        c.executemany(
            "INSERT INTO events (merchant_id, visitor_id, session_id, type, data, created_at) VALUES (?,?,?,?,?,?)",
            [(m["id"], str(body.get("visitor_id"))[:64], str(body.get("session_id"))[:64],
              str(e.get("type"))[:32], json.dumps(e.get("data") or {})[:2000], db.now()) for e in evs])
    return {"ok": True, "n": len(evs)}


# ---------- merchant dashboard & billing ----------

NUDGE_FIELDS = ("id", "created_at", "group_name", "state", "trigger", "reason_category", "confidence", "lever",
                "cart_total", "discount_amount", "final_total", "paid_total", "fee_amount", "model",
                "decision_ms", "rationale", "message", "store_order_id", "invoice_id", "error", "visitor_id",
                "offer", "signals", "cart")


def summary(m: dict) -> dict:
    with db.conn() as c:
        rows = [db.row(r) for r in c.execute(
            "SELECT * FROM nudges WHERE merchant_id = ? ORDER BY created_at DESC", (m["id"],)).fetchall()]
        conv = {r["visitor_id"] for r in c.execute(
            "SELECT DISTINCT visitor_id FROM conversions WHERE merchant_id = ?", (m["id"],)).fetchall()}

    def group_stats(name: str) -> dict:
        g = [r for r in rows if r["group_name"] == name]
        visitors = {r["visitor_id"] for r in g}
        converted = visitors & conv
        return {"visitors": len(visitors), "converted": len(converted),
                "rate": round(len(converted) / len(visitors), 4) if visitors else None}

    t, ctl = group_stats("treatment"), group_stats("control")
    paid = [r for r in rows if r["state"] == "paid"]
    uplift = (t["rate"] - ctl["rate"]) if t["rate"] is not None and ctl["rate"] is not None else None
    levers: dict[str, dict] = {}
    reasons: Counter = Counter()
    daily: dict[str, dict] = defaultdict(lambda: {"nudges": 0, "saved_orders": 0, "saved_revenue": 0.0,
                                                  "conceded": 0.0, "skipped": 0})
    for r in rows:
        day = datetime.fromtimestamp(r["created_at"], timezone.utc).strftime("%Y-%m-%d")
        if r["group_name"] == "treatment":
            daily[day]["nudges"] += 1
        if r["state"] == "skipped":
            daily[day]["skipped"] += 1
        if r["reason_category"]:
            reasons[r["reason_category"]] += 1
        if r["lever"] and r["lever"] != "none":
            s = levers.setdefault(r["lever"], {"offered": 0, "paid": 0, "revenue": 0.0})
            s["offered"] += 1
            if r["state"] == "paid":
                s["paid"] += 1
                s["revenue"] = round(s["revenue"] + (r["paid_total"] or 0), 2)
        if r["state"] == "paid":
            daily[day]["saved_orders"] += 1
            daily[day]["saved_revenue"] = round(daily[day]["saved_revenue"] + (r["paid_total"] or 0), 2)
            daily[day]["conceded"] = round(daily[day]["conceded"] + (r["cart_total"] or 0) - (r["paid_total"] or 0), 2)
    return {
        "merchant": {"name": m["name"], "currency": m["currency"], "fee_pct": m["fee_pct"],
                     "control_pct": m["control_pct"], "min_margin_pct": m["min_margin_pct"],
                     "max_discount_pct": m["max_discount_pct"], "test_mode": bool(m["test_mode"]),
                     "demo": bool(m["demo"])},
        "saved_orders": len(paid),
        "saved_revenue": round(sum(r["paid_total"] or 0 for r in paid), 2),
        # Everything conceded on closed sales: item discounts plus waived shipping.
        "discount_given": round(sum((r["cart_total"] or 0) - (r["paid_total"] or 0) for r in paid), 2),
        "fees_total": round(sum(r["fee_amount"] or 0 for r in paid), 2),
        "fees_uninvoiced": round(sum(r["fee_amount"] or 0 for r in paid if not r["invoice_id"]), 2),
        "skipped": sum(1 for r in rows if r["state"] == "skipped"),
        "treatment": t, "control": ctl, "uplift_pts": round(uplift * 100, 1) if uplift is not None else None,
        "levers": levers,
        "reasons": [{"reason": k, "label": REASON_LABEL.get(k, k), "count": v} for k, v in reasons.most_common()],
        "daily": [{"day": d, **v} for d, v in sorted(daily.items())],
        "nudges": [{k: r.get(k) for k in NUDGE_FIELDS} for r in rows[:500]],
    }


def invoice_fees(m: dict) -> dict:
    if not m["billing_email"]:
        raise NudgeError(400, "add a billing email in NudgeyCart settings first")
    with db.conn() as c:
        rows = [db.row(r) for r in c.execute(
            """SELECT * FROM nudges WHERE merchant_id = ? AND state = 'paid' AND invoice_id IS NULL
               AND fee_amount > 0 ORDER BY created_at""", (m["id"],)).fetchall()]
    if not rows:
        raise NudgeError(409, "nothing to invoice")
    total = policy.money(sum(Decimal(str(r["fee_amount"])) for r in rows))
    lines = [(f"Success fee {m['fee_pct']*100:g}% on saved order "
              f"{r['store_order_id'] or r['capture_id']} ({r['lever']})", policy.money(r["fee_amount"]))
             for r in rows]
    try:
        inv = paypal.create_and_send_invoice(
            m["billing_email"], m["currency"], lines,
            note=f"NudgeyCart success fees: {len(rows)} saved orders. You only pay for sales we closed.")
    except paypal.PayPalError as e:
        log.error("invoice failed: %s", e)
        raise NudgeError(502, f"PayPal invoicing failed: {e}")
    iid = db.new_id("inv")
    with db.conn() as c:
        c.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?)",
                  (iid, m["id"], inv["id"], float(total), len(rows), inv["status"], inv["url"], db.now()))
        c.executemany("UPDATE nudges SET invoice_id = ? WHERE id = ?", [(iid, r["id"]) for r in rows])
    return {"invoice_id": iid, "paypal_invoice_id": inv["id"], "amount": f"{total:.2f}",
            "orders": len(rows), "status": inv["status"], "url": inv["url"]}


def list_invoices(m: dict) -> list[dict]:
    with db.conn() as c:
        return [db.row(r) for r in c.execute(
            "SELECT * FROM invoices WHERE merchant_id = ? ORDER BY created_at DESC", (m["id"],)).fetchall()]


def handle_refund(capture_id: str) -> None:
    """A refunded sale is not a saved sale: drop the fee if it was not invoiced yet."""
    with db.conn() as c:
        c.execute("""UPDATE nudges SET state = 'refunded',
                     fee_amount = CASE WHEN invoice_id IS NULL THEN 0 ELSE fee_amount END
                     WHERE capture_id = ?""", (capture_id,))
