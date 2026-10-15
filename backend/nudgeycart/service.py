"""Use cases: open a nudge, converse, take payment, record the outcome, bill the fee."""
from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal

from . import agent, db
from .core import policy
from .integrations import paypal, woo

log = logging.getLogger("nudgeycart")

DISCOUNT_COOLDOWN_DAYS = 30
# Many shoppers share an IP (offices, mobile carriers), so the IP rule needs several hits.
IP_DISCOUNT_LIMIT = 3
MAX_TURNS = 2

OPENING = {
    "message": "Before you go: is something stopping you from finishing your order?",
    "chips": [
        {"reason": "price", "label": "It's a bit too expensive"},
        {"reason": "payment_timing", "label": "I'd rather pay later"},
        {"reason": "shipping_cost", "label": "Shipping costs too much"},
        {"reason": "checkout_friction", "label": "Checkout takes too long"},
        {"reason": "trust", "label": "Not sure about this store"},
        {"reason": "just_browsing", "label": "Just browsing"},
    ],
}


class NudgeError(Exception):
    def __init__(self, status: int, msg: str):
        self.status = status
        super().__init__(msg)


# ---------- helpers ----------

def merchant_by_key(public_key: str) -> dict:
    with db.conn() as c:
        m = db.row(c.execute("SELECT * FROM merchants WHERE public_key = ?", (public_key,)).fetchone())
    if not m:
        raise NudgeError(401, "unknown merchant key")
    return m


def merchant_by_admin(token: str) -> dict:
    with db.conn() as c:
        m = db.row(c.execute("SELECT * FROM merchants WHERE admin_token = ?", (token,)).fetchone())
    if not m:
        raise NudgeError(401, "unknown admin token")
    return m


def guardrails(m: dict) -> policy.Guardrails:
    return policy.Guardrails(m["min_margin_pct"], m["max_discount_pct"], m["fee_pct"])


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
    money = [r for r in rows if r["lever"] in ("discount", "free_shipping")]
    mine = [r for r in money if r["visitor_id"] == visitor_id]
    return {
        "previous_nudges_30d": len(rows),
        "previous_discount_offers_30d": len(mine),
        "discount_offers_same_ip_30d": len(money) - len(mine),
        "previous_saved_orders_30d": sum(1 for r in rows if r["state"] == "paid"),
    }


def offer_view(n: dict, offer: policy.Offer, decision: agent.Decision, cart: policy.Cart) -> dict:
    out = {
        "nudge_id": n["id"],
        "lever": offer.lever,
        "message": decision.message,
        "cta_label": decision.cta_label if offer.lever == decision.lever else "Checkout with PayPal",
        "currency": cart.currency,
        "item_total": f"{offer.item_total:.2f}",
        "shipping": f"{offer.shipping:.2f}",
        "discount": f"{offer.discount_amount:.2f}",
        "final_total": f"{offer.final_total:.2f}",
        "original_total": f"{cart.total:.2f}",
        "turns_left": MAX_TURNS - sum(1 for t in n["transcript"] if t["role"] == "shopper"),
        # Disclosure for personalised pricing (NY Algorithmic Pricing Disclosure Act, EU Omnibus).
        "disclosure": "Personalized offer, chosen by an automated assistant based on your cart and this visit."
                      if offer.lever in ("discount", "free_shipping") else None,
    }
    if offer.lever == "pay_later":
        out["installment"] = f"{policy.money(offer.final_total / 4):.2f}"
    return out


# ---------- use cases ----------

def open_nudge(m: dict, body: dict, ip: str | None) -> dict:
    visitor_id = str(body.get("visitor_id") or "")[:64]
    if not visitor_id:
        raise NudgeError(400, "visitor_id required")
    session_id = str(body.get("session_id") or "")[:64]
    with db.conn() as c:
        prior = c.execute("SELECT id, group_name FROM nudges WHERE merchant_id = ? AND session_id = ?",
                          (m["id"], session_id)).fetchone()
    if prior:
        return {"show": False, "why": "already nudged this session", "group": prior["group_name"]}
    signals = body.get("signals") or {}
    if not m["test_mode"] and float(signals.get("seconds_on_site", 0)) < 15:
        return {"show": False, "why": "not engaged enough yet"}

    try:
        cart = woo.build_cart(m, body.get("items") or [], body.get("shipping"))
    except woo.StoreError as e:
        return {"show": False, "why": str(e)}

    force = body.get("force") if m["test_mode"] else None
    group = policy.assign_group(m["id"], visitor_id, m["control_pct"], force)
    nid = db.new_id("ndg")
    with db.conn() as c:
        c.execute(
            """INSERT INTO nudges (id, merchant_id, visitor_id, session_id, ip_hash, group_name, trigger,
               signals, cart, cart_total, currency, state, transcript, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (nid, m["id"], visitor_id, session_id, ip_hash(ip), group, body.get("trigger"),
             json.dumps(signals), json.dumps(cart_to_store(cart)), float(cart.total), cart.currency,
             "control" if group == "control" else "opened",
             json.dumps([{"role": "assistant", "text": OPENING["message"]}]), db.now(), db.now()))
    if group == "control":
        return {"show": False, "why": "control group", "group": "control", "nudge_id": nid}
    return {"show": True, "group": "treatment", "nudge_id": nid, "locale": body.get("locale"), **OPENING}


def reply(m: dict, nid: str, body: dict) -> dict:
    n = db.get("nudges", nid)
    if not n or n["merchant_id"] != m["id"] or n["group_name"] != "treatment":
        raise NudgeError(404, "nudge not found")
    if n["state"] in ("paid", "dismissed"):
        raise NudgeError(409, f"nudge is {n['state']}")
    shopper_turns = sum(1 for t in n["transcript"] if t["role"] == "shopper")
    if shopper_turns >= MAX_TURNS:
        raise NudgeError(409, "conversation finished")

    reason = body.get("reason")
    text = str(body.get("text") or "")[:500]
    turn = {"role": "shopper"}
    if reason:
        turn["reason"] = reason
        turn["text"] = next((c["label"] for c in OPENING["chips"] if c["reason"] == reason), reason)
    if text:
        turn["text"] = text
    transcript = n["transcript"] + [turn]

    cart = cart_from_store(n["cart"])
    g = guardrails(m)
    history = visitor_history(m, n["visitor_id"], n["ip_hash"], exclude=nid)
    discount_blocked = (history["previous_discount_offers_30d"] > 0
                        or history["discount_offers_same_ip_30d"] >= IP_DISCOUNT_LIMIT)
    levers = policy.available_levers(cart, g, discount_blocked)
    decision, meta = agent.decide(cart, levers, n["signals"], transcript, history, body.get("locale"))
    offer = policy.clamp(decision.lever, decision.discount_pct, cart, g, levers)

    transcript.append({"role": "assistant", "text": decision.message, "lever": offer.lever,
                       "final_total": f"{offer.final_total:.2f}"})
    rationale = decision.rationale + (f" [guardrail: {'; '.join(offer.notes)}]" if offer.notes else "")
    if discount_blocked:
        rationale += " [money levers blocked: discount already offered to this visitor/IP in the last 30 days]"
    db.update("nudges", nid, transcript=transcript, state="offered", reason_category=decision.reason_category,
              lever=offer.lever, discount_amount=float(offer.discount_amount),
              final_total=float(offer.final_total), message=decision.message, rationale=rationale,
              model=meta["model"], decision_ms=meta["ms"])
    n = db.get("nudges", nid)
    return offer_view(n, offer, decision, cart)


def current_offer(n: dict) -> tuple[policy.Cart, policy.Offer]:
    cart = cart_from_store(n["cart"])
    offer = policy.Offer(lever=n["lever"], item_total=cart.item_total, shipping=cart.shipping,
                         discount_amount=policy.money(n["discount_amount"] or 0))
    if n["lever"] == "free_shipping":
        offer.shipping = Decimal("0")
    return cart, offer


def create_paypal_order(m: dict, nid: str) -> dict:
    n = db.get("nudges", nid)
    if not n or n["merchant_id"] != m["id"] or n["state"] not in ("offered", "accepted"):
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
    n = db.get("nudges", nid)
    if not n or n["merchant_id"] != m["id"] or n["paypal_order_id"] != order_id:
        raise NudgeError(404, "order does not belong to this nudge")
    if n["state"] == "paid":
        return {"status": "COMPLETED", "redirect": n["store_order_url"]}
    try:
        cap = paypal.capture_order(order_id)
    except paypal.PayPalError as e:
        # INSTRUMENT_DECLINED means the buyer should pick another funding source.
        issue = (e.body.get("details") or [{}])[0].get("issue")
        raise NudgeError(422 if issue == "INSTRUMENT_DECLINED" else 502, issue or "capture failed")
    s = paypal.capture_summary(cap)
    if s["status"] != "COMPLETED":
        db.update("nudges", nid, error=f"capture status {s['status']}", capture_id=s["capture_id"])
        return {"status": s["status"], "redirect": None}

    cart, offer = current_offer(n)
    fee = policy.fee_for(s["amount"], guardrails(m))
    db.update("nudges", nid, state="paid", capture_id=s["capture_id"], paid_total=float(s["amount"]),
              fee_amount=float(fee))
    redirect, error = None, None
    try:
        order = woo.create_paid_order(m, cart, offer, s, n)
        redirect = order["url"]
        if order["total"] != s["amount"]:
            error = f"store total {order['total']} differs from captured {s['amount']}"
        db.update("nudges", nid, store_order_id=str(order["id"]), store_order_url=redirect, error=error)
    except woo.StoreError as e:
        # Money is captured: never fail the shopper here. Flag it for the merchant instead.
        error = f"paid but store order failed: {e}"
        db.update("nudges", nid, error=error)
        log.error("%s (nudge %s)", error, nid)
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO conversions VALUES (?,?,?,?,?,?,?)",
                  (db.new_id("cv"), m["id"], n["visitor_id"], f"paypal:{s['capture_id']}",
                   float(s["amount"]), "nudge", db.now()))
    return {"status": "COMPLETED", "redirect": redirect or m["store_url"], "warning": error}


def dismiss(m: dict, nid: str) -> dict:
    n = db.get("nudges", nid)
    if n and n["merchant_id"] == m["id"] and n["state"] in ("opened", "offered"):
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
    for r in rows:
        if r["lever"]:
            s = levers.setdefault(r["lever"], {"offered": 0, "paid": 0, "revenue": 0.0})
            s["offered"] += 1
            if r["state"] == "paid":
                s["paid"] += 1
                s["revenue"] += r["paid_total"] or 0
    return {
        "merchant": {"name": m["name"], "currency": m["currency"], "fee_pct": m["fee_pct"],
                     "control_pct": m["control_pct"], "min_margin_pct": m["min_margin_pct"],
                     "max_discount_pct": m["max_discount_pct"], "test_mode": bool(m["test_mode"])},
        "saved_orders": len(paid),
        "saved_revenue": round(sum(r["paid_total"] or 0 for r in paid), 2),
        # Everything conceded on closed sales: item discounts plus waived shipping.
        "discount_given": round(sum((r["cart_total"] or 0) - (r["paid_total"] or 0) for r in paid), 2),
        "fees_total": round(sum(r["fee_amount"] or 0 for r in paid), 2),
        "fees_uninvoiced": round(sum(r["fee_amount"] or 0 for r in paid if not r["invoice_id"]), 2),
        "treatment": t, "control": ctl, "uplift_pts": round(uplift * 100, 1) if uplift is not None else None,
        "levers": levers,
        "nudges": [{k: r[k] for k in (
            "id", "created_at", "group_name", "state", "trigger", "reason_category", "lever", "cart_total",
            "discount_amount", "final_total", "paid_total", "fee_amount", "model", "decision_ms",
            "rationale", "message", "store_order_id", "invoice_id", "error", "visitor_id", "transcript")}
            for r in rows],
    }


def invoice_fees(m: dict) -> dict:
    if not m["billing_email"]:
        raise NudgeError(400, "merchant has no billing_email")
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
