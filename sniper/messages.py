"""Wiadomości do sprzedawców, pisane jak przez zwykłą osobę.

Zamiast od razu kupować, bot najpierw dopytuje (oryginalność, stan),
a przy ofercie z niską marżą od razu proponuje niższą cenę.
Szablony są losowane, żeby każda wiadomość brzmiała trochę inaczej.
"""

from __future__ import annotations

import random

from .models import Decision

GREETINGS = ["hej", "cześć", "hejka", "dzień dobry", "hej hej"]

INTEREST = {
    "m": [
        "jestem zainteresowany kupnem",
        "chętnie bym to wziął",
        "myślę nad kupnem",
        "jestem zainteresowany",
        "wpadło mi w oko",
    ],
    "f": [
        "jestem zainteresowana kupnem",
        "chętnie bym to wzięła",
        "myślę nad kupnem",
        "jestem zainteresowana",
        "wpadło mi w oko",
    ],
}

QUESTIONS = {
    "clothes": [
        "czy ciuch jest oryginalny?",
        "to oryginał?",
        "jest oryginalny? masz może zdjęcie metki?",
        "możesz podesłać zdjęcie metki od środka?",
        "jak z rozmiarem, nie jest zaniżony?",
    ],
    "shoes": [
        "są oryginalne?",
        "to oryginał? masz może zdjęcie metki z języka?",
        "możesz dorzucić zdjęcie podeszwy?",
        "długo chodzone?",
    ],
    "electronics": [
        "wszystko działa bez zarzutu?",
        "nie ma żadnych blokad na koncie?",
        "możesz podesłać zdjęcie jak jest włączony?",
        "jaka kondycja baterii?",
        "był kiedyś naprawiany?",
    ],
    "other": [
        "wszystko jest w komplecie?",
        "jest jakaś wada której nie widać na zdjęciach?",
        "możesz dorzucić jeszcze jedno zdjęcie z bliska?",
    ],
}

OFFERS = {
    "m": [
        "zszedłbyś do {p} zł?",
        "dałbym {p} zł, pasuje?",
        "{p} zł i biorę od razu",
        "byłaby opcja za {p}?",
        "mogę dać {p}, co ty na to?",
    ],
    "f": [
        "zeszłabyś do {p} zł?",
        "dałabym {p} zł, pasuje?",
        "{p} zł i biorę od razu",
        "byłaby opcja za {p}?",
        "mogę dać {p}, co ty na to?",
    ],
}


def _casual(text: str, rng: random.Random) -> str:
    # Ludzie często piszą z małej litery i bez kropki na końcu
    if rng.random() < 0.6:
        text = text[0].lower() + text[1:]
    else:
        text = text[0].upper() + text[1:]
    if text.endswith(".") and rng.random() < 0.7:
        text = text[:-1]
    return text


def build_seller_message(
    kind: str,
    action: Decision,
    offer_price: float | None = None,
    gender: str = "m",
    rng: random.Random | None = None,
) -> str:
    rng = rng or random.Random()
    gender = gender if gender in INTEREST else "m"
    questions = QUESTIONS.get(kind, QUESTIONS["other"])

    parts = [f"{rng.choice(GREETINGS)}, {rng.choice(INTEREST[gender])}."]
    parts.append(rng.choice(questions))
    if action == Decision.OFFER and offer_price:
        parts.append(rng.choice(OFFERS[gender]).format(p=int(offer_price)))
    return _casual(" ".join(parts), rng)
