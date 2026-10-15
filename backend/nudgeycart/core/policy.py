"""Merchant guardrails. Pure functions, no vendor SDKs.

The language model proposes; this module disposes. Whatever the model says, the
final offer is clamped here so a discount can never break the merchant's margin floor.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")

# Levers ordered from cheapest to most expensive for the merchant.
LEVERS = ("express_checkout", "pay_later", "reassurance", "free_shipping", "discount", "none")

REASON_TO_LEVER = {
    "payment_timing": "pay_later",
    "checkout_friction": "express_checkout",
    "trust": "reassurance",
    "shipping_cost": "free_shipping",
    "price": "discount",
    "comparison_shopping": "discount",
    "just_browsing": "none",
    "other": "reassurance",
}

PAY_LATER_MIN = Decimal("30")
PAY_LATER_MAX = Decimal("1500")


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

    @property
    def final_total(self) -> Decimal:
        return money(self.item_total + self.shipping - self.discount_amount)


def assign_group(merchant_id: str, visitor_id: str, control_pct: int, force: str | None = None) -> str:
    """Deterministic holdout: the same visitor always lands in the same group."""
    if force in ("treatment", "control"):
        return force
    h = int(hashlib.sha256(f"{merchant_id}:{visitor_id}".encode()).hexdigest(), 16) % 100
    return "control" if h < control_pct else "treatment"


def max_discount(cart: Cart, g: Guardrails) -> Decimal:
    """Largest discount that keeps the whole order at or above the margin floor.

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
    by_margin = known_rev - floor_revenue
    by_policy = cart.item_total * Decimal(str(g.max_discount_pct))
    cap = min(by_margin, by_policy)
    return max(Decimal("0"), cap.quantize(CENT, rounding=ROUND_DOWN))


def available_levers(cart: Cart, g: Guardrails, discount_blocked: bool) -> dict[str, dict]:
    """What the agent may offer, with what each one costs the merchant."""
    headroom = Decimal("0") if discount_blocked else max_discount(cart, g)
    levers: dict[str, dict] = {
        "express_checkout": {"merchant_cost": "0", "what": "one-click PayPal checkout, no forms or account"},
        "reassurance": {"merchant_cost": "0", "what": "answer the concern honestly (returns, security, delivery)"},
        "none": {"merchant_cost": "0", "what": "let them go politely, no offer"},
    }
    if PAY_LATER_MIN <= cart.total <= PAY_LATER_MAX:
        levers["pay_later"] = {
            "merchant_cost": "0",
            "what": f"PayPal Pay in 4: 4 interest-free payments of {money(cart.total / 4)} {cart.currency}",
        }
    if cart.shipping > 0 and cart.shipping <= headroom:
        levers["free_shipping"] = {"merchant_cost": str(cart.shipping), "what": "shipping waived"}
    if headroom > 0:
        max_pct = (headroom / cart.item_total * 100).quantize(Decimal("0.1"), rounding=ROUND_DOWN)
        levers["discount"] = {
            "merchant_cost": f"up to {headroom}",
            "what": f"discount on the items, at most {max_pct}% ({headroom} {cart.currency})",
            "max_pct": float(max_pct),
        }
    return levers


def clamp(lever: str, discount_pct: float, cart: Cart, g: Guardrails, levers: dict) -> Offer:
    """Turn the model's proposal into an offer the merchant has pre-approved."""
    offer = Offer(lever=lever, shipping=cart.shipping, item_total=cart.item_total)
    if lever not in levers:
        offer.notes.append(f"lever {lever!r} not available, fell back to reassurance")
        offer.lever = lever = "reassurance" if lever != "none" else "none"
    if lever == "free_shipping":
        offer.shipping = Decimal("0")
    elif lever == "discount":
        headroom = Decimal(levers["discount"]["merchant_cost"].split()[-1])
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
    preferred = REASON_TO_LEVER.get(reason_category, "reassurance")
    if preferred in levers:
        return preferred
    for lv in LEVERS:
        if lv in levers and lv != "none":
            return lv
    return "none"


def fee_for(paid_total: Decimal, g: Guardrails) -> Decimal:
    return money(paid_total * Decimal(str(g.fee_pct)))
