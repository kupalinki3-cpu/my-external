"""Struktury danych używane w całym bocie."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass
class Seller:
    id: int | None = None
    login: str = ""
    feedback_count: int | None = None
    # Vinted podaje reputację w skali 0..1 (1.0 = same pozytywne opinie)
    feedback_reputation: float | None = None


@dataclass
class Listing:
    id: int
    title: str
    price: float
    currency: str
    url: str
    watch: str
    description: str = ""
    photos: list[str] = field(default_factory=list)
    brand: str = ""
    size: str = ""
    condition: str = ""
    seller: Seller = field(default_factory=Seller)


class Decision(str, Enum):
    BUY = "buy"        # cena mocno w dół, niskie ryzyko
    OFFER = "offer"    # marża tylko po zbiciu ceny -> propozycja zniżki
    REVIEW = "review"  # wysokie ryzyko scamu -> decyzja człowieka na Discordzie
    SKIP = "skip"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AICategory(str, Enum):
    GENUINE = "genuine_item"
    DUMMY = "dummy_replica_model"
    BOX_ONLY = "box_or_packaging_only"
    ACCESSORY = "accessory_only"
    PARTS = "for_parts_or_broken"
    COUNTERFEIT = "counterfeit"
    LOCKED = "locked_device"
    UNCLEAR = "unclear"


@dataclass
class AIVerdict:
    category: AICategory
    confidence: float
    scam_risk: int
    matches_query: bool
    red_flags: list[str]
    summary: str


@dataclass
class Evaluation:
    listing: Listing
    decision: Decision
    risk_score: int = 0
    risk_level: RiskLevel = RiskLevel.LOW
    reasons: list[str] = field(default_factory=list)
    reference_price: float | None = None
    expected_profit: float | None = None
    offer_price: float | None = None
    # Co zrobić po zatwierdzeniu na Discordzie (BUY albo OFFER)
    planned_action: Decision | None = None
    ai: AIVerdict | None = None
