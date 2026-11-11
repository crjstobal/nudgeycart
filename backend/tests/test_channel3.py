from nudgeycart.integrations.channel3 import _same_product

OURS = "Lodge 10.25 Inch Cast Iron Skillet"


def test_same_product_accepts_the_same_item_written_differently():
    assert _same_product(OURS, 'Lodge 10.25" Cast Iron Skillet')
    assert _same_product(OURS, "Lodge 10.25 Inch Seasoned Cast Iron Skillet, John Deere Logo")


def test_same_product_rejects_another_size_or_brand():
    assert not _same_product(OURS, "10.5 Inch Square Cast Iron Skillet")
    assert not _same_product(OURS, "Lodge 12 Inch Cast Iron Skillet")
    assert not _same_product(OURS, "Victoria 10.25 Inch Cast Iron Skillet")
    assert not _same_product("Hydro Flask 32 oz Wide Mouth Water Bottle", "Hydro Flask 24 oz Wide Mouth Water Bottle")
