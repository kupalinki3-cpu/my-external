"""Ocena wiarygodności sprzedawcy."""

from __future__ import annotations

from ..models import Listing


def seller_risk(listing: Listing) -> list[tuple[str, int]]:
    s = listing.seller
    out: list[tuple[str, int]] = []
    if s.feedback_count is None:
        out.append(("brak danych o sprzedawcy", 5))
    elif s.feedback_count == 0:
        out.append(("sprzedawca bez opinii", 20))
    elif s.feedback_count < 5:
        out.append((f"mało opinii ({s.feedback_count})", 10))
    if s.feedback_reputation is not None and s.feedback_count and s.feedback_reputation < 0.9:
        out.append((f"słabe opinie ({s.feedback_reputation * 5:.1f}/5)", 15))
    if len(listing.photos) < 2:
        out.append(("mniej niż 2 zdjęcia", 10))
    return out
