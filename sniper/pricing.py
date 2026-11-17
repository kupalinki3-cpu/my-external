"""Wycena rynkowa i kalkulacja zysku.

Używane rzeczy rzadko schodzą po średniej cenie z ogłoszeń. Dlatego cena
odsprzedaży to dolna część rynku: np. 35. percentyl cen PODOBNYCH ofert
(podobny tytuł, ten sam stan). Jeśli bot zna też ceny ofert, które się
sprzedały, albo masz ręczną cenę w configu, brana jest najniższa z wycen.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .config import StrategyConfig, Watch
from .filters.rules import check_text, normalize

# Słowa, które nic nie mówią o produkcie i psułyby podobieństwo tytułów
STOPWORDS = {
    "i", "z", "w", "na", "do", "dla", "the", "and", "with", "nowy", "nowa", "nowe",
    "stan", "idealny", "idealna", "bardzo", "dobry", "super", "okazja", "tanio",
    "sprzedam", "oryginalny", "oryginalna", "oryginal", "new", "rozmiar", "size",
}


@dataclass
class Comparable:
    id: int
    title: str
    price: float
    condition: str = ""


@dataclass
class MarketEstimate:
    price: float
    method: str     # skąd wycena: "podobne oferty", "sprzedane", "ręcznie"
    samples: int

    def describe(self, currency: str = "zł") -> str:
        n = f", {self.samples} ofert" if self.samples else ""
        return f"{self.price:.0f} {currency} ({self.method}{n})"


def _tokens(title: str) -> set[str]:
    return {t for t in normalize(title).split() if t not in STOPWORDS and len(t) > 1}


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _trim_outliers(prices: list[float]) -> list[float]:
    """Wyrzuca śmieciowe ceny (np. etui za 20 zł wśród telefonów)."""
    if len(prices) < 3:
        return prices
    m = median(prices)
    return [p for p in prices if 0.4 * m <= p <= 2.5 * m]


def select_comparables(
    title: str, condition: str, exclude_id: int | None,
    pool: list[Comparable], cfg: StrategyConfig,
) -> list[float]:
    """Ceny ofert podobnych do danej: podobny tytuł, bez atrap/pudełek, najlepiej ten sam stan."""
    similar = [
        c for c in pool
        if c.id != exclude_id
        and not check_text(c.title, "").hard
        and similarity(title, c.title) >= cfg.similarity_min
    ]
    same_condition = [c for c in similar if condition and c.condition == condition]
    chosen = same_condition if len(same_condition) >= cfg.min_comparables else similar
    return _trim_outliers([c.price for c in chosen])


def market_price(
    title: str,
    condition: str,
    listing_id: int | None,
    watch: Watch,
    cfg: StrategyConfig,
    active: list[Comparable],
    sold: list[Comparable] | None = None,
) -> MarketEstimate | None:
    estimates: list[MarketEstimate] = []

    asks = select_comparables(title, condition, listing_id, active, cfg)
    if len(asks) >= cfg.min_comparables:
        estimates.append(MarketEstimate(percentile(asks, cfg.resale_percentile), "podobne oferty", len(asks)))

    sold_prices = select_comparables(title, condition, listing_id, sold or [], cfg)
    if len(sold_prices) >= cfg.min_price_samples:
        estimates.append(MarketEstimate(median(sold_prices), "sprzedane", len(sold_prices)))

    if watch.reference_price:
        estimates.append(MarketEstimate(float(watch.reference_price), "ręcznie", 0))

    # Ostrożnie: najniższa wycena wygrywa
    return min(estimates, key=lambda e: e.price) if estimates else None


def buyer_cost(price: float, cfg: StrategyConfig) -> float:
    """Ile faktycznie zapłacisz: cena + ochrona kupujących + wysyłka."""
    return price + cfg.buyer_fee_fixed + price * cfg.buyer_fee_pct + cfg.shipping_cost


def expected_profit(price: float, ref: float, cfg: StrategyConfig) -> float:
    resale = ref * (1 - cfg.resale_haircut)
    return round(resale - buyer_cost(price, cfg), 2)


def offer_price(price: float, cfg: StrategyConfig) -> float:
    return float(round(price * (1 - cfg.offer_discount)))
