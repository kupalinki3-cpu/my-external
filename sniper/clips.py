"""Komenda /klip: auto-clipper uruchamiany z Discorda.

/klip zrodlo:<link albo nazwa pliku z folderu nagrań>
Bot robi klipy w tle, pokazuje postęp i wrzuca każdy gotowy klip na kanał
z tytułem i hashtagami do skopiowania. Pełna jakość zostaje na dysku.
Discord ma limit wielkości plików (bez Nitro 10 MB), więc jeśli klip jest
większy, na kanał idzie lżejsza wersja do podglądu.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path

import discord
from discord import app_commands

from autoclip.__main__ import parse_args
from autoclip.media import probe_duration
from autoclip.pipeline import ClipError, run, slugify

from .config import Config

log = logging.getLogger(__name__)

PREFIX = "clipdel"
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".webm", ".avi"}

LAYOUTS = [
    app_commands.Choice(name="rozmyte tło (całe wideo)", value="blur"),
    app_commands.Choice(name="kadr na środek", value="crop"),
]


def resolve_input(source: str, input_dir: Path) -> str:
    """Link zostawiamy, nazwę pliku szukamy TYLKO w folderze nagrań (bez wychodzenia poza niego)."""
    source = source.strip()
    if re.match(r"https?://", source):
        return source
    base = input_dir.resolve()
    path = (base / source).resolve()
    if base not in path.parents or not path.is_file():
        raise ClipError(f"Nie ma pliku „{source}” w folderze {input_dir}")
    return str(path)


def list_recordings(input_dir: Path, limit: int = 25) -> list[str]:
    if not input_dir.is_dir():
        return []
    files = [f for f in input_dir.iterdir() if f.suffix.lower() in VIDEO_EXT]
    return [f.name for f in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:limit]]


def build_argv(cfg: Config, source: str, out_dir: Path, count: int, layout: str | None,
               context: str, outro: str | None, music: bool) -> list[str]:
    c = cfg.clips
    argv = [source, "--out", str(out_dir), "--clips", str(count), "--layout", layout or c.layout,
            "--quality", c.quality, "--whisper", c.whisper, "--language", c.language]
    if not c.subtitles:
        argv += ["--no-subs"]
    if context:
        argv += ["--context", context]
    if (outro if outro is not None else c.outro):
        argv += ["--outro", outro if outro is not None else c.outro]
    if music and c.music_dir:
        argv += ["--music", c.music_dir]
    if c.end_speed > 1:
        argv += ["--end-speed", str(c.end_speed)]
    if cfg.ai.enabled and cfg.ai.provider == "anthropic":
        argv += ["--model", cfg.ai.model]
    else:
        argv += ["--no-ai"]
    return argv


def make_preview(path: Path, limit_mb: float) -> Path:
    """Wersja klipu mieszcząca się w limicie Discorda (720p, dobrana przepływność)."""
    if path.stat().st_size <= limit_mb * 1024 * 1024 * 0.95:
        return path
    duration = max(probe_duration(str(path)), 1.0)
    audio_kbps = 96
    video_kbps = max(300, int(limit_mb * 8 * 1024 * 0.85 / duration) - audio_kbps)
    out = path.with_name(path.stem + ".preview.mp4")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf", "scale=720:1280:flags=lanczos",
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{video_kbps}k",
        "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-movflags", "+faststart", str(out),
    ], check=True)
    return out


def clip_caption(entry: dict, index: int, total: int) -> str:
    tags = " ".join(f"#{h}" for h in entry.get("hashtags", []))
    start = time.strftime("%H:%M:%S", time.gmtime(entry["start"]))
    lines = [f"**{index}/{total}. {entry['title']}**  (z {start})"]
    lines.append(f"```{entry['title']}\n\n{tags}```" if tags else f"```{entry['title']}```")
    return "\n".join(lines)


def delete_view(job_dir: str, filename: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    custom_id = f"{PREFIX}:{job_dir}/{filename}"[:100]
    view.add_item(discord.ui.Button(label="Usuń klip", emoji="🗑️",
                                    style=discord.ButtonStyle.secondary, custom_id=custom_id))
    return view


class ClipJobs:
    """Pilnuje, żeby naraz szła jedna robota (klipowanie mocno obciąża komputer)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = asyncio.Lock()
        self.out_root = Path(cfg.clips.out_dir)
        self.input_dir = Path(cfg.clips.input_dir)

    async def handle_delete(self, interaction: discord.Interaction, rel: str) -> None:
        root = self.out_root.resolve()
        path = (root / rel).resolve()
        if root not in path.parents:
            await interaction.response.send_message("Zła ścieżka.", ephemeral=True)
            return
        for f in (path, path.with_suffix(".txt"), path.with_name(path.stem + ".preview.mp4")):
            f.unlink(missing_ok=True)
        await interaction.response.edit_message(content=f"🗑️ Usunięto `{path.name}`", attachments=[], view=None)

    async def run_job(self, interaction: discord.Interaction, source: str, count: int,
                      layout: str | None, context: str, outro: str | None, music: bool) -> None:
        if self.lock.locked():
            await interaction.response.send_message("⏳ Już robię klipy z innego nagrania. Spróbuj, jak skończę.")
            return
        async with self.lock:
            try:
                resolved = resolve_input(source, self.input_dir)
            except ClipError as e:
                await interaction.response.send_message(f"❌ {e}")
                return

            await interaction.response.send_message(f"🎬 Zaczynam: `{source[:80]}` ({count} klipów)")
            status = await interaction.original_response()
            channel = interaction.channel
            loop = asyncio.get_running_loop()

            job_name = time.strftime("%Y%m%d-%H%M%S") + "-" + slugify(Path(source).stem, 20)
            out_dir = self.out_root / job_name
            argv = build_argv(self.cfg, resolved, out_dir, count, layout, context, outro, music)
            args = parse_args(argv)
            started = time.time()
            uploads: list[asyncio.Future] = []

            def progress(msg: str) -> None:
                elapsed = (time.time() - started) / 60
                asyncio.run_coroutine_threadsafe(status.edit(content=f"{msg}\n_{elapsed:.0f} min_"), loop)

            async def upload(entry: dict, path: Path, index: int) -> None:
                preview = await asyncio.to_thread(make_preview, path, self.cfg.clips.upload_limit_mb)
                note = "" if preview == path else "\n_Podgląd w niższej jakości, pełna wersja jest na dysku._"
                await channel.send(
                    clip_caption(entry, index, count) + note,
                    file=discord.File(preview, filename=path.name),
                    view=delete_view(job_name, path.name),
                )

            def on_clip(entry: dict, path: Path) -> None:
                index = len(uploads) + 1
                uploads.append(asyncio.run_coroutine_threadsafe(upload(entry, path, index), loop))

            try:
                summary = await asyncio.to_thread(run, args, progress, on_clip)
                for fut in uploads:
                    await asyncio.wrap_future(fut)
                await status.edit(content=(
                    f"✅ Gotowe: {len(summary)} klipów w {(time.time() - started) / 60:.0f} min.\n"
                    f"Pełna jakość: `{out_dir}`\n"
                    "Viralowy dźwięk dodaj w aplikacji TikTok/Shorts przy wrzucaniu."
                ))
            except ClipError as e:
                await status.edit(content=f"❌ {e}")
            except Exception as e:  # pokaż błąd na Discordzie zamiast cicho paść
                log.exception("Błąd klipowania")
                await status.edit(content=f"❌ Coś poszło nie tak: {type(e).__name__}: {str(e)[:300]}")


def register_clip_commands(tree: app_commands.CommandTree, jobs: ClipJobs) -> None:
    @tree.command(name="klip", description="Zrób Shortsy z długiego nagrania albo linku")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        zrodlo="link (YouTube, Twitch VOD...) albo nazwa pliku z folderu nagrań",
        ile="ile klipów (1-20)", uklad="układ obrazu w pionie",
        kontekst="o czym jest nagranie, np. 'GTA 6, pierwsza misja' (lepsze tytuły)",
        outro="tekst na końcu klipu", muzyka="muzyka w tle z Twojego folderu muzyki",
    )
    @app_commands.choices(uklad=LAYOUTS)
    async def klip(inter: discord.Interaction, zrodlo: str,
                   ile: app_commands.Range[int, 1, 20] = 8,
                   uklad: app_commands.Choice[str] | None = None,
                   kontekst: str = "", outro: str | None = None, muzyka: bool = False):
        await jobs.run_job(inter, zrodlo, ile, uklad.value if uklad else None, kontekst, outro, muzyka)

    @klip.autocomplete("zrodlo")
    async def zrodlo_autocomplete(inter: discord.Interaction, current: str):
        names = [n for n in list_recordings(jobs.input_dir) if current.lower() in n.lower()]
        return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]
