"""
Shop extension — /buy, /drop, ShopApprovalView, DropView
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import hikari
import lightbulb
import miru

from utils.config import OWNER_ID, MOD_ROLE_ID, WEEKLY_LIMIT_USD
from utils.db import (
    add_pending_purchase, adjust_coins, get_balance,
    get_guild_config, get_weekly_spent, add_weekly_spent,
    make_week_key, remove_pending_purchase,
    save_pending_drop, remove_pending_drop,
)
from utils.helpers import is_banned_member

logger = logging.getLogger("HyperCoin")
plugin = lightbulb.Plugin("Shop")

SHOP_ITEMS = [
    "PayPal", "Steam", "Google Play", "Apple Store",
    "Discord Nitro Basic", "Discord Nitro Boost",
    "Nintendo Card", "Roblox"
]

# Items that require the user to provide a region
REGION_ITEMS = {"Google Play", "Apple Store", "Nintendo Card", "Roblox"}
# Items that require the user to provide an email
EMAIL_ITEMS  = {"PayPal"}


# ── Purchase approval view ─────────────────────────────────────────────────────

class ShopAcceptModal(miru.Modal, title="Enter Delivery Details"):
    info = miru.TextInput(
        label="Delivery Info (email / username / code)",
        placeholder="e.g. paypal@example.com",
        required=True,
        max_length=200
    )

    def __init__(self, purchase_meta: dict):
        super().__init__()
        self._meta = purchase_meta

    async def callback(self, ctx: miru.ModalContext):
        bot   = ctx.client.app
        db    = bot.d.db
        meta  = self._meta
        uid   = meta["user_id"]
        gid   = meta["guild_id"]
        coins = meta["coins"]
        amt   = meta["amount"]
        rtype = meta["item_type"]
        mid   = meta["message_id"]

        new_bal = await adjust_coins(db, gid, uid, -coins)

        now  = datetime.now(timezone.utc)
        wkey = make_week_key(uid, now)
        await add_weekly_spent(db, gid, uid, wkey, amt)
        await remove_pending_purchase(db, mid)

        try:
            orig = await bot.rest.fetch_message(meta["channel_id"], mid)
            emb  = orig.embeds[0] if orig.embeds else hikari.Embed()
            emb  = emb.add_field("✅ Approved by", f"<@{ctx.user.id}>", inline=True)
            emb  = emb.add_field("📦 Delivery Info", self.info.value,   inline=False)
            emb.color = 0x00FF88
            await bot.rest.edit_message(meta["channel_id"], mid, embed=emb, components=[])
        except Exception as e:
            logger.warning(f"Could not edit approval msg: {e}")

        try:
            ch = await bot.rest.create_dm_channel(uid)
            await bot.rest.create_message(
                ch,
                f"🎉 Your **{rtype}** purchase of **${amt}** was **approved**! "
                f"Remaining balance: **{new_bal:,} coins**."
            )
        except Exception:
            pass

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

        await ctx.respond(
            f"✅ Approved! {coins:,} coins deducted from <@{uid}>.",
            flags=hikari.MessageFlag.EPHEMERAL
        )


class ShopApprovalView(miru.View):
    def __init__(self, guild_id: int, user_id: int, item_type: str,
                 amount: int, coins: int, channel_id: int, message_id: int):
        super().__init__(timeout=None)
        self._meta = {
            "guild_id":   guild_id,
            "user_id":    user_id,
            "item_type":  item_type,
            "amount":     amount,
            "coins":      coins,
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

        try:
            orig = await bot.rest.fetch_message(meta["channel_id"], meta["message_id"])
            emb  = orig.embeds[0] if orig.embeds else hikari.Embed()
            emb  = emb.add_field("❌ Rejected by", f"<@{ctx.user.id}>", inline=True)
            emb.color = 0xFF3333
            await bot.rest.edit_message(
                meta["channel_id"], meta["message_id"], embed=emb, components=[]
            )
        except Exception as e:
            logger.warning(f"Could not edit rejection msg: {e}")

        try:
            ch = await bot.rest.create_dm_channel(meta["user_id"])
            await bot.rest.create_message(
                ch,
                f"❌ Your **{meta['item_type']}** purchase of **${meta['amount']}** was **rejected**."
            )
        except Exception:
            pass

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
    def __init__(self, bot, guild_id: int, coins: int, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.bot      = bot
        self.guild_id = guild_id
        self.coins    = coins
        self.claimed  = False
        self.msg_id   = None   # set after message is sent
        self.chan_id  = None   # set after message is sent

    @miru.button(label="🪙 Claim!", style=hikari.ButtonStyle.SUCCESS)
    async def claim(self, ctx: miru.ViewContext, _):
        if self.claimed:
            await ctx.respond("Already claimed!", flags=hikari.MessageFlag.EPHEMERAL)
            return
        self.claimed = True
        self.stop()

        # Remove from DB
        if self.msg_id:
            await remove_pending_drop(ctx.client.app.d.db, self.msg_id)

        db = ctx.client.app.d.db
        async with ctx.client.app.d.user_locks[ctx.user.id]:
            from utils.helpers import is_booster
            from utils.db import get_daily_info, add_earned_coins
            boosting = is_booster(ctx.member)
            info     = await get_daily_info(db, self.guild_id, ctx.user.id)
            streak   = info["streak"] if info else 0
            given    = await add_earned_coins(
                db, self.guild_id, ctx.user.id, self.coins, boosting, streak
            )

        if given:
            emb = hikari.Embed(
                title="🎉 Coins Claimed!",
                description=f"<@{ctx.user.id}> grabbed **{given:,} coins**!",
                color=0xFFD700
            )
        else:
            emb = hikari.Embed(
                title="📊 Daily Limit Reached",
                description="You've hit your daily chat limit — no coins added.",
                color=0xFF6600
            )

        try:
            await ctx.message.edit(embed=emb, components=[])
        except Exception:
            pass
        await ctx.respond(embed=emb, flags=hikari.MessageFlag.EPHEMERAL)

    async def on_timeout(self):
        # Remove from DB
        if self.msg_id and self.bot and self.bot.d.db:
            try:
                await remove_pending_drop(self.bot.d.db, self.msg_id)
            except Exception:
                pass
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
@lightbulb.option("email",  "Your PayPal email (PayPal only)",                              required=False)
@lightbulb.option("region", "Region/country (Google Play, Apple, Nintendo, Roblox only)",   required=False)
@lightbulb.option("item",   "Which item to purchase",         choices=SHOP_ITEMS,           required=True)
@lightbulb.option("amount", "Amount in USD to redeem",        type=int,                     required=True)
@lightbulb.command("buy", "Redeem coins for a real-world gift")
@lightbulb.implements(lightbulb.SlashCommand)
async def buy_cmd(ctx: lightbulb.SlashContext):
    bot   = ctx.bot
    db    = bot.d.db
    amt   = ctx.options.amount
    rtype = ctx.options.item

    if amt <= 0:
        await ctx.respond("❌ Amount must be positive.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    cfg = await get_guild_config(db, ctx.guild_id)
    if is_banned_member(cfg["banned_role"], ctx.member):
        await ctx.respond("You are banned from using this bot.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if not cfg["approval_channel"]:
        await ctx.respond(
            "❌ No approval channel set. Ask an admin to use `/setapproval`.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    # Validate required extra info
    region = (ctx.options.region or "").strip()
    email  = (ctx.options.email  or "").strip()

    if rtype in REGION_ITEMS and not region:
        await ctx.respond(
            f"❌ **{rtype}** requires a **region** (e.g. US, UK, Egypt). "
            "Use the `region` option.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    if rtype in EMAIL_ITEMS and not email:
        await ctx.respond(
            "❌ **PayPal** requires your **email address**. Use the `email` option.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    # Price
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
        remaining  = WEEKLY_LIMIT_USD - ws
        days_ahead = (7 - now.weekday()) % 7 or 7
        next_mon   = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_mon  += timedelta(days=days_ahead)
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
    if region:
        emb.add_field("🌍 Region", region, inline=True)
    if email:
        emb.add_field("📧 Email",  email,  inline=True)
    emb.timestamp = now

    msg = await bot.rest.create_message(cfg["approval_channel"], embed=emb)

    await add_pending_purchase(
        db, msg.id, ctx.guild_id, cfg["approval_channel"],
        ctx.user.id, rtype, amt, coins
    )

    view = ShopApprovalView(
        ctx.guild_id, ctx.user.id, rtype, amt, coins,
        cfg["approval_channel"], msg.id
    )
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

    bot    = ctx.bot
    db     = bot.d.db
    amount = ctx.options.amount

    view = DropView(bot, ctx.guild_id, amount)
    emb  = hikari.Embed(
        title="💸 Coin Drop!",
        description=f"**{amount:,} coins** are up for grabs! Click below to claim!",
        color=0xFFD700
    )
    resp = await ctx.respond(embed=emb, components=view)
    msg  = await resp.message()

    view.msg_id  = msg.id
    view.chan_id = ctx.channel_id

    await save_pending_drop(db, msg.id, ctx.guild_id, ctx.channel_id, amount)
    bot.d.miru.start_view(view, bind_to=msg)

    # Log
    cfg = await get_guild_config(db, ctx.guild_id)
    if cfg["log_channel"]:
        try:
            await bot.rest.create_message(
                cfg["log_channel"],
                f"💸 <@{ctx.user.id}> dropped **{amount:,} coins** in <#{ctx.channel_id}>."
            )
        except Exception:
            pass


# ── Extension loader ──────────────────────────────────────────────────────────

def load(bot: lightbulb.BotApp):
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp):
    bot.remove_plugin(plugin)
