"""Diagnostyka: sprawdza, czy bot ma dostęp do Vinted, Discorda, AI i Allegro.

Uruchomienie:  python -m sniper.check config.yaml

Nic nie wysyła na Discorda i nie kupuje. Wynik możesz wkleić do rozmowy z Claude,
sekrety (tokeny, klucze) nie są wypisywane.
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx

from .config import Config, load_config
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


async def check_vinted(cfg: Config) -> None:
    print("\n== Vinted ==")
    if not cfg.watches:
        print(f"{BAD} Brak obserwacji w configu (watches)")
        return
    client = VintedClient(cfg.vinted)
    try:
        home = await client.http.get(client.base, headers={"Accept": "text/html"})
        client._has_session = True
        print(f"{OK if home.status_code == 200 else BAD} Strona główna: HTTP {home.status_code}, "
              f"ciasteczka: {', '.join(sorted(client.http.cookies.keys())) or 'brak'}")
        if home.status_code == 403:
            print("   Prawdopodobnie ochrona antybotowa. Trzeba będzie przejść na przeglądarkę (Playwright).")

        watch = cfg.watches[0]
        resp = await client._get("/api/v2/catalog/items", {
            "search_text": watch.search_text, "order": "newest_first", "per_page": 5, "page": 1,
        })
        items = resp.json().get("items", []) if resp.status_code == 200 else []
        print(f"{OK if items else BAD} Wyszukiwanie „{watch.search_text}”: HTTP {resp.status_code}, ofert: {len(items)}")
        if resp.status_code != 200:
            print(f"   Odpowiedź: {resp.text[:300]}")
            return
        if not items:
            return

        first = items[0]
        print(f"   Pola oferty: {keys(first)}")
        print(f"   price = {first.get('price')!r}, status = {first.get('status')!r}")
        print(f"   user = {keys(first.get('user'))}")
        listing = parse_listing(first, watch.name)
        print(f"   Odczytane: „{listing.title}”, {listing.price} {listing.currency}, "
              f"zdjęć: {len(listing.photos)}, stan: {listing.condition!r}")

        det = await client._get(f"/api/v2/items/{first['id']}")
        item = det.json().get("item") if det.status_code == 200 else None
        print(f"{OK if item else BAD} Szczegóły oferty: HTTP {det.status_code}")
        if item:
            user = item.get("user") or {}
            checks = {
                "opis (description)": bool(item.get("description")),
                "zdjęcia (photos)": bool(item.get("photos")),
                "liczba opinii (user.feedback_count)": "feedback_count" in user,
                "ocena (user.feedback_reputation)": "feedback_reputation" in user,
                "czy sprzedane (is_closed)": "is_closed" in item,
            }
            for name, present in checks.items():
                print(f"   {OK if present else WARN} {name}")
            print(f"   Pola szczegółów: {keys(item)}")
            print(f"   Pola sprzedawcy: {keys(user)}")
        else:
            print(f"   Odpowiedź: {det.text[:300]}")
            page = await client.http.get(listing.url, headers={"Accept": "text/html"}) if listing.url else None
            if page is not None:
                print(f"   Strona oferty HTML: HTTP {page.status_code} (jeśli 200, opis da się czytać ze strony)")
    except httpx.HTTPError as e:
        print(f"{BAD} Błąd połączenia: {e!r}")
    finally:
        await client.close()


async def check_discord(cfg: Config) -> None:
    print("\n== Discord ==")
    token = os.environ.get(cfg.discord.token_env)
    if not token:
        print(f"{BAD} Brak zmiennej {cfg.discord.token_env}")
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
        print(f"{BAD} Brak klucza: ustaw ANTHROPIC_API_KEY ({type(e).__name__})")


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
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
