"""Ogłoszenie do odsprzedaży: tytuł, opis i ceny, gotowe do wklejenia na Vinted."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic
import httpx

from .config import AIConfig, StrategyConfig
from .pricing import Comparable, percentile, select_comparables

log = logging.getLogger(__name__)

# Cena wystawienia: środek-góra rynku, żeby było miejsce na negocjacje
LIST_PERCENTILE = 0.6

SYSTEM = """Piszesz ogłoszenia na Vinted dla osoby, która odsprzedaje używane rzeczy.
Pisz po polsku, naturalnie, jak zwykły sprzedawca, a nie jak sklep czy AI:
krótkie zdania, bez wykrzykników co drugie słowo, bez słów typu "wyjątkowy",
"niesamowity", "idealny wybór", bez emoji. Podaj konkretne informacje: model,
rozmiar/pojemność, stan, co jest w zestawie. Nie wymyślaj faktów, których nie ma
w danych - jeśli czegoś nie wiesz, pomiń to. Tytuł do 60 znaków, opis 3-6 zdań.
Na końcu opisu jedno krótkie zdanie o wysyłce lub pytaniach."""

SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
    "required": ["title", "description"],
    "additionalProperties": False,
}


@dataclass
class ResaleDraft:
    title: str
    description: str
    list_price: float | None
    min_price: float | None
    samples: int = 0


def suggest_prices(title: str, condition: str, active: list[Comparable],
                   cfg: StrategyConfig) -> tuple[float | None, float | None, int]:
    """(cena wystawienia, minimalna cena przy negocjacjach, liczba podobnych ofert)."""
    prices = select_comparables(title, condition, None, active, cfg)
    if len(prices) < cfg.min_comparables:
        return None, None, len(prices)
    list_price = round(percentile(prices, LIST_PERCENTILE) / 5) * 5
    min_price = round(percentile(prices, cfg.resale_percentile) / 5) * 5
    return float(list_price), float(min_price), len(prices)


def _facts(title: str, description: str, condition: str) -> str:
    return (
        f"Oryginalny tytuł: {title}\n"
        f"Stan: {condition or 'nie podano'}\n"
        f"Oryginalny opis sprzedawcy (może być niedokładny):\n{description or '(brak)'}"
    )


def template_draft(title: str, condition: str) -> tuple[str, str]:
    """Wersja bez AI."""
    cond = condition.lower() if condition else "dobrym"
    desc = (
        f"Sprzedam {title}. Stan {cond}, wszystko sprawne. "
        "Więcej zdjęć mogę podesłać na prośbę. Wysyłka w 1-2 dni, w razie pytań piszcie."
    )
    return title[:60], desc


class ListingWriter:
    def __init__(self, cfg: AIConfig):
        self.cfg = cfg
        self._anthropic = anthropic.AsyncAnthropic() if cfg.provider == "anthropic" else None
        self._http = httpx.AsyncClient(timeout=120) if cfg.provider == "ollama" else None

    async def _write_anthropic(self, facts: str) -> dict | None:
        resp = await self._anthropic.beta.messages.create(
            model=self.cfg.model,
            max_tokens=4096,
            system=SYSTEM,
            messages=[{"role": "user", "content": facts}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), None)
        return json.loads(text) if text else None

    async def _write_ollama(self, facts: str) -> dict | None:
        resp = await self._http.post(f"{self.cfg.ollama_url}/api/chat", json={
            "model": self.cfg.ollama_model, "stream": False, "format": SCHEMA,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": facts}],
        })
        resp.raise_for_status()
        return json.loads(resp.json()["message"]["content"])

    async def write(self, title: str, description: str, condition: str) -> tuple[str, str]:
        facts = _facts(title, description, condition)
        try:
            data = await (self._write_anthropic(facts) if self._anthropic else self._write_ollama(facts))
            if data and data.get("title") and data.get("description"):
                return data["title"][:80], data["description"]
        except (anthropic.APIError, httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("Generator ogłoszeń: %s, używam szablonu", e)
        return template_draft(title, condition)


async def build_draft(title: str, description: str, condition: str, active: list[Comparable],
                      cfg: StrategyConfig, writer: ListingWriter | None) -> ResaleDraft:
    list_price, min_price, n = suggest_prices(title, condition, active, cfg)
    if writer:
        new_title, new_desc = await writer.write(title, description, condition)
    else:
        new_title, new_desc = template_draft(title, condition)
    return ResaleDraft(new_title, new_desc, list_price, min_price, n)


def format_draft(d: ResaleDraft) -> str:
    lines = ["📝 **Propozycja ogłoszenia do odsprzedaży**"]
    if d.list_price:
        lines.append(f"Wystaw za **{d.list_price:.0f} zł**, nie schodź poniżej **{d.min_price:.0f} zł** "
                     f"(na podstawie {d.samples} podobnych ofert)")
    else:
        lines.append("Za mało podobnych ofert, żeby podać cenę. Sprawdź rynek ręcznie.")
    lines += ["", f"**Tytuł:**\n```{d.title}```", f"**Opis:**\n```{d.description}```"]
    return "\n".join(lines)
