# Auto-clipper

Wrzucasz długi stream (plik albo link), a narzędzie samo znajduje najlepsze
momenty i robi z nich gotowe Shortsy / TikToki: pion 9:16, napisy słowo po
słowie, tekst-haczyk na początku i efekt na końcu.

## Jak szuka momentów

Każda sekunda nagrania dostaje punkty za:

| Sygnał | Co oznacza |
|---|---|
| **czat** (najmocniejszy) | czat nagle pisze dużo szybciej niż zwykle |
| **głośność** | krzyk, wybuch, śmiech: wyraźnie głośniej niż tło z ostatnich minut |
| **zmiany obrazu** | nagła akcja, błyski, cięcia |
| **słowa** | „o matko”, „co jest”, „nie wierzę”, „no way”, „let's go”... |

Wokół najlepszych szczytów buduje klipy: ~22 s **przed** szczytem (żeby było
widać, co do niego doprowadziło) i ~8 s po, z granicami dociągniętymi do końca
zdań. Na koniec **Claude** czyta transkrypcję kandydatów, wybiera te, które
mają sens jako samodzielny Short, i pisze tytuł, haczyk oraz hashtagi.

## Instalacja

Potrzebny jest **ffmpeg** (Windows: `winget install ffmpeg`, Mac: `brew install ffmpeg`).

```bash
pip install -r requirements.txt
```

Przy pierwszym uruchomieniu pobierze się model rozpoznawania mowy (~500 MB dla `small`).
Działa lokalnie i za darmo. Z kartą graficzną NVIDIA idzie kilka razy szybciej.

## Użycie

```bash
# podstawowo
python -m autoclip stream.mp4

# z czatem i kontekstem dla lepszych tytułów
python -m autoclip stream.mp4 --chat chat.json --clips 15 --context "GTA 6, pierwsza misja"

# prosto z linku, kadr na środek, przyspieszona końcówka i tekst na koniec
python -m autoclip https://www.twitch.tv/videos/123 --layout crop --end-speed 1.5 --outro "OBSERWUJ PO WIĘCEJ"

# kamerka na górze, gra na dole (kamerka w prawym górnym rogu streamu)
python -m autoclip stream.mp4 --layout facecam --facecam 0.75,0,0.25,0.3

# najpierw tylko zobacz, co znalazł (bez renderowania)
python -m autoclip stream.mp4 --dry-run
```

Wynik w katalogu `clips/`: pliki `.mp4`, do każdego `.txt` z tytułem i hashtagami
do wklejenia, oraz `clips.json` ze wszystkim naraz.

**Czat z Twitcha:** `pip install chat-downloader`, potem
`chat_downloader https://www.twitch.tv/videos/123 --output chat.json`.

## Ważne opcje

| Opcja | Co robi |
|---|---|
| `--clips 10` | ile klipów |
| `--layout blur/crop/facecam` | układ obrazu w pionie |
| `--end-speed 1.5` | przyspieszenie końcówki |
| `--end-zoom 0.35` | siła najazdu na końcu |
| `--outro "TEKST"` | napis na końcu |
| `--no-end-effect` | bez efektu na końcu |
| `--whisper medium` | dokładniejsze napisy (wolniej); `tiny` = szybko, gorzej |
| `--speech-language pl` | wymuś język mowy |
| `--no-ai` | bez Claude (klipy po punktach, proste tytuły) |
| `--no-scenes` | pomiń analizę obrazu (szybciej przy bardzo długich nagraniach) |

## Koszt i czas

- **Claude:** jedno wywołanie na cały stream. Z modelem domyślnym to kilka–kilkanaście groszy.
  Taniej: `--model claude-haiku-5-5`, za darmo: `--no-ai`.
- **Czas dla 5 h streamu:** głośność i obraz to kilka–kilkanaście minut. Rozpoznawanie mowy
  na zwykłym procesorze trwa długo (nawet ~1–2 h dla `small`), na karcie NVIDIA ok. 10–20 min.
  Wynik zostaje w cache, więc ponowne cięcie tego samego streamu jest szybkie.
  Render to ok. 15–30 s na klip.

## Prawa autorskie

Klipuj **własne** nagrania albo streamerów, którzy na to pozwalają (np. przez
programy „clipping campaigns”, gdzie płacą za wyświetlenia). Wrzucanie cudzych
streamów bez zgody kończy się zgłoszeniami praw autorskich, a YouTube nie
pozwala zarabiać na cudzych treściach bez przeróbki.
