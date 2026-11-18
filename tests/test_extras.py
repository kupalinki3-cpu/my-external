import asyncio
import json
import time

import httpx

from sniper.config import AIConfig, Config, DiscordConfig, NotifyConfig, StrategyConfig, VintedConfig
from sniper.db import Database
from sniper.discord_bot import SniperBot
from sniper.models import Decision, Evaluation, Listing
from sniper.pricing import Comparable, liquidity
from sniper.resale import ListingWriter, build_draft, format_draft, suggest_prices, template_draft

CFG = StrategyConfig()
TITLE = "iPhone 13 Pro 128GB"


def sold(days):
    return [Comparable(i, TITLE, 2000, days_listed=d) for i, d in enumerate(days)]


# ---------- 5. jak szybko schodzi ----------

def test_liquidity_from_sold_listings():
    liq = liquidity(TITLE, "", sold([2, 3, 4, 5, 30]), CFG)
    assert liq.median_days == 4 and liq.samples == 5
    assert round(liq.per_week, 2) == round(5 / (30 / 7), 2)
    assert "~4 dni" in liq.describe()
    assert liquidity(TITLE, "", sold([1, 2]), CFG) is None  # za mało danych


def test_sold_comparables_have_days_listed():
    db = Database(":memory:")
    now = time.time()
    db.conn.execute("INSERT INTO listings (id, watch, title, price, first_seen, last_seen, status) "
                    "VALUES (1, 'w', ?, 2000, ?, ?, 'sold')", (TITLE, now - 3 * 86400, now))
    db.conn.commit()
    (c,) = db.comparables("w", "sold", 30)
    assert round(c.days_listed) == 3
    assert db.comparables("w", "active", 30) == []


# ---------- 7. ogłoszenie do odsprzedaży ----------

ACTIVE = [Comparable(i, TITLE, p) for i, p in enumerate([1800, 1900, 2000, 2100, 2200, 2300])]


def test_suggest_prices_list_above_minimum():
    list_price, min_price, n = suggest_prices(TITLE, "", ACTIVE, CFG)
    assert n == 6 and list_price > min_price
    assert list_price % 5 == 0 and min_price % 5 == 0


def test_template_draft_without_ai():
    draft = asyncio.run(build_draft(TITLE, "", "Bardzo dobry", ACTIVE, CFG, writer=None))
    text = format_draft(draft)
    assert TITLE in text and "Wystaw za" in text
    assert template_draft("x" * 100, "")[0] == "x" * 60


def test_ollama_writer_and_fallback():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            body = json.loads(request.content)
            assert body["format"]["required"] == ["title", "description"]
            return httpx.Response(200, json={"message": {"content": json.dumps(
                {"title": "iPhone 13 Pro 128 GB grafitowy", "description": "Telefon w bardzo dobrym stanie."})}})
        return httpx.Response(500)

    w = ListingWriter(AIConfig(provider="ollama"))
    w._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert asyncio.run(w.write(TITLE, "opis", "Bardzo dobry"))[0] == "iPhone 13 Pro 128 GB grafitowy"
    # Błąd modelu -> szablon zamiast wywalenia się
    title, desc = asyncio.run(w.write(TITLE, "opis", "Bardzo dobry"))
    assert title == TITLE and "Sprzedam" in desc


# ---------- 1. priorytetowe powiadomienia ----------

def test_priority_push_to_ntfy():
    sent = {}

    def handler(request):
        sent["url"] = str(request.url)
        sent["body"] = json.loads(request.content)
        return httpx.Response(200)

    cfg = Config(vinted=VintedConfig(), discord=DiscordConfig(), ai=AIConfig(), strategy=CFG, watches=[],
                 notify=NotifyConfig(ntfy_topic="moj-sniper-123"))
    bot = SniperBot(cfg, Database(":memory:"), None)
    bot._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    listing = Listing(1, "Łódka iPhone 13 Pro", 1250, "PLN", "https://www.vinted.pl/items/1", "w")
    ev = Evaluation(listing, Decision.OFFER, expected_profit=520, planned_action=Decision.OFFER,
                    offer_price=875, priority=True)
    asyncio.run(bot._push(ev))
    b = sent["body"]
    assert sent["url"] == "https://ntfy.sh" and b["topic"] == "moj-sniper-123"
    assert b["title"] == "Łódka iPhone 13 Pro" and b["click"] == listing.url and b["priority"] == 4
    assert "1250 zł" in b["message"] and "875" in b["message"]
