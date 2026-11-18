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

## Ochrona przed scamem: kradzione zdjęcia

Każde zdjęcie dostaje "odcisk" (dHash), który nie zmienia się po przeskalowaniu
ani kompresji. Bot zapamiętuje odciski wszystkich ofert, które widzi. Jeśli
zdjęcie pierwszy raz pojawiło się u innego sprzedawcy, oferta dostaje 60 pkt
ryzyka i zawsze trafia do Approve/Deny. Właściciel, który wystawia rzecz ponownie,
nie jest oskarżany, bo liczy się, kto wrzucił zdjęcie pierwszy.

## Spadki cen

Oferty trochę za drogie (do 35% powyżej opłacalnej ceny) bot śledzi przez 14 dni
i sprawdza co 30 minut. Gdy sprzedawca obniży cenę do opłacalnej, przychodzi
alert *„📉 Cena spadła z 2600 na 1500 zł”*.

## Komendy na Discordzie

| Komenda | Co robi |
|---|---|
| `/obserwuj nazwa fraza [rodzaj] [cena_max] [min_zysk] [cena_ref]` | dodaje lub zmienia obserwację |
| `/usun nazwa` | usuwa obserwację |
| `/pauza [nazwa]` / `/wznow [nazwa]` | wstrzymuje jedną obserwację albo cały bot |
| `/lista` | pokazuje obserwacje |
| `/kupione oferta cena` | zapisuje zakup (link albo id z Vinted) |
| `/ogloszenie oferta` | generuje ogłoszenie do odsprzedaży |
| `/sprzedane zakup cena` | zapisuje sprzedaż (numer zakupu albo link) |
| `/stats` | zysk, liczba sprzedanych, średni czas sprzedaży, wyniki wg obserwacji |
| `/raport` | czego bot nauczył się z Twoich Approve/Deny i sugerowany próg ryzyka |
| `/prog wartosc` | zmienia próg wysokiego ryzyka |
| `/klip zrodlo [ile] [uklad] [kontekst] [outro] [muzyka]` | robi Shortsy z nagrania (patrz niżej) |

Komendy zarządzające (obserwuj, usun, pauza, wznow, prog) widzą domyślnie tylko
admini serwera.

## Klipy z Discorda: `/klip`

Ten sam bot robi też Shortsy ze streamów (auto-clipper z folderu `autoclip/`).

- `/klip zrodlo:https://www.twitch.tv/videos/...`: z linku,
- `/klip zrodlo:stream.mp4`: z pliku w folderze `nagrania/`. Bot podpowiada
  nazwy plików, gdy zaczniesz pisać. Pliki spoza tego folderu są niedostępne.

Bot pokazuje postęp (pobieranie, analiza, mowa, render) i wrzuca każdy klip
na kanał od razu, gdy jest gotowy, z tytułem i hashtagami do skopiowania oraz
przyciskiem 🗑️ do usuwania słabych. Pełna jakość zostaje w `clips/`. Discord
bez Nitro przyjmuje pliki do 10 MB, więc większe klipy idą jako podgląd 720p.

Naraz idzie jedna robota, bo klipowanie mocno obciąża komputer. Ustawienia
są w sekcji `clips` configu. Jeśli chcesz tylko klipów, bez snipera, zostaw
puste `watches`.

## Nauka z Twoich decyzji

- `/raport` pokazuje, które powody alertów prawie zawsze zatwierdzasz (fałszywe
  alarmy), i proponuje próg ryzyka najlepiej zgodny z Twoimi decyzjami
  (po 15 ocenach). Raport przychodzi też sam raz w tygodniu.
- Ostatnie decyzje dla danej obserwacji trafiają do AI jako przykłady, więc
  model dopasowuje się do tego, co uważasz za dobrą okazję.

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
3. **Allegro** (opcjonalnie, `allegro.enabled`): podobne oferty z Allegro przez
   oficjalne API, ten sam percentyl. Wymaga aplikacji na
   https://apps.developer.allegro.pl, a wyszukiwanie ofert Allegro daje tylko
   zweryfikowanym aplikacjom. Bez dostępu bot sam wyłączy to źródło.
4. **`reference_price` z configu**, jeśli ustawisz: działa jak górny limit.

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

## Najlepsze okazje na telefon

Okazje z dużym zyskiem (domyślnie ≥ 200 zł) i niskim ryzykiem (≤ 30 pkt),
które szybko schodzą, są oznaczone 🔥. Bot pinguje Cię na Discordzie
(`notify.mention_user_id`) i wysyła push przez [ntfy](https://ntfy.sh)
(`notify.ntfy_topic`). Wymyśl długą, trudną do zgadnięcia nazwę tematu, bo kto ją
zna, ten widzi powiadomienia. Zwykłe oferty przychodzą na kanał bez dźwięku.

## Jak szybko schodzi

Z ofert, które się sprzedały, bot liczy typowy czas sprzedaży i liczbę
sprzedaży na tydzień dla podobnych rzeczy. Widać to przy każdej ofercie.
Rzeczy, które schodzą dłużej niż `max_days_to_sell` (domyślnie 30 dni), dostają
uwagę i nie są oznaczane jako najlepsze okazje. Uwaga: bot liczy czas od
momentu, gdy sam zobaczył ofertę, więc przez pierwsze tygodnie wynik jest zaniżony.

## Ogłoszenie do odsprzedaży

Po `/kupione` bot od razu proponuje tytuł, opis (napisany naturalnie, nie jak
przez AI) i dwie ceny: za ile wystawić i poniżej ilu nie schodzić przy
negocjacjach. Ponownie: `/ogloszenie oferta:<link>`. Bez AI dostajesz prosty szablon.

## Diagnostyka

Przed pierwszym uruchomieniem:

```bash
python -m sniper.check config.yaml
```

Sprawdza, czy Vinted odpowiada (i nie blokuje), jakie pola zwraca, czy działa
token Discorda, kanał, klucz do AI i Allegro. Niczego nie wysyła ani nie kupuje.

## Uruchomienie

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml     # uzupełnij kanał i obserwacje

export DISCORD_TOKEN=...               # token bota z Discord Developer Portal
export ANTHROPIC_API_KEY=...           # klucz do Claude (gdy ai.provider: anthropic)
export ALLEGRO_CLIENT_ID=...           # opcjonalnie, gdy allegro.enabled
export ALLEGRO_CLIENT_SECRET=...
python -m sniper.main config.yaml
```

Bot Discord potrzebuje uprawnień *Send Messages*, *Embed Links* i zaproszenia
ze scope `applications.commands` (dla komend /...).

Testy: `pytest`

## Uwaga

Vinted nie ma publicznego API, a regulamin zabrania botów. Bot używa tych
samych endpointów co strona: mogą się zmienić, a zbyt częste odpytywanie
kończy się blokadą IP lub konta. Nie schodź z `poll_interval_sec` poniżej ~15 s.
