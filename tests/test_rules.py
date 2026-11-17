import pytest

from sniper.filters.rules import check_text, normalize


@pytest.mark.parametrize("title,desc,expected", [
    ("iPhone 15 Pro Max 256GB", "Atrapa telefonu, idealna do sklepu", "atrapa / dummy"),
    ("iPhone 14 Pro", "dummy phone, non working display model", "atrapa / dummy"),
    ("iPhone 13", "to jest d u m m y", "atrapa / dummy"),
    ("iPhone 13", "a.t.r.a.p.a", "atrapa / dummy"),
    ("iPhone 15", "Sprzedam samo pudełko po telefonie", "samo pudełko / opakowanie"),
    ("Pudełko po iPhone 15 Pro", "", "samo pudełko / opakowanie"),
    ("PS5", "box only, empty box", "samo pudełko / opakowanie"),
    ("AirPods Pro", "Tylko etui ładujące", "samo akcesorium"),
    ("iPhone 12", "na części, nie włącza się", "na części / zepsute"),
    ("iPhone 12", "blokada iCloud, nie znam hasła", "blokada (iCloud / simlock)"),
    ("Nike Dunk", "replika 1:1 bardzo dobra jakość", "podróbka"),
    ("Stone Island", "nieoryginalna, ale wygląda super", "podróbka"),
])
def test_hard_rules_reject(title, desc, expected):
    assert expected in check_text(title, desc).hard


@pytest.mark.parametrize("title,desc", [
    ("iPhone 13 Pro 128GB", "Bez blokady iCloud, w zestawie pudełko i etui. Oryginalny."),
    ("Nike Air Max", "100% oryginał, nie podróbka, nie replika"),
    ("Samsung S23", "Telefon nieuszkodzony, brak blokady simlock"),
    ("Kurtka TNF", "Oryginalna, z metkami, pudełko w komplecie"),
    ("iPhone 14", "no icloud lock, works perfectly"),
])
def test_legit_listings_pass(title, desc):
    assert check_text(title, desc).hard == []


def test_soft_rules_add_points():
    r = check_text("iPhone 14", "Kontakt tylko przez WhatsApp, przelew na konto")
    reasons = [reason for reason, _ in r.soft]
    assert "kontakt poza Vinted" in reasons
    assert "przelew bezpośredni" in reasons
    assert r.soft_points >= 50


def test_normalize_strips_polish_chars():
    assert normalize("Łódź, PUDEŁKO!") == "lodz pudelko"
