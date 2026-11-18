"""Rozpoznawanie mowy (lokalnie, za darmo) przez faster-whisper.

Wynik jest zapisywany w pliku cache, więc przy ponownym cięciu tego samego
streamu nie trzeba czekać drugi raz.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Sentence:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    words: list[Word]
    sentences: list[Sentence]

    def text_between(self, start: float, end: float) -> str:
        return " ".join(w.text for w in self.words if start <= w.start < end).strip()

    def words_between(self, start: float, end: float) -> list[Word]:
        return [w for w in self.words if w.start >= start and w.end <= end]

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({
            "words": [asdict(w) for w in self.words],
            "sentences": [asdict(s) for s in self.sentences],
        }, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        data = json.loads(path.read_text())
        return cls([Word(**w) for w in data["words"]], [Sentence(**s) for s in data["sentences"]])


EMPTY = Transcript([], [])


def transcribe(video: str, cache: Path, model_size: str = "small", language: str | None = None) -> Transcript:
    if cache.exists():
        log.info("Transkrypcja z cache: %s", cache)
        return Transcript.load(cache)

    from faster_whisper import WhisperModel  # import tu, bo ładowanie trwa

    log.info("Rozpoznawanie mowy (model %s). Przy 5 h streamu to może potrwać...", model_size)
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    segments, info = model.transcribe(video, word_timestamps=True, vad_filter=True, language=language)
    words: list[Word] = []
    sentences: list[Sentence] = []
    for seg in segments:
        sentences.append(Sentence(seg.start, seg.end, seg.text.strip()))
        for w in seg.words or []:
            words.append(Word(w.start, w.end, w.word.strip()))
        if len(sentences) % 200 == 0:
            log.info("  ... %.0f min", seg.end / 60)
    log.info("Język: %s, słów: %d", info.language, len(words))
    result = Transcript(words, sentences)
    result.save(cache)
    return result
