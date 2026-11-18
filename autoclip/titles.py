"""Wybór najlepszych klipów, tytuły i hashtagi.

Z AI (Claude): model czyta transkrypcję kandydatów, wybiera te, które mają
sens jako samodzielny Short, pisze chwytliwy tytuł, tekst-haczyk na pierwsze
sekundy i hashtagi, i może dociąć granice. Bez AI: kolejność po punktach.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import anthropic

from .highlights import Candidate

log = logging.getLogger(__name__)


@dataclass
class ClipPlan:
    start: float
    end: float
    title: str
    hook: str = ""
    hashtags: list[str] = field(default_factory=list)
    score: float = 0.0


SYSTEM = """You pick the best moments from a long stream for YouTube Shorts / TikTok.
You get candidate segments with timestamps and their transcript (may be empty or messy).
Pick segments that work standalone: clear setup and payoff, funny, shocking or hype.
Reject segments where nothing understandable happens. For each pick write:
- title: catchy, max 70 chars, in the requested language, no clickbait lies
- hook: 2-5 words shown on screen in the first seconds, in the requested language
- hashtags: 3-6 relevant, without '#'
- start/end: you may tighten the segment, but stay inside the candidate bounds
Order picks from best to worst."""

SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "start", "end", "title", "hook", "hashtags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}


def fallback_plans(cands: list[Candidate], count: int, context: str) -> list[ClipPlan]:
    plans = []
    for i, c in enumerate(cands[:count], 1):
        h, rem = divmod(int(c.peak), 3600)
        when = f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"
        title = f"{context} #{i}" if context else f"Najlepszy moment #{i}"
        plans.append(ClipPlan(c.start, c.end, f"{title} ({when})",
                              score=c.score, hashtags=["shorts", "stream"]))
    return plans


def plan_with_ai(cands: list[Candidate], count: int, context: str, language: str,
                 model: str, client: anthropic.Anthropic | None = None) -> list[ClipPlan]:
    client = client or anthropic.Anthropic()
    listing = [
        {"id": i, "start": c.start, "end": c.end, "signals": c.reasons, "transcript": c.text[:1500]}
        for i, c in enumerate(cands)
    ]
    prompt = (
        f"Stream context: {context or 'unknown'}\nLanguage for titles: {language}\n"
        f"Pick up to {count} clips.\n\nCandidates:\n{json.dumps(listing, ensure_ascii=False)}"
    )
    resp = client.beta.messages.create(
        model=model,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model odmówił")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    plans = []
    for p in json.loads(text)["picks"][:count]:
        if not 0 <= p["id"] < len(cands):
            continue
        c = cands[p["id"]]
        start = min(max(float(p["start"]), c.start), c.end - 5)
        end = max(min(float(p["end"]), c.end), start + 5)
        plans.append(ClipPlan(start, end, p["title"], p["hook"],
                              [h.lstrip("#") for h in p["hashtags"]], c.score))
    return plans


def make_plans(cands: list[Candidate], count: int, context: str = "", language: str = "polski",
               use_ai: bool = True, model: str = "claude-opus-5-5") -> list[ClipPlan]:
    if use_ai and cands:
        try:
            plans = plan_with_ai(cands, count, context, language, model)
            if plans:
                return plans
        except (anthropic.APIError, RuntimeError, ValueError, KeyError) as e:
            log.warning("AI niedostępne (%s), biorę klipy po punktach", e)
        except Exception as e:  # np. brak klucza API
            log.warning("AI niedostępne (%s), biorę klipy po punktach", e)
    return fallback_plans(cands, count, context)
