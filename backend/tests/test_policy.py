from decimal import Decimal as D

from nudgeycart.core import policy as p
from nudgeycart.integrations.paypal import order_payload

G = p.Guardrails(min_margin_pct=0.25, max_discount_pct=0.15, fee_pct=0.05)


def cart(*lines, shipping="7.99"):
    return p.Cart(lines=[p.Line(i, f"item{i}", q, D(price), D(cost) if cost else None)
                         for i, (price, cost, q) in enumerate(lines, 1)],
                  shipping=D(shipping), currency="USD")


def test_margin_floor_binds_before_policy_cap():
    # revenue 100, cost 65, floor margin 25% + 5% fee => revenue >= 65 / 0.70 = 92.857
    c = cart(("100", "65", 1))
    assert p.max_discount(c, G) == D("7.14")


def test_policy_cap_binds_on_high_margin_items():
    c = cart(("100", "10", 1))
    assert p.max_discount(c, G) == D("15.00")


def test_unknown_cost_gives_no_headroom():
    c = cart(("100", None, 1))
    assert p.max_discount(c, G) == 0
    assert "discount" not in p.available_levers(c, G, discount_blocked=False)


def test_discount_is_clamped_whatever_the_model_says():
    c = cart(("100", "65", 1))
    levers = p.available_levers(c, G, discount_blocked=False)
    offer = p.clamp("discount", 90, c, G, levers)
    assert offer.discount_amount == D("7.14")
    assert offer.notes


def test_margin_holds_after_clamp():
    c = cart(("189", "96", 1), ("64", "27", 2))
    levers = p.available_levers(c, G, discount_blocked=False)
    offer = p.clamp("discount", 50, c, G, levers)
    revenue = c.item_total - offer.discount_amount
    cost = D("96") + D("27") * 2
    assert (revenue - cost) / revenue >= D("0.30") - D("0.0001")


def test_blocked_visitor_gets_no_money_levers():
    c = cart(("100", "10", 1))
    levers = p.available_levers(c, G, discount_blocked=True)
    assert "discount" not in levers and "free_shipping" not in levers
    assert "pay_later" in levers


def test_unavailable_lever_falls_back():
    c = cart(("10", "1", 1))  # below the Pay Later minimum
    levers = p.available_levers(c, G, discount_blocked=False)
    assert p.clamp("pay_later", 0, c, G, levers).lever == "reassurance"


def test_free_shipping_zeroes_shipping():
    c = cart(("100", "10", 1))
    levers = p.available_levers(c, G, discount_blocked=False)
    offer = p.clamp("free_shipping", 0, c, G, levers)
    assert offer.final_total == D("100.00")


def test_control_group_is_deterministic():
    a = p.assign_group("m", "visitor-1", 10)
    assert all(p.assign_group("m", "visitor-1", 10) == a for _ in range(5))
    share = sum(p.assign_group("m", f"v{i}", 10) == "control" for i in range(5000)) / 5000
    assert 0.08 < share < 0.12


def test_paypal_breakdown_adds_up():
    c = cart(("189", "96", 1), ("64", "27", 2))
    levers = p.available_levers(c, G, discount_blocked=False)
    offer = p.clamp("discount", 10, c, G, levers)
    pu = order_payload(c, offer, "ref", "Store", None)["purchase_units"][0]
    b = pu["amount"]["breakdown"]
    total = D(b["item_total"]["value"]) + D(b["shipping"]["value"]) - D(b["discount"]["value"])
    assert total == D(pu["amount"]["value"])
    items = sum(D(i["unit_amount"]["value"]) * int(i["quantity"]) for i in pu["items"])
    assert items == D(b["item_total"]["value"])
