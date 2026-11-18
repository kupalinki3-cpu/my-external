"""Renderowanie Shorta: pion 9:16, napisy słowo po słowie, efekt na końcu.

Układy obrazu:
- blur:    całe wideo na środku, tło to rozmyte powiększenie (nic nie ucina)
- crop:    wycięty środek kadru na cały ekran (dobre do gier, gdy akcja jest na środku)
- facecam: kamerka na górze, gra na dole (podajesz, gdzie na streamie jest kamerka)

Efekt końcowy (ostatnie ~1,2 s): błysk, najazd kamery (zoom), opcjonalnie
przyspieszenie i tekst, np. "Obserwuj po więcej".
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .transcribe import Word

W, H = 1080, 1920


@dataclass
class RenderStyle:
    layout: str = "blur"                  # blur | crop | facecam
    facecam: tuple[float, float, float, float] | None = None  # x, y, w, h jako ułamki kadru
    subtitles: bool = True
    uppercase: bool = True
    font: str = "Arial"
    end_effect: bool = True
    end_seconds: float = 1.2
    end_zoom: float = 0.35                # o ile powiększyć (0.35 = 35%)
    end_speed: float = 1.0                # >1 przyspiesza końcówkę, np. 1.5
    outro_text: str = ""
    crf: int = 21
    preset: str = "veryfast"
    fps: int = 30
    audio_bitrate: str = "160k"
    # Muzyka w tle: TWÓJ plik (bez praw autorskich). Ścisza się, gdy ktoś mówi.
    music: str | None = None
    music_volume: float = 0.18
    music_offset: float = 0.0             # od której sekundy utworu zacząć (np. od refrenu)

    @classmethod
    def high_quality(cls, source_fps: float, **kw) -> "RenderStyle":
        """Lepsza jakość: płynne 60 kl./s (jeśli źródło ma tyle), mniejsza kompresja."""
        fps = 60 if source_fps >= 50 else 30
        return cls(crf=17, preset="slow", fps=fps, audio_bitrate="192k", **kw)


# ---------- napisy ASS ----------

def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def group_words(words: list[Word], max_words: int = 3, max_gap: float = 0.5,
                max_span: float = 1.6) -> list[list[Word]]:
    groups: list[list[Word]] = []
    for w in words:
        if groups:
            g = groups[-1]
            if len(g) < max_words and w.start - g[-1].end <= max_gap and w.end - g[0].start <= max_span:
                g.append(w)
                continue
        groups.append([w])
    return groups


def build_ass(words: list[Word], clip_start: float, clip_len: float, style: RenderStyle,
              hook: str = "") -> str:
    """Napisy w stylu TikToka: 2-3 słowa naraz, aktualne słowo na żółto i większe."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{style.font},88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,2,60,60,560,1
Style: Hook,{style.font},92,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,8,3,8,60,60,240,1
Style: Outro,{style.font},104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,8,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    fmt = (lambda s: s.upper()) if style.uppercase else (lambda s: s)

    if style.subtitles:
        rel = [Word(w.start - clip_start, w.end - clip_start, _clean(w.text)) for w in words if w.text.strip()]
        for group in group_words(rel):
            for i, w in enumerate(group):
                end = group[i + 1].start if i + 1 < len(group) else w.end + 0.15
                parts = [
                    (r"{\c&H0000FFFF&\fscx112\fscy112}" + fmt(x.text) + r"{\r}") if j == i else fmt(x.text)
                    for j, x in enumerate(group)
                ]
                events.append(f"Dialogue: 0,{_ts(w.start)},{_ts(end)},Sub,,0,0,0,,{' '.join(parts)}")

    if hook:
        events.append(f"Dialogue: 1,{_ts(0)},{_ts(min(3.0, clip_len))},Hook,,0,0,0,,"
                      r"{\fad(150,250)}" + fmt(_clean(hook)))
    if style.outro_text and style.end_effect:
        t0 = max(0.0, clip_len - style.end_seconds)
        events.append(f"Dialogue: 2,{_ts(t0)},{_ts(clip_len + 1)},Outro,,0,0,0,,"
                      r"{\fad(120,0)\t(0,300,\fscx115\fscy115)}" + fmt(_clean(style.outro_text)))
    return header + "\n".join(events) + "\n"


# ---------- ffmpeg ----------

def layout_filter(style: RenderStyle) -> str:
    if style.layout == "crop":
        return f"[0:v]scale=-2:{H}:flags=lanczos,crop={W}:{H}[base]"
    if style.layout == "facecam" and style.facecam:
        x, y, w, h = style.facecam
        top, bottom = int(H * 0.4), H - int(H * 0.4)
        return (
            f"[0:v]split=2[cam][game];"
            f"[cam]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},"
            f"scale={W}:{top}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{top}[top];"
            f"[game]scale=-2:{bottom}:flags=lanczos,crop={W}:{bottom}[bot];"
            f"[top][bot]vstack[base]"
        )
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3[bgb];"
        f"[fg]scale={W}:-2:flags=lanczos[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[base]"
    )


def build_filtergraph(duration: float, style: RenderStyle, audio: bool, subs_file: str | None) -> tuple[str, float]:
    """Zwraca (filtergraph, długość wyniku). Wyjścia: [vout] i (jeśli jest dźwięk) [aout]."""
    chains = [layout_filter(style)]
    v = "base"
    if subs_file:
        chains.append(f"[{v}]ass={subs_file}[subbed]")
        v = "subbed"
    a = "0:a" if audio else None

    end_len = min(style.end_seconds, duration / 4) if style.end_effect else 0.0
    t0 = duration - end_len
    final = duration
    if style.end_effect and style.end_speed > 1.0 and end_len > 0:
        s = style.end_speed
        chains.append(f"[{v}]split=2[v1i][v2i];[v1i]trim=0:{t0:.3f},setpts=PTS-STARTPTS[v1];"
                      f"[v2i]trim={t0:.3f},setpts=(PTS-STARTPTS)/{s}[v2]")
        if audio:
            chains.append(f"[{a}]asplit=2[a1i][a2i];[a1i]atrim=0:{t0:.3f},asetpts=PTS-STARTPTS[a1];"
                          f"[a2i]atrim={t0:.3f},asetpts=PTS-STARTPTS,atempo={s}[a2]")
            chains.append("[v1][a1][v2][a2]concat=n=2:v=1:a=1[vc][ac]")
            a = "ac"
        else:
            chains.append("[v1][v2]concat=n=2:v=1:a=0[vc]")
        v = "vc"
        final = t0 + end_len / s

    if style.end_effect and end_len > 0:
        span = final - t0
        z = style.end_zoom
        chains.append(
            f"[{v}]fps={style.fps},zoompan=z='if(gte(it,{t0:.3f}),1+{z}*min((it-{t0:.3f})/{span:.3f},1),1)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={style.fps},"
            f"drawbox=enable='between(t,{t0:.3f},{t0 + 0.12:.3f})':color=white@0.75:t=fill[vout]"
        )
    else:
        chains.append(f"[{v}]fps={style.fps}[vout]")

    norm = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    if style.music:
        fade_at = max(0.0, final - 1.0)
        chains.append(
            f"[1:a]atrim={style.music_offset:.3f}:{style.music_offset + final:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,volume={style.music_volume},"
            f"afade=t=in:d=0.5,afade=t=out:st={fade_at:.3f}:d=1[mus]"
        )
        if audio:
            # Ducking: muzyka cichnie, gdy w nagraniu ktoś mówi albo jest głośno
            chains.append(f"[{a}]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[voice][sc]")
            chains.append("[mus][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350[duck]")
            chains.append(f"[voice][duck]amix=inputs=2:duration=first:normalize=0,{norm}[aout]")
        else:
            chains.append(f"[mus]{norm}[aout]")
    elif audio:
        chains.append(f"[{a}]{norm}[aout]")
    return ";".join(chains), final


def render_clip(video: str, start: float, end: float, out: Path, style: RenderStyle,
                words: list[Word], hook: str = "", audio: bool = True) -> Path:
    duration = end - start
    with tempfile.TemporaryDirectory() as tmp:
        subs = None
        if (style.subtitles and words) or hook or style.outro_text:
            (Path(tmp) / "subs.ass").write_text(build_ass(words, start, duration, style, hook), encoding="utf-8")
            subs = "subs.ass"  # ścieżka względna: ffmpeg działa w katalogu tmp, bez problemów z cytowaniem
        graph, _ = build_filtergraph(duration, style, audio, subs)
        cmd = [
            "ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-i", str(Path(video).resolve()),
        ]
        if style.music:
            # -stream_loop: krótki utwór zapętla się do długości klipu
            cmd += ["-stream_loop", "-1", "-i", str(Path(style.music).resolve())]
        cmd += ["-filter_complex", graph, "-map", "[vout]"]
        if audio or style.music:
            cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", style.audio_bitrate]
        cmd += ["-c:v", "libx264", "-preset", style.preset, "-crf", str(style.crf), "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(out.resolve())]
        res = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg: {res.stderr[-1500:]}")
    return out
