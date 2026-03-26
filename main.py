"""
Hyper Coin Bot — main entry point
Loads extensions and handles bot lifecycle.
"""

import os
from dotenv import load_dotenv
load_dotenv(override=True)  # .env values always take precedence

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

import aiohttp
import hikari
import lightbulb
import miru

from utils.db import (
    init_db, migrate_from_json,
    get_all_pending_purchases,
    get_all_pending_drops, remove_pending_drop,
)
from utils.helpers import SpamTracker

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("HyperCoin")

# ── Bot ───────────────────────────────────────────────────────────────────────

bot = lightbulb.BotApp(
    token=os.environ.get("BOT_TOKEN"),
    intents=(
        hikari.Intents.ALL_UNPRIVILEGED
        | hikari.Intents.MESSAGE_CONTENT
        | hikari.Intents.GUILD_MEMBERS
    ),
    default_enabled_guilds=(),
)

miru_client = miru.Client(bot, ignore_unknown_interactions=True)

# Shared state — safe defaults so extensions never see AttributeError at import
bot.d.db             = None
bot.d.http           = None
bot.d.miru           = miru_client
bot.d.rate_cache     = {}
bot.d.rate_lock      = asyncio.Lock()
bot.d.user_locks     = defaultdict(asyncio.Lock)
bot.d.coin_cooldowns = {}
bot.d.spam           = SpamTracker()

# ── Extensions ────────────────────────────────────────────────────────────────

bot.load_extensions_from("./extensions")

# ── Lifecycle ─────────────────────────────────────────────────────────────────

@bot.listen(hikari.StartedEvent)
async def on_start(_: hikari.StartedEvent):
    bot.d.http = aiohttp.ClientSession()
    bot.d.db   = await init_db()
    await migrate_from_json(bot.d.db)

    # ── Re-attach purchase approval views ──────────────────────────────────
    from extensions.shop import ShopApprovalView
    for p in await get_all_pending_purchases(bot.d.db):
        try:
            msg  = await bot.rest.fetch_message(p["channel_id"], p["message_id"])
            view = ShopApprovalView(
                guild_id   = p["guild_id"],
                user_id    = p["user_id"],
                item_type  = p["item_type"],
                amount     = p["amount"],
                coins      = p["coins"],
                channel_id = p["channel_id"],
                message_id = p["message_id"],
            )
            miru_client.start_view(view, bind_to=msg)
        except Exception as e:
            logger.warning(f"Re-attach purchase view failed (msg {p['message_id']}): {e}")

    # ── Re-attach active drop views ────────────────────────────────────────
    from extensions.shop import DropView
    now = datetime.now(timezone.utc)
    for d in await get_all_pending_drops(bot.d.db):
        try:
            created   = datetime.fromisoformat(d["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age       = (now - created).total_seconds()
            remaining = 120.0 - age

            if remaining <= 5:
                # Drop expired while bot was offline — mark it as expired
                try:
                    msg = await bot.rest.fetch_message(d["channel_id"], d["message_id"])
                    emb = hikari.Embed(
                        title="💨 Drop Expired",
                        description="Bot restarted — the drop has expired.",
                        color=0x888888
                    )
                    await bot.rest.edit_message(d["channel_id"], d["message_id"],
                                                embed=emb, components=[])
                except Exception:
                    pass
                await remove_pending_drop(bot.d.db, d["message_id"])
            else:
                msg  = await bot.rest.fetch_message(d["channel_id"], d["message_id"])
                view = DropView(bot, d["guild_id"], d["coins"], timeout=remaining)
                view.msg_id  = d["message_id"]
                view.chan_id = d["channel_id"]
                miru_client.start_view(view, bind_to=msg)
        except Exception as e:
            logger.warning(f"Re-attach drop view failed (msg {d['message_id']}): {e}")
            try:
                await remove_pending_drop(bot.d.db, d["message_id"])
            except Exception:
                pass

    await bot.update_presence(
        status=hikari.Status.DO_NOT_DISTURB,
        activity=hikari.Activity(name="Zo's wallet", type=hikari.ActivityType.WATCHING),
    )
    logger.info("Hyper Coin Bot started.")


@bot.listen(hikari.StoppingEvent)
async def on_stop(_: hikari.StoppingEvent):
    if bot.d.db:
        await bot.d.db.close()
    if bot.d.http and not bot.d.http.closed:
        await bot.d.http.close()
    logger.info("Hyper Coin Bot stopped.")


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run()
