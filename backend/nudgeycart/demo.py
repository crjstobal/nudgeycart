"""Self-service demo merchants for WordPress Playground stores.

Every Playground visitor boots a fresh WooCommerce store. When the NudgeyCart plugin
activates there, it registers itself here and gets its own merchant account in test mode,
with 30 days of illustrative history so the dashboard is not empty on first load.
Seeded rows are marked with model "demo-seed" and never touch PayPal.
"""
from __future__ import annotations

import json
import random
import secrets
from decimal import Decimal

from . import db, settings
from .core import policy
from .service import NudgeError, ip_hash

SEED_DAYS = 30

# (reason, lever, evidence, rationale template, signals)
SCENARIOS = [
    ("comparison_shopping", "price_match", ["copied the product name", "away 40s in another tab"],
     "Copied the product name and spent 40s in another tab: comparing prices. {competitor} lists it "
     "{gap} lower, which fits the margin floor, so a price match beats a generic discount.",
     {"copied_product_name": 1, "tab_switches": 2, "away_s": 41}),
    ("shipping_cost", "free_shipping", ["6s on the shipping line", "edited the postcode"],
     "Lingered on the shipping cost and re-entered the postcode: shipping is the blocker. Waiving it "
     "costs less than any discount that would compensate.",
     {"shipping_dwell_s": 6.2, "postcode_edits": 1}),
    ("price", "discount", ["focused the coupon field twice", "reduced quantity"],
     "Hunted for a coupon and reduced the quantity: price sensitive. Offered the smallest discount "
     "that plausibly closes, well inside the cap.",
     {"coupon_focus": 2, "qty_decreases": 1}),
    ("trust", "reassurance", ["opened the returns policy", "read reviews"],
     "Read the returns policy and reviews before leaving: a trust question, not a price one. "
     "Reassurance about PayPal Buyer Protection costs nothing.",
     {"policy_views": 2, "reviews_views": 1}),
    ("checkout_friction", "reassurance", ["stalled 50s on checkout", "one form error"],
     "Stalled on the checkout form after an error: friction. One-tap PayPal checkout removes the form.",
     {"checkout_fields_filled": 4, "form_errors": 1, "idle_s": 50}),
    ("just_browsing", "none", ["12s visit", "no cart interaction"],
     "Short visit with no engagement beyond adding to cart: likely browsing. Did not interrupt.",
     {"seconds_on_site": 12}),
    ("unclear", "none", ["mixed signals"],
     "No clear reason in the signals (confidence 0.3). Not interrupting is cheaper than a wrong offer.",
     {"tab_switches": 1, "away_s": 4}),
]
SCENARIO_WEIGHTS = [16, 14, 18, 12, 8, 18, 14]
# Probability that an offered nudge ends up paid, per lever.
CLOSE_RATE = {"price_match": 0.16, "free_shipping": 0.14, "discount": 0.12, "reassurance": 0.07}
COMPETITORS = ["amazon.com", "target.com", "walmart.com", "bestbuy.com", "rei.com"]
TRIGGERS = ["exit_intent", "exit_intent", "exit_intent", "tab_return", "idle", "scroll_up"]

DEFAULT_CATALOG = [
    {"product_id": 1, "name": "Demo product", "price": "60.00", "cost": "30.00"},
]


def _signup_count(iph: str | None) -> int:
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) FROM merchants WHERE signup_ip_hash = ? AND created_at > ?",
                         (iph, db.now() - 3600)).fetchone()[0]


def create_demo_merchant(body: dict, ip: str | None) -> dict:
    iph = ip_hash(ip)
    if iph and _signup_count(iph) >= settings.DEMO_SIGNUPS_PER_HOUR:
        raise NudgeError(429, "too many demo stores from this address, try again later")
    currency = str(body.get("currency") or "USD")[:3].upper()
    try:
        buyer = settings.get_key(settings.PAYPAL_SECRETS, "PAYPAL_SANDBOX_BUYER_EMAIL")
    except Exception:
        buyer = None
    m = {
        "id": db.new_id("mch"), "name": str(body.get("store_name") or "Demo store")[:120],
        "secret_key": f"sk_{secrets.token_hex(20)}", "platform": "woocommerce",
        "store_url": str(body.get("store_url") or "")[:300] or None, "payee_email": None,
        # Demo fee invoices go to the sandbox buyer so the PayPal invoice can be opened and paid.
        "billing_email": buyer, "currency": currency, "min_margin_pct": 0.25, "max_discount_pct": 0.15,
        "fee_pct": 0.05, "control_pct": 10, "test_mode": 1, "demo": 1, "signup_ip_hash": iph,
        "created_at": db.now(),
    }
    with db.conn() as c:
        c.execute(f"INSERT INTO merchants ({','.join(m)}) VALUES ({','.join('?' * len(m))})", tuple(m.values()))
    catalog = [p for p in (body.get("catalog") or []) if p.get("name") and p.get("price")][:30] or DEFAULT_CATALOG
    seed_history(m, catalog)
    return {"merchant_id": m["id"], "secret_key": m["secret_key"]}


def seed_history(m: dict, catalog: list[dict], seed: int | None = None) -> int:
    rnd = random.Random(seed if seed is not None else m["id"])
    g = policy.Guardrails(m["min_margin_pct"], m["max_discount_pct"], m["fee_pct"])
    now = db.now()
    nudges, conversions = [], []
    for day in range(SEED_DAYS, 0, -1):
        # A slow upward trend plus weekend bumps, so the charts look like a real store.
        base = 9 + (SEED_DAYS - day) * 0.15 + (4 if day % 7 in (0, 1) else 0)
        for _ in range(int(rnd.gauss(base, 2)) if base > 2 else 2):
            ts = now - day * 86400 + rnd.uniform(0, 86400)
            visitor = f"seed_{rnd.getrandbits(48):012x}"
            items = rnd.sample(catalog, k=min(len(catalog), rnd.choice([1, 1, 1, 2, 2, 3])))
            lines = [policy.Line(int(p["product_id"]), p["name"], rnd.choice([1, 1, 1, 2]),
                                 policy.money(p["price"]), policy.money(p["cost"]) if p.get("cost") else None)
                     for p in items]
            cart = policy.Cart(lines=lines, shipping=policy.money("7.99"), currency=m["currency"])
            group = "control" if rnd.random() < m["control_pct"] / 100 else "treatment"
            row = {"id": db.new_id("ndg"), "merchant_id": m["id"], "visitor_id": visitor,
                   "session_id": f"seed_{rnd.getrandbits(32):08x}", "group_name": group,
                   "trigger": rnd.choice(TRIGGERS), "cart": json.dumps({**cart.to_dict()}),
                   "cart_total": float(cart.total), "currency": cart.currency, "created_at": ts, "updated_at": ts}
            if group == "control":
                row.update(state="control", signals="{}")
                nudges.append(row)
                if rnd.random() < 0.08:
                    conversions.append((visitor, float(cart.total), ts + 120))
                continue
            reason, lever, evidence, why, signals = rnd.choices(SCENARIOS, SCENARIO_WEIGHTS)[0]
            offer = policy.Offer(lever=lever, item_total=cart.item_total, shipping=cart.shipping)
            competitor = rnd.choice(COMPETITORS)
            headroom = policy.max_discount(cart, g)
            if lever == "price_match":
                gap = policy.money(min(policy.margin_headroom(cart, g), cart.item_total * Decimal("0.07")))
                offer.discount_amount = gap if gap > 0 else Decimal("0")
            elif lever == "discount":
                offer.discount_amount = policy.money(min(headroom, cart.item_total * Decimal("0.08")))
            elif lever == "free_shipping":
                offer.shipping = Decimal("0")
            if lever in ("price_match", "discount") and offer.discount_amount <= 0:
                lever = offer.lever = "reassurance"
            row.update(reason_category=reason, confidence=round(rnd.uniform(0.55, 0.92), 2) if lever != "none"
                       else round(rnd.uniform(0.15, 0.4), 2), lever=lever, signals=json.dumps(signals),
                       rationale=why.format(competitor=competitor, gap=f"{offer.discount_amount} {cart.currency}"),
                       offer=json.dumps({"evidence": evidence}), discount_amount=float(offer.discount_amount),
                       final_total=float(offer.final_total), model="demo-seed",
                       decision_ms=int(rnd.uniform(1400, 3200)))
            if lever == "none":
                row["state"] = "skipped"
            elif rnd.random() < CLOSE_RATE[lever]:
                paid = offer.final_total
                row.update(state="paid", paid_total=float(paid), fee_amount=float(policy.fee_for(paid, g)),
                           capture_id=f"SEED{rnd.getrandbits(40):010X}",
                           store_order_id=f"seed-{rnd.randint(1000, 9999)}")
                conversions.append((visitor, float(paid), ts + 90))
            else:
                row["state"] = rnd.choice(["dismissed", "dismissed", "dismissed", "accepted"])
                if rnd.random() < 0.06:
                    conversions.append((visitor, float(cart.total), ts + 600))
            nudges.append(row)
    with db.conn() as c:
        for r in nudges:
            c.execute(f"INSERT INTO nudges ({','.join(r)}) VALUES ({','.join('?' * len(r))})", tuple(r.values()))
        c.executemany("INSERT OR IGNORE INTO conversions VALUES (?,?,?,?,?,?,?)",
                      [(db.new_id("cv"), m["id"], v, f"seed:{i}", t, "seed", ts)
                       for i, (v, t, ts) in enumerate(conversions)])
        # Fees older than two weeks were already billed: one paid PayPal invoice in the history.
        old = [r for r in nudges if r.get("state") == "paid" and r["created_at"] < now - 14 * 86400]
        if old:
            iid = db.new_id("inv")
            c.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?)",
                      (iid, m["id"], "SEED-INV", round(sum(r["fee_amount"] for r in old), 2), len(old),
                       "PAID", None, now - 13 * 86400))
            c.executemany("UPDATE nudges SET invoice_id = ? WHERE id = ?", [(iid, r["id"]) for r in old])
    return len(nudges)
