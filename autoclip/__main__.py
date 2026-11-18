"""Auto-clipper: długi stream -> gotowe Shortsy.

Przykłady:
  python -m autoclip stream.mp4
  python -m autoclip stream.mp4 --chat chat.json --clips 15 --context "GTA 6, pierwszy dzień"
  python -m autoclip https://www.youtube.com/watch?v=... --layout crop --end-speed 1.5
  python -m autoclip stream.mp4 --layout facecam --facecam 0.75,0.0,0.25,0.3
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import unicodedata
from pathlib import Path

from .chat import load_chat_times
from .highlights import ClipRules, pick_candidates, score_timeline
from .media import has_audio, loudness_envelope, probe_duration, scene_changes
from .render import RenderStyle, render_clip
from .titles import make_plans
from .transcribe import EMPTY, transcribe

log = logging.getLogger("autoclip")


def slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:max_len] or "klip"


def download(url: str, workdir: Path) -> str:
    import yt_dlp

    opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
    }
    log.info("Pobieram %s ...", url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info).rsplit(".", 1)[0] + ".mp4"


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
    p.add_argument("--min-len", type=float, default=15)
    p.add_argument("--max-len", type=float, default=59)
    p.add_argument("--dry-run", action="store_true", help="tylko pokaż znalezione momenty, nie renderuj")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    video = download(args.input, out_dir) if re.match(r"https?://", args.input) else args.input
    if not Path(video).exists():
        log.error("Nie ma pliku: %s", video)
        return 1

    duration = probe_duration(video)
    audio = has_audio(video)
    log.info("Wideo: %.0f min, dźwięk: %s", duration / 60, "tak" if audio else "nie")

    log.info("Analiza głośności...")
    loud = loudness_envelope(video) if audio else []
    scenes = [] if args.no_scenes else (log.info("Analiza zmian obrazu...") or scene_changes(video))
    chat = load_chat_times(args.chat) if args.chat else None
    if chat:
        log.info("Czat: %d wiadomości", len(chat))

    transcript = EMPTY
    if audio and not args.no_subs:
        cache = out_dir / f"{slugify(Path(video).stem)}.transcript.json"
        transcript = transcribe(video, cache, args.whisper, args.speech_language)

    import numpy as np
    scores, parts = score_timeline(duration, np.asarray(loud, dtype=np.float32), chat, scenes, transcript)
    rules = ClipRules(min_len=args.min_len, max_len=args.max_len)
    # Bierzemy więcej kandydatów, żeby AI miało z czego wybierać
    cands = pick_candidates(scores, parts, duration, args.clips * 3, rules, transcript)
    if not cands:
        log.warning("Nie znalazłem wyraźnych momentów. Spróbuj z czatem (--chat) albo na innym nagraniu.")
        return 0
    log.info("Kandydatów: %d", len(cands))

    plans = make_plans(cands, args.clips, args.context, args.language,
                       use_ai=not args.no_ai, model=args.model)

    for i, plan in enumerate(plans, 1):
        log.info("%2d. %s  [%s - %s]", i, plan.title,
                 time.strftime("%H:%M:%S", time.gmtime(plan.start)),
                 time.strftime("%H:%M:%S", time.gmtime(plan.end)))
    if args.dry_run:
        return 0

    style = RenderStyle(
        layout=args.layout,
        facecam=tuple(float(x) for x in args.facecam.split(",")) if args.facecam else None,
        subtitles=not args.no_subs,
        end_effect=not args.no_end_effect,
        end_speed=args.end_speed,
        end_zoom=args.end_zoom,
        outro_text=args.outro,
    )
    if style.layout == "facecam" and not style.facecam:
        log.error("--layout facecam wymaga --facecam x,y,szer,wys")
        return 1

    summary = []
    for i, plan in enumerate(plans, 1):
        out = out_dir / f"{i:02d}-{slugify(plan.title)}.mp4"
        log.info("Renderuję %s", out.name)
        words = transcript.words_between(plan.start, plan.end)
        render_clip(video, plan.start, plan.end, out, style, words, plan.hook, audio)
        caption = plan.title + "\n\n" + " ".join(f"#{h}" for h in plan.hashtags)
        out.with_suffix(".txt").write_text(caption, encoding="utf-8")
        summary.append({"file": out.name, "title": plan.title, "hook": plan.hook,
                        "hashtags": plan.hashtags, "start": plan.start, "end": plan.end,
                        "score": round(plan.score, 2)})
    (out_dir / "clips.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Gotowe: %d klipów w %s (%.1f min)", len(summary), out_dir, (time.time() - t_start) / 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
