"""
Database layer — all reads and writes go through here.
Uses aiosqlite (SQLite with async support).
"""

import aiosqlite
import json
import logging
import os
from datetime import datetime, timezone
from utils.config import DEFAULT_SHOP_PRICES

logger = logging.getLogger("HyperCoin")

DB_PATH = "hyper_coin.db"


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

CREATE TABLE IF NOT EXISTS users (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    coins    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_prefs (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    currency TEXT    NOT NULL DEFAULT 'USD',
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS daily_claims (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    last_daily TEXT,
    streak     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS daily_earnings (
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    earn_date TEXT    NOT NULL,
    earned    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, earn_date)
);

CREATE TABLE IF NOT EXISTS weekly_spending (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    week_key TEXT    NOT NULL,
    spent    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, week_key)
);

CREATE TABLE IF NOT EXISTS pending_purchases (
    message_id INTEGER PRIMARY KEY,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    item_type  TEXT    NOT NULL,
    amount     INTEGER NOT NULL,
    coins      INTEGER NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_drops (
    message_id INTEGER PRIMARY KEY,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    coins      INTEGER NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id           INTEGER PRIMARY KEY,
    approval_channel   INTEGER,
    log_channel        INTEGER,
    drop_channel       INTEGER,
    price_per_usd      INTEGER NOT NULL DEFAULT 100,
    banned_role        INTEGER,
    shop_prices        TEXT    NOT NULL DEFAULT '{}',
    allowed_roles      TEXT    NOT NULL DEFAULT '[]',
    uncounted_channels TEXT    NOT NULL DEFAULT '[]'
);
"""


# ── Init & migration ──────────────────────────────────────────────────────────

async def init_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    await db.commit()
    logger.info("Database initialised.")
    return db


async def migrate_from_json(db: aiosqlite.Connection) -> None:
    path = "data.json"
    done = "data.json.migrated"
    if not os.path.exists(path) or os.path.exists(done):
        return
    logger.info("Migrating data.json → SQLite …")
    try:
        with open(path, "r") as f:
            raw = json.load(f)

        for gid_str, gd in raw.items():
            gid = int(gid_str)
            cfg = gd.get("config", {})

            prices   = cfg.get("shop_prices", DEFAULT_SHOP_PRICES)
            roles    = gd.get("allowed_roles", [])
            unc      = list(gd.get("uncounted", []))

            await db.execute(
                """
                INSERT OR REPLACE INTO guild_config
                    (guild_id, approval_channel, log_channel, drop_channel,
                     price_per_usd, banned_role, shop_prices,
                     allowed_roles, uncounted_channels)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (gid,
                 cfg.get("approval_channel"),
                 cfg.get("log_channel"),
                 cfg.get("drop_channel"),
                 cfg.get("price_per_usd", 100),
                 gd.get("banned_role"),
                 json.dumps(prices),
                 json.dumps(roles),
                 json.dumps(unc))
            )

            for uid_s, coins in gd.get("users", {}).items():
                await db.execute(
                    "INSERT OR REPLACE INTO users (guild_id, user_id, coins) VALUES (?,?,?)",
                    (gid, int(uid_s), coins)
                )

            for uid_s, prefs in gd.get("user_prefs", {}).items():
                cur = prefs.get("currency", "USD") if isinstance(prefs, dict) else "USD"
                await db.execute(
                    "INSERT OR REPLACE INTO user_prefs (guild_id, user_id, currency) VALUES (?,?,?)",
                    (gid, int(uid_s), cur)
                )

            daily   = gd.get("daily", {})
            streaks = gd.get("streaks", {})
            for uid_s in set(daily) | set(streaks):
                uid = int(uid_s)
                await db.execute(
                    "INSERT OR REPLACE INTO daily_claims (guild_id, user_id, last_daily, streak) VALUES (?,?,?,?)",
                    (gid, uid, daily.get(uid_s), streaks.get(uid_s, 0))
                )

            for dk, earned in gd.get("daily_earnings", {}).items():
                parts = dk.split("_", 1)
                if len(parts) == 2:
                    try:
                        await db.execute(
                            "INSERT OR REPLACE INTO daily_earnings (guild_id, user_id, earn_date, earned) VALUES (?,?,?,?)",
                            (gid, int(parts[0]), parts[1], earned)
                        )
                    except ValueError:
                        pass

            for wk, spent in gd.get("weekly_spent", {}).items():
                parts = wk.split("_", 1)
                if len(parts) == 2:
                    try:
                        await db.execute(
                            "INSERT OR REPLACE INTO weekly_spending (guild_id, user_id, week_key, spent) VALUES (?,?,?,?)",
                            (gid, int(parts[0]), wk, spent)
                        )
                    except ValueError:
                        pass

            for mid_s, p in gd.get("pending_purchases", {}).items():
                try:
                    await db.execute(
                        """INSERT OR REPLACE INTO pending_purchases
                           (message_id, guild_id, channel_id, user_id,
                            item_type, amount, coins, created_at)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (int(mid_s), gid, p["channel_id"], p["user_id"],
                         p["type"], p["amount"], p["coins"],
                         datetime.now(timezone.utc).isoformat())
                    )
                except (KeyError, ValueError):
                    pass

        await db.commit()
        os.rename(path, done)
        logger.info("Migration complete — data.json → data.json.migrated")
    except Exception as e:
        logger.error(f"Migration failed: {e}")


# ── Guild config ──────────────────────────────────────────────────────────────

async def get_guild_config(db: aiosqlite.Connection, guild_id: int) -> aiosqlite.Row:
    async with db.execute(
        "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
    ) as c:
        row = await c.fetchone()
    if row is None:
        await db.execute(
            "INSERT OR IGNORE INTO guild_config (guild_id, shop_prices) VALUES (?,?)",
            (guild_id, json.dumps(DEFAULT_SHOP_PRICES))
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        ) as c:
            row = await c.fetchone()
    return row


async def set_guild_config_field(db, guild_id: int, field: str, value) -> None:
    await db.execute(
        f"INSERT INTO guild_config (guild_id, {field}) VALUES (?,?) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {field} = excluded.{field}",
        (guild_id, value)
    )
    await db.commit()


# ── Users / coins ─────────────────────────────────────────────────────────────

async def get_balance(db, guild_id: int, user_id: int) -> int:
    async with db.execute(
        "SELECT coins FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)
    ) as c:
        row = await c.fetchone()
    return row["coins"] if row else 0


async def set_coins(db, guild_id: int, user_id: int, coins: int) -> None:
    await db.execute(
        "INSERT INTO users (guild_id, user_id, coins) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET coins = excluded.coins",
        (guild_id, user_id, coins)
    )
    await db.commit()


async def adjust_coins(db, guild_id: int, user_id: int, delta: int) -> int:
    bal = await get_balance(db, guild_id, user_id)
    new = max(0, bal + delta)
    await set_coins(db, guild_id, user_id, new)
    return new


async def add_earned_coins(
    db, guild_id: int, user_id: int,
    base: int, is_boosting: bool, streak: int
) -> int:
    from utils.helpers import get_streak_mult
    daily_limit = 400 if is_boosting else 200
    today = datetime.now(timezone.utc).date().isoformat()

    async with db.execute(
        "SELECT earned FROM daily_earnings WHERE guild_id=? AND user_id=? AND earn_date=?",
        (guild_id, user_id, today)
    ) as c:
        row = await c.fetchone()
    earned = row["earned"] if row else 0

    if earned >= daily_limit:
        return 0

    mult   = get_streak_mult(streak) * (2.0 if is_boosting else 1.0)
    actual = max(1, int(base * mult))
    actual = min(actual, daily_limit - earned)

    await db.execute(
        "INSERT INTO users (guild_id, user_id, coins) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET coins = coins + excluded.coins",
        (guild_id, user_id, actual)
    )
    await db.execute(
        "INSERT INTO daily_earnings (guild_id, user_id, earn_date, earned) VALUES (?,?,?,?) "
        "ON CONFLICT(guild_id, user_id, earn_date) DO UPDATE SET earned = earned + excluded.earned",
        (guild_id, user_id, today, actual)
    )
    await db.commit()
    return actual


async def get_daily_earned_today(db, guild_id: int, user_id: int) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    async with db.execute(
        "SELECT earned FROM daily_earnings WHERE guild_id=? AND user_id=? AND earn_date=?",
        (guild_id, user_id, today)
    ) as c:
        row = await c.fetchone()
    return row["earned"] if row else 0


async def get_leaderboard(db, guild_id: int) -> list:
    async with db.execute(
        "SELECT user_id, coins FROM users WHERE guild_id=? ORDER BY coins DESC",
        (guild_id,)
    ) as c:
        return await c.fetchall()


# ── Daily claims ──────────────────────────────────────────────────────────────

async def get_daily_info(db, guild_id: int, user_id: int):
    async with db.execute(
        "SELECT last_daily, streak FROM daily_claims WHERE guild_id=? AND user_id=?",
        (guild_id, user_id)
    ) as c:
        return await c.fetchone()


async def update_daily_claim(db, guild_id: int, user_id: int, now_iso: str, streak: int) -> None:
    await db.execute(
        "INSERT INTO daily_claims (guild_id, user_id, last_daily, streak) VALUES (?,?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET last_daily=excluded.last_daily, streak=excluded.streak",
        (guild_id, user_id, now_iso, streak)
    )
    await db.commit()


# ── Weekly spending ───────────────────────────────────────────────────────────

def make_week_key(user_id: int, now: datetime) -> str:
    iso = now.isocalendar()
    return f"{user_id}_{iso.year}_{iso.week}"


async def get_weekly_spent(db, guild_id: int, user_id: int, week_key: str) -> int:
    async with db.execute(
        "SELECT spent FROM weekly_spending WHERE guild_id=? AND user_id=? AND week_key=?",
        (guild_id, user_id, week_key)
    ) as c:
        row = await c.fetchone()
    return row["spent"] if row else 0


async def add_weekly_spent(db, guild_id: int, user_id: int, week_key: str, amount: int) -> None:
    await db.execute(
        "INSERT INTO weekly_spending (guild_id, user_id, week_key, spent) VALUES (?,?,?,?) "
        "ON CONFLICT(guild_id, user_id, week_key) DO UPDATE SET spent = spent + excluded.spent",
        (guild_id, user_id, week_key, amount)
    )
    await db.commit()


# ── Pending purchases ─────────────────────────────────────────────────────────

async def add_pending_purchase(
    db, message_id: int, guild_id: int, channel_id: int,
    user_id: int, item_type: str, amount: int, coins: int
) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO pending_purchases "
        "(message_id, guild_id, channel_id, user_id, item_type, amount, coins, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (message_id, guild_id, channel_id, user_id, item_type, amount, coins,
         datetime.now(timezone.utc).isoformat())
    )
    await db.commit()


async def remove_pending_purchase(db, message_id: int) -> None:
    await db.execute(
        "DELETE FROM pending_purchases WHERE message_id=?", (message_id,)
    )
    await db.commit()


async def get_all_pending_purchases(db) -> list:
    async with db.execute("SELECT * FROM pending_purchases") as c:
        return await c.fetchall()


# ── Pending drops ─────────────────────────────────────────────────────────────

async def save_pending_drop(
    db, message_id: int, guild_id: int, channel_id: int, coins: int
) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO pending_drops "
        "(message_id, guild_id, channel_id, coins, created_at) VALUES (?,?,?,?,?)",
        (message_id, guild_id, channel_id, coins,
         datetime.now(timezone.utc).isoformat())
    )
    await db.commit()


async def remove_pending_drop(db, message_id: int) -> None:
    await db.execute("DELETE FROM pending_drops WHERE message_id=?", (message_id,))
    await db.commit()


async def get_all_pending_drops(db) -> list:
    async with db.execute("SELECT * FROM pending_drops") as c:
        return await c.fetchall()


# ── User prefs ────────────────────────────────────────────────────────────────

async def get_user_currency(db, guild_id: int, user_id: int) -> str:
    async with db.execute(
        "SELECT currency FROM user_prefs WHERE guild_id=? AND user_id=?",
        (guild_id, user_id)
    ) as c:
        row = await c.fetchone()
    return row["currency"] if row else "USD"


async def set_user_currency(db, guild_id: int, user_id: int, currency: str) -> None:
    await db.execute(
        "INSERT INTO user_prefs (guild_id, user_id, currency) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET currency = excluded.currency",
        (guild_id, user_id, currency)
    )
    await db.commit()
