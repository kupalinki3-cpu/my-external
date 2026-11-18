"""Szukanie najlepszych momentów w długim nagraniu.

Każda sekunda dostaje punkty z kilku sygnałów:
- głośność wyraźnie powyżej tła z ostatnich minut (krzyk, wybuch, śmiech),
- czat nagle pisze dużo szybciej niż zwykle (najmocniejszy sygnał na streamach),
- nagłe zmiany obrazu (akcja, cięcia),
- słowa typu "no way", "o matko", "co jest", "let's go".

Potem wybieramy najwyżej punktowane szczyty, oddalone od siebie, i wokół
każdego budujemy klip: więcej czasu PRZED szczytem (żeby było widać, co do
niego doprowadziło), mniej po. Granice dociągamy do końców zdań.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .transcribe import Transcript

HYPE_WORDS = [
    # polskie
    "o matko", "o boże", "o kurde", "kurde", "co jest", "nie wierzę", "nie no", "jazda",
    "dawaj", "ale akcja", "masakra", "o nie", "uciekaj", "co to było", "co się dzieje",
    # angielskie
    "no way", "oh my god", "omg", "let's go", "lets go", "what the", "wtf", "bro",
    "run", "holy", "insane", "crazy", "yo", "clip that", "clip it",
]
_HYPE_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in HYPE_WORDS) + r")\b", re.IGNORECASE)


@dataclass
class Weights:
    loudness: float = 1.0
    chat: float = 1.5
    scenes: float = 0.5
    words: float = 0.8


@dataclass
class ClipRules:
    min_len: float = 15.0
    max_len: float = 59.0     # Shorts / TikTok: bezpiecznie poniżej minuty
    pre: float = 22.0         # ile sekund przed szczytem
    post: float = 8.0         # ile sekund po szczycie
    min_gap: float = 90.0     # minimalny odstęp między klipami
    snap: float = 5.0         # o ile sekund wolno przesunąć granicę do końca zdania
    min_score: float = 3.0    # słabsze momenty pomijamy, nawet jeśli brakuje klipów
    min_rel: float = 0.15     # ...i te poniżej 15% najlepszego momentu


@dataclass
class Candidate:
    start: float
    end: float
    peak: float
    score: float
    reasons: list[str] = field(default_factory=list)
    text: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


def _rolling(values: np.ndarray, window: int, fn) -> np.ndarray:
    if len(values) == 0:
        return values
    pad = window // 2
    padded = np.pad(values, pad, mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, 2 * pad + 1)
    return fn(view, axis=1)[: len(values)]


def zscore_local(values: np.ndarray, window: int = 301, min_spread: float = 1e-3) -> np.ndarray:
    """Jak bardzo wartość odstaje od tła z ostatnich ~5 minut (odporne na zmiany głośności streamu).

    min_spread chroni przed fałszywymi alarmami w bardzo równych fragmentach:
    gdy tło prawie się nie zmienia, nawet drobny szum dawałby ogromny wynik.
    """
    if len(values) == 0:
        return values
    window = min(window, len(values) | 1)
    base = _rolling(values, window, np.median)
    spread = np.maximum(_rolling(np.abs(values - base), window, np.median) * 1.4826, min_spread)
    return np.clip((values - base) / spread, -3, 8)


def counts_per_second(times: list[float], duration: float) -> np.ndarray:
    n = int(np.ceil(duration))
    counts = np.zeros(n, dtype=np.float32)
    for t in times:
        if 0 <= t < n:
            counts[int(t)] += 1
    return counts


def word_hits(transcript: Transcript, duration: float) -> np.ndarray:
    hits = np.zeros(int(np.ceil(duration)), dtype=np.float32)
    for s in transcript.sentences:
        n = len(_HYPE_RE.findall(s.text))
        if n and int(s.start) < len(hits):
            hits[int(s.start): max(int(s.end), int(s.start) + 1)] += n
    return hits


def score_timeline(
    duration: float,
    loudness: np.ndarray,
    chat_times: list[float] | None = None,
    scene_times: list[float] | None = None,
    transcript: Transcript | None = None,
    weights: Weights = Weights(),
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    n = int(np.ceil(duration))
    parts: dict[str, np.ndarray] = {}

    loud = np.zeros(n, dtype=np.float32)
    loud[: min(n, len(loudness))] = loudness[:n]
    # skok o mniej niż ~3 dB to nie wydarzenie
    parts["loudness"] = np.clip(zscore_local(loud, min_spread=3.0), 0, None)

    if chat_times:
        chat = _rolling(counts_per_second(chat_times, duration), 5, np.mean)  # 5 s okno
        # min. ~0,5 wiadomości/s ponad normę, żeby losowe wahania nie udawały ekscytacji
        parts["chat"] = np.clip(zscore_local(chat, min_spread=0.5), 0, None)
    if scene_times:
        parts["scenes"] = np.clip(_rolling(counts_per_second(scene_times, duration), 5, np.sum), 0, 3)
    if transcript and transcript.sentences:
        parts["words"] = np.clip(word_hits(transcript, duration), 0, 3)

    total = sum(getattr(weights, name) * arr for name, arr in parts.items())
    return _rolling(np.asarray(total, dtype=np.float32), 3, np.mean), parts


def _snap(t: float, transcript: Transcript | None, max_shift: float, to_start: bool) -> float:
    if not transcript or not transcript.sentences:
        return t
    edges = [s.start if to_start else s.end for s in transcript.sentences]
    best = min(edges, key=lambda e: abs(e - t))
    return best if abs(best - t) <= max_shift else t


def pick_candidates(
    scores: np.ndarray,
    parts: dict[str, np.ndarray],
    duration: float,
    count: int,
    rules: ClipRules = ClipRules(),
    transcript: Transcript | None = None,
) -> list[Candidate]:
    if len(scores) == 0:
        return []
    order = np.argsort(scores)[::-1]
    cutoff = max(rules.min_score, rules.min_rel * float(scores.max()))
    peaks: list[int] = []
    for idx in order:
        if scores[idx] < cutoff:  # dalej już nic ciekawego
            break
        if all(abs(int(idx) - p) >= rules.min_gap for p in peaks):
            peaks.append(int(idx))
        if len(peaks) >= count:
            break

    out = []
    for p in peaks:
        start = _snap(max(0.0, p - rules.pre), transcript, rules.snap, to_start=True)
        end = _snap(min(duration, p + rules.post), transcript, rules.snap, to_start=False)
        if end - start > rules.max_len:
            start = end - rules.max_len
        if end - start < rules.min_len:
            start = max(0.0, end - rules.min_len)
            end = min(duration, start + rules.min_len)
        reasons = [name for name, arr in parts.items() if p < len(arr) and arr[p] >= 1.0]
        text = transcript.text_between(start, end) if transcript else ""
        out.append(Candidate(round(start, 2), round(end, 2), float(p), float(scores[p]), reasons, text))
    return sorted(out, key=lambda c: -c.score)
