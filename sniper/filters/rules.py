"""Twarde i miękkie reguły tekstowe.

Twarde reguły odrzucają ofertę od razu (atrapa, samo pudełko, na części...).
Miękkie reguły tylko podbijają ryzyko scamu.

Tekst jest normalizowany (małe litery, bez polskich znaków i interpunkcji),
więc wzorce piszemy w formie "pudelko", "dziala" itd.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Negacja przed słowem ("nie podróbka", "bez blokady", "no box") nie powinna
# odpalać reguły. Każdy lookbehind ma stałą szerokość, więc Python go przyjmie.
NEG = r"(?<!\bnie )(?<!\bbez )(?<!\bbrak )(?<!\bno )(?<!\bnot )(?<!\bwithout )"

HARD_RULES: dict[str, list[str]] = {
    "atrapa / dummy": [
        r"\bdummy\b",
        r"\batrap\w*",
        r"\bmakiet\w*",
        r"\bmock ?up\b",
        r"\battrappe\b",
        r"\bfactice\b",
        r"\bmaquette\b",
        r"\bnon working (display|model|phone)\b",
        r"\bnie ?dzialajac\w* (model|egzemplarz) (wystawow|pokazow)\w*",
    ],
    "samo pudełko / opakowanie": [
        r"\b(tylko|samo|same|sam|puste|pusty|pusta) (pudelk\w*|opakowani\w*|karton\w*)",
        r"\bpudelko po\b",
        r"\bbox only\b",
        r"\bonly (the )?box\b",
        r"\bempty box\b",
        r"\bnur (die |der )?(ovp|karton|verpackung)\b",
        r"\bboite vide\b",
        r"\bseulement (la )?boite\b",
        r"\bbez (telefonu|urzadzenia|zawartosci|konsoli|sluchawek|zegarka)\b",
    ],
    "samo akcesorium": [
        r"\b(tylko|samo|sama|same|sam) (etui|case|szklo|ladowark\w*|kabel|obudow\w*|pasek)\b",
        r"\bcase only\b",
    ],
    "na części / zepsute": [
        r"\bna czesci\b",
        r"\bfor parts\b",
        r"\bnie wlacza sie\b",
        NEG + r"\bnot working\b",
    ],
    "blokada (iCloud / simlock)": [
        NEG + r"\bblokad\w* (icloud|simlock|operator\w*|aktywacj\w*)",
        NEG + r"\bicloud lock\w*",
        NEG + r"\bactivation lock\b",
        r"\bzablokowan\w*",
    ],
    "podróbka": [
        NEG + r"\breplik\w*",
        NEG + r"\bpodrob\w*",
        NEG + r"\bfake\b",
        r"\binspired\b",
        r"\bjak oryginal\w*",
        r"\bnieoryginal\w*",
        r"\bnie oryginal\w*",
    ],
}

# (wzorzec, punkty ryzyka, powód)
SOFT_RULES: list[tuple[str, int, str]] = [
    (r"\b(whatsapp|watsap|telegram|signal)\b", 30, "kontakt poza Vinted"),
    (r"\bpoza (vinted\w*|platform\w*|aplikacj\w*)\b", 30, "transakcja poza Vinted"),
    (r"\bprzelew\w* (bezposredni\w*|na konto)\b", 25, "przelew bezpośredni"),
    (r"\bblik\w*\b", 15, "płatność BLIK"),
    (r"\b(numer|nr) (tel|telefonu)\b", 10, "prośba o numer telefonu"),
    (r"\bzdjeci\w* (poglad\w*|z internetu|z neta)\b", 20, "zdjęcia poglądowe"),
    (r"\bmodel (wystawow\w*|demo\w*|pokazow\w*)\b", 15, "model wystawowy"),
    (r"\bnie ?testowan\w*", 15, "nietestowany"),
    (NEG + r"(?<!\bnie jest )\buszkodzon\w*", 20, "uszkodzony"),
    (r"\bnie dziala\w*", 25, "coś nie działa"),
    (r"\b(pekniet\w*|zbit\w*|rozbit\w*)", 15, "pęknięcia / zbity"),
    (r"\b(wymienion\w*|zamiennik\w*) (ekran|wyswietlacz|bateri)\w*", 10, "części zamienne"),
]

# Słowa kluczowe łapane także w wersji "rozstrzelonej": "d u m m y", "a.t.r.a.p.a"
SPACED_KEYWORDS = {
    "dummy": "atrapa / dummy",
    "atrapa": "atrapa / dummy",
    "makieta": "atrapa / dummy",
    "replika": "podróbka",
    "podrobka": "podróbka",
    "fake": "podróbka",
}

_HARD = {name: [re.compile(p) for p in pats] for name, pats in HARD_RULES.items()}
_SOFT = [(re.compile(p), pts, reason) for p, pts, reason in SOFT_RULES]
_SPACED = re.compile(r"\b(?:[a-z0-9] ){2,}[a-z0-9]\b")


def normalize(text: str) -> str:
    text = text.lower().replace("ł", "l")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class RuleResult:
    hard: list[str] = field(default_factory=list)
    soft: list[tuple[str, int]] = field(default_factory=list)

    @property
    def soft_points(self) -> int:
        return sum(p for _, p in self.soft)


def check_text(title: str, description: str) -> RuleResult:
    text = normalize(f"{title} {description}")
    result = RuleResult()

    for name, patterns in _HARD.items():
        if any(p.search(text) for p in patterns):
            result.hard.append(name)

    for match in _SPACED.finditer(text):
        joined = match.group(0).replace(" ", "")
        for kw, name in SPACED_KEYWORDS.items():
            if kw in joined and name not in result.hard:
                result.hard.append(name)

    for pattern, points, reason in _SOFT:
        if pattern.search(text):
            result.soft.append((reason, points))

    return result
