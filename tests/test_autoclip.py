import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from autoclip.chat import load_chat_times
from autoclip.highlights import ClipRules, pick_candidates, score_timeline, zscore_local
from autoclip.render import RenderStyle, build_ass, build_filtergraph, group_words, render_clip
from autoclip.titles import fallback_plans
from autoclip.transcribe import Sentence, Transcript, Word
from autoclip.__main__ import slugify


def flat_loudness(n, events=()):
    rng = np.random.default_rng(0)
    loud = -45 + rng.normal(0, 1.5, n).astype(np.float32)
    for t, length in events:
        loud[t:t + length] = -15
    return loud


def test_loud_events_are_found_and_spaced():
    loud = flat_loudness(1800, events=[(300, 4), (1200, 5)])
    scores, parts = score_timeline(1800, loud)
    cands = pick_candidates(scores, parts, 1800, 5)
    peaks = sorted(round(c.peak) for c in cands)
    assert len(peaks) == 2 and abs(peaks[0] - 301) <= 3 and abs(peaks[1] - 1202) <= 3


def test_quiet_noise_gives_no_clips():
    scores, parts = score_timeline(900, flat_loudness(900))
    assert pick_candidates(scores, parts, 900, 5) == []


def test_chat_burst_beats_loudness_and_clip_has_buildup():
    loud = flat_loudness(1200, events=[(200, 3)])
    chat = list(np.random.default_rng(1).uniform(0, 1200, 600)) + list(np.linspace(800, 806, 200))
    scores, parts = score_timeline(1200, loud, chat_times=chat)
    best = pick_candidates(scores, parts, 1200, 2)[0]
    assert 795 <= best.peak <= 810 and "chat" in best.reasons
    # więcej czasu przed szczytem niż po nim
    assert best.peak - best.start > best.end - best.peak


def test_clip_length_limits_and_snap_to_sentences():
    loud = flat_loudness(600, events=[(300, 3)])
    t = Transcript([], [Sentence(276.5, 280, "a"), Sentence(305, 309.2, "b")])
    scores, parts = score_timeline(600, loud)
    c = pick_candidates(scores, parts, 600, 1, ClipRules(), t)[0]
    assert c.start == 276.5 and c.end == 309.2
    c2 = pick_candidates(scores, parts, 600, 1, ClipRules(pre=80, max_len=59))[0]
    assert c2.duration <= 59


def test_zscore_min_spread():
    flat = np.full(400, -40.0, dtype=np.float32)
    flat[200] = -39  # 1 dB szumu
    assert zscore_local(flat, min_spread=3.0).max() < 1


def test_chat_formats(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps([{"time_in_seconds": 5.0}, {"time_in_seconds": -3}, {"time_in_seconds": 1.5}]))
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"comments": [{"content_offset_seconds": 9}, {"content_offset_seconds": 2}]}))
    assert load_chat_times(a) == [1.5, 5.0]
    assert load_chat_times(b) == [2.0, 9.0]


def test_word_groups_and_ass():
    words = [Word(10.0, 10.3, "o"), Word(10.35, 10.7, "matko"), Word(10.75, 11.0, "co"),
             Word(11.05, 11.3, "jest"), Word(13.0, 13.4, "{dziwne}")]
    groups = group_words(words)
    assert [len(g) for g in groups] == [3, 1, 1]
    ass = build_ass(words, clip_start=10.0, clip_len=5.0, style=RenderStyle(outro_text="obserwuj"), hook="patrz")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert r"{\c&H0000FFFF&\fscx112\fscy112}MATKO{\r}" in ass  # aktualne słowo na żółto
    assert "Dialogue: 0,0:00:00.00," in ass                      # czasy względem początku klipu
    assert "{DZIWNE}" not in ass and "(DZIWNE)" in ass           # klamry nie psują ASS
    assert ",Hook," in ass and "OBSERWUJ" in ass


def test_filtergraph_speed_ramp_duration():
    graph, final = build_filtergraph(30.0, RenderStyle(end_speed=1.5), audio=True, subs_file=None)
    assert abs(final - (30 - 1.2 + 1.2 / 1.5)) < 1e-6
    assert "atempo=1.5" in graph and "zoompan" in graph and "[aout]" in graph
    graph2, final2 = build_filtergraph(30.0, RenderStyle(end_effect=False), audio=False, subs_file=None)
    assert final2 == 30 and "zoompan" not in graph2 and "[aout]" not in graph2


def test_fallback_titles_and_slug():
    from autoclip.highlights import Candidate
    plans = fallback_plans([Candidate(3700, 3730, 3725, 9.0)], 1, "")
    assert plans[0].title.endswith("(1:02:05)")
    assert slugify("Łódź: Speed robi MISJĘ!!") == "lodz-speed-robi-misje"


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("v") / "in.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=12",
        "-f", "lavfi", "-i", "sine=f=300:d=12", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest", str(path),
    ], check=True)
    return path


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.parametrize("style", [
    RenderStyle(layout="blur", end_speed=1.5, outro_text="obserwuj"),
    RenderStyle(layout="crop", end_effect=False),
    RenderStyle(layout="facecam", facecam=(0.7, 0.0, 0.3, 0.3)),
])
def test_render_vertical_clip(sample_video, tmp_path, style):
    out = tmp_path / "clip.mp4"
    words = [Word(2.0, 2.4, "test"), Word(2.5, 2.9, "napisów")]
    render_clip(str(sample_video), 1.0, 9.0, out, style, words, hook="haczyk")
    info = probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    expected = 8 - 1.2 + 1.2 / 1.5 if style.end_speed > 1 else 8
    assert abs(float(info["format"]["duration"]) - expected) < 0.25
