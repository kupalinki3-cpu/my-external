import asyncio
import subprocess
from pathlib import Path

import pytest

from autoclip.pipeline import ClipError
from sniper.clips import ClipJobs, build_argv, clip_caption, list_recordings, make_preview, resolve_input
from sniper.config import AIConfig, ClipsConfig, Config, DiscordConfig, StrategyConfig, VintedConfig


def make_cfg(tmp_path, **clips) -> Config:
    return Config(vinted=VintedConfig(), discord=DiscordConfig(), ai=AIConfig(enabled=False),
                  strategy=StrategyConfig(), watches=[],
                  clips=ClipsConfig(input_dir=str(tmp_path / "nagrania"), out_dir=str(tmp_path / "clips"),
                                    subtitles=False, quality="normal", **clips))


def test_resolve_input_stays_inside_folder(tmp_path):
    rec = tmp_path / "nagrania"
    rec.mkdir()
    (rec / "stream.mp4").write_bytes(b"x")
    (tmp_path / "sekret.mp4").write_bytes(b"x")
    assert resolve_input("stream.mp4", rec).endswith("stream.mp4")
    assert resolve_input("https://www.twitch.tv/videos/1", rec).startswith("https://")
    for bad in ["../sekret.mp4", "/etc/passwd", "brak.mp4"]:
        with pytest.raises(ClipError):
            resolve_input(bad, rec)
    assert list_recordings(rec) == ["stream.mp4"]


def test_build_argv(tmp_path):
    cfg = make_cfg(tmp_path, outro="OBSERWUJ", music_dir="muzyka", end_speed=1.5)
    argv = build_argv(cfg, "a.mp4", Path("out"), 5, "crop", "GTA 6", None, music=True)
    assert argv[:2] == ["a.mp4", "--out"] and "--no-ai" in argv and "--no-subs" in argv
    assert argv[argv.index("--layout") + 1] == "crop" and argv[argv.index("--outro") + 1] == "OBSERWUJ"
    assert "--music" in argv and argv[argv.index("--end-speed") + 1] == "1.5"
    no_music = build_argv(cfg, "a.mp4", Path("out"), 5, None, "", "", music=False)
    assert "--music" not in no_music and "--outro" not in no_music


def test_preview_fits_discord_limit(tmp_path):
    big = tmp_path / "big.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=8",
                    "-f", "lavfi", "-i", "anoisesrc=d=8", "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(big)], check=True)
    assert big.stat().st_size > 2 * 1024 * 1024
    prev = make_preview(big, limit_mb=1.0)
    assert prev != big and prev.stat().st_size <= 1.0 * 1024 * 1024
    assert make_preview(prev, limit_mb=50) == prev  # mały plik idzie bez zmian


def test_caption():
    text = clip_caption({"title": "Speed robi misję", "hashtags": ["gta6", "shorts"], "start": 3725}, 2, 8)
    assert "2/8" in text and "01:02:05" in text and "#gta6 #shorts" in text


# ---------- cała robota z "Discorda" (udawane obiekty) ----------

class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, content=None, **kw):
        self.edits.append(content)


class FakeResponse:
    def __init__(self, status):
        self.sent, self.status = [], status

    async def send_message(self, content, **kw):
        self.sent.append(content)


class FakeChannel:
    def __init__(self):
        self.posts = []

    async def send(self, content=None, file=None, view=None):
        self.posts.append((content, Path(file.fp.name) if file else None, view))


class FakeInteraction:
    def __init__(self):
        self.status = FakeMessage()
        self.response = FakeResponse(self.status)
        self.channel = FakeChannel()

    async def original_response(self):
        return self.status


def test_full_clip_job_from_discord(tmp_path):
    cfg = make_cfg(tmp_path)
    rec = tmp_path / "nagrania"
    rec.mkdir()
    # 90 s nagrania z głośnym momentem w 50 s
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=90",
        "-f", "lavfi", "-i", "anoisesrc=d=90:c=pink:a=0.02", "-f", "lavfi", "-i", "sine=f=500:d=90",
        "-filter_complex", "[2:a]volume='if(between(t,50,54),0.9,0)':eval=frame[b];[1:a][b]amix=inputs=2:normalize=0[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        str(rec / "stream.mp4"),
    ], check=True)

    jobs = ClipJobs(cfg)
    inter = FakeInteraction()
    asyncio.run(jobs.run_job(inter, "stream.mp4", 3, None, "test", None, False))

    assert inter.response.sent[0].startswith("🎬 Zaczynam")
    assert inter.status.edits[-1].startswith("✅ Gotowe: 1 klipów"), inter.status.edits
    assert any("Renderuję" in (e or "") for e in inter.status.edits)  # postęp był pokazywany
    (content, file, view) = inter.channel.posts[0]
    assert "1/3" in content and file.exists()
    assert view.children[0].custom_id.startswith("clipdel:")

    # przycisk Usuń kasuje plik
    class DelResponse:
        rejected = None

        async def edit_message(self, **kw):
            self.kw = kw

        async def send_message(self, content, **kw):
            self.rejected = content

    class DelInter:
        response = DelResponse()

    rel = view.children[0].custom_id.split(":", 1)[1]
    asyncio.run(jobs.handle_delete(DelInter(), rel))
    assert not file.exists()
    outside = tmp_path / "poza.mp4"
    outside.write_bytes(b"x")
    asyncio.run(jobs.handle_delete(DelInter(), "../poza.mp4"))  # nie wychodzi poza folder
    assert outside.exists() and DelInter.response.rejected == "Zła ścieżka."


def test_busy_lock(tmp_path):
    jobs = ClipJobs(make_cfg(tmp_path))
    inter = FakeInteraction()

    async def go():
        await jobs.lock.acquire()
        await jobs.run_job(inter, "x.mp4", 1, None, "", None, False)
    asyncio.run(go())
    assert "Już robię klipy" in inter.response.sent[0]
