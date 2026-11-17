"""Cena referencyjna i kalkulacja zysku."""

from __future__ import annotations

from statistics import median

from .config import StrategyConfig, Watch


def reference_price(watch: Watch, sold_prices: list[float], cfg: StrategyConfig) -> float | None:
    """Ręczna cena z configu albo mediana ostatnich sprzedaży."""
    if watch.reference_price:
        return float(watch.reference_price)
    if len(sold_prices) >= cfg.min_price_samples:
        return float(median(sold_prices))
    return None


def buyer_cost(price: float, cfg: StrategyConfig) -> float:
    """Ile faktycznie zapłacisz: cena + ochrona kupujących + wysyłka."""
    return price + cfg.buyer_fee_fixed + price * cfg.buyer_fee_pct + cfg.shipping_cost


def expected_profit(price: float, ref: float, cfg: StrategyConfig) -> float:
    resale = ref * (1 - cfg.resale_haircut)
    return round(resale - buyer_cost(price, cfg), 2)


def offer_price(price: float, cfg: StrategyConfig) -> float:
    return float(round(price * (1 - cfg.offer_discount)))
