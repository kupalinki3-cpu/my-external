import asyncio
import json

import httpx

from sniper import check
from sniper.config import Config, DiscordConfig, AIConfig, StrategyConfig, VintedConfig, Watch
from sniper.vinted import VintedClient

ITEM = {"id": 123456789, "title": "iPhone 17 Pro Max", "price": {"amount": "4500.0", "currency_code": "PLN"},
        "url": "https://www.vinted.pl/items/123456789", "status": "Bardzo dobry",
        "photo": {"url": "https://img/1.jpg"}, "user": {"id": 5, "login": "anna"}}


def fake_vinted(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    authed = request.headers.get("authorization") == "Bearer tok123"
    if path == "/":
        return httpx.Response(200, html="<html>home</html>", headers={"set-cookie": "access_token_web=tok123; Path=/"})
    if path == "/catalog":
        return httpx.Response(200, html='<a href="/items/123456789-iphone">x</a><script>"/api/v2/catalog/items"</script>')
    if path == "/web/api/core/catalog/items" and authed:
        return httpx.Response(200, json={"items": [ITEM]})
    if path == "/api/v2/items/123456789/details" and authed:
        return httpx.Response(200, json={"item": {**ITEM, "description": "opis", "photos": []}})
    return httpx.Response(404, html="<title>La page n'existe pas</title>")


def test_probe_finds_working_variant(capsys, monkeypatch):
    cfg = Config(vinted=VintedConfig(), discord=DiscordConfig(), ai=AIConfig(enabled=False),
                 strategy=StrategyConfig(), watches=[Watch(name="w", search_text="iphone 17 pro max")])
    real_init = VintedClient.__init__

    def init(self, c):
        real_init(self, c)
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(fake_vinted), follow_redirects=True)

    monkeypatch.setattr(VintedClient, "__init__", init)
    asyncio.run(check.check_vinted(cfg))
    out = capsys.readouterr().out
    assert "✅ Działa: /web/api/core/catalog/items z tokenem" in out
    assert 'search_path: "/web/api/core/catalog/items"' in out and "bearer_auth: true" in out
    assert 'details_path: "/api/v2/items/{id}/details"' in out
    assert "linków do ofert: 1" in out and "/api/v2/catalog/items" in out


def test_client_uses_configured_path_and_token():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("authorization")))
        return fake_vinted(request)

    client = VintedClient(VintedConfig(search_path="/web/api/core/catalog/items", bearer_auth=True,
                                       details_path="/api/v2/items/{id}/details"))
    client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)

    async def go():
        items = await client.search_newest(Watch(name="w", search_text="iphone"))
        det = await client.item_details(123456789)
        return items, det

    items, det = asyncio.run(go())
    assert items[0]["id"] == 123456789 and det["description"] == "opis"
    assert ("/web/api/core/catalog/items", "Bearer tok123") in seen
