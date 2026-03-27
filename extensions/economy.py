"""
Economy extension — /daily, /balance, /leaderboard, /currency, on_message
"""

import json
import logging
import random
from datetime import datetime, timedelta, timezone

import hikari
import lightbulb
import miru

from utils.config import COIN_COOLDOWN_SECONDS
from utils.db import (
    add_earned_coins, get_balance, get_daily_earned_today,
    get_daily_info, get_leaderboard, get_user_currency,
    get_weekly_spent, make_week_key, set_user_currency,
    update_daily_claim, get_guild_config,
)
from utils.helpers import (
    get_exchange_rate, get_streak_mult, is_booster,
    is_banned_member, normalize_currency,
)

logger = logging.getLogger("HyperCoin")
plugin = lightbulb.Plugin("Economy")


# ── Leaderboard view ──────────────────────────────────────────────────────────

class LeaderboardView(miru.View):
    def __init__(self, pages: list, current: int = 0):
        super().__init__(timeout=60.0)
        self.pages   = pages
        self.current = current

    async def _update(self, ctx: miru.ViewContext):
        await ctx.edit_response(embed=self.pages[self.current], components=self)

    @miru.button(label="<<<", style=hikari.ButtonStyle.DANGER)
    async def first(self, ctx: miru.ViewContext, _):
        self.current = 0
        await self._update(ctx)

    @miru.button(label="<", style=hikari.ButtonStyle.PRIMARY)
    async def prev(self, ctx: miru.ViewContext, _):
        self.current = max(0, self.current - 1)
        await self._update(ctx)

    @miru.button(label=">", style=hikari.ButtonStyle.PRIMARY)
    async def nxt(self, ctx: miru.ViewContext, _):
        self.current = min(len(self.pages) - 1, self.current + 1)
        await self._update(ctx)

    @miru.button(label=">>>", style=hikari.ButtonStyle.DANGER)
    async def last(self, ctx: miru.ViewContext, _):
        self.current = len(self.pages) - 1
        await self._update(ctx)


# ── Commands ──────────────────────────────────────────────────────────────────

@plugin.command
@lightbulb.command("daily", "Claim your daily coin reward")
@lightbulb.implements(lightbulb.SlashCommand)
async def daily_cmd(ctx: lightbulb.SlashContext):
    bot = ctx.bot
    db  = bot.d.db
    cfg = await get_guild_config(db, ctx.guild_id)
    if is_banned_member(cfg["banned_role"], ctx.member):
        await ctx.respond("You are banned from using this bot.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    now    = datetime.now(timezone.utc)
    info   = await get_daily_info(db, ctx.guild_id, ctx.user.id)
    streak = info["streak"] if info and info["streak"] else 0

    if info and info["last_daily"]:
        last = datetime.fromisoformat(info["last_daily"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        hours = (now - last).total_seconds() / 3600
        if hours < 24:
            next_ts = int((last + timedelta(hours=24)).timestamp())
            await ctx.respond(
                f"⏳ Already claimed! Come back <t:{next_ts}:R>.",
                flags=hikari.MessageFlag.EPHEMERAL
            )
            return
        if hours > 48:
            streak = 0

    boosting   = is_booster(ctx.member)
    amt        = random.randint(1, 20) * (2 if boosting else 1)
    new_streak = min(streak + 1, 7)

    await update_daily_claim(db, ctx.guild_id, ctx.user.id, now.isoformat(), new_streak)
    async with bot.d.user_locks[ctx.user.id]:
        await add_earned_coins(db, ctx.guild_id, ctx.user.id, amt, boosting, new_streak,
                               track_progress=False)

    mult    = get_streak_mult(new_streak)
    next_ts = int((now + timedelta(hours=24)).timestamp())

    emb = hikari.Embed(title="🎁 Daily Reward!", color=0xFFD700)
    emb.set_thumbnail(ctx.user.avatar_url or ctx.user.default_avatar_url)
    emb.description = f"You received **{amt:,} coins**!"
    emb.add_field("🔥 Streak",     f"{new_streak} day{'s' if new_streak != 1 else ''}",  inline=True)
    emb.add_field("✨ Multiplier", f"×{mult}",                                             inline=True)
    emb.add_field("⚡ Booster",    "Yes — 2× daily!" if boosting else "No",                inline=True)
    emb.add_field("⏰ Next Claim", f"<t:{next_ts}:R>",                                     inline=False)
    emb.timestamp = now
    await ctx.respond(embed=emb)


@plugin.command
@lightbulb.option("user", "Check another user's balance", type=hikari.User, required=False)
@lightbulb.command("balance", "Check your coin balance and stats")
@lightbulb.implements(lightbulb.SlashCommand)
async def balance_cmd(ctx: lightbulb.SlashContext):
    bot    = ctx.bot
    db     = bot.d.db
    target = ctx.options.user or ctx.user

    # Block bots
    if target.is_bot:
        await ctx.respond("❌ Bots don't have coin balances.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    bal  = await get_balance(db, ctx.guild_id, target.id)
    cfg  = await get_guild_config(db, ctx.guild_id)
    ppu  = cfg["price_per_usd"] or 100
    usd  = bal / ppu
    pref = await get_user_currency(db, ctx.guild_id, target.id)

    val_str = f"${usd:.2f} USD"
    if pref not in ("USD", "COINS"):
        rate = await get_exchange_rate(bot, pref)
        if rate:
            val_str = f"{usd * rate:.2f} {pref}"

    try:
        member   = await bot.rest.fetch_member(ctx.guild_id, target.id)
        boosting = is_booster(member)
    except Exception:
        boosting = False

    daily_limit  = 400 if boosting else 200
    earned_today = await get_daily_earned_today(db, ctx.guild_id, target.id)
    filled       = int((earned_today / daily_limit) * 10)
    bar          = "█" * filled + "░" * (10 - filled)

    info      = await get_daily_info(db, ctx.guild_id, target.id)
    streak    = info["streak"] if info else 0
    mult      = get_streak_mult(streak)

    now        = datetime.now(timezone.utc)
    wk         = make_week_key(target.id, now)
    week_spent = await get_weekly_spent(db, ctx.guild_id, target.id, wk)

    emb = hikari.Embed(title="<a:balance:1486881268502102066> Balance", color=0xFFD700)
    emb.set_author(name=target.username)
    emb.set_thumbnail(target.avatar_url or target.default_avatar_url)
    emb.add_field("<:coins:1486881548069507164> Coins",      f"{bal:,}",                                   inline=True)
    emb.add_field("<:Value:1486881823995723786> Value",      val_str,                                       inline=True)
    emb.add_field("<:Multiplier:1486881692290646167> Multiplier", f"×{mult}",                              inline=True)
    emb.add_field("<:Streak:1486881911606612087> Streak",    f"{streak} day{'s' if streak != 1 else ''}",  inline=True)
    emb.add_field("<:Booster:1486882041378111639> Booster",
                  "Yes" if boosting else "No",                           inline=True)
    emb.add_field("<a:weeklyspend:1486882130909728828> Weekly Spend", f"${week_spent}/$20",                inline=True)
    emb.add_field(
        f"<a:dailyprogress:1486882218369482772> Daily Progress  ({earned_today}/{daily_limit})",
        f"`{bar}`",
        inline=False
    )
    emb.timestamp = now
    await ctx.respond(embed=emb)


@plugin.command
@lightbulb.command("leaderboard", "See the top coin earners")
@lightbulb.implements(lightbulb.SlashCommand)
async def leaderboard_cmd(ctx: lightbulb.SlashContext):
    bot  = ctx.bot
    rows = await get_leaderboard(bot.d.db, ctx.guild_id)
    if not rows:
        await ctx.respond("No data yet.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    pages = []
    for i in range(0, len(rows), 10):
        emb = hikari.Embed(title="🏆 Leaderboard", color=0xFFD700)
        emb.description = "\n".join(
            f"**#{r}** <@{row['user_id']}>: {row['coins']:,} coins"
            for r, row in enumerate(rows[i:i+10], start=i+1)
        )
        total = (len(rows) - 1) // 10 + 1
        emb.set_footer(text=f"Page {i//10 + 1}/{total}")
        pages.append(emb)

    view = LeaderboardView(pages)
    await ctx.respond(embed=pages[0], components=view)
    bot.d.miru.start_view(view)


@plugin.command
@lightbulb.option("currency",
    "Currency code, country name, or 'coins' to reset (e.g. USD, Egypt, euro, coins)",
    required=True)
@lightbulb.command("currency", "Set your preferred display currency")
@lightbulb.implements(lightbulb.SlashCommand)
async def currency_cmd(ctx: lightbulb.SlashContext):
    bot = ctx.bot
    db  = bot.d.db
    raw = ctx.options.currency.strip()

    code = normalize_currency(raw)
    if code is None:
        await ctx.respond(
            f"❌ Could not recognise **{raw}**. Try a currency code (`EUR`, `EGP`), "
            "a country name (`Egypt`, `France`), or `coins` to reset.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    if code == 'COINS':
        await set_user_currency(db, ctx.guild_id, ctx.user.id, 'USD')
        await ctx.respond(
            "✅ Display currency reset to **coins** (default).",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    rate = await get_exchange_rate(bot, code)
    if rate is None:
        await ctx.respond(
            f"❌ Currency **{code}** is not supported by our exchange rate provider.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    await set_user_currency(db, ctx.guild_id, ctx.user.id, code)
    await ctx.respond(
        f"✅ Display currency set to **{code}** (1 USD ≈ {rate:.4f} {code}).",
        flags=hikari.MessageFlag.EPHEMERAL
    )


@plugin.command
@lightbulb.command("help", "Show all available commands")
@lightbulb.implements(lightbulb.SlashCommand)
async def help_cmd(ctx: lightbulb.SlashContext):
    from utils.config import MOD_ROLE_ID, OWNER_ID
    is_mod = MOD_ROLE_ID in ctx.member.role_ids or ctx.user.id == OWNER_ID
    emb = hikari.Embed(title="📚 Hyper Coin — Commands", color=0x5865F2)
    emb.add_field(
        "👤 User",
        "• `/daily` — Claim daily reward\n"
        "• `/balance` — Coins, stats, progress\n"
        "• `/leaderboard` — Top earners\n"
        "• `/buy` — Purchase a gift\n"
        "• `/currency` — Set display currency\n"
        "• `/help` — This message"
    )
    if is_mod:
        emb.add_field(
            "🛡️ Staff",
            "• `/coins` — Add/remove coins\n"
            "• `/uncounted` — Manage excluded channels\n"
            "• `/bannedrole` — Set banned role\n"
            "• `/allowedrole` — Manage allowed earning roles\n"
            "• `/setapproval` — Set purchase approval channel\n"
            "• `/setlog` — Set admin log channel\n"
            "• `/setprice` — Set gift prices\n"
            "• `/drop` — Drop coins in channel\n"
            "• `/customize` — Change bot avatar/banner (Owner only)"
        )
    emb.set_footer(text="💡 Earn coins by chatting (1 coin per 25 s) and daily rewards.")
    await ctx.respond(embed=emb, flags=hikari.MessageFlag.EPHEMERAL)


# ── Message listener ──────────────────────────────────────────────────────────

@plugin.listener(hikari.GuildMessageCreateEvent)
async def on_message(event: hikari.GuildMessageCreateEvent):
    if event.is_bot or not event.guild_id:
        return

    now     = datetime.now(timezone.utc)
    content = event.content or ""

    if event.app.d.spam.detect(event.author_id, content, now):
        return

    cooldowns = event.app.d.coin_cooldowns
    last      = cooldowns.get(event.author_id)
    if last and (now - last).total_seconds() < COIN_COOLDOWN_SECONDS:
        return
    cooldowns[event.author_id] = now

    db  = event.app.d.db
    cfg = await get_guild_config(db, event.guild_id)

    uncounted = json.loads(cfg["uncounted_channels"] or "[]")
    if event.channel_id in uncounted:
        return

    if event.member:
        if is_banned_member(cfg["banned_role"], event.member):
            return
        allowed = json.loads(cfg["allowed_roles"] or "[]")
        if allowed and not any(r in event.member.role_ids for r in allowed):
            return

    boosting = is_booster(event.member)
    info     = await get_daily_info(db, event.guild_id, event.author_id)
    streak   = info["streak"] if info else 0

    async with event.app.d.user_locks[event.author_id]:
        await add_earned_coins(db, event.guild_id, event.author_id, 1, boosting, streak)


# ── Extension loader ──────────────────────────────────────────────────────────

def load(bot: lightbulb.BotApp):
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp):
    bot.remove_plugin(plugin)
