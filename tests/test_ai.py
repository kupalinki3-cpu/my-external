import asyncio
import json

import httpx

from sniper.config import AIConfig
from sniper.filters.ai import OllamaClassifier, make_classifier, parse_verdict
from sniper.models import AICategory, Listing

ANSWER = {"category": "dummy_replica_model", "confidence": 0.9, "scam_risk": 80,
          "matches_query": False, "red_flags": ["ten sam ekran na zdjęciach"], "summary": "atrapa"}


def test_parse_verdict_handles_garbage():
    assert parse_verdict("nie json") is None
    assert parse_verdict(json.dumps(ANSWER)).category == AICategory.DUMMY


def test_ollama_sends_images_and_parses():
    seen = {}

    def handler(request: httpx.Request):
        if request.url.path == "/api/chat":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"content": json.dumps(ANSWER)}})
        return httpx.Response(200, content=b"\xff\xd8jpeg")

    clf = OllamaClassifier(AIConfig(provider="ollama"))
    clf.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    listing = Listing(1, "iPhone 13 Pro", 500, "PLN", "u", "w", photos=["https://img/1.jpg"])
    v = asyncio.run(clf.classify(listing, "iphone 13 pro"))
    assert v.category == AICategory.DUMMY
    assert len(seen["body"]["messages"][1]["images"]) == 1
    assert seen["body"]["format"]["required"]


def test_make_classifier_picks_provider():
    assert isinstance(make_classifier(AIConfig(provider="ollama")), OllamaClassifier)
