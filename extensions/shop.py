"""
Shop extension — /buy, /drop, /setdrop, ShopApprovalView, DropClaimView
"""

import asyncio
import json
import logging
import random
from datetime import datetime, timezone

import hikari
import lightbulb
import miru

from utils.config import (OWNER_ID, MOD_ROLE_ID, WEEKLY_LIMIT_USD,
                          DEFAULT_SHOP_PRICES)
from utils.db import (
    add_pending_purchase, adjust_coins, get_balance,
    get_guild_config, get_weekly_spent, add_weekly_spent,
    make_week_key, remove_pending_purchase, set_guild_config_field,
)
from utils.helpers import is_banned_member

logger = logging.getLogger("HyperCoin")
plugin = lightbulb.Plugin("Shop")

SHOP_ITEMS = [
    "PayPal", "Steam", "Google Play", "Apple Store",
    "Discord Nitro Basic", "Discord Nitro Boost",
    "Nintendo Card", "Roblox"
]


# ── Purchase approval view ─────────────────────────────────────────────────────

class ShopAcceptModal(miru.Modal, title="Enter PayPal / Gift Card Info"):
    info = miru.TextInput(
        label="Delivery Info (email / username / etc.)",
        placeholder="e.g. paypal@example.com",
        required=True,
        max_length=200
    )

    def __init__(self, purchase_meta: dict):
        super().__init__()
        self._meta = purchase_meta

    async def callback(self, ctx: miru.ModalContext):
        bot    = ctx.client.app
        db     = bot.d.db
        meta   = self._meta

        uid     = meta["user_id"]
        gid     = meta["guild_id"]
        coins   = meta["coins"]
        amt     = meta["amount"]
        rtype   = meta["item_type"]
        msg_id  = meta["message_id"]

        # Deduct coins
        new_bal = await adjust_coins(db, gid, uid, -coins)

        # Weekly spend tracking
        now  = datetime.now(timezone.utc)
        wkey = make_week_key(uid, now)
        await add_weekly_spent(db, gid, uid, wkey, amt)

        # Remove pending
        await remove_pending_purchase(db, msg_id)

        # Edit approval message
        try:
            orig = await bot.rest.fetch_message(meta["channel_id"], msg_id)
            emb  = orig.embeds[0] if orig.embeds else hikari.Embed()
            emb  = emb.add_field("✅ Approved by", f"<@{ctx.user.id}>", inline=True)
            emb  = emb.add_field("📦 Delivery Info", self.info.value,   inline=False)
            emb.color = 0x00FF88
            await bot.rest.edit_message(meta["channel_id"], msg_id, embed=emb, components=[])
        except Exception as e:
            logger.warning(f"Could not edit approval msg: {e}")

        # DM user
        try:
            ch = await bot.rest.create_dm_channel(uid)
            await bot.rest.create_message(
                ch,
                f"🎉 Your **{rtype}** purchase of **${amt}** was **approved**! "
                f"Remaining balance: **{new_bal:,} coins**."
            )
        except Exception:
            pass

        # Log
        cfg = await get_guild_config(db, gid)
        if cfg["log_channel"]:
            try:
                await bot.rest.create_message(
                    cfg["log_channel"],
                    f"✅ <@{ctx.user.id}> approved **${amt} {rtype}** for <@{uid}>. "
                    f"Delivery: `{self.info.value}`"
                )
            except Exception:
                pass

        await ctx.respond(f"✅ Approved! {coins:,} coins deducted from <@{uid}>.", flags=hikari.MessageFlag.EPHEMERAL)


class ShopApprovalView(miru.View):
    def __init__(self, guild_id: int, user_id: int, item_type: str,
                 amount: int, coins: int, channel_id: int, message_id: int):
        super().__init__(timeout=None)
        self._meta = {
            "guild_id":  guild_id,
            "user_id":   user_id,
            "item_type": item_type,
            "amount":    amount,
            "coins":     coins,
            "channel_id": channel_id,
            "message_id": message_id,
        }

    @miru.button(label="✅ Accept", style=hikari.ButtonStyle.SUCCESS)
    async def accept(self, ctx: miru.ViewContext, _):
        if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("❌ You don't have permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        modal = ShopAcceptModal(self._meta)
        await ctx.respond_with_modal(modal)

    @miru.button(label="❌ Reject", style=hikari.ButtonStyle.DANGER)
    async def reject(self, ctx: miru.ViewContext, _):
        if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("❌ You don't have permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        bot  = ctx.client.app
        db   = bot.d.db
        meta = self._meta

        await remove_pending_purchase(db, meta["message_id"])

        # Edit original
        try:
            orig = await bot.rest.fetch_message(meta["channel_id"], meta["message_id"])
            emb  = orig.embeds[0] if orig.embeds else hikari.Embed()
            emb  = emb.add_field("❌ Rejected by", f"<@{ctx.user.id}>", inline=True)
            emb.color = 0xFF3333
            await bot.rest.edit_message(meta["channel_id"], meta["message_id"], embed=emb, components=[])
        except Exception as e:
            logger.warning(f"Could not edit rejection msg: {e}")

        # DM user
        try:
            ch = await bot.rest.create_dm_channel(meta["user_id"])
            await bot.rest.create_message(
                ch,
                f"❌ Your **{meta['item_type']}** purchase of **${meta['amount']}** was **rejected**."
            )
        except Exception:
            pass

        # Log
        cfg = await get_guild_config(db, meta["guild_id"])
        if cfg["log_channel"]:
            try:
                await bot.rest.create_message(
                    cfg["log_channel"],
                    f"❌ <@{ctx.user.id}> rejected **${meta['amount']} {meta['item_type']}** "
                    f"for <@{meta['user_id']}>."
                )
            except Exception:
                pass

        self.stop()
        await ctx.respond("❌ Purchase rejected.", flags=hikari.MessageFlag.EPHEMERAL)


# ── Drop view ─────────────────────────────────────────────────────────────────

class DropView(miru.View):
    def __init__(self, guild_id: int, coins: int, drop_id: str):
        super().__init__(timeout=120.0)
        self.guild_id  = guild_id
        self.coins     = coins
        self.drop_id   = drop_id
        self.claimed   = False

    @miru.button(label="🪙 Claim!", style=hikari.ButtonStyle.SUCCESS)
    async def claim(self, ctx: miru.ViewContext, _):
        if self.claimed:
            await ctx.respond("Already claimed!", flags=hikari.MessageFlag.EPHEMERAL)
            return
        self.claimed = True
        self.stop()

        db = ctx.client.app.d.db
        async with ctx.client.app.d.user_locks[ctx.user.id]:
            from utils.helpers import is_booster, get_streak_mult
            from utils.db import get_daily_info
            boosting = is_booster(ctx.member)
            info     = await get_daily_info(db, self.guild_id, ctx.user.id)
            streak   = info["streak"] if info else 0
            from utils.db import add_earned_coins
            given = await add_earned_coins(db, self.guild_id, ctx.user.id, self.coins, boosting, streak)

        if given:
            emb = hikari.Embed(
                title="🎉 Coins Claimed!",
                description=f"<@{ctx.user.id}> grabbed **{given:,} coins**!",
                color=0xFFD700
            )
        else:
            emb = hikari.Embed(
                title="📊 Daily Limit Reached",
                description="You've hit your daily limit — no coins added.",
                color=0xFF6600
            )

        try:
            await ctx.message.edit(embed=emb, components=[])
        except Exception:
            pass
        await ctx.respond(embed=emb, flags=hikari.MessageFlag.EPHEMERAL)

    async def on_timeout(self):
        try:
            emb = hikari.Embed(
                title="💨 Drop Expired",
                description="Nobody claimed the coins in time!",
                color=0x888888
            )
            msg = self.message
            if msg:
                await msg.edit(embed=emb, components=[])
        except Exception:
            pass


# ── Commands ──────────────────────────────────────────────────────────────────

@plugin.command
@lightbulb.option("item",   "Which item to purchase",         choices=SHOP_ITEMS, required=True)
@lightbulb.option("amount", "Amount in USD to redeem",        type=int,           required=True)
@lightbulb.command("buy", "Redeem coins for a real-world gift")
@lightbulb.implements(lightbulb.SlashCommand)
async def buy_cmd(ctx: lightbulb.SlashContext):
    bot    = ctx.bot
    db     = bot.d.db
    amt    = ctx.options.amount
    rtype  = ctx.options.item

    if amt <= 0:
        await ctx.respond("❌ Amount must be positive.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    cfg = await get_guild_config(db, ctx.guild_id)
    if is_banned_member(cfg["banned_role"], ctx.member):
        await ctx.respond("You are banned from using this bot.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if not cfg["approval_channel"]:
        await ctx.respond("❌ No approval channel set. Ask an admin to use `/setapproval`.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    # Price check
    prices = json.loads(cfg["shop_prices"] or "{}")
    ppu    = prices.get(rtype, cfg["price_per_usd"] or 100)
    coins  = amt * ppu

    bal = await get_balance(db, ctx.guild_id, ctx.user.id)
    if bal < coins:
        await ctx.respond(
            f"❌ You need **{coins:,} coins** but only have **{bal:,}**.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    # Weekly limit
    now  = datetime.now(timezone.utc)
    wkey = make_week_key(ctx.user.id, now)
    ws   = await get_weekly_spent(db, ctx.guild_id, ctx.user.id, wkey)
    if ws + amt > WEEKLY_LIMIT_USD:
        remaining = WEEKLY_LIMIT_USD - ws
        # Next Monday timestamp
        days_ahead = (7 - now.weekday()) % 7 or 7
        next_mon   = now.replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta
        next_mon   = next_mon + timedelta(days=days_ahead)
        next_ts    = int(next_mon.timestamp())
        await ctx.respond(
            f"❌ Weekly limit: **${WEEKLY_LIMIT_USD}/week**. "
            f"You have **${remaining}** left (resets <t:{next_ts}:R>).",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    emb = hikari.Embed(title="🛍️ Purchase Request", color=0xFFD700)
    emb.set_thumbnail(ctx.user.avatar_url or ctx.user.default_avatar_url)
    emb.add_field("👤 User",       ctx.user.mention,              inline=True)
    emb.add_field("🎁 Item",       rtype,                         inline=True)
    emb.add_field("💵 Amount",     f"${amt}",                     inline=True)
    emb.add_field("🪙 Coins",      f"{coins:,} (pending)",        inline=True)
    emb.add_field("💰 Balance",    f"{bal:,}",                    inline=True)
    emb.add_field("📅 Weekly",     f"${ws}/${WEEKLY_LIMIT_USD}",  inline=True)
    emb.timestamp = now

    # Send to approval channel first to get message_id
    view = None  # will fill in after we have message_id
    msg  = await bot.rest.create_message(cfg["approval_channel"], embed=emb)

    # Store in DB
    await add_pending_purchase(
        db, msg.id, ctx.guild_id, cfg["approval_channel"],
        ctx.user.id, rtype, amt, coins
    )

    # Create view with message_id and edit message
    view = ShopApprovalView(ctx.guild_id, ctx.user.id, rtype, amt, coins,
                            cfg["approval_channel"], msg.id)
    await bot.rest.edit_message(cfg["approval_channel"], msg.id, components=view)
    bot.d.miru.start_view(view)

    await ctx.respond(
        f"✅ Request submitted for **${amt} {rtype}** ({coins:,} coins pending). "
        f"Awaiting staff approval!",
        flags=hikari.MessageFlag.EPHEMERAL
    )


@plugin.command
@lightbulb.option("amount", "Coins to drop", type=int, required=True)
@lightbulb.command("drop", "Staff: drop coins for someone to claim")
@lightbulb.implements(lightbulb.SlashCommand)
async def drop_cmd(ctx: lightbulb.SlashContext):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("❌ Staff only.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    if ctx.options.amount <= 0:
        await ctx.respond("❌ Amount must be positive.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    drop_id = f"{ctx.guild_id}_{ctx.channel_id}_{datetime.now(timezone.utc).timestamp()}"
    view    = DropView(ctx.guild_id, ctx.options.amount, drop_id)
    emb     = hikari.Embed(
        title="💸 Coin Drop!",
        description=f"**{ctx.options.amount:,} coins** are up for grabs! Click below to claim!",
        color=0xFFD700
    )
    resp = await ctx.respond(embed=emb, components=view)
    msg  = await resp.message()
    view.message = msg
    ctx.bot.d.miru.start_view(view)


@plugin.command
@lightbulb.option("channel", "Channel for auto-drops", type=hikari.TextableGuildChannel, required=True)
@lightbulb.command("setdrop", "Staff: set the auto-drop channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def setdrop_cmd(ctx: lightbulb.SlashContext):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("❌ Staff only.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    ch = ctx.options.channel
    await set_guild_config_field(ctx.bot.d.db, ctx.guild_id, "drop_channel", ch.id)
    await ctx.respond(f"✅ Auto-drop channel set to {ch.mention}.")


# ── Extension loader ──────────────────────────────────────────────────────────

def load(bot: lightbulb.BotApp):
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp):
    bot.remove_plugin(plugin)
