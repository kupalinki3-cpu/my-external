"""Diagnostyka: sprawdza, czy bot ma dostęp do Vinted, Discorda, AI i Allegro.

Uruchomienie:  python -m sniper.check config.yaml

Nic nie wysyła na Discorda i nie kupuje. Wynik możesz wkleić do rozmowy z Claude,
sekrety (tokeny, klucze) nie są wypisywane.
"""

from __future__ import annotations

import asyncio
import re
import sys

import httpx

from .config import Config, apply_api_keys, discord_token, load_config
from .vinted import VintedClient, parse_listing

OK, BAD, WARN = "✅", "❌", "⚠️"


def keys(d: dict | None, depth: int = 1) -> str:
    if not isinstance(d, dict):
        return str(type(d).__name__)
    if depth == 0:
        return "{...}"
    return "{" + ", ".join(
        f"{k}: {keys(v, depth - 1)}" if isinstance(v, dict) else k for k in sorted(d) for v in [d[k]]
    ) + "}"


SEARCH_PATHS = ["/api/v2/catalog/items", "/web/api/core/catalog/items", "/api/v2/catalog/items/"]
DETAIL_PATHS = ["/api/v2/items/{id}", "/api/v2/items/{id}/details", "/web/api/core/items/{id}",
                "/api/v2/item_upload/items/{id}"]


def _json_items(resp: httpx.Response) -> list | None:
    if "json" not in resp.headers.get("content-type", ""):
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


async def probe_search(client: VintedClient, query: str) -> tuple[str, bool, list] | None:
    """Próbuje różnych adresów i trybów logowania. Zwraca pierwszy działający."""
    params = {"search_text": query, "order": "newest_first", "per_page": 5, "page": 1}
    working = None
    for path in SEARCH_PATHS:
        for bearer in (False, True):
            resp = await client._get(path, params, bearer=bearer)
            items = _json_items(resp)
            mark = OK if items else (WARN if items == [] else BAD)
            kind = "JSON" if items is not None else resp.headers.get("content-type", "?").split(";")[0]
            print(f"   {mark} {path} {'+ token' if bearer else '':8} HTTP {resp.status_code}, {kind}, "
                  f"ofert: {len(items) if items else 0}")
            if items and working is None:
                working = (path, bearer, items)
    return working


async def probe_html(client: VintedClient, query: str) -> list[int]:
    """Zwykła strona wyszukiwania: czy da się z niej czytać oferty i jakie API wywołuje."""
    resp = await client.http.get(f"{client.base}/catalog", params={"search_text": query, "order": "newest_first"},
                                 headers={"Accept": "text/html"})
    html = resp.text
    ids = list(dict.fromkeys(int(i) for i in re.findall(r"/items/(\d{6,})", html)))
    apis = sorted(set(re.findall(r"[\"'](/(?:web/)?api/[a-zA-Z0-9_/\-]+)", html)))[:15]
    print(f"   Strona wyszukiwania HTML: HTTP {resp.status_code}, linków do ofert: {len(ids)}, "
          f"__NEXT_DATA__: {'tak' if '__NEXT_DATA__' in html else 'nie'}")
    if apis:
        print(f"   Adresy API widoczne w stronie: {', '.join(apis)}")
    return ids


async def check_vinted(cfg: Config) -> None:
    print("\n== Vinted ==")
    if not cfg.watches:
        print(f"{WARN} Brak obserwacji w configu (watches), testuję na frazie 'iphone'")
    query = cfg.watches[0].search_text if cfg.watches else "iphone"
    client = VintedClient(cfg.vinted)
    try:
        home = await client.http.get(client.base, headers={"Accept": "text/html"})
        client._has_session = True
        print(f"{OK if home.status_code == 200 else BAD} Strona główna: HTTP {home.status_code}, "
              f"ciasteczka: {', '.join(sorted(client.http.cookies.keys())) or 'brak'}")
        if home.status_code == 403:
            print("   Prawdopodobnie ochrona antybotowa. Trzeba będzie przejść na przeglądarkę (Playwright).")
            return

        print(f"\nWyszukiwanie „{query}” (różne warianty):")
        found = await probe_search(client, query)
        html_ids = await probe_html(client, query)

        item_id = None
        if found:
            path, bearer, items = found
            first = items[0]
            item_id = first.get("id")
            print(f"\n{OK} Działa: {path}" + (" z tokenem" if bearer else ""))
            print(f"   Pola oferty: {keys(first)}")
            print(f"   price = {first.get('price')!r}, status = {first.get('status')!r}")
            print(f"   user = {keys(first.get('user'))}")
            listing = parse_listing(first, "test")
            print(f"   Odczytane: „{listing.title}”, {listing.price} {listing.currency}, "
                  f"zdjęć: {len(listing.photos)}, stan: {listing.condition!r}")
        else:
            print(f"\n{BAD} Żaden adres API nie zwrócił ofert.")
            item_id = html_ids[0] if html_ids else None

        if item_id:
            print(f"\nSzczegóły oferty {item_id}:")
            detail_ok = None
            for dpath in DETAIL_PATHS:
                for bearer in (False, True):
                    resp = await client._get(dpath.format(id=item_id), bearer=bearer)
                    is_json = "json" in resp.headers.get("content-type", "")
                    data = resp.json() if is_json and resp.status_code == 200 else None
                    item = (data.get("item") or data) if isinstance(data, dict) else None
                    good = bool(item and ("description" in item or "photos" in item))
                    print(f"   {OK if good else BAD} {dpath} {'+ token' if bearer else '':8} HTTP {resp.status_code}")
                    if good and detail_ok is None:
                        detail_ok = (dpath, bearer, item)
            if detail_ok:
                dpath, bearer, item = detail_ok
                user = item.get("user") or {}
                print(f"   {OK} Pola szczegółów: {keys(item)}")
                print(f"   Pola sprzedawcy: {keys(user)}")
            else:
                page = await client.http.get(f"{client.base}/items/{item_id}", headers={"Accept": "text/html"})
                print(f"   Strona oferty HTML: HTTP {page.status_code}, "
                      f"opis w stronie: {'tak' if 'description' in page.text else 'nie wiadomo'}")

        if found:
            path, bearer, _ = found
            print("\n👉 Wpisz do config.yaml w sekcji vinted:")
            print(f"     search_path: \"{path}\"")
            print(f"     bearer_auth: {'true' if bearer else 'false'}")
            if item_id and detail_ok:
                print(f"     details_path: \"{detail_ok[0]}\"")
    except httpx.HTTPError as e:
        print(f"{BAD} Błąd połączenia: {e!r}")
    finally:
        await client.close()


async def check_discord(cfg: Config) -> None:
    print("\n== Discord ==")
    token = discord_token(cfg)
    if not token:
        print(f"{BAD} Brak tokena: wpisz discord.token w config.yaml")
        return
    async with httpx.AsyncClient(base_url="https://discord.com/api/v10",
                                 headers={"Authorization": f"Bot {token}"}, timeout=15) as http:
        me = await http.get("/users/@me")
        if me.status_code != 200:
            print(f"{BAD} Token odrzucony: HTTP {me.status_code}")
            return
        print(f"{OK} Bot: {me.json().get('username')}")
        ch = await http.get(f"/channels/{cfg.discord.channel_id}")
        print(f"{OK if ch.status_code == 200 else BAD} Kanał {cfg.discord.channel_id}: HTTP {ch.status_code}"
              + (f" (#{ch.json().get('name')})" if ch.status_code == 200 else
                 " - sprawdź channel_id i czy bot jest na serwerze"))


async def check_ai(cfg: Config) -> None:
    print("\n== AI ==")
    if not cfg.ai.enabled:
        print(f"{WARN} Wyłączone (ai.enabled: false)")
        return
    if cfg.ai.provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                tags = (await http.get(f"{cfg.ai.ollama_url}/api/tags")).json()
            names = [m.get("name", "") for m in tags.get("models", [])]
            has = any(n.startswith(cfg.ai.ollama_model) for n in names)
            print(f"{OK if has else BAD} Ollama działa, model {cfg.ai.ollama_model}: "
                  f"{'jest' if has else 'brak, uruchom: ollama pull ' + cfg.ai.ollama_model}")
        except httpx.HTTPError as e:
            print(f"{BAD} Ollama nie odpowiada pod {cfg.ai.ollama_url}: {e!r}")
        return

    import anthropic
    try:
        model = await anthropic.AsyncAnthropic().models.retrieve(cfg.ai.model)
        print(f"{OK} Claude API działa, model: {model.id}")
    except anthropic.AuthenticationError:
        print(f"{BAD} Zły albo brakujący klucz (ANTHROPIC_API_KEY)")
    except anthropic.NotFoundError:
        print(f"{BAD} Model {cfg.ai.model} niedostępny dla tego klucza")
    except anthropic.APIConnectionError as e:
        print(f"{BAD} Brak połączenia z API: {e!r}")
    except anthropic.APIStatusError as e:
        print(f"{BAD} Błąd API: HTTP {e.status_code}")
    except Exception as e:  # np. brak jakichkolwiek danych logowania
        print(f"{BAD} Brak klucza: wpisz ai.api_key w config.yaml albo ustaw ai.enabled: false ({type(e).__name__})")


async def check_allegro(cfg: Config) -> None:
    print("\n== Allegro ==")
    if not cfg.allegro.enabled:
        print(f"{WARN} Wyłączone (allegro.enabled: false)")
        return
    from .allegro import AllegroPrices
    prices = AllegroPrices(cfg.allegro)
    try:
        await prices._get_token()
        print(f"{OK} Token Allegro pobrany")
        comps = await prices.search(cfg.watches[0].search_text) if cfg.watches else []
        print(f"{OK if comps else BAD} Wyszukiwanie: {len(comps)} ofert"
              + ("" if comps else " (brak dostępu do /offers/listing albo pusty wynik)"))
    except Exception as e:
        print(f"{BAD} {e}")
    finally:
        await prices.close()


async def run(cfg: Config) -> None:
    await check_vinted(cfg)
    await check_discord(cfg)
    await check_ai(cfg)
    await check_allegro(cfg)
    print("\nWklej ten wynik do rozmowy, jeśli coś jest na czerwono.")


def main() -> None:
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
    apply_api_keys(cfg)
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
