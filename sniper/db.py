"""SQLite: oferty, historia cen, decyzje, zdjęcia, zakupy i obserwacje."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict

from .config import Watch
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
    description TEXT DEFAULT '',
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

-- Odciski zdjęć (dHash) do wykrywania kradzionych zdjęć
CREATE TABLE IF NOT EXISTS photo_hashes (
    hash TEXT NOT NULL,
    listing_id INTEGER NOT NULL,
    seller_id INTEGER,
    first_seen REAL,
    PRIMARY KEY (hash, listing_id)
);

-- Twoje zakupy i sprzedaże (komendy /kupione, /sprzedane)
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER,
    title TEXT,
    watch TEXT,
    buy_price REAL,
    buy_ts REAL,
    sell_price REAL,
    sell_ts REAL
);

-- Oferty za drogie o niewiele: pilnujemy, czy stanieją
CREATE TABLE IF NOT EXISTS price_watch (
    listing_id INTEGER PRIMARY KEY,
    watch TEXT,
    last_price REAL,
    target_price REAL,
    added REAL,
    last_check REAL
);

-- Obserwacje zarządzane z Discorda (/obserwuj, /usun, /pauza)
CREATE TABLE IF NOT EXISTS watches (
    name TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    paused INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
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
        if "description" not in cols:
            self.conn.execute("ALTER TABLE listings ADD COLUMN description TEXT DEFAULT ''")

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    # ---------- oferty ----------

    def is_seen(self, listing_id: int) -> bool:
        cur = self.conn.execute("SELECT 1 FROM listings WHERE id = ?", (listing_id,))
        return cur.fetchone() is not None

    def get_listing(self, listing_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()

    def upsert_listing(self, listing: Listing) -> None:
        now = time.time()
        self._exec(
            """INSERT INTO listings (id, watch, title, price, currency, url, condition, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET price = excluded.price, last_seen = excluded.last_seen,
                   status = 'active'""",
            (listing.id, listing.watch, listing.title, listing.price,
             listing.currency, listing.url, listing.condition, now, now),
        )

    def stale_active(self, older_than_sec: float, limit: int = 20) -> list[sqlite3.Row]:
        """Aktywne oferty, których dawno nie było w wynikach - mogły się sprzedać."""
        cutoff = time.time() - older_than_sec
        return self.conn.execute(
            "SELECT * FROM listings WHERE status = 'active' AND last_seen < ? "
            "ORDER BY last_seen LIMIT ?",
            (cutoff, limit),
        ).fetchall()

    def set_status(self, listing_id: int, status: str) -> None:
        self._exec("UPDATE listings SET status = ? WHERE id = ?", (status, listing_id))

    def comparables(self, watch: str, status: str, days: int) -> list[Comparable]:
        """Oferty z danej obserwacji: aktywne (do porównania cen) albo sprzedane."""
        cutoff = time.time() - days * 86400
        rows = self.conn.execute(
            "SELECT id, title, price, condition, first_seen, last_seen FROM listings "
            "WHERE watch = ? AND status = ? AND last_seen >= ?",
            (watch, status, cutoff),
        ).fetchall()
        return [
            Comparable(
                r["id"], r["title"] or "", r["price"], r["condition"] or "",
                days_listed=(r["last_seen"] - r["first_seen"]) / 86400 if status == "sold" else None,
            )
            for r in rows
        ]

    def set_description(self, listing_id: int, description: str) -> None:
        self._exec("UPDATE listings SET description = ? WHERE id = ?", (description, listing_id))

    # ---------- decyzje ----------

    def save_decision(self, ev: Evaluation, message_id: int | None = None) -> None:
        self._exec(
            """INSERT OR REPLACE INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ev.listing.id, ev.decision.value,
                ev.planned_action.value if ev.planned_action else None,
                ev.risk_score, ev.risk_level.value, json.dumps(ev.reasons, ensure_ascii=False),
                ev.reference_price, ev.expected_profit, ev.offer_price, time.time(),
                message_id, "pending" if message_id else None, None,
            ),
        )

    def get_decision(self, listing_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT d.*, l.url, l.title, l.watch, l.price FROM decisions d "
            "JOIN listings l ON l.id = d.listing_id WHERE d.listing_id = ?",
            (listing_id,),
        ).fetchone()

    def set_review(self, listing_id: int, status: str, user: str) -> bool:
        """Zwraca False, jeśli ktoś już zdecydował (podwójne kliknięcie)."""
        cur = self._exec(
            "UPDATE decisions SET review_status = ?, reviewed_by = ? "
            "WHERE listing_id = ? AND review_status = 'pending'",
            (status, user, listing_id),
        )
        return cur.rowcount > 0

    def reviewed(self, since_ts: float = 0) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT d.*, l.title, l.watch, l.price FROM decisions d "
            "JOIN listings l ON l.id = d.listing_id "
            "WHERE d.review_status IN ('approved', 'denied') AND d.created >= ?",
            (since_ts,),
        ).fetchall()

    def recent_feedback(self, watch: str, limit: int = 6) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT l.title, l.price, d.review_status, d.reasons FROM decisions d "
            "JOIN listings l ON l.id = d.listing_id "
            "WHERE l.watch = ? AND d.review_status IN ('approved', 'denied') "
            "ORDER BY d.created DESC LIMIT ?",
            (watch, limit),
        ).fetchall()

    # ---------- zdjęcia ----------

    def all_photo_hashes(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT hash, listing_id, seller_id FROM photo_hashes ORDER BY first_seen"
        ).fetchall()

    def add_photo_hash(self, h: str, listing_id: int, seller_id: int | None) -> None:
        self._exec(
            "INSERT OR IGNORE INTO photo_hashes VALUES (?, ?, ?, ?)",
            (h, listing_id, seller_id, time.time()),
        )

    # ---------- zakupy ----------

    def add_purchase(self, listing_id: int | None, title: str, watch: str, price: float) -> int:
        cur = self._exec(
            "INSERT INTO purchases (listing_id, title, watch, buy_price, buy_ts) VALUES (?, ?, ?, ?, ?)",
            (listing_id, title, watch, price, time.time()),
        )
        return cur.lastrowid

    def mark_sold(self, purchase_id: int, price: float) -> bool:
        cur = self._exec(
            "UPDATE purchases SET sell_price = ?, sell_ts = ? WHERE id = ? AND sell_price IS NULL",
            (price, time.time(), purchase_id),
        )
        return cur.rowcount > 0

    def find_open_purchase(self, listing_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM purchases WHERE listing_id = ? AND sell_price IS NULL ORDER BY id DESC",
            (listing_id,),
        ).fetchone()

    def get_purchase(self, purchase_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()

    def purchases(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM purchases ORDER BY id").fetchall()

    # ---------- śledzenie spadków cen ----------

    def add_price_watch(self, listing_id: int, watch: str, price: float, target: float) -> None:
        now = time.time()
        self._exec(
            "INSERT OR IGNORE INTO price_watch VALUES (?, ?, ?, ?, ?, ?)",
            (listing_id, watch, price, target, now, now),
        )

    def due_price_watch(self, every_sec: float, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM price_watch WHERE last_check < ? ORDER BY last_check LIMIT ?",
            (time.time() - every_sec, limit),
        ).fetchall()

    def update_price_watch(self, listing_id: int, price: float) -> None:
        self._exec(
            "UPDATE price_watch SET last_price = ?, last_check = ? WHERE listing_id = ?",
            (price, time.time(), listing_id),
        )

    def remove_price_watch(self, listing_id: int) -> None:
        self._exec("DELETE FROM price_watch WHERE listing_id = ?", (listing_id,))

    def expire_price_watch(self, max_age_sec: float) -> None:
        self._exec("DELETE FROM price_watch WHERE added < ?", (time.time() - max_age_sec,))

    # ---------- obserwacje ----------

    def import_watches(self, watches: list[Watch]) -> None:
        """Dodaje obserwacje z configu, których jeszcze nie ma w bazie."""
        for w in watches:
            self._exec(
                "INSERT OR IGNORE INTO watches (name, data) VALUES (?, ?)",
                (w.name, json.dumps(asdict(w), ensure_ascii=False)),
            )

    def save_watch(self, w: Watch) -> None:
        self._exec(
            "INSERT INTO watches (name, data) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET data = excluded.data",
            (w.name, json.dumps(asdict(w), ensure_ascii=False)),
        )

    def delete_watch(self, name: str) -> bool:
        return self._exec("DELETE FROM watches WHERE name = ?", (name,)).rowcount > 0

    def set_watch_paused(self, name: str, paused: bool) -> bool:
        cur = self._exec("UPDATE watches SET paused = ? WHERE name = ?", (int(paused), name))
        return cur.rowcount > 0

    def list_watches(self, include_paused: bool = True) -> list[tuple[Watch, bool]]:
        rows = self.conn.execute("SELECT data, paused FROM watches ORDER BY name").fetchall()
        out = [(Watch(**json.loads(r["data"])), bool(r["paused"])) for r in rows]
        return out if include_paused else [(w, p) for w, p in out if not p]

    # ---------- ustawienia ----------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
