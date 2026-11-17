"""SQLite: widziane oferty, historia cen i decyzje z Discorda."""

from __future__ import annotations

import json
import sqlite3
import time

from .models import Evaluation, Listing
from .pricing import Comparable

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    watch TEXT NOT NULL,
    title TEXT,
    price REAL,
    currency TEXT,
    url TEXT,
    condition TEXT DEFAULT '',
    first_seen REAL,
    last_seen REAL,
    status TEXT DEFAULT 'active'  -- active | sold | gone
);
CREATE INDEX IF NOT EXISTS idx_listings_watch ON listings(watch, status);

CREATE TABLE IF NOT EXISTS decisions (
    listing_id INTEGER PRIMARY KEY,
    decision TEXT,
    planned_action TEXT,
    risk_score INTEGER,
    risk_level TEXT,
    reasons TEXT,
    reference_price REAL,
    expected_profit REAL,
    offer_price REAL,
    created REAL,
    discord_message_id INTEGER,
    review_status TEXT,  -- pending | approved | denied
    reviewed_by TEXT
);
"""


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Migracja starszych baz bez kolumny condition
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(listings)")}
        if "condition" not in cols:
            self.conn.execute("ALTER TABLE listings ADD COLUMN condition TEXT DEFAULT ''")

    def is_seen(self, listing_id: int) -> bool:
        cur = self.conn.execute("SELECT 1 FROM listings WHERE id = ?", (listing_id,))
        return cur.fetchone() is not None

    def upsert_listing(self, listing: Listing) -> None:
        now = time.time()
        self.conn.execute(
            """INSERT INTO listings (id, watch, title, price, currency, url, condition, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET price = excluded.price, last_seen = excluded.last_seen,
                   status = 'active'""",
            (listing.id, listing.watch, listing.title, listing.price,
             listing.currency, listing.url, listing.condition, now, now),
        )
        self.conn.commit()

    def stale_active(self, older_than_sec: float, limit: int = 20) -> list[sqlite3.Row]:
        """Aktywne oferty, których dawno nie było w wynikach - mogły się sprzedać."""
        cutoff = time.time() - older_than_sec
        return self.conn.execute(
            "SELECT * FROM listings WHERE status = 'active' AND last_seen < ? "
            "ORDER BY last_seen LIMIT ?",
            (cutoff, limit),
        ).fetchall()

    def set_status(self, listing_id: int, status: str) -> None:
        self.conn.execute("UPDATE listings SET status = ? WHERE id = ?", (status, listing_id))
        self.conn.commit()

    def comparables(self, watch: str, status: str, days: int) -> list[Comparable]:
        """Oferty z danej obserwacji: aktywne (do porównania cen) albo sprzedane."""
        cutoff = time.time() - days * 86400
        rows = self.conn.execute(
            "SELECT id, title, price, condition FROM listings "
            "WHERE watch = ? AND status = ? AND last_seen >= ?",
            (watch, status, cutoff),
        ).fetchall()
        return [Comparable(r["id"], r["title"] or "", r["price"], r["condition"] or "") for r in rows]

    def save_decision(self, ev: Evaluation, message_id: int | None = None) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ev.listing.id, ev.decision.value,
                ev.planned_action.value if ev.planned_action else None,
                ev.risk_score, ev.risk_level.value, json.dumps(ev.reasons, ensure_ascii=False),
                ev.reference_price, ev.expected_profit, ev.offer_price, time.time(),
                message_id, "pending" if message_id else None, None,
            ),
        )
        self.conn.commit()

    def get_decision(self, listing_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT d.*, l.url, l.title, l.watch, l.price FROM decisions d "
            "JOIN listings l ON l.id = d.listing_id WHERE d.listing_id = ?",
            (listing_id,),
        ).fetchone()

    def set_review(self, listing_id: int, status: str, user: str) -> bool:
        """Zwraca False, jeśli ktoś już zdecydował (podwójne kliknięcie)."""
        cur = self.conn.execute(
            "UPDATE decisions SET review_status = ?, reviewed_by = ? "
            "WHERE listing_id = ? AND review_status = 'pending'",
            (status, user, listing_id),
        )
        self.conn.commit()
        return cur.rowcount > 0
