"""Wczytywanie konfiguracji z pliku YAML."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
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
    # Adresy API Vinted (nieoficjalne, mogą się zmienić; sprawdz.bat podpowie właściwe)
    search_path: str = "/api/v2/catalog/items"
    details_path: str = "/api/v2/items/{id}"
    # Wysyłaj token sesji (ciasteczko access_token_web) w nagłówku Authorization
    bearer_auth: bool = False
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )


@dataclass
class DiscordConfig:
    channel_id: int = 0
    # Token bota: wpisz tutaj ALBO ustaw zmienną środowiskową o nazwie z token_env
    token: str | None = None
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
    # Klucz do Claude: wpisz tutaj ALBO ustaw zmienną ANTHROPIC_API_KEY
    api_key: str | None = None
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
    # Powyżej tylu dni typowej sprzedaży oferta dostaje uwagę "wolno schodzi"
    max_days_to_sell: float = 30.0
    risk: RiskThresholds = field(default_factory=RiskThresholds)


@dataclass
class NotifyConfig:
    # Kogo pingować przy najlepszych okazjach (ID użytkownika Discord)
    mention_user_id: int | None = None
    # Okazja jest "priorytetowa", gdy zysk >= tyle i ryzyko <= tyle punktów
    ping_min_profit: float = 200.0
    ping_max_risk: int = 30
    # Push na telefon przez ntfy (aplikacja ntfy, temat wymyślasz sam)
    ntfy_topic: str | None = None
    ntfy_url: str = "https://ntfy.sh"


@dataclass
class ClipsConfig:
    """Auto-clipper z Discorda (/klip)."""
    enabled: bool = True
    input_dir: str = "nagrania"       # stąd /klip bierze pliki po nazwie
    out_dir: str = "clips"
    upload_limit_mb: float = 10       # limit plików na Discordzie (bez Nitro 10 MB)
    layout: str = "blur"
    quality: str = "high"
    whisper: str = "small"
    subtitles: bool = True            # False = bez rozpoznawania mowy (dużo szybciej)
    language: str = "polski"
    outro: str = ""
    end_speed: float = 1.0
    music_dir: str | None = None


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
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    clips: ClipsConfig = field(default_factory=ClipsConfig)
    database: str = "sniper.db"


def _make(cls, data: dict | None, section: str, **extra):
    """Tworzy sekcję configu; literówki i nieznane pola dają ostrzeżenie zamiast błędu."""
    data = dict(data or {})
    known = {f.name for f in fields(cls)}
    for key in sorted(set(data) - known):
        print(f"⚠️  config.yaml: nieznane pole '{section}.{key}' (pomijam). Literówka?")
        data.pop(key)
    return cls(**data, **extra)


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    top = {"vinted", "discord", "ai", "strategy", "watches", "messages", "allegro", "notify", "clips", "database"}
    for key in sorted(set(raw) - top):
        print(f"⚠️  config.yaml: nieznana sekcja '{key}' (pomijam). Literówka?")
    strategy_raw = dict(raw.get("strategy") or {})
    risk = _make(RiskThresholds, strategy_raw.pop("risk", None), "strategy.risk")
    return Config(
        vinted=_make(VintedConfig, raw.get("vinted"), "vinted"),
        discord=_make(DiscordConfig, raw.get("discord"), "discord"),
        ai=_make(AIConfig, raw.get("ai"), "ai"),
        strategy=_make(StrategyConfig, strategy_raw, "strategy", risk=risk),
        watches=[_make(Watch, w, "watches") for w in raw.get("watches") or []],
        messages=_make(MessagesConfig, raw.get("messages"), "messages"),
        allegro=_make(AllegroConfig, raw.get("allegro"), "allegro"),
        notify=_make(NotifyConfig, raw.get("notify"), "notify"),
        clips=_make(ClipsConfig, raw.get("clips"), "clips"),
        database=raw.get("database", "sniper.db"),
    )


def discord_token(cfg: Config) -> str | None:
    return cfg.discord.token or os.environ.get(cfg.discord.token_env)


def apply_api_keys(cfg: Config) -> None:
    """Klucz Claude z configu trafia tam, gdzie szuka go biblioteka Anthropic."""
    if cfg.ai.api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = cfg.ai.api_key
