# Vinted Sniper

Bot, który co kilkanaście sekund przegląda najnowsze oferty na Vinted, liczy,
czy da się na nich zarobić, odsiewa atrapy, puste pudełka i scam, a okazje
wysyła na Discorda z przyciskami **Approve / Deny**.

## Jak to działa

```
Vinted (najnowsze) ──► szybki filtr ceny ──► szczegóły oferty ──► twarde reguły
                                                                     │
         Discord ◄── decyzja ◄── punkty ryzyka ◄── AI (tekst+zdjęcia) ◄┘
```

1. **Skaner** pobiera najnowsze oferty dla każdej obserwacji z `config.yaml`.
   Pierwszy przebieg tylko zapisuje to, co już jest, żeby nie zalać kanału.
2. **Szybki filtr ceny**: jeśli nawet po zbiciu ceny o 30% nie ma zysku,
   oferta jest pomijana (bez pobierania szczegółów i bez płacenia za AI).
3. **Twarde reguły** (`sniper/filters/rules.py`): atrapa/dummy, samo pudełko,
   samo etui, na części, blokada iCloud, podróbka. Rozumieją negacje
   ("bez blokady iCloud", "nie podróbka") i rozstrzelone słowa ("d u m m y").
4. **Punkty ryzyka**: podejrzanie niska cena, kontakt poza Vinted, BLIK/przelew,
   sprzedawca bez opinii, mało zdjęć.
5. **AI (Claude)** ogląda zdjęcia i opis, a potem klasyfikuje ofertę: prawdziwy produkt,
   atrapa, pudełko, akcesorium, podróbka, zablokowany albo niepewne. Pewna atrapa odpada od razu.
6. **Decyzja**:
   - 🔴 wysokie ryzyko (≥ 60 pkt): **REVIEW**, czyli Ty decydujesz na Discordzie,
   - 🟢 cena ≤ 65% rynku i zysk ≥ min: **BUY**,
   - 🔵 zysk dopiero po zniżce: **OFFER** (propozycja -30%),
   - reszta: pomijana.

Po kliknięciu **Approve** bot daje gotową wiadomość do sprzedawcy, napisaną
jak przez człowieka, np. *"hej, jestem zainteresowany kupnem. czy ciuch jest
oryginalny? dałbym 210 zł, pasuje?"*. Bot niczego nie kupuje sam: najpierw pytasz,
a kupujesz, jak sprzedawca odpowie.

## Opłacalność

```
koszt    = cena + 0,70 zł + 5% ceny (ochrona kupujących) + wysyłka
sprzedaż = realna cena odsprzedaży × 0,95
zysk     = sprzedaż − koszt
```

**Realna cena odsprzedaży** to najniższa z dostępnych wycen:

1. **Podobne oferty na Vinted**: bot zbiera ogłoszenia z tej samej obserwacji
   (na start kilka stron, potem na bieżąco) i wybiera podobne: zbliżony tytuł
   (np. 128GB ≠ 256GB), najlepiej ten sam stan, bez atrap, pudełek i akcesoriów.
   Bierze **35. percentyl**, czyli cenę niższą niż u 65% konkurencji. Używane
   rzeczy schodzą tanio, a średnia z ogłoszeń jest zawyżona przez ludzi, którzy
   wystawiają drogo i nigdy nie sprzedają.
2. **Ceny sprzedanych ofert**: oferty, które zniknęły z wyników i zostały
   sprzedane (mediana z 30 dni).
3. **`reference_price` z configu**, jeśli ustawisz: działa jak górny limit.

Wszystkie progi zmienisz w sekcji `strategy`.

## AI: API czy lokalny model?

| | Claude API (`anthropic`) | Lokalny (`ollama`) |
|---|---|---|
| Koszt | Haiku 5.5: ~0,5 gr / ofertę, Opus 5.5: ~15 gr / ofertę | 0 zł (prąd) |
| Sprzęt | żaden | karta graficzna z ~8 GB+ VRAM |
| Trafność (atrapy na zdjęciach) | najlepsza | wyraźnie słabsza przy małych modelach |
| Szybkość | 2–5 s | 5–30 s, zależnie od karty |

AI sprawdza tylko oferty, które przeszły filtr ceny i reguły (zwykle kilka
procent wszystkich), więc nawet przy API koszt jest niewielki.

Lokalnie: zainstaluj [Ollama](https://ollama.com), `ollama pull qwen2.5vl:7b`,
w configu ustaw `ai.provider: ollama`.

## Uruchomienie

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml     # uzupełnij kanał i obserwacje

export DISCORD_TOKEN=...               # token bota z Discord Developer Portal
export ANTHROPIC_API_KEY=...           # klucz do Claude (gdy ai.provider: anthropic)
python -m sniper.main config.yaml
```

Bot Discord potrzebuje tylko uprawnień: *Send Messages*, *Embed Links*.

Testy: `pytest`

## Uwaga

Vinted nie ma publicznego API, a regulamin zabrania botów. Bot używa tych
samych endpointów co strona: mogą się zmienić, a zbyt częste odpytywanie
kończy się blokadą IP lub konta. Nie schodź z `poll_interval_sec` poniżej ~15 s.
