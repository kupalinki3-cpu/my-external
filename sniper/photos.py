"""Wykrywanie kradzionych zdjęć.

Scamerzy wrzucają te same zdjęcia na nowe konta albo kopiują je z prawdziwych
ofert. Dla każdego zdjęcia liczymy "odcisk" (dHash, 64 bity). Odcisk prawie się
nie zmienia po przeskalowaniu, kompresji czy lekkiej zmianie jasności, więc
miniaturka i pełne zdjęcie dają ten sam wynik.
"""

from __future__ import annotations

import asyncio
import io
import logging

import httpx
from PIL import Image

from .db import Database
from .models import Listing

log = logging.getLogger(__name__)

# Ile bitów może się różnić, żeby uznać zdjęcia za to samo
MAX_DISTANCE = 6


def dhash(image_bytes: bytes, size: int = 8) -> int:
    img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((size + 1, size))
    px = list(img.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return bits


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


class PhotoChecker:
    def __init__(self, db: Database, http: httpx.AsyncClient | None = None, concurrency: int = 5):
        self.db = db
        self.http = http or httpx.AsyncClient(timeout=20, follow_redirects=True)
        self._sem = asyncio.Semaphore(concurrency)
        # Wszystkie znane odciski w pamięci: (hash, listing_id, seller_id)
        self._known: list[tuple[int, int, int | None]] = [
            (int(r["hash"], 16), r["listing_id"], r["seller_id"]) for r in db.all_photo_hashes()
        ]

    async def _hash_url(self, url: str) -> int | None:
        async with self._sem:
            try:
                resp = await self.http.get(url)
                resp.raise_for_status()
                return await asyncio.to_thread(dhash, resp.content)
            except Exception as e:  # zły obraz albo błąd sieci - po prostu pomijamy
                log.debug("Nie da się pobrać zdjęcia %s: %s", url, e)
                return None

    def find_matches(self, h: int, listing: Listing) -> list[tuple[int, int | None]]:
        """Inne oferty z prawie identycznym zdjęciem: (listing_id, seller_id)."""
        return [
            (lid, sid) for known, lid, sid in self._known
            if lid != listing.id and distance(h, known) <= MAX_DISTANCE
        ]

    def remember(self, h: int, listing: Listing) -> None:
        self._known.append((h, listing.id, listing.seller.id))
        self.db.add_photo_hash(f"{h:016x}", listing.id, listing.seller.id)

    async def check(self, listing: Listing, max_photos: int = 3) -> list[tuple[str, int]]:
        """Sprawdza zdjęcia oferty i zapamiętuje je. Zwraca (powód, punkty ryzyka)."""
        hashes = await asyncio.gather(*(self._hash_url(u) for u in listing.photos[:max_photos]))
        risks: list[tuple[str, int]] = []
        other_sellers: set[int] = set()
        reposted = False
        for h in (h for h in hashes if h is not None):
            matches = self.find_matches(h, listing)
            if matches:
                # Liczy się, kto pierwszy wrzucił to zdjęcie (lista jest w kolejności dodania).
                # Dzięki temu właściciel wystawiający ponownie nie jest oskarżany o kradzież
                # tylko dlatego, że ktoś skopiował jego zdjęcie.
                first_lid, first_sid = matches[0]
                if first_sid is not None and listing.seller.id is not None and first_sid != listing.seller.id:
                    other_sellers.add(first_lid)
                else:
                    reposted = True
            self.remember(h, listing)

        if other_sellers:
            ids = ", ".join(str(i) for i in sorted(other_sellers)[:3])
            # Kradzione zdjęcie to mocny sygnał scamu: zawsze trafia do Approve/Deny
            risks.append((f"to samo zdjęcie u innego sprzedawcy (oferty: {ids})", 60))
        elif reposted:
            risks.append(("ten sam sprzedawca wystawia to ponownie", 5))
        return risks

    async def remember_thumbnail(self, listing: Listing) -> None:
        """Przy zwykłym skanowaniu zapamiętujemy miniaturkę każdej oferty."""
        if listing.photos:
            h = await self._hash_url(listing.photos[0])
            if h is not None:
                self.remember(h, listing)

    async def close(self) -> None:
        await self.http.aclose()
