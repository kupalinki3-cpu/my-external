"""Wczytywanie konfiguracji z pliku YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Watch:
    name: str
    search_text: str
    price_from: float | None = None
    price_to: float | None = None
    catalog_ids: list[int] = field(default_factory=list)
    brand_ids: list[int] = field(default_factory=list)
    # Ręczna cena odsprzedaży; jeśli brak, liczona z historii sprzedaży
    reference_price: float | None = None
    # Nadpisuje strategy.min_profit dla tej obserwacji
    min_profit: float | None = None
    # Rodzaj produktu, wpływa na pytania do sprzedawcy:
    # clothes | shoes | electronics | other
    kind: str = "other"


@dataclass
class VintedConfig:
    domain: str = "vinted.pl"
    poll_interval_sec: float = 20.0
    per_page: int = 48
    # Ile stron ofert pobrać przy starcie, żeby mieć z czym porównywać ceny
    seed_pages: int = 5
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )


@dataclass
class DiscordConfig:
    channel_id: int = 0
    token_env: str = "DISCORD_TOKEN"
    # ID serwera: komendy /... pojawią się od razu (bez tego do godziny)
    guild_id: int | None = None


@dataclass
class AllegroConfig:
    enabled: bool = False
    client_id_env: str = "ALLEGRO_CLIENT_ID"
    client_secret_env: str = "ALLEGRO_CLIENT_SECRET"
    limit: int = 60
    cache_minutes: int = 60
    # Dodatkowe parametry wyszukiwania, np. tylko używane
    extra_params: dict = field(default_factory=dict)


@dataclass
class AIConfig:
    enabled: bool = True
    # anthropic = Claude przez API, ollama = lokalny model na Twoim komputerze
    provider: str = "anthropic"
    model: str = "claude-opus-5-5"
    effort: str = "low"
    max_images: int = 4
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5vl:7b"


@dataclass
class RiskThresholds:
    medium: int = 30
    high: int = 60


@dataclass
class StrategyConfig:
    # Kup, gdy cena <= ratio * cena referencyjna (0.65 = 35% poniżej rynku)
    buy_below_ratio: float = 0.65
    # Proponowana zniżka, gdy marża jest za mała przy obecnej cenie
    offer_discount: float = 0.30
    min_profit: float = 100.0
    # Dodatkowy zapas na cenie odsprzedaży (wycena i tak jest z dolnej części rynku)
    resale_haircut: float = 0.05
    # Który percentyl cen podobnych ofert to realna cena odsprzedaży
    # (0.35 = taniej niż 65% konkurencji, żeby faktycznie zeszło)
    resale_percentile: float = 0.35
    # Minimalna liczba podobnych ofert, żeby im ufać
    min_comparables: int = 5
    # Z ilu dni brać aktywne oferty do porównania
    comparable_days: int = 14
    # Jak bardzo tytuł musi być podobny (0..1, wspólne słowa / wszystkie słowa)
    similarity_min: float = 0.35
    shipping_cost: float = 15.0
    # Ochrona kupujących Vinted: stała + procent
    buyer_fee_fixed: float = 0.70
    buyer_fee_pct: float = 0.05
    # Poniżej tego ułamka ceny rynkowej oferta jest "za dobra, żeby była prawdziwa"
    too_good_ratio: float = 0.30
    min_price_samples: int = 5
    price_history_days: int = 30
    # Śledź oferty droższe o maksymalnie tyle od opłacalnej ceny (0.35 = 35%)
    price_drop_window: float = 0.35
    price_drop_days: int = 14
    price_check_every_min: int = 30
    risk: RiskThresholds = field(default_factory=RiskThresholds)


@dataclass
class MessagesConfig:
    # m / f - forma czasowników w wiadomościach ("wziął" / "wzięła")
    gender: str = "m"


@dataclass
class Config:
    vinted: VintedConfig
    discord: DiscordConfig
    ai: AIConfig
    strategy: StrategyConfig
    watches: list[Watch]
    messages: MessagesConfig = field(default_factory=MessagesConfig)
    allegro: AllegroConfig = field(default_factory=AllegroConfig)
    database: str = "sniper.db"


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    strategy_raw = dict(raw.get("strategy") or {})
    risk = RiskThresholds(**(strategy_raw.pop("risk", None) or {}))
    return Config(
        vinted=VintedConfig(**(raw.get("vinted") or {})),
        discord=DiscordConfig(**(raw.get("discord") or {})),
        ai=AIConfig(**(raw.get("ai") or {})),
        strategy=StrategyConfig(risk=risk, **strategy_raw),
        watches=[Watch(**w) for w in raw.get("watches") or []],
        messages=MessagesConfig(**(raw.get("messages") or {})),
        allegro=AllegroConfig(**(raw.get("allegro") or {})),
        database=raw.get("database", "sniper.db"),
    )
