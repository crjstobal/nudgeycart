"""Merchant guardrails. Pure functions, no vendor SDKs.

The language model proposes; this module disposes. Whatever the model says, the
final offer is clamped here so a concession can never break the merchant's margin floor.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")

# Levers ordered from cheapest to most expensive for the merchant. Pay Later and express
# PayPal checkout are not levers: they belong in every checkout, so the offer dialog always
# shows them instead of "rewarding" a shopper with them.
LEVERS = ("reassurance", "free_shipping", "price_match", "discount", "none")

REASON_TO_LEVER = {
    "comparison_shopping": "price_match",
    "shipping_cost": "free_shipping",
    "price": "discount",
    "trust": "reassurance",
    "checkout_friction": "reassurance",
    "just_browsing": "none",
    "unclear": "none",
}

# A competitor listing far below our price is usually a different product (used, a part,
# a smaller size), not the same one cheaper. Ignore matches below this share of our price.
MATCH_MIN_RATIO = Decimal("0.6")


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Line:
    product_id: int
    name: str
    qty: int
    unit_price: Decimal
    unit_cost: Decimal | None = None
    sku: str | None = None

    @property
    def total(self) -> Decimal:
        return money(self.unit_price * self.qty)


@dataclass
class Cart:
    lines: list[Line]
    shipping: Decimal
    currency: str

    @property
    def item_total(self) -> Decimal:
        return money(sum((l.total for l in self.lines), Decimal("0")))

    @property
    def total(self) -> Decimal:
        return money(self.item_total + self.shipping)

    def to_dict(self) -> dict:
        """Shopper-safe view: never includes unit costs."""
        return {
            "currency": self.currency,
            "shipping": str(self.shipping),
            "item_total": str(self.item_total),
            "total": str(self.total),
            "lines": [
                {"product_id": l.product_id, "name": l.name, "qty": l.qty,
                 "unit_price": str(l.unit_price), "sku": l.sku}
                for l in self.lines
            ],
        }


@dataclass
class MarketOffer:
    """The cheapest comparable listing for one of our products at another retailer."""
    product_id: int
    domain: str
    price: Decimal
    url: str | None = None


@dataclass
class Guardrails:
    min_margin_pct: float
    max_discount_pct: float
    fee_pct: float


@dataclass
class Offer:
    lever: str
    discount_amount: Decimal = Decimal("0")
    shipping: Decimal = Decimal("0")
    item_total: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)
    matches: list[dict] = field(default_factory=list)

    @property
    def final_total(self) -> Decimal:
        return money(self.item_total + self.shipping - self.discount_amount)


def assign_group(merchant_id: str, visitor_id: str, control_pct: int, force: str | None = None) -> str:
    """Deterministic holdout: the same visitor always lands in the same group."""
    if force in ("treatment", "control"):
        return force
    h = int(hashlib.sha256(f"{merchant_id}:{visitor_id}".encode()).hexdigest(), 16) % 100
    return "control" if h < control_pct else "treatment"


def margin_headroom(cart: Cart, g: Guardrails) -> Decimal:
    """Largest concession that keeps the whole order at or above the margin floor.

    margin = (revenue - cost) / revenue >= m   =>   revenue >= cost / (1 - m)
    Lines without a known cost get zero headroom (we never guess a merchant's cost).
    The fee NudgeyCart charges is counted as a cost, so the floor holds after our cut.
    """
    known_rev = Decimal("0")
    known_cost = Decimal("0")
    for l in cart.lines:
        if l.unit_cost is None:
            continue
        known_rev += l.total
        known_cost += l.unit_cost * l.qty
    if known_rev <= 0:
        return Decimal("0")
    m = Decimal(str(g.min_margin_pct)) + Decimal(str(g.fee_pct))
    if m >= 1:
        return Decimal("0")
    floor_revenue = known_cost / (1 - m)
    return max(Decimal("0"), (known_rev - floor_revenue).quantize(CENT, rounding=ROUND_DOWN))


def max_discount(cart: Cart, g: Guardrails) -> Decimal:
    """A plain discount is also capped by the merchant's max discount policy."""
    by_policy = cart.item_total * Decimal(str(g.max_discount_pct))
    return min(margin_headroom(cart, g), by_policy.quantize(CENT, rounding=ROUND_DOWN))


def price_match_quote(cart: Cart, market: list[MarketOffer], g: Guardrails) -> dict | None:
    """What it costs to match the cheapest real competitor price for every line in the cart.

    A price match is all or nothing: if matching would break the margin floor, it is not
    offered at all (a partial "match" would be a plain discount with a misleading label).
    The max discount policy does not apply, because the shopper can already buy at that price.
    """
    best: dict[int, MarketOffer] = {}
    for o in market:
        if o.price > 0 and (o.product_id not in best or o.price < best[o.product_id].price):
            best[o.product_id] = o
    matches, needed = [], Decimal("0")
    for l in cart.lines:
        o = best.get(l.product_id)
        if not o or o.price >= l.unit_price or o.price < l.unit_price * MATCH_MIN_RATIO:
            continue
        gap = money((l.unit_price - o.price) * l.qty)
        needed += gap
        matches.append({"product_id": l.product_id, "name": l.name, "our_price": str(l.unit_price),
                        "competitor": o.domain, "competitor_price": str(money(o.price)),
                        "url": o.url, "saving": str(gap)})
    if not matches or needed > margin_headroom(cart, g):
        return None
    return {"amount": money(needed), "matches": matches}


def available_levers(cart: Cart, g: Guardrails, discount_blocked: bool,
                     market: list[MarketOffer] | None = None) -> dict[str, dict]:
    """What the agent may offer, with what each one costs the merchant."""
    headroom = Decimal("0") if discount_blocked else max_discount(cart, g)
    margin = Decimal("0") if discount_blocked else margin_headroom(cart, g)
    levers: dict[str, dict] = {
        "reassurance": {"merchant_cost": "0",
                        "what": "answer the likely concern honestly (returns, PayPal Buyer Protection, delivery)"},
        "none": {"merchant_cost": "0", "what": "do not interrupt the shopper at all"},
    }
    # The max discount policy is about item prices; waiving shipping only has to respect the margin.
    if cart.shipping > 0 and cart.shipping <= margin:
        levers["free_shipping"] = {"merchant_cost": str(cart.shipping), "what": "shipping waived"}
    quote = None if discount_blocked else price_match_quote(cart, market or [], g)
    if quote:
        levers["price_match"] = {
            "merchant_cost": str(quote["amount"]),
            "what": "match the lowest in-stock price found at other retailers: " + "; ".join(
                f"{m['name']} is {m['competitor_price']} at {m['competitor']} (ours {m['our_price']})"
                for m in quote["matches"]),
            "matches": quote["matches"],
        }
    if headroom > 0:
        max_pct = (headroom / cart.item_total * 100).quantize(Decimal("0.1"), rounding=ROUND_DOWN)
        levers["discount"] = {
            "merchant_cost": f"up to {headroom}",
            "what": f"discount on the items, at most {max_pct}% ({headroom} {cart.currency})",
            "max_pct": float(max_pct),
            "max_amount": str(headroom),
        }
    return levers


def clamp(lever: str, discount_pct: float, cart: Cart, g: Guardrails, levers: dict) -> Offer:
    """Turn the model's proposal into an offer the merchant has pre-approved."""
    offer = Offer(lever=lever, shipping=cart.shipping, item_total=cart.item_total)
    if lever not in levers:
        offer.notes.append(f"lever {lever!r} not available, fell back to reassurance")
        offer.lever = lever = "reassurance"
    if lever == "free_shipping":
        offer.shipping = Decimal("0")
    elif lever == "price_match":
        offer.discount_amount = Decimal(levers["price_match"]["merchant_cost"])
        offer.matches = levers["price_match"]["matches"]
    elif lever == "discount":
        headroom = Decimal(levers["discount"]["max_amount"])
        wanted = money(cart.item_total * Decimal(str(max(discount_pct, 0))) / 100)
        offer.discount_amount = min(wanted, headroom)
        if wanted > headroom:
            offer.notes.append(f"discount clamped from {wanted} to {headroom}")
        if offer.discount_amount <= 0:
            offer.lever = "reassurance"
            offer.notes.append("zero discount, switched to reassurance")
    return offer


def fallback_lever(reason_category: str, levers: dict) -> str:
    """Rule-based choice used when the model is unavailable."""
    preferred = REASON_TO_LEVER.get(reason_category, "none")
    if preferred in levers:
        return preferred
    if preferred == "price_match" and "discount" in levers:
        return "discount"
    return "reassurance" if preferred != "none" else "none"


def fee_for(paid_total: Decimal, g: Guardrails) -> Decimal:
    return money(paid_total * Decimal(str(g.fee_pct)))
