"""
Admin extension — staff commands for configuration and coin management
"""

import json
import logging
from datetime import datetime, timezone

import hikari
import lightbulb

from utils.config import OWNER_ID, MOD_ROLE_ID
from utils.db import (
    adjust_coins, get_guild_config,
    set_guild_config_field,
)
from utils.helpers import is_banned_member

logger = logging.getLogger("HyperCoin")
plugin = lightbulb.Plugin("Admin")

SHOP_ITEMS = [
    "PayPal", "Steam", "Google Play", "Apple Store",
    "Discord Nitro Basic", "Discord Nitro Boost",
    "Nintendo Card", "Roblox"
]


def _is_staff(ctx: lightbulb.SlashContext) -> bool:
    return MOD_ROLE_ID in ctx.member.role_ids or ctx.user.id == OWNER_ID


async def _log(bot, channel_id, embed: hikari.Embed) -> None:
    if channel_id:
        try:
            await bot.rest.create_message(channel_id, embed=embed)
        except Exception:
            pass


# ── Coins ─────────────────────────────────────────────────────────────────────

@plugin.command
@lightbulb.option("amount", "Positive = add, negative = remove", type=int,         required=True)
@lightbulb.option("user",   "Target user",                       type=hikari.User, required=True)
@lightbulb.command("coins", "Staff: add or remove coins from a user")
@lightbulb.implements(lightbulb.SlashCommand)
async def coins_cmd(ctx: lightbulb.SlashContext):
    if not _is_staff(ctx):
        await ctx.respond("❌ Staff only.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    target = ctx.options.user
    if target.is_bot:
        await ctx.respond("❌ Cannot modify coins for bots.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    amt = ctx.options.amount
    db  = ctx.bot.d.db

    async with ctx.bot.d.user_locks[target.id]:
        new = await adjust_coins(db, ctx.guild_id, target.id, amt)

    verb   = "added to" if amt >= 0 else "removed from"
    symbol = "+" if amt >= 0 else ""
    await ctx.respond(
        f"💰 **{symbol}{amt:,} coins** {verb} <@{target.id}>. "
        f"New balance: **{new:,} coins**."
    )

    cfg   = await get_guild_config(db, ctx.guild_id)
    color = 0x00FF88 if amt >= 0 else 0xFF3333
    title = "💰 Coins Added" if amt >= 0 else "💰 Coins Removed"
    emb   = hikari.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    emb.add_field("🛡️ Staff",      f"<@{ctx.user.id}>",   inline=True)
    emb.add_field("👤 User",       f"<@{target.id}>",      inline=True)
    emb.add_field("🪙 Change",     f"{symbol}{amt:,}",     inline=True)
    emb.add_field("💰 New Balance", f"{new:,} coins",      inline=True)
    await _log(ctx.bot, cfg["log_channel"], emb)


# ── Channel management ────────────────────────────────────────────────────────

@plugin.command
@lightbulb.option("channel", "Channel to toggle", type=hikari.TextableGuildChannel, required=True)
@lightbulb.command("uncounted", "Staff: toggle a channel from earning coins")
@lightbulb.implements(lightbulb.SlashCommand)
async def uncounted_cmd(ctx: lightbulb.SlashContext):
    if not _is_staff(ctx):
        await ctx.respond("❌ Staff only.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    db      = ctx.bot.d.db
    ch      = ctx.options.channel
    cfg     = await get_guild_config(db, ctx.guild_id)
    current = json.loads(cfg["uncounted_channels"] or "[]")

    if ch.id in current:
        current.remove(ch.id)
        action = "can now earn coins"
    else:
        current.append(ch.id)
        action = "excluded from coin earning"

    await set_guild_config_field(db, ctx.guild_id, "uncounted_channels", json.dumps(current))
    await ctx.respond(f"✅ {ch.mention} is now {action}.")

    emb = hikari.Embed(title="🔇 Channel Toggled", color=0x5865F2,
                       timestamp=datetime.now(timezone.utc))
    emb.add_field("🛡️ Staff",   f"<@{ctx.user.id}>", inline=True)
    emb.add_field("📢 Channel", ch.mention,            inline=True)
    emb.add_field("Status",     action,                 inline=True)
    await _log(ctx.bot, cfg["log_channel"], emb)


@plugin.command
@lightbulb.option("role", "The banned role", type=hikari.Role, required=True)
@lightbulb.command("bannedrole", "Staff: set which role cannot use the bot")
@lightbulb.implements(lightbulb.SlashCommand)
async def bannedrole_cmd(ctx: lightbulb.SlashContext):
    if not _is_staff(ctx):
        await ctx.respond("❌ Staff only.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    role = ctx.options.role
    db   = ctx.bot.d.db
    await set_guild_config_field(db, ctx.guild_id, "banned_role", role.id)
    await ctx.respond(f"✅ Banned role set to **{role.name}**.")

    cfg = await get_guild_config(db, ctx.guild_id)
    emb = hikari.Embed(title="🚫 Banned Role Updated", color=0xFF6600,
                       timestamp=datetime.now(timezone.utc))
    emb.add_field("🛡️ Staff", f"<@{ctx.user.id}>", inline=True)
    emb.add_field("🚫 Role",  role.name,             inline=True)
    await _log(ctx.bot, cfg["log_channel"], emb)


@plugin.command
@lightbulb.option("role", "Role to toggle", type=hikari.Role, required=True)
@lightbulb.command("allowedrole", "Staff: toggle a role that can earn coins (empty = everyone)")
@lightbulb.implements(lightbulb.SlashCommand)
async def allowedrole_cmd(ctx: lightbulb.SlashContext):
    if not _is_staff(ctx):
        await ctx.respond("❌ Staff only.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    db      = ctx.bot.d.db
    role    = ctx.options.role
    cfg     = await get_guild_config(db, ctx.guild_id)
    current = json.loads(cfg["allowed_roles"] or "[]")

    if role.id in current:
        current.remove(role.id)
        action = "removed from allowed earner roles"
    else:
        current.append(role.id)
        action = "added to allowed earner roles"

    await set_guild_config_field(db, ctx.guild_id, "allowed_roles", json.dumps(current))
    await ctx.respond(f"✅ **{role.name}** {action}.")

    emb = hikari.Embed(title="👥 Allowed Role Updated", color=0x5865F2,
                       timestamp=datetime.now(timezone.utc))
    emb.add_field("🛡️ Staff", f"<@{ctx.user.id}>", inline=True)
    emb.add_field("👥 Role",  role.name,             inline=True)
    emb.add_field("Status",   action,                 inline=True)
    await _log(ctx.bot, cfg["log_channel"], emb)


# ── Channel config ────────────────────────────────────────────────────────────

@plugin.command
@lightbulb.option("channel", "Approval channel", type=hikari.TextableGuildChannel, required=True)
@lightbulb.command("setapproval", "Staff: set the purchase approval channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def setapproval_cmd(ctx: lightbulb.SlashContext):
    if not _is_staff(ctx):
        await ctx.respond("❌ Staff only.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    ch  = ctx.options.channel
    db  = ctx.bot.d.db
    await set_guild_config_field(db, ctx.guild_id, "approval_channel", ch.id)
    await ctx.respond(f"✅ Approval channel set to {ch.mention}.")

    cfg = await get_guild_config(db, ctx.guild_id)
    emb = hikari.Embed(title="📋 Approval Channel Set", color=0x5865F2,
                       timestamp=datetime.now(timezone.utc))
    emb.add_field("🛡️ Staff",    f"<@{ctx.user.id}>", inline=True)
    emb.add_field("📋 Channel",  ch.mention,            inline=True)
    await _log(ctx.bot, cfg["log_channel"], emb)


@plugin.command
@lightbulb.option("channel", "Log channel", type=hikari.TextableGuildChannel, required=True)
@lightbulb.command("setlog", "Staff: set the admin log channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def setlog_cmd(ctx: lightbulb.SlashContext):
    if not _is_staff(ctx):
        await ctx.respond("❌ Staff only.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    ch = ctx.options.channel
    await set_guild_config_field(ctx.bot.d.db, ctx.guild_id, "log_channel", ch.id)
    await ctx.respond(f"✅ Log channel set to {ch.mention}.")


# ── Shop pricing ──────────────────────────────────────────────────────────────

@plugin.command
@lightbulb.option("price", "Coins per $1",          type=int,           required=True)
@lightbulb.option("item",  "Item to set price for", choices=SHOP_ITEMS, required=False)
@lightbulb.command("setprice", "Staff: set item price (coins per $1)")
@lightbulb.implements(lightbulb.SlashCommand)
async def setprice_cmd(ctx: lightbulb.SlashContext):
    if not _is_staff(ctx):
        await ctx.respond("❌ Staff only.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    db    = ctx.bot.d.db
    price = ctx.options.price
    item  = ctx.options.item

    if price <= 0:
        await ctx.respond("❌ Price must be positive.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    cfg = await get_guild_config(db, ctx.guild_id)

    if item:
        prices = json.loads(cfg["shop_prices"] or "{}")
        prices[item] = price
        await set_guild_config_field(db, ctx.guild_id, "shop_prices", json.dumps(prices))
        reply    = f"✅ **{item}** now costs **{price} coins per $1**."
        log_item = item
    else:
        await set_guild_config_field(db, ctx.guild_id, "price_per_usd", price)
        reply    = f"✅ Default price set to **{price} coins per $1**."
        log_item = "Default (all items)"

    await ctx.respond(reply)

    emb = hikari.Embed(title="💲 Price Updated", color=0x5865F2,
                       timestamp=datetime.now(timezone.utc))
    emb.add_field("🛡️ Staff", f"<@{ctx.user.id}>",  inline=True)
    emb.add_field("🎁 Item",  log_item,               inline=True)
    emb.add_field("💲 Price", f"{price} coins / $1",  inline=True)
    await _log(ctx.bot, cfg["log_channel"], emb)


# ── Bot customisation ─────────────────────────────────────────────────────────

@plugin.command
@lightbulb.option("banner_file", "Upload a banner image file",      type=hikari.Attachment, required=False)
@lightbulb.option("banner_url",  "URL for the bot banner",          required=False)
@lightbulb.option("pfp_file",    "Upload a profile picture file",   type=hikari.Attachment, required=False)
@lightbulb.option("pfp_url",     "URL for the bot profile picture", required=False)
@lightbulb.command("customize", "Owner: change the bot avatar and/or banner")
@lightbulb.implements(lightbulb.SlashCommand)
async def customize_cmd(ctx: lightbulb.SlashContext):
    if ctx.user.id != OWNER_ID:
        await ctx.respond("❌ Owner only.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    bot         = ctx.bot
    avatar_data = None
    banner_data = None
    changes     = []

    async def _fetch(url: str) -> bytes | None:
        try:
            async with bot.d.http.get(url) as r:
                if r.status != 200:
                    raise ValueError(f"HTTP {r.status}")
                return await r.read()
        except Exception as e:
            await ctx.respond(f"❌ Failed to fetch image: {e}", flags=hikari.MessageFlag.EPHEMERAL)
            return None

    if ctx.options.pfp_file:
        avatar_data = await _fetch(ctx.options.pfp_file.url)
        if avatar_data is None:
            return
        changes.append("avatar")
    elif ctx.options.pfp_url:
        avatar_data = await _fetch(ctx.options.pfp_url)
        if avatar_data is None:
            return
        changes.append("avatar")

    if ctx.options.banner_file:
        banner_data = await _fetch(ctx.options.banner_file.url)
        if banner_data is None:
            return
        changes.append("banner")
    elif ctx.options.banner_url:
        banner_data = await _fetch(ctx.options.banner_url)
        if banner_data is None:
            return
        changes.append("banner")

    if not changes:
        await ctx.respond(
            "❌ Please provide at least one of: `pfp_url`, `pfp_file`, `banner_url`, `banner_file`.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    try:
        kwargs = {}
        if avatar_data:
            kwargs["avatar"] = avatar_data
        if banner_data:
            kwargs["banner"] = banner_data
        await bot.rest.edit_my_user(**kwargs)
        changed = " and ".join(changes)
        await ctx.respond(f"✅ Bot **{changed}** updated!")

        cfg = await get_guild_config(bot.d.db, ctx.guild_id)
        emb = hikari.Embed(title="🎨 Bot Customised", color=0x5865F2,
                           timestamp=datetime.now(timezone.utc))
        emb.add_field("🛡️ Owner",   f"<@{ctx.user.id}>", inline=True)
        emb.add_field("✏️ Changed", changed,               inline=True)
        await _log(bot, cfg["log_channel"], emb)
    except Exception as e:
        await ctx.respond(f"❌ Failed to update: {e}", flags=hikari.MessageFlag.EPHEMERAL)


# ── Ping ──────────────────────────────────────────────────────────────────────

@plugin.command
@lightbulb.command("ping", "Check bot latency")
@lightbulb.implements(lightbulb.SlashCommand)
async def ping_cmd(ctx: lightbulb.SlashContext):
    hb = round(ctx.bot.heartbeat_latency * 1000)
    await ctx.respond(f"🏓 Pong! Latency: **{hb} ms**")


# ── Extension loader ──────────────────────────────────────────────────────────

def load(bot: lightbulb.BotApp):
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp):
    bot.remove_plugin(plugin)
