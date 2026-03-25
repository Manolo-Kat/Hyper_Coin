"""
Hyper Coin Bot — main entry point
Loads extensions and handles bot lifecycle.
"""

import os
from dotenv import load_dotenv
load_dotenv(override=True)  # .env values always take precedence

import asyncio
import logging
import random
from collections import defaultdict
from datetime import datetime, timezone

import aiohttp
import hikari
import lightbulb
import miru

from utils.db import init_db, migrate_from_json, get_guild_config, get_all_pending_purchases
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

miru_client = miru.Client(bot)

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

# ── Auto-drop loop ────────────────────────────────────────────────────────────

async def auto_drop_loop():
    """Fires every 30–90 minutes, drops random coins into the drop channel."""
    from extensions.shop import DropView
    await asyncio.sleep(15)  # allow bot to fully boot

    while True:
        delay = random.randint(1800, 5400)  # 30–90 min
        await asyncio.sleep(delay)
        try:
            guilds = bot.cache.get_available_guilds_view()
            for gid in guilds:
                try:
                    cfg = await get_guild_config(bot.d.db, gid)
                    if not cfg["drop_channel"]:
                        continue
                    coins   = random.randint(50, 500)
                    drop_id = f"{gid}_{datetime.now(timezone.utc).timestamp()}"
                    view    = DropView(gid, coins, drop_id)
                    emb     = hikari.Embed(
                        title="💸 Auto Coin Drop!",
                        description=f"**{coins:,} coins** just dropped! Claim it fast!",
                        color=0xFFD700,
                    )
                    msg = await bot.rest.create_message(
                        cfg["drop_channel"], embed=emb, components=view
                    )
                    view.message = msg
                    miru_client.start_view(view)
                except Exception as e:
                    logger.warning(f"Auto-drop failed (guild {gid}): {e}")
        except Exception as e:
            logger.warning(f"Auto-drop loop error: {e}")


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@bot.listen(hikari.StartedEvent)
async def on_start(_: hikari.StartedEvent):
    bot.d.http = aiohttp.ClientSession()
    bot.d.db   = await init_db()
    await migrate_from_json(bot.d.db)

    # Re-attach persistent approval views
    from extensions.shop import ShopApprovalView
    pending = await get_all_pending_purchases(bot.d.db)
    for p in pending:
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
            logger.warning(f"Re-attach view failed (msg {p['message_id']}): {e}")

    await bot.update_presence(
        status=hikari.Status.DO_NOT_DISTURB,
        activity=hikari.Activity(name="Zo's wallet", type=hikari.ActivityType.WATCHING),
    )

    asyncio.create_task(auto_drop_loop())
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
