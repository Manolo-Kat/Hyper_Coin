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

REGION_ITEMS = {"Google Play", "Apple Store", "Nintendo Card", "Roblox"}
EMAIL_ITEMS  = {"PayPal"}
GIFT_CARD_ITEMS = {"Steam", "Google Play", "Apple Store", "Nintendo Card", "Roblox",
                   "Discord Nitro Basic", "Discord Nitro Boost"}

MIN_PURCHASE_USD = 5


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _log_embed(bot, channel_id, embed: hikari.Embed) -> None:
    if channel_id:
        try:
            await bot.rest.create_message(channel_id, embed=embed)
        except Exception:
            pass


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

        # Edit the approval message
        try:
            orig = await bot.rest.fetch_message(meta["channel_id"], mid)
            emb  = orig.embeds[0] if orig.embeds else hikari.Embed()
            emb  = emb.add_field("✅ Approved by", f"<@{ctx.user.id}>", inline=True)
            emb  = emb.add_field("📦 Delivery Info", self.info.value,   inline=False)
            emb.color = 0x00FF88
            await bot.rest.edit_message(meta["channel_id"], mid, embed=emb, components=[])
        except Exception as e:
            logger.warning(f"Could not edit approval msg: {e}")

        # DM the user — gift cards get the code, PayPal just gets "approved"
        try:
            ch = await bot.rest.create_dm_channel(uid)
            if rtype in GIFT_CARD_ITEMS:
                dm_emb = hikari.Embed(
                    title="🎉 Purchase Approved!",
                    color=0x00FF88,
                    timestamp=now
                )
                dm_emb.add_field("🎁 Item",         rtype,                inline=True)
                dm_emb.add_field("💵 Amount",        f"${amt}",            inline=True)
                dm_emb.add_field("💰 New Balance",   f"{new_bal:,} coins", inline=True)
                dm_emb.add_field("🎫 Your Code / Details", self.info.value, inline=False)
                dm_emb.set_footer(text="Keep this code safe!")
            else:
                # PayPal — don't reveal delivery notes, just confirm approval
                dm_emb = hikari.Embed(
                    title="🎉 Purchase Approved!",
                    description=(
                        f"Your **PayPal** transfer of **${amt}** has been approved "
                        "and will be sent to your account shortly."
                    ),
                    color=0x00FF88,
                    timestamp=now
                )
                dm_emb.add_field("💰 New Balance", f"{new_bal:,} coins", inline=True)
            await bot.rest.create_message(ch, embed=dm_emb)
        except Exception:
            pass

        # Log (embed)
        cfg = await get_guild_config(db, gid)
        log_emb = hikari.Embed(
            title="✅ Purchase Approved",
            color=0x00FF88,
            timestamp=now
        )
        log_emb.add_field("🛡️ Staff",    f"<@{ctx.user.id}>", inline=True)
        log_emb.add_field("👤 User",     f"<@{uid}>",          inline=True)
        log_emb.add_field("🎁 Item",     rtype,                 inline=True)
        log_emb.add_field("💵 Amount",   f"${amt}",             inline=True)
        log_emb.add_field("📦 Delivery", self.info.value,        inline=False)
        await _log_embed(bot, cfg["log_channel"], log_emb)

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

        # Edit approval message
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

        # DM user (embed)
        try:
            ch = await bot.rest.create_dm_channel(meta["user_id"])
            dm_emb = hikari.Embed(
                title="❌ Purchase Rejected",
                description=(
                    f"Your **{meta['item_type']}** purchase of **${meta['amount']}** "
                    "was rejected by staff."
                ),
                color=0xFF3333,
                timestamp=datetime.now(timezone.utc)
            )
            await bot.rest.create_message(ch, embed=dm_emb)
        except Exception:
            pass

        # Log (embed)
        cfg = await get_guild_config(db, meta["guild_id"])
        log_emb = hikari.Embed(
            title="❌ Purchase Rejected",
            color=0xFF3333,
            timestamp=datetime.now(timezone.utc)
        )
        log_emb.add_field("🛡️ Staff",  f"<@{ctx.user.id}>",     inline=True)
        log_emb.add_field("👤 User",   f"<@{meta['user_id']}>",  inline=True)
        log_emb.add_field("🎁 Item",   meta["item_type"],          inline=True)
        log_emb.add_field("💵 Amount", f"${meta['amount']}",       inline=True)
        await _log_embed(bot, cfg["log_channel"], log_emb)

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
        self.msg_id   = None
        self.chan_id  = None

    @miru.button(label="🪙 Claim!", style=hikari.ButtonStyle.SUCCESS)
    async def claim(self, ctx: miru.ViewContext, _):
        if self.claimed:
            await ctx.respond("Already claimed!", flags=hikari.MessageFlag.EPHEMERAL)
            return
        self.claimed = True
        self.stop()

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
                description=f"<@{ctx.user.id}> tried to claim but hit their daily chat limit.",
                color=0xFF6600
            )

        # Edit the drop message — no private reply to the claimer
        await ctx.edit_response(embed=emb, components=[])

    async def on_timeout(self):
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
            if self.message:
                await self.message.edit(embed=emb, components=[])
        except Exception:
            pass


# ── Commands ──────────────────────────────────────────────────────────────────

@plugin.command
@lightbulb.option("email",  "Your PayPal email (PayPal only)",                             required=False)
@lightbulb.option("region", "Region/country (Google Play, Apple, Nintendo, Roblox only)",  required=False)
@lightbulb.option("item",   "Which item to purchase",        choices=SHOP_ITEMS,           required=True)
@lightbulb.option("amount", "Amount in USD to redeem",       type=int,                     required=True)
@lightbulb.command("buy", "Redeem coins for a real-world gift")
@lightbulb.implements(lightbulb.SlashCommand)
async def buy_cmd(ctx: lightbulb.SlashContext):
    bot   = ctx.bot
    db    = bot.d.db
    amt   = ctx.options.amount
    rtype = ctx.options.item

    if amt < MIN_PURCHASE_USD:
        await ctx.respond(
            f"❌ Minimum purchase amount is **${MIN_PURCHASE_USD}**.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
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

    region = (ctx.options.region or "").strip()
    email  = (ctx.options.email  or "").strip()

    if rtype in REGION_ITEMS and not region:
        await ctx.respond(
            f"❌ **{rtype}** requires a **region** (e.g. US, UK, Egypt). Use the `region` option.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    if rtype in EMAIL_ITEMS and not email:
        await ctx.respond(
            "❌ **PayPal** requires your **email address**. Use the `email` option.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return

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

    # Log (embed)
    cfg = await get_guild_config(db, ctx.guild_id)
    if cfg["log_channel"]:
        log_emb = hikari.Embed(
            title="💸 Coin Drop Created",
            color=0xFFD700,
            timestamp=datetime.now(timezone.utc)
        )
        log_emb.add_field("🛡️ Staff",    f"<@{ctx.user.id}>", inline=True)
        log_emb.add_field("💰 Amount",   f"{amount:,} coins",  inline=True)
        log_emb.add_field("📢 Channel",  f"<#{ctx.channel_id}>", inline=True)
        await _log_embed(bot, cfg["log_channel"], log_emb)


# ── Extension loader ──────────────────────────────────────────────────────────

def load(bot: lightbulb.BotApp):
    bot.add_plugin(plugin)


def unload(bot: lightbulb.BotApp):
    bot.remove_plugin(plugin)
