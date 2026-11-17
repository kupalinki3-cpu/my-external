"""Klient Vinted oparty o nieoficjalne endpointy strony.

Vinted nie ma publicznego API. Te endpointy używa ich własna strona,
mogą się zmienić bez ostrzeżenia, a zbyt częste odpytywanie kończy się
blokadą IP. Regulamin Vinted zabrania botów - używasz na własne ryzyko.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import VintedConfig, Watch
from .models import Listing, Seller

log = logging.getLogger(__name__)


class VintedClient:
    def __init__(self, cfg: VintedConfig):
        self.cfg = cfg
        self.base = f"https://www.{cfg.domain}"
        self.http = httpx.AsyncClient(
            headers={"User-Agent": cfg.user_agent, "Accept": "application/json, text/plain, */*"},
            follow_redirects=True,
            timeout=20,
        )
        self._has_session = False

    async def _ensure_session(self) -> None:
        # Wejście na stronę główną ustawia ciasteczko sesji wymagane przez API
        if not self._has_session:
            await self.http.get(self.base, headers={"Accept": "text/html"})
            self._has_session = True

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        await self._ensure_session()
        resp = await self.http.get(f"{self.base}{path}", params=params)
        if resp.status_code == 401:
            self._has_session = False
            await self._ensure_session()
            resp = await self.http.get(f"{self.base}{path}", params=params)
        return resp

    async def search_newest(self, watch: Watch, page: int = 1) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "search_text": watch.search_text,
            "order": "newest_first",
            "per_page": self.cfg.per_page,
            "page": page,
        }
        if watch.price_from is not None:
            params["price_from"] = watch.price_from
        if watch.price_to is not None:
            params["price_to"] = watch.price_to
        if watch.catalog_ids:
            params["catalog_ids"] = ",".join(map(str, watch.catalog_ids))
        if watch.brand_ids:
            params["brand_ids"] = ",".join(map(str, watch.brand_ids))

        resp = await self._get("/api/v2/catalog/items", params)
        resp.raise_for_status()
        return resp.json().get("items", [])

    async def item_details(self, item_id: int) -> dict[str, Any] | None:
        """Szczegóły (opis, wszystkie zdjęcia, opinie sprzedawcy). None = oferta zniknęła."""
        resp = await self._get(f"/api/v2/items/{item_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("item")

    async def close(self) -> None:
        await self.http.aclose()


def _price(raw: Any) -> tuple[float, str]:
    if isinstance(raw, dict):
        return float(raw.get("amount", 0)), raw.get("currency_code", "")
    return float(raw or 0), ""


def _photo_url(p: dict) -> str | None:
    return p.get("full_size_url") or p.get("url")


def parse_listing(item: dict[str, Any], watch: str, details: dict[str, Any] | None = None) -> Listing:
    """Składa Listing z wyniku wyszukiwania i (opcjonalnie) szczegółów."""
    src = {**item, **(details or {})}
    price, currency = _price(src.get("price"))
    currency = currency or src.get("currency", "")

    photos = [u for p in src.get("photos") or [] if (u := _photo_url(p))]
    if not photos and isinstance(src.get("photo"), dict) and (u := _photo_url(src["photo"])):
        photos = [u]

    user = src.get("user") or {}
    seller = Seller(
        id=user.get("id"),
        login=user.get("login", ""),
        feedback_count=user.get("feedback_count"),
        feedback_reputation=user.get("feedback_reputation"),
    )
    return Listing(
        id=int(src["id"]),
        title=src.get("title", ""),
        price=price,
        currency=currency,
        url=src.get("url", ""),
        watch=watch,
        description=src.get("description", "") or "",
        photos=photos,
        brand=src.get("brand_title", "") or "",
        size=src.get("size_title", "") or "",
        condition=src.get("status", "") or "",
        seller=seller,
    )


def is_sold(details: dict[str, Any]) -> bool:
    return bool(details.get("is_closed") or details.get("is_sold"))
