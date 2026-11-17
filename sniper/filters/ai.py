"""Klasyfikacja oferty przez Claude: tytuł + opis + zdjęcia.

Wywoływana dopiero, gdy oferta przeszła twarde reguły i ma potencjał zysku,
więc płacimy tylko za kilka procent ofert.
"""

from __future__ import annotations

import json
import logging

import anthropic

from ..config import AIConfig
from ..models import AICategory, AIVerdict, Listing

log = logging.getLogger(__name__)

SYSTEM = """You review second-hand marketplace listings (Vinted) for a reseller.
Listings may be in any language (often Polish). Decide what is ACTUALLY being sold,
using the photos as the main evidence and the text as support.

Categories:
- genuine_item: the real, complete product the buyer searched for
- dummy_replica_model: display dummy / non-working mock-up / toy model
- box_or_packaging_only: only the box, packaging or manuals
- accessory_only: only a case, charger, strap, cable, etc.
- for_parts_or_broken: damaged, not working, sold for parts
- counterfeit: fake / replica / "inspired" product
- locked_device: iCloud / activation / carrier locked device
- unclear: not enough evidence to decide

Clues for dummies: identical static wallpaper on every photo, no photo of the device
powered on from another angle, no ports or buttons detail, suspiciously low price.
Clues for scams: stock photos, contact or payment outside the platform, price far
below market. Write `summary` and `red_flags` in Polish, short and concrete."""

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": [c.value for c in AICategory]},
        "confidence": {"type": "number", "description": "0.0 - 1.0"},
        "scam_risk": {"type": "integer", "description": "0 - 100"},
        "matches_query": {
            "type": "boolean",
            "description": "Is this the product the buyer searched for?",
        },
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["category", "confidence", "scam_risk", "matches_query", "red_flags", "summary"],
    "additionalProperties": False,
}


class AIClassifier:
    def __init__(self, cfg: AIConfig, client: anthropic.AsyncAnthropic | None = None):
        self.cfg = cfg
        self.client = client or anthropic.AsyncAnthropic()

    def _content(self, listing: Listing, query: str, with_images: bool) -> list[dict]:
        content: list[dict] = []
        if with_images:
            for url in listing.photos[: self.cfg.max_images]:
                content.append({"type": "image", "source": {"type": "url", "url": url}})
        content.append({
            "type": "text",
            "text": (
                f"Buyer is searching for: {query}\n"
                f"Title: {listing.title}\n"
                f"Price: {listing.price} {listing.currency}\n"
                f"Brand: {listing.brand}\nCondition: {listing.condition}\n"
                f"Description:\n{listing.description or '(no description)'}"
            ),
        })
        return content

    async def _call(self, content: list[dict]):
        return await self.client.beta.messages.create(
            model=self.cfg.model,
            max_tokens=4096,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={
                "effort": self.cfg.effort,
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            # Gdy model odmówi, API samo spróbuje modelu zapasowego
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    async def classify(self, listing: Listing, query: str) -> AIVerdict | None:
        try:
            try:
                resp = await self._call(self._content(listing, query, with_images=True))
            except anthropic.BadRequestError as e:
                # Najczęściej: zdjęcie z CDN Vinted nie dało się pobrać
                log.warning("AI: błąd ze zdjęciami (%s), ponawiam bez zdjęć", e)
                resp = await self._call(self._content(listing, query, with_images=False))
        except anthropic.RateLimitError:
            log.warning("AI: limit zapytań, pomijam klasyfikację")
            return None
        except anthropic.APIStatusError as e:
            log.error("AI: błąd API %s", e.status_code)
            return None
        except anthropic.APIConnectionError:
            log.error("AI: brak połączenia")
            return None

        if resp.stop_reason == "refusal":
            log.warning("AI: odmowa dla oferty %s", listing.id)
            return None

        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            return None
        data = json.loads(text)
        return AIVerdict(
            category=AICategory(data["category"]),
            confidence=max(0.0, min(1.0, float(data["confidence"]))),
            scam_risk=max(0, min(100, int(data["scam_risk"]))),
            matches_query=bool(data["matches_query"]),
            red_flags=list(data["red_flags"]),
            summary=data["summary"],
        )
