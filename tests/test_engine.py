import asyncio
import random

from sniper.config import StrategyConfig, Watch
from sniper.engine import evaluate, has_potential
from sniper.messages import build_seller_message
from sniper.models import AICategory, AIVerdict, Decision, Listing, RiskLevel, Seller
from sniper.pricing import buyer_cost, expected_profit, offer_price, reference_price

CFG = StrategyConfig()
WATCH = Watch(name="iphone", search_text="iphone 13 pro", kind="electronics")
GOOD_SELLER = Seller(id=1, login="anna", feedback_count=50, feedback_reputation=1.0)


def make_listing(price, desc="Sprawny, bez blokad", photos=3, seller=GOOD_SELLER):
    return Listing(
        id=1, title="iPhone 13 Pro 128GB", price=price, currency="PLN",
        url="https://www.vinted.pl/items/1", watch="iphone", description=desc,
        photos=[f"https://img/{i}.jpg" for i in range(photos)], seller=seller,
    )


class FakeAI:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0

    async def classify(self, listing, query):
        self.calls += 1
        return self.verdict


def verdict(category=AICategory.GENUINE, confidence=0.95, scam_risk=5, matches=True):
    return AIVerdict(category, confidence, scam_risk, matches, [], "ok")


def run(coro):
    return asyncio.run(coro)


def test_profit_math():
    # 1000 zł: 0.70 + 5% (50) + 15 wysyłki
    assert buyer_cost(1000, CFG) == 1065.70
    # odsprzedaż 2000 * 0.9 = 1800
    assert expected_profit(1000, 2000, CFG) == 734.30
    assert offer_price(1000, CFG) == 700


def test_reference_price_needs_samples():
    assert reference_price(WATCH, [1000, 1100], CFG) is None
    assert reference_price(WATCH, [1000, 1100, 1200, 1300, 5000], CFG) == 1200
    manual = Watch(name="x", search_text="x", reference_price=900)
    assert reference_price(manual, [], CFG) == 900


def test_big_discount_low_risk_is_buy():
    ev = run(evaluate(make_listing(1200), WATCH, 2200, CFG, FakeAI(verdict())))
    assert ev.decision == Decision.BUY
    assert ev.risk_level == RiskLevel.LOW


def test_small_margin_becomes_offer():
    # 1900 przy rynku 2200: zysk teraz < 100, po -30% (1330) duży
    ev = run(evaluate(make_listing(1900), WATCH, 2200, CFG, FakeAI(verdict())))
    assert ev.decision == Decision.OFFER
    assert ev.offer_price == 1330


def test_no_profit_even_after_offer_is_skip():
    ai = FakeAI(verdict())
    ev = run(evaluate(make_listing(2600), WATCH, 2200, CFG, ai))
    assert ev.decision == Decision.SKIP
    assert ai.calls == 0  # nie płacimy za AI, gdy nie ma zysku


def test_hard_rule_skips_before_ai():
    ai = FakeAI(verdict())
    ev = run(evaluate(make_listing(300, desc="atrapa, nie działa"), WATCH, 2200, CFG, ai))
    assert ev.decision == Decision.SKIP
    assert ai.calls == 0


def test_ai_confident_dummy_is_skip():
    ai = FakeAI(verdict(AICategory.DUMMY, confidence=0.9))
    ev = run(evaluate(make_listing(1200), WATCH, 2200, CFG, ai))
    assert ev.decision == Decision.SKIP


def test_high_risk_goes_to_review():
    shady = Seller(id=2, login="new", feedback_count=0)
    ai = FakeAI(verdict(AICategory.UNCLEAR, confidence=0.5, scam_risk=70))
    listing = make_listing(500, desc="kontakt whatsapp", photos=1, seller=shady)
    ev = run(evaluate(listing, WATCH, 2200, CFG, ai))
    assert ev.decision == Decision.REVIEW
    assert ev.planned_action == Decision.BUY
    assert ev.risk_level == RiskLevel.HIGH


def test_has_potential_prefilter():
    assert has_potential(1900, 2200, WATCH, CFG)
    assert not has_potential(2600, 2200, WATCH, CFG)
    assert not has_potential(100, None, WATCH, CFG)


def test_seller_message_sounds_human():
    msg = build_seller_message("clothes", Decision.OFFER, 70, rng=random.Random(1))
    assert "70" in msg
    assert "AI" not in msg and "bot" not in msg.lower()
    # Pytania różnią się między wiadomościami
    msgs = {build_seller_message("clothes", Decision.BUY, rng=random.Random(i)) for i in range(20)}
    assert len(msgs) > 5
