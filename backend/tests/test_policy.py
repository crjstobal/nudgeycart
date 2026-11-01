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
    market = [p.MarketOffer(1, "rival.com", D("90"))]
    levers = p.available_levers(c, G, discount_blocked=True, market=market)
    assert not {"discount", "free_shipping", "price_match"} & set(levers)
    assert "reassurance" in levers


def test_unavailable_lever_falls_back():
    c = cart(("100", "10", 1))  # no market data, so no price match
    levers = p.available_levers(c, G, discount_blocked=False)
    assert p.clamp("price_match", 0, c, G, levers).lever == "reassurance"


def test_price_match_uses_cheapest_competitor_per_line():
    c = cart(("100", "50", 2), ("40", "10", 1))
    market = [p.MarketOffer(1, "a.com", D("95")), p.MarketOffer(1, "b.com", D("92")),
              p.MarketOffer(2, "c.com", D("45"))]  # dearer than ours: ignored
    levers = p.available_levers(c, G, discount_blocked=False, market=market)
    offer = p.clamp("price_match", 0, c, G, levers)
    assert offer.discount_amount == D("16.00")  # (100 - 92) * 2
    assert [m["competitor"] for m in offer.matches] == ["b.com"]


def test_price_match_is_all_or_nothing_against_the_margin_floor():
    # revenue 100, cost 65: headroom 7.14, matching 90 would need 10
    c = cart(("100", "65", 1))
    levers = p.available_levers(c, G, discount_blocked=False, market=[p.MarketOffer(1, "a.com", D("90"))])
    assert "price_match" not in levers


def test_price_match_may_exceed_the_discount_cap_but_not_the_margin():
    # cost 10: margin headroom is huge, policy cap is 15; a 20 gap is still matched
    c = cart(("100", "10", 1))
    levers = p.available_levers(c, G, discount_blocked=False, market=[p.MarketOffer(1, "a.com", D("80"))])
    assert p.clamp("price_match", 0, c, G, levers).discount_amount == D("20.00")


def test_implausibly_cheap_listing_is_not_a_match():
    c = cart(("100", "10", 1))
    levers = p.available_levers(c, G, discount_blocked=False, market=[p.MarketOffer(1, "a.com", D("30"))])
    assert "price_match" not in levers


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


def test_free_shipping_respects_margin_not_the_discount_cap():
    # item 34, cost 14: discount cap 5.10 but margin headroom 14.00, so 7.99 shipping can be waived
    c = cart(("34", "14", 1))
    assert "free_shipping" in p.available_levers(c, G, discount_blocked=False)
    thin = cart(("34", "22", 1))  # headroom 2.57: cannot waive 7.99
    assert "free_shipping" not in p.available_levers(thin, G, discount_blocked=False)
