"""Nauka z Twoich kliknięć Approve/Deny i statystyki zakupów."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean

# Minimalna liczba ocen, żeby proponować nowy próg ryzyka
MIN_REVIEWS_FOR_THRESHOLD = 15
# Powód, który zatwierdzasz w >= tylu % przypadków, to raczej fałszywy alarm
FALSE_ALARM_RATE = 0.8
MIN_REASON_COUNT = 3


def reason_key(reason: str) -> str:
    """'mało opinii (3)' -> 'mało opinii', 'AI: ...' -> 'AI'."""
    if reason.startswith("AI: ") and not reason.startswith("AI: to chyba"):
        return "uwagi AI"
    return re.sub(r"\s*\(.*\)$", "", reason).strip()


@dataclass
class FeedbackReport:
    total: int = 0
    approved: int = 0
    by_reason: dict[str, list[int]] = field(default_factory=dict)  # powód -> [approved, denied]
    false_alarms: list[str] = field(default_factory=list)
    suggested_threshold: int | None = None
    threshold_accuracy: float | None = None


def build_report(rows: list[sqlite3.Row], current_threshold: int) -> FeedbackReport:
    rep = FeedbackReport(total=len(rows))
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    scored: list[tuple[int, bool]] = []
    for r in rows:
        approved = r["review_status"] == "approved"
        rep.approved += approved
        scored.append((r["risk_score"] or 0, approved))
        for reason in {reason_key(x) for x in json.loads(r["reasons"] or "[]")}:
            counts[reason][0 if approved else 1] += 1
    rep.by_reason = dict(sorted(counts.items(), key=lambda kv: -sum(kv[1])))

    rep.false_alarms = [
        reason for reason, (ok, bad) in rep.by_reason.items()
        if ok + bad >= MIN_REASON_COUNT and ok / (ok + bad) >= FALSE_ALARM_RATE
    ]

    if len(scored) >= MIN_REVIEWS_FOR_THRESHOLD and 0 < rep.approved < len(scored):
        # Próg, przy którym reguła "ryzyko >= próg -> odrzuć" najlepiej zgadza się z Tobą
        best = max(
            range(10, 100, 5),
            key=lambda t: (sum((s >= t) != ok for s, ok in scored), -abs(t - current_threshold)),
        )
        acc = sum((s >= best) != ok for s, ok in scored) / len(scored)
        rep.suggested_threshold, rep.threshold_accuracy = best, acc
    return rep


def format_report(rep: FeedbackReport, current_threshold: int) -> str:
    if rep.total == 0:
        return "Brak ocenionych ofert. Klikaj Approve/Deny, a bot zacznie się uczyć."
    lines = [
        f"**Raport z Twoich decyzji** ({rep.total} ofert, zatwierdzone: {rep.approved / rep.total:.0%})",
        "",
        "**Powody alertów** (✅ zatwierdzone / ❌ odrzucone):",
    ]
    for reason, (ok, bad) in list(rep.by_reason.items())[:12]:
        lines.append(f"• {reason}: ✅ {ok} / ❌ {bad}")
    if rep.false_alarms:
        lines += ["", "**Prawdopodobnie fałszywe alarmy** (prawie zawsze zatwierdzasz):"]
        lines += [f"• {r}" for r in rep.false_alarms]
    if rep.suggested_threshold is not None:
        lines.append("")
        if rep.suggested_threshold != current_threshold:
            lines.append(
                f"**Próg wysokiego ryzyka:** teraz {current_threshold}, sugerowany "
                f"**{rep.suggested_threshold}** (zgodny z Tobą w {rep.threshold_accuracy:.0%}). "
                f"Ustaw: `/prog {rep.suggested_threshold}`"
            )
        else:
            lines.append(f"Próg ryzyka {current_threshold} pasuje do Twoich decyzji 👍")
    else:
        lines += ["", f"Propozycja progu pojawi się po {MIN_REVIEWS_FOR_THRESHOLD} ocenach."]
    return "\n".join(lines)


def feedback_examples(rows: list[sqlite3.Row]) -> list[str]:
    """Twoje ostatnie decyzje jako przykłady dla AI."""
    out = []
    for r in rows:
        verdict = "approved (good deal)" if r["review_status"] == "approved" else "rejected"
        reasons = ", ".join(json.loads(r["reasons"] or "[]")[:3])
        out.append(f"'{r['title']}' for {r['price']:.0f}: {verdict}" + (f" (flags: {reasons})" if reasons else ""))
    return out


# ---------- statystyki zakupów ----------

@dataclass
class ProfitStats:
    bought: int = 0
    sold: int = 0
    invested: float = 0.0
    revenue: float = 0.0
    profit: float = 0.0
    avg_days_to_sell: float | None = None
    in_stock_value: float = 0.0
    by_watch: dict[str, dict] = field(default_factory=dict)


def profit_stats(rows: list[sqlite3.Row]) -> ProfitStats:
    st = ProfitStats(bought=len(rows))
    days: list[float] = []
    per: dict[str, dict] = defaultdict(lambda: {"bought": 0, "sold": 0, "profit": 0.0})
    for r in rows:
        w = per[r["watch"] or "?"]
        w["bought"] += 1
        st.invested += r["buy_price"]
        if r["sell_price"] is None:
            st.in_stock_value += r["buy_price"]
            continue
        st.sold += 1
        w["sold"] += 1
        gain = r["sell_price"] - r["buy_price"]
        st.revenue += r["sell_price"]
        st.profit += gain
        w["profit"] += gain
        days.append((r["sell_ts"] - r["buy_ts"]) / 86400)
    st.avg_days_to_sell = mean(days) if days else None
    st.by_watch = dict(per)
    return st


def format_stats(st: ProfitStats) -> str:
    if st.bought == 0:
        return "Nic jeszcze nie kupiłeś. Dodaj zakup: `/kupione oferta:<link albo id> cena:<zł>`"
    lines = [
        "**Twoje wyniki**",
        f"Kupione: {st.bought} · sprzedane: {st.sold} · w magazynie: {st.bought - st.sold} "
        f"({st.in_stock_value:.0f} zł zamrożone)",
        f"Zysk na sprzedanych: **{st.profit:+.0f} zł**",
    ]
    if st.avg_days_to_sell is not None:
        lines.append(f"Średnio schodzi w {st.avg_days_to_sell:.1f} dnia")
    if st.by_watch:
        lines += ["", "**Wg obserwacji:**"]
        for name, w in sorted(st.by_watch.items(), key=lambda kv: -kv[1]["profit"]):
            lines.append(f"• {name}: kupione {w['bought']}, sprzedane {w['sold']}, zysk {w['profit']:+.0f} zł")
    return "\n".join(lines)
