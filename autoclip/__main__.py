"""Auto-clipper: długi stream -> gotowe Shortsy.

Przykłady:
  python -m autoclip stream.mp4
  python -m autoclip stream.mp4 --chat chat.json --clips 15 --context "GTA 6, pierwszy dzień"
  python -m autoclip https://www.youtube.com/watch?v=... --layout crop --end-speed 1.5
  python -m autoclip stream.mp4 --layout facecam --facecam 0.75,0.0,0.25,0.3
"""

from __future__ import annotations

import argparse
import logging
import sys

from .pipeline import ClipError, download, music_tracks, run, slugify  # noqa: F401 (eksport)

log = logging.getLogger("autoclip")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="autoclip", description="Długi stream -> gotowe Shortsy")
    p.add_argument("input", help="plik wideo albo link (YouTube, Twitch VOD, ...)")
    p.add_argument("--out", default="clips", help="katalog na klipy (domyślnie: clips)")
    p.add_argument("--clips", type=int, default=10, help="ile klipów zrobić")
    p.add_argument("--chat", help="plik JSON z czatem (chat-downloader albo TwitchDownloader)")
    p.add_argument("--context", default="", help="o czym jest stream, pomaga AI w tytułach")
    p.add_argument("--language", default="polski", help="język tytułów")
    p.add_argument("--no-ai", action="store_true", help="bez Claude: klipy po punktach, proste tytuły")
    p.add_argument("--model", default="claude-opus-5-5", help="model Claude do wyboru klipów i tytułów")
    p.add_argument("--whisper", default="small", help="model rozpoznawania mowy: tiny/base/small/medium/large-v3")
    p.add_argument("--speech-language", default=None, help="język mowy, np. pl, en (domyślnie: wykryj)")
    p.add_argument("--no-subs", action="store_true", help="bez napisów (i bez rozpoznawania mowy)")
    p.add_argument("--no-scenes", action="store_true", help="pomiń analizę zmian obrazu (szybciej)")
    p.add_argument("--layout", choices=["blur", "crop", "facecam"], default="blur")
    p.add_argument("--facecam", help="gdzie jest kamerka: x,y,szer,wys jako ułamki, np. 0.75,0,0.25,0.3")
    p.add_argument("--no-end-effect", action="store_true", help="bez efektu na końcu")
    p.add_argument("--end-speed", type=float, default=1.0, help="przyspieszenie końcówki, np. 1.5")
    p.add_argument("--end-zoom", type=float, default=0.35, help="najazd na końcu (0.35 = 35%%)")
    p.add_argument("--outro", default="", help="tekst na końcu, np. 'OBSERWUJ PO WIĘCEJ'")
    p.add_argument("--quality", choices=["normal", "high"], default="high",
                   help="high: 60 kl./s (jeśli źródło ma), mniejsza kompresja; normal: szybciej, mniejsze pliki")
    p.add_argument("--music", help="plik albo katalog z muzyką w tle (losowy utwór na klip). "
                   "Tylko muzyka bez praw autorskich! Viralowe dźwięki dodawaj w aplikacji TikTok/Shorts.")
    p.add_argument("--music-volume", type=float, default=0.18, help="głośność muzyki (0.18 = cicho pod głosem)")
    p.add_argument("--min-len", type=float, default=15)
    p.add_argument("--max-len", type=float, default=59)
    p.add_argument("--dry-run", action="store_true", help="tylko pokaż znalezione momenty, nie renderuj")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        run(args)
    except ClipError as e:
        log.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
