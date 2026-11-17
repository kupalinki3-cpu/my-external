"""Bot Discord: wysyła oferty z przyciskami Approve / Deny.

Approve -> bot odpisuje gotową wiadomością do sprzedawcy (pytanie o
oryginalność/stan, przy OFFER także propozycja ceny) i linkiem do oferty.
Deny -> oferta odrzucona, decyzja zapisana w bazie (przyda się do strojenia filtrów).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

import discord

from .config import Config
from .db import Database
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
        description=TITLES[ev.decision],
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


class SniperBot(discord.Client):
    def __init__(self, cfg: Config, db: Database, scanner: Callable[["SniperBot"], Awaitable[None]]):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.db = db
        self._scanner = scanner
        self._watch_kinds = {w.name: w.kind for w in cfg.watches}

    async def setup_hook(self) -> None:
        self.loop.create_task(self._run_scanner())

    async def _run_scanner(self) -> None:
        await self.wait_until_ready()
        try:
            await self._scanner(self)
        except Exception:
            log.exception("Skaner padł")

    async def post(self, ev: Evaluation) -> int | None:
        channel = self.get_channel(self.cfg.discord.channel_id) or await self.fetch_channel(
            self.cfg.discord.channel_id
        )
        msg = await channel.send(embeds=build_embeds(ev), view=build_view(ev.listing.id))
        return msg.id

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
                kind=self._watch_kinds.get(row["watch"], "other"),
                action=planned,
                offer_price=row["offer_price"],
                gender=self.cfg.messages.gender,
            )
            await interaction.followup.send(
                f"**Napisz do sprzedawcy:** {row['url']}\n```{text}```\n"
                "Kup dopiero, jak odpowie i wszystko się zgadza."
            )
