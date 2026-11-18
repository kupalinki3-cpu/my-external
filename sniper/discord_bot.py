"""Bot Discord: wysyła oferty z przyciskami Approve / Deny.

Approve -> bot odpisuje gotową wiadomością do sprzedawcy (pytanie o
oryginalność/stan, przy OFFER także propozycja ceny) i linkiem do oferty.
Deny -> oferta odrzucona, decyzja zapisana w bazie (przyda się do strojenia filtrów).
"""

from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable

import discord
from discord import app_commands

from .config import Config, Watch
from .db import Database
from .feedback import build_report, format_report, format_stats, profit_stats
from .messages import build_seller_message
from .models import Decision, Evaluation, RiskLevel

log = logging.getLogger(__name__)

PREFIX = "sniper"

COLORS = {
    Decision.BUY: discord.Color.green(),
    Decision.OFFER: discord.Color.blue(),
    Decision.REVIEW: discord.Color.red(),
}
TITLES = {
    Decision.BUY: "🟢 Okazja: cena mocno w dół",
    Decision.OFFER: "🔵 Do negocjacji: zaproponuj niższą cenę",
    Decision.REVIEW: "🔴 Wysokie ryzyko scamu: zdecyduj",
}
RISK_LABEL = {RiskLevel.LOW: "niskie", RiskLevel.MEDIUM: "średnie", RiskLevel.HIGH: "WYSOKIE"}
ACTION_LABEL = {Decision.BUY: "kupno", Decision.OFFER: "oferta -%"}


def build_embeds(ev: Evaluation) -> list[discord.Embed]:
    l = ev.listing
    cur = l.currency or "zł"
    embed = discord.Embed(
        title=l.title[:250] or f"Oferta {l.id}",
        url=l.url or None,
        description=f"{ev.alert_note}\n{TITLES[ev.decision]}" if ev.alert_note else TITLES[ev.decision],
        color=COLORS[ev.decision],
    )
    embed.add_field(name="Cena", value=f"**{l.price:.0f} {cur}**")
    if ev.reference_price:
        value = ev.market_note or f"{ev.reference_price:.0f} {cur}"
        embed.add_field(name="Realna cena odsprzedaży",
                        value=f"{value}\noferta = {l.price / ev.reference_price:.0%} tej ceny")
    if ev.expected_profit is not None:
        embed.add_field(name="Zysk (szac.)", value=f"{ev.expected_profit:.0f} {cur}")
    if ev.planned_action == Decision.OFFER and ev.offer_price:
        embed.add_field(name="Proponowana cena", value=f"{ev.offer_price:.0f} {cur}")
    embed.add_field(name="Ryzyko", value=f"{RISK_LABEL[ev.risk_level]} ({ev.risk_score}/100)")
    if ev.planned_action:
        embed.add_field(name="Plan", value=ACTION_LABEL[ev.planned_action])

    seller = l.seller
    seller_txt = seller.login or "?"
    if seller.feedback_count is not None:
        seller_txt += f" · {seller.feedback_count} opinii"
    embed.add_field(name="Sprzedawca", value=seller_txt, inline=False)

    if l.description:
        desc = l.description if len(l.description) < 600 else l.description[:600] + "…"
        embed.add_field(name="Opis", value=desc, inline=False)
    if ev.ai:
        embed.add_field(name="AI", value=ev.ai.summary[:1000] or "-", inline=False)
    if ev.reasons:
        embed.add_field(name="Uwagi", value="\n".join(f"• {r}" for r in ev.reasons)[:1000], inline=False)
    if l.photos:
        embed.set_image(url=l.photos[0])
    embed.set_footer(text=f"{l.watch} · id {l.id}")

    # Do 3 dodatkowych zdjęć: embedy z tym samym url łączą się w galerię
    embeds = [embed]
    for photo in l.photos[1:4]:
        extra = discord.Embed(url=l.url or None)
        extra.set_image(url=photo)
        embeds.append(extra)
    return embeds


def build_view(listing_id: int, disabled: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Approve", style=discord.ButtonStyle.success,
        custom_id=f"{PREFIX}:approve:{listing_id}", disabled=disabled,
    ))
    view.add_item(discord.ui.Button(
        label="Deny", style=discord.ButtonStyle.danger,
        custom_id=f"{PREFIX}:deny:{listing_id}", disabled=disabled,
    ))
    return view


def parse_listing_id(text: str) -> int | None:
    """Link do oferty Vinted albo samo id."""
    m = re.search(r"/items/(\d+)", text) or re.fullmatch(r"\s*(\d+)\s*", text)
    return int(m.group(1)) if m else None


KIND_CHOICES = [
    app_commands.Choice(name="ciuchy", value="clothes"),
    app_commands.Choice(name="buty", value="shoes"),
    app_commands.Choice(name="elektronika", value="electronics"),
    app_commands.Choice(name="inne", value="other"),
]


class SniperBot(discord.Client):
    def __init__(self, cfg: Config, db: Database, scanner: Callable[["SniperBot"], Awaitable[None]]):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.db = db
        self._scanner = scanner
        self.tree = app_commands.CommandTree(self)
        self._register_commands()

    async def setup_hook(self) -> None:
        if self.cfg.discord.guild_id:
            guild = discord.Object(id=self.cfg.discord.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.loop.create_task(self._run_scanner())

    async def _run_scanner(self) -> None:
        await self.wait_until_ready()
        try:
            await self._scanner(self)
        except Exception:
            log.exception("Skaner padł")

    async def _channel(self):
        cid = self.cfg.discord.channel_id
        return self.get_channel(cid) or await self.fetch_channel(cid)

    async def post(self, ev: Evaluation) -> int | None:
        channel = await self._channel()
        msg = await channel.send(embeds=build_embeds(ev), view=build_view(ev.listing.id))
        return msg.id

    async def post_text(self, text: str) -> None:
        channel = await self._channel()
        await channel.send(text[:2000])

    def _watch_kind(self, name: str) -> str:
        return next((w.kind for w, _ in self.db.list_watches() if w.name == name), "other")

    # ---------- przyciski Approve / Deny ----------

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        parts = custom_id.split(":")
        if len(parts) != 3 or parts[0] != PREFIX:
            return
        action, listing_id = parts[1], int(parts[2])
        user = str(interaction.user)

        row = self.db.get_decision(listing_id)
        if row is None:
            await interaction.response.send_message("Nie znam tej oferty.", ephemeral=True)
            return
        if not self.db.set_review(listing_id, "approved" if action == "approve" else "denied", user):
            await interaction.response.send_message(
                f"Już zdecydowane ({row['review_status']}).", ephemeral=True
            )
            return

        # Wyłącz przyciski i dopisz, kto zdecydował
        embeds = interaction.message.embeds if interaction.message else []
        if embeds:
            status = "✅ Zatwierdzone" if action == "approve" else "❌ Odrzucone"
            embeds[0].set_footer(text=f"{embeds[0].footer.text} · {status} przez {user}")
        await interaction.response.edit_message(embeds=embeds, view=build_view(listing_id, disabled=True))

        if action == "approve":
            planned = Decision(row["planned_action"]) if row["planned_action"] else Decision.BUY
            text = build_seller_message(
                kind=self._watch_kind(row["watch"]),
                action=planned,
                offer_price=row["offer_price"],
                gender=self.cfg.messages.gender,
            )
            await interaction.followup.send(
                f"**Napisz do sprzedawcy:** {row['url']}\n```{text}```\n"
                "Kup dopiero, jak odpowie i wszystko się zgadza. "
                f"Po zakupie: `/kupione oferta:{listing_id} cena:<zł>`"
            )

    # ---------- komendy ----------

    def _register_commands(self) -> None:
        tree, db, cfg = self.tree, self.db, self.cfg
        admin = app_commands.default_permissions(manage_guild=True)

        @tree.command(name="obserwuj", description="Dodaj albo zmień obserwację")
        @admin
        @app_commands.describe(
            nazwa="krótka nazwa, np. dunki", fraza="czego szukać na Vinted",
            rodzaj="wpływa na pytania do sprzedawcy", cena_max="maks. cena oferty",
            min_zysk="minimalny zysk w zł", cena_ref="opcjonalna górna granica wyceny",
        )
        @app_commands.choices(rodzaj=KIND_CHOICES)
        async def obserwuj(
            inter: discord.Interaction, nazwa: str, fraza: str,
            rodzaj: app_commands.Choice[str] | None = None,
            cena_max: float | None = None, min_zysk: float | None = None,
            cena_ref: float | None = None,
        ):
            w = Watch(name=nazwa, search_text=fraza, price_to=cena_max, min_profit=min_zysk,
                      reference_price=cena_ref, kind=rodzaj.value if rodzaj else "other")
            db.save_watch(w)
            await inter.response.send_message(
                f"👀 Obserwuję **{nazwa}**: „{fraza}”. Pierwszy przebieg zbierze ceny do porównań, alerty od następnego."
            )

        @tree.command(name="usun", description="Usuń obserwację")
        @admin
        async def usun(inter: discord.Interaction, nazwa: str):
            ok = db.delete_watch(nazwa)
            await inter.response.send_message(f"🗑️ Usunięto **{nazwa}**." if ok else f"Nie ma obserwacji **{nazwa}**.")

        @tree.command(name="pauza", description="Wstrzymaj jedną obserwację albo wszystko")
        @admin
        @app_commands.describe(nazwa="puste = wstrzymaj cały bot")
        async def pauza(inter: discord.Interaction, nazwa: str | None = None):
            if nazwa is None:
                db.set_setting("paused", "1")
                await inter.response.send_message("⏸️ Bot wstrzymany. Wznów: `/wznow`")
            elif db.set_watch_paused(nazwa, True):
                await inter.response.send_message(f"⏸️ Wstrzymano **{nazwa}**.")
            else:
                await inter.response.send_message(f"Nie ma obserwacji **{nazwa}**.")

        @tree.command(name="wznow", description="Wznów obserwację albo cały bot")
        @admin
        async def wznow(inter: discord.Interaction, nazwa: str | None = None):
            if nazwa is None:
                db.set_setting("paused", "0")
                await inter.response.send_message("▶️ Bot działa.")
            elif db.set_watch_paused(nazwa, False):
                await inter.response.send_message(f"▶️ Wznowiono **{nazwa}**.")
            else:
                await inter.response.send_message(f"Nie ma obserwacji **{nazwa}**.")

        @tree.command(name="lista", description="Pokaż obserwacje")
        async def lista(inter: discord.Interaction):
            watches = db.list_watches()
            if not watches:
                await inter.response.send_message("Brak obserwacji. Dodaj: `/obserwuj`")
                return
            paused_all = db.get_setting("paused") == "1"
            lines = ["⏸️ **Cały bot wstrzymany**"] if paused_all else []
            for w, paused in watches:
                extra = []
                if w.price_to:
                    extra.append(f"max {w.price_to:.0f} zł")
                if w.min_profit is not None:
                    extra.append(f"zysk ≥ {w.min_profit:.0f} zł")
                icon = "⏸️" if paused else "👀"
                lines.append(f"{icon} **{w.name}**: „{w.search_text}” ({w.kind}) {' · '.join(extra)}")
            await inter.response.send_message("\n".join(lines))

        @tree.command(name="kupione", description="Zapisz zakup (do statystyk zysku)")
        @app_commands.describe(oferta="link albo id oferty z Vinted", cena="ile zapłaciłeś łącznie z wysyłką")
        async def kupione(inter: discord.Interaction, oferta: str, cena: float):
            lid = parse_listing_id(oferta)
            row = db.get_listing(lid) if lid else None
            title = row["title"] if row else oferta[:100]
            watch = row["watch"] if row else "?"
            pid = db.add_purchase(lid, title, watch, cena)
            await inter.response.send_message(
                f"🛒 Zakup **#{pid}**: {title} za {cena:.0f} zł. "
                f"Po sprzedaży: `/sprzedane zakup:{pid} cena:<zł>`"
            )

        @tree.command(name="sprzedane", description="Zapisz sprzedaż")
        @app_commands.describe(zakup="numer zakupu (#) albo link/id oferty", cena="ile dostałeś na rękę")
        async def sprzedane(inter: discord.Interaction, zakup: str, cena: float):
            ref = zakup.strip().lstrip("#")
            purchase = None
            if ref.isdigit() and len(ref) < 6:
                purchase = db.get_purchase(int(ref))
            elif (lid := parse_listing_id(zakup)) is not None:
                purchase = db.find_open_purchase(lid)
            if purchase is None or not db.mark_sold(purchase["id"], cena):
                await inter.response.send_message("Nie znalazłem niesprzedanego zakupu. Sprawdź numer w `/stats`.")
                return
            gain = cena - purchase["buy_price"]
            await inter.response.send_message(f"💰 Sprzedane: {purchase['title']}, zysk **{gain:+.0f} zł**")

        @tree.command(name="stats", description="Twój zysk z zakupów")
        async def stats(inter: discord.Interaction):
            await inter.response.send_message(format_stats(profit_stats(db.purchases())))

        @tree.command(name="raport", description="Czego bot nauczył się z Twoich Approve/Deny")
        async def raport(inter: discord.Interaction):
            thr = cfg.strategy.risk.high
            await inter.response.send_message(format_report(build_report(db.reviewed(), thr), thr)[:2000])

        @tree.command(name="prog", description="Ustaw próg wysokiego ryzyka (domyślnie 60)")
        @admin
        async def prog(inter: discord.Interaction, wartosc: app_commands.Range[int, 10, 100]):
            cfg.strategy.risk.high = wartosc
            db.set_setting("risk_high", str(wartosc))
            await inter.response.send_message(f"🎚️ Próg wysokiego ryzyka: **{wartosc}**")
