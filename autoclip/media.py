"""Odczyt wideo przez ffmpeg: długość, głośność w czasie, zmiany scen."""

from __future__ import annotations

import json
import re
import subprocess

import numpy as np

ENVELOPE_SR = 8000


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def probe_fps(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    num, _, den = out.partition("/")
    return float(num) / float(den or 1) if num and float(den or 1) else 30.0


def has_audio(path: str) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout
    return bool(out.strip())


def loudness_envelope(path: str, hop: float = 1.0) -> np.ndarray:
    """Głośność (dB RMS) w kolejnych oknach po `hop` sekund.

    Czyta dźwięk strumieniowo, więc 5-godzinny stream nie zapycha pamięci.
    """
    samples_per_hop = int(ENVELOPE_SR * hop)
    bytes_per_hop = samples_per_hop * 2
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(ENVELOPE_SR),
         "-f", "s16le", "-"],
        stdout=subprocess.PIPE,
    )
    values: list[float] = []
    buf = b""
    assert proc.stdout is not None
    while chunk := proc.stdout.read(bytes_per_hop * 64):
        buf += chunk
        full = len(buf) // bytes_per_hop * bytes_per_hop
        if full:
            block = np.frombuffer(buf[:full], dtype=np.int16).astype(np.float32).reshape(-1, samples_per_hop)
            rms = np.sqrt(np.mean(block ** 2, axis=1)) + 1e-6
            values.extend((20 * np.log10(rms / 32768)).tolist())
            buf = buf[full:]
    proc.wait()
    return np.array(values, dtype=np.float32)


def scene_changes(path: str, threshold: float = 0.35) -> list[float]:
    """Czasy nagłych zmian obrazu (cięcia, wybuchy). Analiza na małej rozdzielczości, 2 kl./s."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", path, "-an",
         "-vf", f"fps=2,scale=160:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", proc.stderr)]
