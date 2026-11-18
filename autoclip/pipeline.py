"""Cały proces: nagranie -> momenty -> wybór -> render. Używany z linii komend i z bota Discord."""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import time
import unicodedata
from pathlib import Path
from typing import Callable

from .chat import load_chat_times
from .highlights import ClipRules, pick_candidates, score_timeline
from .media import has_audio, loudness_envelope, probe_duration, probe_fps, scene_changes
from .render import RenderStyle, render_clip
from .titles import make_plans
from .transcribe import EMPTY, transcribe

log = logging.getLogger("autoclip")


class ClipError(Exception):
    """Błąd, który warto pokazać użytkownikowi (zły plik, brak momentów...)."""


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


MUSIC_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def music_tracks(path: str) -> list[Path]:
    p = Path(path)
    if p.is_file():
        return [p]
    return sorted(f for f in p.glob("*") if f.suffix.lower() in MUSIC_EXT) if p.is_dir() else []



def run(
    args: argparse.Namespace,
    progress: Callable[[str], None] = lambda msg: None,
    on_clip: Callable[[dict, Path], None] = lambda entry, path: None,
) -> list[dict]:
    """Robi klipy. progress() dostaje opisy etapów, on_clip() każdy gotowy klip."""
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    if re.match(r"https?://", args.input):
        progress("⬇️ Pobieram nagranie...")
    video = download(args.input, out_dir) if re.match(r"https?://", args.input) else args.input
    if not Path(video).exists():
        raise ClipError(f"Nie ma pliku: {video}")

    duration = probe_duration(video)
    audio = has_audio(video)
    log.info("Wideo: %.0f min, dźwięk: %s", duration / 60, "tak" if audio else "nie")

    progress(f"🎬 Wideo: {duration / 60:.0f} min. Analizuję głośność i obraz...")
    log.info("Analiza głośności...")
    loud = loudness_envelope(video) if audio else []
    scenes = [] if args.no_scenes else (log.info("Analiza zmian obrazu...") or scene_changes(video))
    chat = load_chat_times(args.chat) if args.chat else None
    if chat:
        log.info("Czat: %d wiadomości", len(chat))

    transcript = EMPTY
    if audio and not args.no_subs:
        cache = out_dir / f"{slugify(Path(video).stem)}.transcript.json"
        progress("🗣️ Rozpoznaję mowę (przy długim streamie to trwa najdłużej)...")
        transcript = transcribe(video, cache, args.whisper, args.speech_language)

    import numpy as np
    scores, parts = score_timeline(duration, np.asarray(loud, dtype=np.float32), chat, scenes, transcript)
    rules = ClipRules(min_len=args.min_len, max_len=args.max_len)
    # Bierzemy więcej kandydatów, żeby AI miało z czego wybierać
    cands = pick_candidates(scores, parts, duration, args.clips * 3, rules, transcript)
    if not cands:
        raise ClipError("Nie znalazłem wyraźnych momentów. Spróbuj z czatem albo na innym nagraniu.")
    log.info("Kandydatów: %d", len(cands))

    progress(f"🔎 Znalazłem {len(cands)} kandydatów, wybieram najlepsze...")
    plans = make_plans(cands, args.clips, args.context, args.language,
                       use_ai=not args.no_ai, model=args.model)

    for i, plan in enumerate(plans, 1):
        log.info("%2d. %s  [%s - %s]", i, plan.title,
                 time.strftime("%H:%M:%S", time.gmtime(plan.start)),
                 time.strftime("%H:%M:%S", time.gmtime(plan.end)))
    if args.dry_run:
        return [{"title": p.title, "start": p.start, "end": p.end, "score": round(p.score, 2)} for p in plans]

    opts = dict(
        layout=args.layout,
        facecam=tuple(float(x) for x in args.facecam.split(",")) if args.facecam else None,
        subtitles=not args.no_subs,
        end_effect=not args.no_end_effect,
        end_speed=args.end_speed,
        end_zoom=args.end_zoom,
        outro_text=args.outro,
    )
    if args.quality == "high":
        src_fps = probe_fps(video)
        style = RenderStyle.high_quality(src_fps, **opts)
        log.info("Jakość: wysoka (%d kl./s, źródło %.0f kl./s)", style.fps, src_fps)
    else:
        style = RenderStyle(**opts)
    if style.layout == "facecam" and not style.facecam:
        raise ClipError("--layout facecam wymaga --facecam x,y,szer,wys")

    tracks = music_tracks(args.music) if args.music else []
    if args.music and not tracks:
        raise ClipError(f"Nie znalazłem plików muzyki w {args.music}")

    summary = []
    for i, plan in enumerate(plans, 1):
        out = out_dir / f"{i:02d}-{slugify(plan.title)}.mp4"
        log.info("Renderuję %s", out.name)
        progress(f"🎞️ Renderuję klip {i}/{len(plans)}: {plan.title}")
        words = transcript.words_between(plan.start, plan.end)
        if tracks:
            style.music = str(random.choice(tracks))
            style.music_volume = args.music_volume
            log.info("  muzyka: %s", Path(style.music).name)
        render_clip(video, plan.start, plan.end, out, style, words, plan.hook, audio)
        caption = plan.title + "\n\n" + " ".join(f"#{h}" for h in plan.hashtags)
        out.with_suffix(".txt").write_text(caption, encoding="utf-8")
        entry = {"file": out.name, "title": plan.title, "hook": plan.hook,
                        "hashtags": plan.hashtags, "start": plan.start, "end": plan.end,
                        "music": Path(style.music).name if style.music else None,
                        "score": round(plan.score, 2)}
        summary.append(entry)
        on_clip(entry, out)
    (out_dir / "clips.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Gotowe: %d klipów w %s (%.1f min)", len(summary), out_dir, (time.time() - t_start) / 60)
    return summary
