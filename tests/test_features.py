import asyncio
import io
import json
import random
import time

import httpx
from PIL import Image

from sniper.allegro import parse_listing_response
from sniper.config import StrategyConfig, Watch
from sniper.db import Database
from sniper.discord_bot import parse_listing_id
from sniper.engine import has_potential
from sniper.feedback import build_report, format_report, format_stats, profit_stats, reason_key
from sniper.models import Listing, Seller
from sniper.photos import PhotoChecker, dhash, distance
from sniper.pricing import Comparable, market_price, max_buy_price

CFG = StrategyConfig()


def png(seed: int, size=(200, 150)) -> bytes:
    rng = random.Random(seed)
    img = Image.new("RGB", (8, 6))
    img.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(48)])
    buf = io.BytesIO()
    img.resize(size, Image.BILINEAR).save(buf, "JPEG", quality=70)
    return buf.getvalue()


# ---------- 1. zdjęcia ----------

def test_dhash_survives_resize_but_differs_between_photos():
    assert distance(dhash(png(1)), dhash(png(1, size=(800, 600)))) <= 6
    assert distance(dhash(png(1)), dhash(png(2))) > 10


def make_listing(lid, seller_id, photos):
    return Listing(lid, "iPhone 13 Pro", 1000, "PLN", f"u{lid}", "w",
                   photos=photos, seller=Seller(id=seller_id))


def test_stolen_photo_from_other_seller_is_flagged():
    images = {"https://img/a.jpg": png(1), "https://img/a_big.jpg": png(1, (900, 700)),
              "https://img/b.jpg": png(2)}
    http = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=images[str(r.url)])))
    checker = PhotoChecker(Database(":memory:"), http)

    async def run():
        await checker.remember_thumbnail(make_listing(1, seller_id=10, photos=["https://img/a.jpg"]))
        stolen = await checker.check(make_listing(2, seller_id=99, photos=["https://img/a_big.jpg"]))
        own = await checker.check(make_listing(3, seller_id=10, photos=["https://img/a.jpg"]))
        fresh = await checker.check(make_listing(4, seller_id=55, photos=["https://img/b.jpg"]))
        return stolen, own, fresh

    stolen, own, fresh = asyncio.run(run())
    assert stolen and stolen[0][1] == 60 and "innego sprzedawcy" in stolen[0][0]
    assert own == [("ten sam sprzedawca wystawia to ponownie", 5)]
    assert fresh == []


def test_photo_hashes_persist_between_restarts(tmp_path):
    db = Database(str(tmp_path / "s.db"))
    db.add_photo_hash(f"{dhash(png(1)):016x}", 1, 10)
    checker = PhotoChecker(Database(str(tmp_path / "s.db")))
    assert checker.find_matches(dhash(png(1)), make_listing(2, 99, []))


# ---------- 2. nauka z decyzji ----------

def fake_reviews(db: Database, n_ok: int, n_bad: int):
    db.conn.execute("INSERT INTO listings (id, watch, title, price) VALUES (0, 'w', 'x', 1)")
    rows = [(i, 20, ["mało opinii (3)"], "approved") for i in range(n_ok)]
    rows += [(100 + i, 80, ["kontakt poza Vinted", "mało opinii (1)"], "denied") for i in range(n_bad)]
    for lid, score, reasons, status in rows:
        db.conn.execute("INSERT OR IGNORE INTO listings (id, watch, title, price) VALUES (?, 'w', 't', 500)", (lid,))
        db.conn.execute(
            "INSERT INTO decisions (listing_id, risk_score, reasons, created, review_status) VALUES (?, ?, ?, ?, ?)",
            (lid, score, json.dumps(reasons), time.time(), status),
        )
    db.conn.commit()


def test_report_finds_false_alarms_and_suggests_threshold():
    db = Database(":memory:")
    fake_reviews(db, n_ok=12, n_bad=8)
    rep = build_report(db.reviewed(), current_threshold=15)
    assert rep.by_reason["mało opinii"] == [12, 8]
    assert "kontakt poza Vinted" not in rep.false_alarms
    assert 25 <= rep.suggested_threshold <= 80 and rep.threshold_accuracy == 1.0
    text = format_report(rep, 15)
    assert "/prog" in text


def test_reason_key():
    assert reason_key("mało opinii (3)") == "mało opinii"
    assert reason_key("AI: ten sam ekran") == "uwagi AI"


# ---------- 3. zakupy ----------

def test_profit_stats():
    db = Database(":memory:")
    a = db.add_purchase(1, "iPhone", "iphone", 1200)
    db.add_purchase(2, "Kurtka", "stone", 150)
    assert db.mark_sold(a, 1700)
    assert not db.mark_sold(a, 1800)  # drugi raz się nie da
    st = profit_stats(db.purchases())
    assert (st.bought, st.sold, st.profit, st.in_stock_value) == (2, 1, 500, 150)
    assert "+500 zł" in format_stats(st)


# ---------- 4. spadki cen ----------

def test_max_buy_price_matches_has_potential():
    w = Watch(name="w", search_text="x", min_profit=200)
    target = max_buy_price(2200, 200, CFG)
    assert has_potential(target - 2, 2200, w, CFG)
    assert not has_potential(target + 5, 2200, w, CFG)


def test_price_watch_queue():
    db = Database(":memory:")
    db.add_price_watch(1, "w", 2100, 1800)
    assert db.due_price_watch(every_sec=-1, limit=10)[0]["target_price"] == 1800
    db.update_price_watch(1, 2000)
    assert db.due_price_watch(every_sec=3600, limit=10) == []
    db.remove_price_watch(1)
    assert db.due_price_watch(every_sec=-1, limit=10) == []


# ---------- 5. obserwacje z Discorda ----------

def test_watches_import_and_manage():
    db = Database(":memory:")
    db.import_watches([Watch(name="a", search_text="iphone")])
    db.import_watches([Watch(name="a", search_text="ZMIENIONE")])  # nie nadpisuje
    db.save_watch(Watch(name="b", search_text="dunk", kind="shoes", price_to=300))
    assert db.set_watch_paused("a", True)
    active = db.list_watches(include_paused=False)
    assert [w.name for w, _ in active] == ["b"] and active[0][0].kind == "shoes"
    assert db.list_watches()[0][0].search_text == "iphone"
    assert db.delete_watch("b") and not db.delete_watch("b")


def test_parse_listing_id():
    assert parse_listing_id("https://www.vinted.pl/items/4567890123-iphone-13") == 4567890123
    assert parse_listing_id(" 123456 ") == 123456
    assert parse_listing_id("cos tam") is None


# ---------- 6. Allegro ----------

def test_allegro_parsing_and_estimate():
    data = {"items": {
        "promoted": [{"id": "1", "name": "iPhone 13 Pro 128GB grafitowy", "sellingMode": {"price": {"amount": "2300.00"}}}],
        "regular": [{"id": str(i), "name": "Apple iPhone 13 Pro 128GB", "sellingMode": {"price": {"amount": str(p)}}}
                    for i, p in enumerate([1900, 2000, 2100, 2200], 2)]
                   + [{"id": "x", "name": "Etui iPhone 13 Pro", "sellingMode": {"price": {"amount": "29.99"}}},
                      {"id": "bad"}],
    }}
    comps = parse_listing_response(data)
    assert len(comps) == 6
    m = market_price("iPhone 13 Pro 128GB", "", 1, Watch(name="w", search_text="x"), CFG,
                     active=[], external={"Allegro": comps})
    assert m.method == "Allegro" and 1950 <= m.price <= 2100


def test_lowest_of_vinted_and_allegro_wins():
    vinted = [Comparable(i, "iPhone 13 Pro 128GB", p) for i, p in enumerate([1500, 1550, 1600, 1650, 1700])]
    allegro = [Comparable(-i, "iPhone 13 Pro 128GB", p) for i, p in enumerate([2000, 2100, 2200, 2300, 2400], 1)]
    m = market_price("iPhone 13 Pro 128GB", "", 99, Watch(name="w", search_text="x"), CFG,
                     active=vinted, external={"Allegro": allegro})
    assert m.method == "podobne oferty"
