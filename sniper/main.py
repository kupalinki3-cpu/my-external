"""Punkt startowy: skaner Vinted + bot Discord w jednej pętli asyncio.

Uruchomienie:  python -m sniper.main config.yaml
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field

from .allegro import AllegroPrices
from .config import Config, Watch, load_config
from .db import Database
from .discord_bot import SniperBot
from .engine import evaluate, has_potential
from .feedback import build_report, feedback_examples, format_report
from .filters.ai import make_classifier
from .models import Decision, Listing
from .photos import PhotoChecker
from .pricing import market_price, max_buy_price
from .vinted import VintedClient, is_sold, parse_listing

log = logging.getLogger("sniper")

# Co ile sekund sprawdzać, czy "zniknięte" oferty się sprzedały
SOLD_CHECK_EVERY = 600
# Oferta nieobecna w wynikach przez tyle sekund -> sprawdzamy jej status
STALE_AFTER = 3600
# Ile śledzonych ofert sprawdzać naraz pod kątem spadku ceny
PRICE_CHECK_BATCH = 15
WEEK = 7 * 86400


@dataclass
class Services:
    cfg: Config
    db: Database
    vinted: VintedClient
    bot: SniperBot
    classifier: object | None = None
    photos: PhotoChecker | None = None
    allegro: AllegroPrices | None = None
    seeded: set[str] = field(default_factory=set)


def _min_profit(watch: Watch, svc: Services) -> float:
    return watch.min_profit if watch.min_profit is not None else svc.cfg.strategy.min_profit


async def estimate(listing: Listing, watch: Watch, svc: Services):
    s = svc.cfg.strategy
    active = svc.db.comparables(watch.name, "active", s.comparable_days)
    sold = svc.db.comparables(watch.name, "sold", s.price_history_days)
    external = {}
    if svc.allegro:
        external["Allegro"] = await svc.allegro.search(watch.search_text)
    return market_price(listing.title, listing.condition, listing.id, watch, s, active, sold, external)


async def evaluate_and_post(item: dict, listing: Listing, watch: Watch, market, svc: Services,
                            alert_note: str = "") -> None:
    details = await svc.vinted.item_details(listing.id)
    if details is None:
        return
    listing = parse_listing(item, watch.name, details)

    extra = await svc.photos.check(listing) if svc.photos else []
    examples = feedback_examples(svc.db.recent_feedback(watch.name))
    ev = await evaluate(listing, watch, market.price if market else None, svc.cfg.strategy,
                        svc.classifier, extra_risks=extra, examples=examples)
    if market:
        ev.market_note = market.describe(listing.currency or "zł")
    ev.alert_note = alert_note
    log.info("[%s] %s | %.0f | %s | ryzyko %s | %s", watch.name, listing.title, listing.price,
             ev.decision.value, ev.risk_score, "; ".join(ev.reasons))
    if ev.decision == Decision.SKIP:
        svc.db.save_decision(ev)
        return
    svc.db.save_decision(ev, await svc.bot.post(ev))


async def scan_watch(watch: Watch, svc: Services) -> None:
    cfg, db = svc.cfg, svc.db
    if watch.name not in svc.seeded:
        # Start: zbierz kilka stron ofert jako bazę porównawczą cen i zdjęć, bez alertów
        for page in range(1, cfg.vinted.seed_pages + 1):
            listings = [parse_listing(i, watch.name) for i in await svc.vinted.search_newest(watch, page)]
            for listing in listings:
                db.upsert_listing(listing)
            if svc.photos:
                await asyncio.gather(*(svc.photos.remember_thumbnail(l) for l in listings))
            await asyncio.sleep(random.uniform(1, 2))
        svc.seeded.add(watch.name)
        return

    new = []
    for item in await svc.vinted.search_newest(watch):
        listing = parse_listing(item, watch.name)
        if not db.is_seen(listing.id):
            new.append((item, listing))
        db.upsert_listing(listing)

    for item, listing in new:
        market = await estimate(listing, watch, svc)
        if market and has_potential(listing.price, market.price, watch, cfg.strategy):
            await evaluate_and_post(item, listing, watch, market, svc)
            continue
        if market:
            # Za drogo, ale niewiele? Pilnuj, czy stanieje
            target = max_buy_price(market.price, _min_profit(watch, svc), cfg.strategy)
            if 0 < target and listing.price <= target * (1 + cfg.strategy.price_drop_window):
                db.add_price_watch(listing.id, watch.name, listing.price, target)
        # Zdjęcie zapamiętujemy zawsze: przyda się do wykrycia kradzieży zdjęć w przyszłości
        if svc.photos:
            await svc.photos.remember_thumbnail(listing)


async def check_price_drops(svc: Services) -> None:
    s = svc.cfg.strategy
    svc.db.expire_price_watch(s.price_drop_days * 86400)
    watches = {w.name: w for w, paused in svc.db.list_watches() if not paused}
    for row in svc.db.due_price_watch(s.price_check_every_min * 60, PRICE_CHECK_BATCH):
        watch = watches.get(row["watch"])
        details = await svc.vinted.item_details(row["listing_id"])
        if watch is None or details is None or is_sold(details):
            svc.db.remove_price_watch(row["listing_id"])
            continue
        listing = parse_listing(details, watch.name, details)
        svc.db.upsert_listing(listing)
        if listing.price < row["last_price"]:
            market = await estimate(listing, watch, svc)
            if market and has_potential(listing.price, market.price, watch, s):
                svc.db.remove_price_watch(row["listing_id"])
                note = f"📉 Cena spadła z {row['last_price']:.0f} na {listing.price:.0f} zł"
                await evaluate_and_post(details, listing, watch, market, svc, alert_note=note)
                continue
        svc.db.update_price_watch(row["listing_id"], listing.price)
        await asyncio.sleep(1)


async def refresh_sold(svc: Services) -> None:
    for row in svc.db.stale_active(STALE_AFTER):
        details = await svc.vinted.item_details(row["id"])
        if details is None:
            svc.db.set_status(row["id"], "gone")
        elif is_sold(details):
            svc.db.set_status(row["id"], "sold")
        await asyncio.sleep(1)


async def weekly_report(svc: Services) -> None:
    last = float(svc.db.get_setting("last_report", "0"))
    if time.time() - last < WEEK:
        return
    rows = svc.db.reviewed(since_ts=time.time() - WEEK)
    svc.db.set_setting("last_report", str(time.time()))
    if len(rows) >= 5:
        thr = svc.cfg.strategy.risk.high
        await svc.bot.post_text("🗓️ " + format_report(build_report(rows, thr), thr))


def make_scanner(cfg: Config, db: Database):
    async def scanner(bot: SniperBot) -> None:
        svc = Services(
            cfg=cfg, db=db, vinted=VintedClient(cfg.vinted), bot=bot,
            classifier=make_classifier(cfg.ai) if cfg.ai.enabled else None,
            photos=PhotoChecker(db),
            allegro=AllegroPrices(cfg.allegro) if cfg.allegro.enabled else None,
        )
        periodic = {"sold": 0.0, "drops": 0.0, "report": 0.0}
        try:
            while True:
                if db.get_setting("paused") == "1":
                    await asyncio.sleep(10)
                    continue
                for watch, _ in db.list_watches(include_paused=False):
                    try:
                        await scan_watch(watch, svc)
                    except Exception:
                        log.exception("Błąd skanowania %s", watch.name)
                    await asyncio.sleep(random.uniform(1, 3))

                now = time.time()
                for name, every, job in (
                    ("sold", SOLD_CHECK_EVERY, refresh_sold),
                    ("drops", 300, check_price_drops),
                    ("report", 3600, weekly_report),
                ):
                    if now - periodic[name] > every:
                        periodic[name] = now
                        try:
                            await job(svc)
                        except Exception:
                            log.exception("Błąd zadania %s", name)

                # Losowy odstęp, żeby nie odpytywać jak metronom
                interval = cfg.vinted.poll_interval_sec
                await asyncio.sleep(random.uniform(interval * 0.8, interval * 1.3))
        finally:
            await svc.vinted.close()
            if svc.photos:
                await svc.photos.close()
            if svc.allegro:
                await svc.allegro.close()

    return scanner


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
    token = os.environ.get(cfg.discord.token_env)
    if not token:
        sys.exit(f"Ustaw zmienną środowiskową {cfg.discord.token_env} z tokenem bota Discord")
    db = Database(cfg.database)
    db.import_watches(cfg.watches)
    if (thr := db.get_setting("risk_high")) is not None:
        cfg.strategy.risk.high = int(thr)
    bot = SniperBot(cfg, db, make_scanner(cfg, db))
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
