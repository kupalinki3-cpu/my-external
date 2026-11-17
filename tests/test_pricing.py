from sniper.config import StrategyConfig, Watch
from sniper.pricing import Comparable, market_price, percentile, select_comparables, similarity

CFG = StrategyConfig()
WATCH = Watch(name="iphone", search_text="iphone 13 pro")


def comps(prices, title="iPhone 13 Pro 128GB", condition="Bardzo dobry", start=100):
    return [Comparable(start + i, title, p, condition) for i, p in enumerate(prices)]


def test_similarity():
    assert similarity("iPhone 13 Pro 128GB", "Apple iPhone 13 Pro 128 GB stan idealny") > 0.35
    assert similarity("iPhone 13 Pro 128GB", "Etui do Samsung S21") == 0


def test_percentile():
    assert percentile([100, 200, 300, 400, 500], 0.5) == 300
    assert percentile([100, 200, 300, 400, 500], 0.25) == 200


def test_resale_price_is_low_end_of_similar_listings():
    active = comps([2000, 2100, 2200, 2300, 2400, 2500, 2600])
    m = market_price("iPhone 13 Pro 128GB", "Bardzo dobry", 1, WATCH, CFG, active)
    assert m.method == "podobne oferty"
    # 35. percentyl, a nie średnia (2300)
    assert 2150 <= m.price <= 2250


def test_ignores_dummies_boxes_and_other_products():
    active = (
        comps([2000, 2100, 2200, 2300, 2400])
        + comps([150, 120], title="Atrapa iPhone 13 Pro", start=200)
        + comps([60], title="Pudełko po iPhone 13 Pro", start=300)
        + comps([40, 45], title="Etui Samsung Galaxy", start=400)
    )
    prices = select_comparables("iPhone 13 Pro 128GB", "Bardzo dobry", 1, active, CFG)
    assert sorted(prices) == [2000, 2100, 2200, 2300, 2400]


def test_prefers_same_condition():
    active = comps([2400] * 5, condition="Nowy z metką") + comps([1800] * 5, condition="Dobry", start=200)
    m = market_price("iPhone 13 Pro 128GB", "Dobry", 1, WATCH, CFG, active)
    assert m.price == 1800


def test_excludes_listing_itself_and_needs_enough_samples():
    active = comps([2000, 2100, 2200, 2300])  # 4 < min_comparables
    assert market_price("iPhone 13 Pro 128GB", "", 999, WATCH, CFG, active) is None


def test_lowest_estimate_wins():
    active = comps([2000, 2100, 2200, 2300, 2400])
    sold = comps([1700, 1750, 1800, 1850, 1900], start=500)
    m = market_price("iPhone 13 Pro 128GB", "Bardzo dobry", 1, WATCH, CFG, active, sold)
    assert m.method == "sprzedane" and m.price == 1800
    manual = Watch(name="x", search_text="x", reference_price=1500)
    assert market_price("iPhone 13 Pro 128GB", "", 1, manual, CFG, active, sold).method == "ręcznie"
