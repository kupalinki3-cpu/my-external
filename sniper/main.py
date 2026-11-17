"""Punkt startowy: skaner Vinted + bot Discord w jednej pętli asyncio.

Uruchomienie:  python -m sniper.main config.yaml
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys

from .config import Config, Watch, load_config
from .db import Database
from .discord_bot import SniperBot
from .engine import evaluate, has_potential
from .filters.ai import make_classifier
from .models import Decision
from .pricing import market_price
from .vinted import VintedClient, is_sold, parse_listing

log = logging.getLogger("sniper")

# Co ile sekund sprawdzać, czy "zniknięte" oferty się sprzedały
SOLD_CHECK_EVERY = 600
# Oferta nieobecna w wynikach przez tyle sekund -> sprawdzamy jej status
STALE_AFTER = 3600


async def scan_watch(
    watch: Watch, cfg: Config, db: Database, vinted: VintedClient,
    classifier, bot: SniperBot, seed_only: bool,
) -> None:
    s = cfg.strategy
    if seed_only:
        # Start: zbierz kilka stron ofert jako bazę porównawczą cen, bez alertów
        for page in range(1, cfg.vinted.seed_pages + 1):
            for item in await vinted.search_newest(watch, page):
                db.upsert_listing(parse_listing(item, watch.name))
            await asyncio.sleep(random.uniform(1, 2))
        return

    items = await vinted.search_newest(watch)

    new_listings = []
    for item in items:
        listing = parse_listing(item, watch.name)
        if not db.is_seen(listing.id):
            new_listings.append((item, listing))
        db.upsert_listing(listing)

    # Baza porównawcza: wszystkie aktywne i sprzedane oferty z tej obserwacji
    active = db.comparables(watch.name, "active", s.comparable_days)
    sold = db.comparables(watch.name, "sold", s.price_history_days)

    for item, listing in new_listings:
        market = market_price(listing.title, listing.condition, listing.id, watch, s, active, sold)
        ref = market.price if market else None
        # Szczegóły pobieramy tylko, gdy oferta w ogóle może dać zysk
        if not has_potential(listing.price, ref, watch, s):
            continue

        details = await vinted.item_details(listing.id)
        if details is None:
            continue
        listing = parse_listing(item, watch.name, details)

        ev = await evaluate(listing, watch, ref, s, classifier)
        if market:
            ev.market_note = market.describe(listing.currency or "zł")
        log.info("[%s] %s | %.0f | %s | ryzyko %s | %s",
                 watch.name, listing.title, listing.price, ev.decision.value,
                 ev.risk_score, "; ".join(ev.reasons))
        if ev.decision == Decision.SKIP:
            db.save_decision(ev)
            continue
        message_id = await bot.post(ev)
        db.save_decision(ev, message_id)


async def refresh_sold(db: Database, vinted: VintedClient) -> None:
    for row in db.stale_active(STALE_AFTER):
        details = await vinted.item_details(row["id"])
        if details is None:
            db.set_status(row["id"], "gone")
        elif is_sold(details):
            db.set_status(row["id"], "sold")
        await asyncio.sleep(1)


def make_scanner(cfg: Config, db: Database):
    async def scanner(bot: SniperBot) -> None:
        vinted = VintedClient(cfg.vinted)
        classifier = make_classifier(cfg.ai) if cfg.ai.enabled else None
        seeded: set[str] = set()
        last_sold_check = 0.0
        loop = asyncio.get_running_loop()
        try:
            while True:
                for watch in cfg.watches:
                    try:
                        # Pierwszy przebieg tylko zapisuje obecne oferty, bez alertów
                        await scan_watch(watch, cfg, db, vinted, classifier, bot,
                                         seed_only=watch.name not in seeded)
                        seeded.add(watch.name)
                    except Exception:
                        log.exception("Błąd skanowania %s", watch.name)
                    await asyncio.sleep(random.uniform(1, 3))

                if loop.time() - last_sold_check > SOLD_CHECK_EVERY:
                    last_sold_check = loop.time()
                    try:
                        await refresh_sold(db, vinted)
                    except Exception:
                        log.exception("Błąd sprawdzania sprzedanych")

                # Losowy odstęp, żeby nie odpytywać jak metronom
                interval = cfg.vinted.poll_interval_sec
                await asyncio.sleep(random.uniform(interval * 0.8, interval * 1.3))
        finally:
            await vinted.close()

    return scanner


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
    token = os.environ.get(cfg.discord.token_env)
    if not token:
        sys.exit(f"Ustaw zmienną środowiskową {cfg.discord.token_env} z tokenem bota Discord")
    db = Database(cfg.database)
    bot = SniperBot(cfg, db, make_scanner(cfg, db))
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
