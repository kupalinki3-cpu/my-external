"""Ceny z Allegro przez oficjalne REST API (dodatkowe źródło ceny rynkowej).

Wymaga aplikacji zarejestrowanej na https://apps.developer.allegro.pl
(typ: dostęp bez użytkownika / client_credentials). Uwaga: wyszukiwanie ofert
(/offers/listing) Allegro udostępnia tylko zweryfikowanym aplikacjom - jeśli
dostaniesz 403, trzeba złożyć wniosek o dostęp w panelu Allegro.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from .config import AllegroConfig
from .pricing import Comparable

log = logging.getLogger(__name__)

API = "https://api.allegro.pl"
TOKEN_URL = "https://allegro.pl/auth/oauth/token"
ACCEPT = "application/vnd.allegro.public.v1+json"


class AllegroPrices:
    def __init__(self, cfg: AllegroConfig, http: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.http = http or httpx.AsyncClient(timeout=20)
        self._token: str | None = None
        self._token_exp = 0.0
        self._cache: dict[str, tuple[float, list[Comparable]]] = {}
        self._disabled = False

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        client_id = os.environ.get(self.cfg.client_id_env, "")
        secret = os.environ.get(self.cfg.client_secret_env, "")
        if not client_id or not secret:
            raise RuntimeError(f"Brak {self.cfg.client_id_env} / {self.cfg.client_secret_env}")
        resp = await self.http.post(
            TOKEN_URL, params={"grant_type": "client_credentials"}, auth=(client_id, secret)
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + float(data.get("expires_in", 3600))
        return self._token

    async def search(self, phrase: str) -> list[Comparable]:
        """Oferty z Allegro dla frazy (z cache, domyślnie godzina)."""
        if self._disabled:
            return []
        cached = self._cache.get(phrase)
        if cached and time.time() - cached[0] < self.cfg.cache_minutes * 60:
            return cached[1]

        try:
            token = await self._get_token()
            params = {"phrase": phrase, "limit": self.cfg.limit, **self.cfg.extra_params}
            resp = await self.http.get(
                f"{API}/offers/listing", params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT},
            )
            if resp.status_code == 403:
                log.error("Allegro: brak dostępu do /offers/listing (aplikacja niezweryfikowana). Wyłączam.")
                self._disabled = True
                return []
            resp.raise_for_status()
        except (httpx.HTTPError, RuntimeError) as e:
            log.warning("Allegro: %s", e)
            return cached[1] if cached else []

        result = parse_listing_response(resp.json())
        self._cache[phrase] = (time.time(), result)
        return result

    async def close(self) -> None:
        await self.http.aclose()


def parse_listing_response(data: dict) -> list[Comparable]:
    items = data.get("items", {})
    out = []
    for offer in (items.get("promoted") or []) + (items.get("regular") or []):
        try:
            price = float(offer["sellingMode"]["price"]["amount"])
        except (KeyError, TypeError, ValueError):
            continue
        # id Allegro to tekst; Comparable.id jest używane tylko do wykluczania tej samej oferty
        out.append(Comparable(id=-abs(hash(offer.get("id", ""))), title=offer.get("name", ""), price=price))
    return out
