"""Czat ze streamu: gdy czat nagle przyspiesza, coś się dzieje.

Obsługiwane formaty JSON:
- chat-downloader (pip install chat-downloader): lista obiektów z `time_in_seconds`
- TwitchDownloaderCLI: {"comments": [{"content_offset_seconds": ...}]}
"""

from __future__ import annotations

import json
from pathlib import Path


def load_chat_times(path: str | Path) -> list[float]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "comments" in data:
        return sorted(float(c["content_offset_seconds"]) for c in data["comments"])
    if isinstance(data, list):
        times = []
        for m in data:
            t = m.get("time_in_seconds", m.get("time", m.get("offset")))
            if t is not None and float(t) >= 0:
                times.append(float(t))
        return sorted(times)
    raise ValueError("Nieznany format czatu")
