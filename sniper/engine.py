"""Łączy wszystkie filtry w jedną decyzję: BUY / OFFER / REVIEW / SKIP."""

from __future__ import annotations

from typing import Protocol

from .config import StrategyConfig, Watch
from .filters.rules import check_text
from .filters.seller import seller_risk
from .models import AICategory, AIVerdict, Decision, Evaluation, Listing, RiskLevel
from .pricing import expected_profit, offer_price


class Classifier(Protocol):
    async def classify(
        self, listing: Listing, query: str, examples: list[str] | None = None
    ) -> AIVerdict | None: ...


# Pewność AI, powyżej której kategoria inna niż "genuine" od razu odrzuca ofertę
AI_REJECT_CONFIDENCE = 0.8


def risk_level(score: int, cfg: StrategyConfig) -> RiskLevel:
    if score >= cfg.risk.high:
        return RiskLevel.HIGH
    if score >= cfg.risk.medium:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def has_potential(price: float, ref: float | None, watch: Watch, cfg: StrategyConfig) -> bool:
    """Szybki filtr przed pobieraniem szczegółów: czy nawet po zbiciu ceny jest zysk."""
    if ref is None:
        return False
    min_profit = watch.min_profit if watch.min_profit is not None else cfg.min_profit
    return expected_profit(offer_price(price, cfg), ref, cfg) >= min_profit


async def evaluate(
    listing: Listing,
    watch: Watch,
    ref: float | None,
    cfg: StrategyConfig,
    classifier: Classifier | None = None,
    extra_risks: list[tuple[str, int]] | None = None,
    examples: list[str] | None = None,
) -> Evaluation:
    ev = Evaluation(listing=listing, decision=Decision.SKIP, reference_price=ref)

    # 1. Twarde reguły tekstowe
    rules = check_text(listing.title, listing.description)
    if rules.hard:
        ev.reasons = [f"odrzucone: {r}" for r in rules.hard]
        return ev

    # 2. Ekonomia
    if ref is None:
        ev.reasons = ["brak ceny referencyjnej"]
        return ev
    min_profit = watch.min_profit if watch.min_profit is not None else cfg.min_profit
    profit_now = expected_profit(listing.price, ref, cfg)
    ev.offer_price = offer_price(listing.price, cfg)
    profit_offer = expected_profit(ev.offer_price, ref, cfg)
    if profit_offer < min_profit:
        ev.reasons = [f"za mały zysk ({profit_offer:.0f} zł nawet po zniżce)"]
        return ev

    # 3. Punkty ryzyka
    score = 0
    reasons: list[str] = []

    ratio = listing.price / ref
    if ratio < cfg.too_good_ratio:
        score += 35
        reasons.append(f"cena podejrzanie niska ({ratio:.0%} rynku)")
    elif ratio < cfg.too_good_ratio + 0.15:
        score += 15
        reasons.append(f"bardzo niska cena ({ratio:.0%} rynku)")

    for reason, pts in rules.soft + seller_risk(listing) + (extra_risks or []):
        score += pts
        reasons.append(reason)

    # 4. AI (tekst + zdjęcia)
    if classifier is not None:
        verdict = await classifier.classify(listing, watch.search_text, examples=examples)
        ev.ai = verdict
        if verdict is None:
            score += 15
            reasons.append("AI niedostępne, sprawdź ręcznie")
        else:
            if verdict.category != AICategory.GENUINE:
                if verdict.confidence >= AI_REJECT_CONFIDENCE and verdict.category != AICategory.UNCLEAR:
                    ev.reasons = [f"odrzucone przez AI: {verdict.category.value}"] + verdict.red_flags
                    return ev
                score += 25
                reasons.append(f"AI niepewne: {verdict.category.value} ({verdict.confidence:.0%})")
            if not verdict.matches_query:
                score += 20
                reasons.append("AI: to chyba nie ten produkt")
            score += round(verdict.scam_risk * 0.4)
            reasons.extend(f"AI: {f}" for f in verdict.red_flags)

    ev.risk_score = min(score, 100)
    ev.risk_level = risk_level(ev.risk_score, cfg)
    ev.reasons = reasons

    # 5. Decyzja
    if profit_now >= min_profit and ratio <= cfg.buy_below_ratio:
        ev.planned_action = Decision.BUY
        ev.expected_profit = profit_now
    else:
        ev.planned_action = Decision.OFFER
        ev.expected_profit = profit_offer

    ev.decision = Decision.REVIEW if ev.risk_level == RiskLevel.HIGH else ev.planned_action
    return ev
