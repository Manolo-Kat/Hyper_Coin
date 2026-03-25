import hikari
import lightbulb
import miru
import asyncio
import aiohttp
import aiofiles
import random
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import json
import os
from dotenv import dotenv_values

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("HyperCoin")

config_env = dotenv_values(".env")
BOT_TOKEN = os.environ.get('BOT_TOKEN') or config_env.get('BOT_TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID') or config_env.get('OWNER_ID') or '823310792291385424')
MOD_ROLE_ID = int(os.environ.get('MOD_ROLE_ID') or config_env.get('MOD_ROLE_ID') or '1373312465626202222')

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found!")
    exit(1)

bot = lightbulb.BotApp(
    token=BOT_TOKEN,
    intents=hikari.Intents.ALL,
    default_enabled_guilds=()
)
miru_client = miru.Client(bot)

# ── Global state ──────────────────────────────────────────────────────────────
guild_data: dict = {}
http_session: aiohttp.ClientSession | None = None
save_event = asyncio.Event()
rate_cache: dict = {}
rate_lock = asyncio.Lock()
user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
message_cooldowns: dict[int, datetime] = {}

WEEKLY_LIMIT_USD = 20

# ── Data helpers ──────────────────────────────────────────────────────────────

def get_guild_data(guild_id: int) -> dict:
    if guild_id not in guild_data:
        guild_data[guild_id] = {
            'users': {},
            'user_prefs': {},
            'daily_earnings': defaultdict(int),
            'last_daily': {},
            'streaks': {},
            'last_activity': {},
            'uncounted': set(),
            'banned_role': None,
            'allowed_roles': [],
            'weekly_spent': {},
            'pending_purchases': {},
            'config': {
                'approval_channel': None,
                'price_per_usd': 100,
                'shop_prices': {
                    'PayPal': 100, 'Steam': 100, 'Google Play': 100,
                    'Apple Store': 100, 'Discord Nitro Basic': 100,
                    'Discord Nitro Boost': 100, 'Nintendo Card': 100, 'Roblox': 100
                },
                'log_channel': None,
                'drop_channel': None,
                'price_history': []
            }
        }
    return guild_data[guild_id]


def load_data():
    global guild_data
    try:
        if not os.path.exists('data.json'):
            return
        with open('data.json', 'r') as f:
            raw = json.load(f)
        for gid_str, gd in raw.items():
            gid = int(gid_str)
            cfg = gd.get('config', {})
            cfg.setdefault('price_per_usd', 100)
            cfg.setdefault('drop_channel', None)
            cfg.setdefault('shop_prices', {
                'PayPal': 100, 'Steam': 100, 'Google Play': 100,
                'Apple Store': 100, 'Discord Nitro Basic': 100,
                'Discord Nitro Boost': 100, 'Nintendo Card': 100, 'Roblox': 100
            })
            guild_data[gid] = {
                'users': {int(k): v for k, v in gd.get('users', {}).items()},
                'user_prefs': {int(k): v for k, v in gd.get('user_prefs', {}).items()},
                'daily_earnings': defaultdict(int, {k: v for k, v in gd.get('daily_earnings', {}).items()}),
                'last_daily': {int(k): datetime.fromisoformat(v) for k, v in gd.get('daily', {}).items()},
                'streaks': {int(k): v for k, v in gd.get('streaks', {}).items()},
                'last_activity': {int(k): datetime.fromisoformat(v) for k, v in gd.get('activity', {}).items()},
                'uncounted': set(gd.get('uncounted', [])),
                'banned_role': gd.get('banned_role'),
                'allowed_roles': gd.get('allowed_roles', []),
                'weekly_spent': gd.get('weekly_spent', {}),
                'pending_purchases': gd.get('pending_purchases', {}),
                'config': cfg
            }
        logger.info("Data loaded.")
    except Exception as e:
        logger.error(f"load_data failed: {e}")


async def save_data():
    try:
        out = {}
        for gid, gd in guild_data.items():
            out[str(gid)] = {
                'users': gd['users'],
                'user_prefs': gd.get('user_prefs', {}),
                'daily_earnings': dict(gd['daily_earnings']),
                'daily': {k: v.isoformat() for k, v in gd['last_daily'].items()},
                'streaks': gd['streaks'],
                'activity': {k: v.isoformat() for k, v in gd['last_activity'].items()},
                'uncounted': list(gd['uncounted']),
                'banned_role': gd['banned_role'],
                'allowed_roles': gd['allowed_roles'],
                'weekly_spent': gd.get('weekly_spent', {}),
                'pending_purchases': gd.get('pending_purchases', {}),
                'config': gd['config']
            }
        async with aiofiles.open('data.json', 'w') as f:
            await f.write(json.dumps(out, indent=2))
    except Exception as e:
        logger.error(f"save_data failed: {e}")


def mark_dirty():
    save_event.set()


async def autosave_loop():
    while True:
        await save_event.wait()
        save_event.clear()
        await asyncio.sleep(5)
        await save_data()


async def get_exchange_rate(currency: str) -> float | None:
    async with rate_lock:
        if currency in rate_cache:
            rate, fetched_at = rate_cache[currency]
            age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
            if age < 3600:
                return rate
    try:
        async with http_session.get(
            f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={currency}"
        ) as r:
            if r.status == 200:
                data = await r.json()
                rate = data['rates'].get(currency)
                if rate:
                    async with rate_lock:
                        rate_cache[currency] = (rate, datetime.now(timezone.utc))
                return rate
    except Exception as e:
        logger.warning(f"Currency fetch failed for {currency}: {e}")
    return None

# ── Coin helpers ──────────────────────────────────────────────────────────────

def get_streak_mult(guild_id: int, user_id: int) -> float:
    streak = get_guild_data(guild_id)['streaks'].get(user_id, 0)
    table = {0: 1.0, 1: 1.25, 2: 1.5, 3: 1.75, 4: 2.0, 5: 2.25, 6: 2.25, 7: 2.5}
    return table.get(min(streak, 7), 1.0)


def is_banned(guild_id: int, member) -> bool:
    gd = get_guild_data(guild_id)
    if not gd['banned_role'] or not member:
        return False
    return gd['banned_role'] in member.role_ids


def is_booster(member) -> bool:
    return bool(member and member.premium_since is not None)


async def add_coins(guild_id: int, user_id: int, amount: int, member=None) -> bool:
    async with user_locks[user_id]:
        gd = get_guild_data(guild_id)
        allowed = gd.get('allowed_roles', [])
        if allowed and member:
            if not any(r in member.role_ids for r in allowed):
                return False
        if user_id not in gd['users']:
            gd['users'][user_id] = 0
        today = datetime.now(timezone.utc).date()
        dk = f"{user_id}_{today}"
        boosting = is_booster(member)
        daily_limit = 400 if boosting else 200
        if gd['daily_earnings'][dk] >= daily_limit:
            return False
        mult = get_streak_mult(guild_id, user_id)
        if boosting:
            mult *= 2
        actual = max(1, int(amount * mult))
        remaining = daily_limit - gd['daily_earnings'][dk]
        actual = min(actual, remaining)
        gd['users'][user_id] += actual
        gd['daily_earnings'][dk] += actual
        mark_dirty()
        return True

# ── Views ─────────────────────────────────────────────────────────────────────

class LeaderboardView(miru.View):
    def __init__(self, pages: list, current_page: int = 0):
        super().__init__(timeout=60.0)
        self.pages = pages
        self.current_page = current_page

    async def _update(self, ctx: miru.ViewContext):
        await ctx.edit_response(embed=self.pages[self.current_page], components=self)

    @miru.button(label="<<<", style=hikari.ButtonStyle.DANGER)
    async def first_page(self, ctx: miru.ViewContext, _):
        self.current_page = 0
        await self._update(ctx)

    @miru.button(label="<", style=hikari.ButtonStyle.PRIMARY)
    async def prev_page(self, ctx: miru.ViewContext, _):
        self.current_page = max(0, self.current_page - 1)
        await self._update(ctx)

    @miru.button(label=">", style=hikari.ButtonStyle.PRIMARY)
    async def next_page(self, ctx: miru.ViewContext, _):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        await self._update(ctx)

    @miru.button(label=">>>", style=hikari.ButtonStyle.DANGER)
    async def last_page(self, ctx: miru.ViewContext, _):
        self.current_page = len(self.pages) - 1
        await self._update(ctx)


class DropClaimView(miru.View):
    def __init__(self, gid: int, amount: int):
        super().__init__(timeout=300.0)
        self.gid = gid
        self.amount = amount
        self.claimed = False

    @miru.button(label="🎁 Claim!", style=hikari.ButtonStyle.SUCCESS)
    async def claim_btn(self, ctx: miru.ViewContext, _):
        if self.claimed:
            await ctx.respond("Already claimed!", flags=hikari.MessageFlag.EPHEMERAL)
            return
        self.claimed = True
        gd = get_guild_data(self.gid)
        gd['users'][ctx.user.id] = gd['users'].get(ctx.user.id, 0) + self.amount
        mark_dirty()
        emb = hikari.Embed(
            title="🎁 Claimed!",
            description=f"{ctx.user.mention} grabbed **{self.amount:,} coins**!",
            color=0x00FF00
        )
        emb.timestamp = datetime.now(timezone.utc)
        await ctx.edit_response(embed=emb, components=[])
        self.stop()


class ShopApprovalView(miru.View):
    def __init__(self, gid: int, uid: int, rtype: str, amt: int, coins: int):
        super().__init__(timeout=None)
        self.gid, self.uid, self.rtype, self.amt, self.coins = gid, uid, rtype, amt, coins

    @miru.button(label="✅ Accept", style=hikari.ButtonStyle.SUCCESS)
    async def approve(self, ctx: miru.ViewContext, _):
        try:
            member = await bot.rest.fetch_member(self.gid, ctx.user.id)
        except Exception as e:
            logger.warning(f"fetch_member failed in approve: {e}")
            return
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        modal = ShopAcceptModal(self.gid, self.uid, self.rtype, self.amt, self.coins, ctx.message.id)
        await ctx.respond_with_modal(modal)
        miru_client.start_modal(modal)

    @miru.button(label="❌ Reject", style=hikari.ButtonStyle.DANGER)
    async def reject(self, ctx: miru.ViewContext, _):
        try:
            member = await bot.rest.fetch_member(self.gid, ctx.user.id)
        except Exception as e:
            logger.warning(f"fetch_member failed in reject: {e}")
            return
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        gd = get_guild_data(self.gid)
        gd.get('pending_purchases', {}).pop(str(ctx.message.id), None)
        mark_dirty()
        try:
            u = await bot.rest.fetch_user(self.uid)
            await u.send(
                embed=hikari.Embed(
                    title="❌ Purchase Rejected",
                    description=f"Your request for **{self.rtype} (${self.amt})** was rejected.\nNo coins were deducted.",
                    color=0xFF0000
                )
            )
        except Exception as e:
            logger.warning(f"DM to user failed on reject: {e}")
        rej_emb = hikari.Embed(title="❌ Rejected", color=0xFF0000)
        rej_emb.description = (
            f"**Rejected by:** {ctx.user.mention}\n"
            f"**User:** <@{self.uid}>\n"
            f"**Item:** {self.rtype} (${self.amt})"
        )
        rej_emb.timestamp = datetime.now(timezone.utc)
        await ctx.edit_response(embed=rej_emb, components=[])
        log_id = gd['config'].get('log_channel')
        if log_id:
            try:
                emb = hikari.Embed(title="❌ Purchase Rejected", color=0xFF0000)
                emb.add_field("User", f"<@{self.uid}>", inline=True)
                emb.add_field("Staff", ctx.user.mention, inline=True)
                emb.add_field("Item", f"{self.rtype} (${self.amt})", inline=True)
                emb.timestamp = datetime.now(timezone.utc)
                await bot.rest.create_message(log_id, embed=emb)
            except Exception as e:
                logger.warning(f"Log message failed on reject: {e}")
        self.stop()


class ShopAcceptModal(miru.Modal):
    def __init__(self, gid: int, uid: int, rtype: str, amt: int, coins: int, mid: int):
        super().__init__("Accept Request")
        self.gid, self.uid, self.rtype, self.amt, self.coins, self.mid = gid, uid, rtype, amt, coins, mid
        self.details = miru.TextInput(
            label="Gift Code / Details",
            style=hikari.TextInputStyle.PARAGRAPH,
            required=True
        )
        self.add_item(self.details)

    async def callback(self, ctx: miru.ModalContext):
        gd = get_guild_data(self.gid)
        current_bal = gd['users'].get(self.uid, 0)
        if current_bal < self.coins:
            await ctx.respond(
                f"User <@{self.uid}> no longer has enough coins ({current_bal}/{self.coins}). Auto-rejecting.",
                flags=hikari.MessageFlag.EPHEMERAL
            )
            gd.get('pending_purchases', {}).pop(str(self.mid), None)
            rej_emb = hikari.Embed(title="❌ Rejected (Insufficient Coins)", color=0xFF0000)
            rej_emb.description = (
                f"**Auto-rejected:** User no longer has enough coins.\n"
                f"**User:** <@{self.uid}>\n"
                f"**Item:** {self.rtype} (${self.amt})"
            )
            rej_emb.timestamp = datetime.now(timezone.utc)
            try:
                await bot.rest.edit_message(ctx.channel_id, self.mid, embed=rej_emb, components=[])
            except Exception as e:
                logger.warning(f"Edit approval message failed: {e}")
            mark_dirty()
            return
        gd['users'][self.uid] -= self.coins
        gd.get('pending_purchases', {}).pop(str(self.mid), None)
        await save_data()
        try:
            u = await bot.rest.fetch_user(self.uid)
            dm_emb = hikari.Embed(title="✅ Purchase Approved!", color=0x00FF00)
            dm_emb.add_field("Item", f"{self.rtype} (${self.amt})", inline=True)
            dm_emb.add_field("Details", f"```\n{self.details.value}\n```", inline=False)
            await u.send(embed=dm_emb)
        except Exception as e:
            logger.warning(f"DM to user failed on accept: {e}")
        acc_emb = hikari.Embed(title="✅ Approved", color=0x00FF00)
        acc_emb.description = (
            f"**Approved by:** {ctx.user.mention}\n"
            f"**User:** <@{self.uid}>\n"
            f"**Item:** {self.rtype} (${self.amt})"
        )
        acc_emb.timestamp = datetime.now(timezone.utc)
        try:
            await bot.rest.edit_message(ctx.channel_id, self.mid, embed=acc_emb, components=[])
        except Exception as e:
            logger.warning(f"Edit approval message failed: {e}")
        await ctx.respond("Done! ✅", flags=hikari.MessageFlag.EPHEMERAL)
        log_id = gd['config'].get('log_channel')
        if log_id:
            try:
                log_emb = hikari.Embed(title="✅ Purchase Completed", color=0x00FF00)
                log_emb.add_field("User", f"<@{self.uid}>", inline=True)
                log_emb.add_field("Staff", ctx.user.mention, inline=True)
                log_emb.add_field("Item", f"{self.rtype} (${self.amt})", inline=True)
                log_emb.add_field("Coins", f"{self.coins:,}", inline=True)
                log_emb.timestamp = datetime.now(timezone.utc)
                await bot.rest.create_message(log_id, log_emb)
            except Exception as e:
                logger.warning(f"Log message failed on accept: {e}")

# ── Message listener ──────────────────────────────────────────────────────────

@bot.listen(hikari.GuildMessageCreateEvent)
async def on_message(event: hikari.GuildMessageCreateEvent):
    if event.is_bot or not event.guild_id:
        return
    if len(event.content or "") < 5:
        return
    now = datetime.now(timezone.utc)
    last = message_cooldowns.get(event.author_id)
    if last and (now - last).total_seconds() < 60:
        return
    message_cooldowns[event.author_id] = now
    gd = get_guild_data(event.guild_id)
    if event.channel_id in gd['uncounted']:
        return
    if is_banned(event.guild_id, event.member):
        return
    await add_coins(event.guild_id, event.author_id, 1, event.member)

# ── User commands ─────────────────────────────────────────────────────────────

@bot.command
@lightbulb.command("daily", "Claim your daily coin reward")
@lightbulb.implements(lightbulb.SlashCommand)
async def daily_cmd(ctx: lightbulb.SlashContext):
    if is_banned(ctx.guild_id, ctx.member):
        await ctx.respond("You are banned from using this bot.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gd = get_guild_data(ctx.guild_id)
    now = datetime.now(timezone.utc)
    boosting = is_booster(ctx.member)
    if ctx.user.id in gd['last_daily']:
        hours_passed = (now - gd['last_daily'][ctx.user.id]).total_seconds() / 3600
        if hours_passed < 24:
            next_claim = gd['last_daily'][ctx.user.id] + timedelta(hours=24)
            next_ts = int(next_claim.timestamp())
            await ctx.respond(
                f"⏳ You already claimed today! Come back <t:{next_ts}:R>.",
                flags=hikari.MessageFlag.EPHEMERAL
            )
            return
        if hours_passed > 48:
            gd['streaks'][ctx.user.id] = 0
    amt = random.randint(1, 20)
    if boosting:
        amt *= 2
    gd['users'][ctx.user.id] = gd['users'].get(ctx.user.id, 0) + amt
    gd['last_daily'][ctx.user.id] = now
    gd['streaks'][ctx.user.id] = min(gd['streaks'].get(ctx.user.id, 0) + 1, 7)
    mark_dirty()
    streak = gd['streaks'][ctx.user.id]
    mult = get_streak_mult(ctx.guild_id, ctx.user.id)
    next_claim = now + timedelta(hours=24)
    next_ts = int(next_claim.timestamp())
    emb = hikari.Embed(title="🎁 Daily Reward!", color=0xFFD700)
    emb.set_thumbnail(ctx.user.avatar_url or ctx.user.default_avatar_url)
    emb.description = f"You received **{amt:,} coins**!"
    emb.add_field("🔥 Streak", f"{streak} day{'s' if streak != 1 else ''}", inline=True)
    emb.add_field("✨ Multiplier", f"x{mult}", inline=True)
    emb.add_field("⚡ Booster", "Yes — 2x daily!" if boosting else "No", inline=True)
    emb.add_field("⏰ Next Claim", f"<t:{next_ts}:R>", inline=False)
    emb.timestamp = now
    await ctx.respond(embed=emb)


@bot.command
@lightbulb.option("user", "Check another user's balance", type=hikari.User, required=False)
@lightbulb.command("balance", "Check your coin balance")
@lightbulb.implements(lightbulb.SlashCommand)
async def balance_cmd(ctx: lightbulb.SlashContext):
    target_user = ctx.options.user or ctx.user
    gd = get_guild_data(ctx.guild_id)
    bal = gd['users'].get(target_user.id, 0)
    price_per_usd = gd['config'].get('price_per_usd', 100)
    usd_val = bal / price_per_usd
    pref = gd.get('user_prefs', {}).get(target_user.id, {}).get('currency', 'USD').upper()
    val_str = f"${usd_val:.2f} USD"
    if pref != 'USD':
        rate = await get_exchange_rate(pref)
        if rate:
            val_str = f"{usd_val * rate:.2f} {pref}"
    today = datetime.now(timezone.utc).date()
    dk = f"{target_user.id}_{today}"
    earned_today = gd['daily_earnings'].get(dk, 0)
    try:
        member = await bot.rest.fetch_member(ctx.guild_id, target_user.id)
        boosting = is_booster(member)
    except Exception:
        boosting = False
    daily_limit = 400 if boosting else 200
    filled = int((earned_today / daily_limit) * 10)
    bar = "█" * filled + "░" * (10 - filled)
    streak = gd['streaks'].get(target_user.id, 0)
    mult = get_streak_mult(ctx.guild_id, target_user.id)
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    week_key = f"{target_user.id}_{iso.year}_{iso.week}"
    spent_this_week = gd.get('weekly_spent', {}).get(week_key, 0)
    emb = hikari.Embed(title="💰 Balance", color=0xFFD700)
    emb.set_author(name=target_user.username)
    emb.set_thumbnail(target_user.avatar_url or target_user.default_avatar_url)
    emb.add_field("🪙 Coins", f"{bal:,}", inline=True)
    emb.add_field("💵 Value", val_str, inline=True)
    emb.add_field("✨ Multiplier", f"x{mult}", inline=True)
    emb.add_field("🔥 Streak", f"{streak} day{'s' if streak != 1 else ''}", inline=True)
    if boosting:
        emb.add_field("⚡ Booster", "Active — 2x coins & 400/day", inline=True)
    else:
        emb.add_field("⚡ Booster", "Not boosting", inline=True)
    emb.add_field("📅 Weekly Spend", f"${spent_this_week}/$20", inline=True)
    emb.add_field(f"📊 Daily Progress ({earned_today}/{daily_limit})", f"`{bar}`", inline=False)
    emb.timestamp = now
    await ctx.respond(embed=emb)


@bot.command
@lightbulb.command("leaderboard", "See the top coin earners")
@lightbulb.implements(lightbulb.SlashCommand)
async def leaderboard_cmd(ctx: lightbulb.SlashContext):
    gd = get_guild_data(ctx.guild_id)
    users = sorted(gd['users'].items(), key=lambda x: x[1], reverse=True)
    if not users:
        await ctx.respond("No data yet.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    pages = []
    for i in range(0, len(users), 10):
        emb = hikari.Embed(title="🏆 Leaderboard", color=0xFFD700)
        lines = [
            f"**#{r}** <@{uid}>: {coins:,} coins"
            for r, (uid, coins) in enumerate(users[i:i+10], start=i+1)
        ]
        emb.description = "\n".join(lines)
        emb.set_footer(text=f"Page {i//10+1}/{(len(users)-1)//10+1}")
        pages.append(emb)
    view = LeaderboardView(pages)
    await ctx.respond(embed=pages[0], components=view)
    miru_client.start_view(view)


@bot.command
@lightbulb.option("gmail", "Your PayPal Gmail address", required=False)
@lightbulb.option("region", "Gift card region (e.g. US, UK)", required=False)
@lightbulb.option("amount", "Amount in USD (min $5)", type=int)
@lightbulb.option("type", "Gift type", choices=[
    "PayPal", "Steam", "Google Play", "Apple Store",
    "Discord Nitro Basic", "Discord Nitro Boost", "Nintendo Card", "Roblox"
])
@lightbulb.command("buy", "Purchase a gift with your coins")
@lightbulb.implements(lightbulb.SlashCommand)
async def buy_cmd(ctx: lightbulb.SlashContext):
    if is_banned(ctx.guild_id, ctx.member):
        await ctx.respond("You are banned from using this bot.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gd = get_guild_data(ctx.guild_id)
    if ctx.options.amount < 5:
        await ctx.respond("❌ Minimum purchase is **$5**.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    if ctx.options.type == "PayPal" and (not ctx.options.gmail or "@" not in ctx.options.gmail):
        await ctx.respond("❌ A valid Gmail address is required for PayPal.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    if ctx.options.type != "PayPal" and (not ctx.options.region or len(ctx.options.region) < 2):
        await ctx.respond("❌ A valid region (e.g. US, UK) is required.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    week_key = f"{ctx.user.id}_{iso.year}_{iso.week}"
    weekly_spent = gd.setdefault('weekly_spent', {})
    spent_this_week = weekly_spent.get(week_key, 0)
    if spent_this_week + ctx.options.amount > WEEKLY_LIMIT_USD:
        days_until_monday = (7 - now.weekday()) % 7 or 7
        reset_dt = (now + timedelta(days=days_until_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        reset_ts = int(reset_dt.timestamp())
        remaining = WEEKLY_LIMIT_USD - spent_this_week
        await ctx.respond(
            f"❌ Weekly limit reached! You've spent **${spent_this_week}/$20** this week.\n"
            f"{'You have $' + str(remaining) + ' left.' if remaining > 0 else 'No budget left.'}\n"
            f"Resets <t:{reset_ts}:R>.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return
    price = gd['config'].get('shop_prices', {}).get(ctx.options.type, 100)
    req = ctx.options.amount * price
    bal = gd['users'].get(ctx.user.id, 0)
    if bal < req:
        await ctx.respond(
            f"❌ You need **{req:,} coins** but only have **{bal:,}**.",
            flags=hikari.MessageFlag.EPHEMERAL
        )
        return
    app_id = gd['config'].get('approval_channel')
    if not app_id:
        await ctx.respond("❌ The shop hasn't been set up yet. Contact an admin.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    emb = hikari.Embed(title="🛒 New Purchase Request", color=0x5865F2)
    emb.set_thumbnail(ctx.user.avatar_url or ctx.user.default_avatar_url)
    emb.add_field("User", f"{ctx.user.username} (<@{ctx.user.id}>)", inline=True)
    emb.add_field("Item", f"{ctx.options.type} (${ctx.options.amount})", inline=True)
    coin_str = f"{req:,}"
    pref = gd.get('user_prefs', {}).get(ctx.user.id, {}).get('currency', 'USD').upper()
    if pref != 'USD':
        rate = await get_exchange_rate(pref)
        if rate:
            coin_str += f" (~{ctx.options.amount * rate:.2f} {pref})"
    emb.add_field("Coins", coin_str, inline=True)
    emb.add_field("Region", ctx.options.region or "N/A", inline=True)
    emb.add_field("Contact", f"Gmail: {ctx.options.gmail}" if ctx.options.type == "PayPal" else "N/A", inline=True)
    emb.add_field("User Balance", f"{bal:,} coins", inline=True)
    emb.timestamp = now
    view = ShopApprovalView(ctx.guild_id, ctx.user.id, ctx.options.type, ctx.options.amount, req)
    msg = await bot.rest.create_message(app_id, embed=emb, components=view)
    miru_client.start_view(view)
    gd['pending_purchases'][str(msg.id)] = {
        'user_id': ctx.user.id,
        'type': ctx.options.type,
        'amount': ctx.options.amount,
        'coins': req,
        'channel_id': app_id
    }
    weekly_spent[week_key] = spent_this_week + ctx.options.amount
    await save_data()
    await ctx.respond(
        f"✅ Request sent! Your coins won't be deducted until it's approved.\n"
        f"Weekly spend: **${spent_this_week + ctx.options.amount}/$20**",
        flags=hikari.MessageFlag.EPHEMERAL
    )


@bot.command
@lightbulb.option("currency", "Currency code (e.g. EUR, GBP, EGP)", required=True)
@lightbulb.command("currency", "Set your preferred display currency")
@lightbulb.implements(lightbulb.SlashCommand)
async def currency_cmd(ctx: lightbulb.SlashContext):
    curr = ctx.options.currency.upper()
    rate = await get_exchange_rate(curr)
    if not rate:
        await ctx.respond(f"❌ Invalid or unsupported currency: **{curr}**", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gd = get_guild_data(ctx.guild_id)
    gd.setdefault('user_prefs', {}).setdefault(ctx.user.id, {})['currency'] = curr
    mark_dirty()
    await ctx.respond(f"✅ Display currency set to **{curr}**.", flags=hikari.MessageFlag.EPHEMERAL)


@bot.command
@lightbulb.command("help", "Show all available commands")
@lightbulb.implements(lightbulb.SlashCommand)
async def help_cmd(ctx: lightbulb.SlashContext):
    is_mod = MOD_ROLE_ID in ctx.member.role_ids or ctx.user.id == OWNER_ID
    emb = hikari.Embed(title="📚 Hyper Coin — Commands", color=0x5865F2)
    emb.add_field(
        "👤 User",
        "• `/daily` — Claim daily reward\n"
        "• `/balance` — Check your coins & stats\n"
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
            "• `/allowedrole` — Manage allowed roles\n"
            "• `/setapproval` — Set purchase approval channel\n"
            "• `/setlog` — Set admin log channel\n"
            "• `/setprice` — Set gift prices\n"
            "• `/drop` — Drop coins in channel\n"
            "• `/setdrop` — Set auto-drop channel\n"
            "• `/customize` — Change bot avatar (Owner only)"
        )
    emb.set_footer(text="💡 Users earn coins by chatting (1 coin/min) and daily rewards.")
    await ctx.respond(embed=emb, flags=hikari.MessageFlag.EPHEMERAL)

# ── Staff commands ────────────────────────────────────────────────────────────

@bot.command
@lightbulb.option("amount", "Amount of coins", type=int)
@lightbulb.option("user", "Target user", type=hikari.User)
@lightbulb.option("action", "Add or remove coins", choices=["add", "remove"])
@lightbulb.command("coins", "Add or remove coins from a user")
@lightbulb.implements(lightbulb.SlashCommand)
async def coins_cmd(ctx: lightbulb.SlashContext):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        return
    gd = get_guild_data(ctx.guild_id)
    uid = ctx.options.user.id
    old_bal = gd['users'].get(uid, 0)
    if ctx.options.action == "add":
        gd['users'][uid] = old_bal + ctx.options.amount
    else:
        gd['users'][uid] = max(0, old_bal - ctx.options.amount)
    new_bal = gd['users'][uid]
    await save_data()
    await ctx.respond(
        f"✅ {ctx.options.action.capitalize()}ed **{ctx.options.amount:,} coins** {'to' if ctx.options.action == 'add' else 'from'} <@{uid}>. "
        f"New balance: **{new_bal:,}**.",
        flags=hikari.MessageFlag.EPHEMERAL
    )
    log_id = gd['config'].get('log_channel')
    if log_id:
        try:
            emb = hikari.Embed(title="💰 Coins Adjusted", color=0x5865F2)
            emb.add_field("Staff", ctx.user.mention, inline=True)
            emb.add_field("User", f"<@{uid}>", inline=True)
            emb.add_field("Action", ctx.options.action.capitalize(), inline=True)
            emb.add_field("Amount", f"{ctx.options.amount:,}", inline=True)
            emb.add_field("Old Balance", f"{old_bal:,}", inline=True)
            emb.add_field("New Balance", f"{new_bal:,}", inline=True)
            emb.timestamp = datetime.now(timezone.utc)
            await bot.rest.create_message(log_id, embed=emb)
        except Exception as e:
            logger.warning(f"Log failed for /coins: {e}")


@bot.command
@lightbulb.option("channel", "Channel to exclude", type=hikari.TextableGuildChannel, required=False)
@lightbulb.option("action", "Action", choices=["add", "remove", "show"])
@lightbulb.command("uncounted", "Manage channels excluded from earning coins")
@lightbulb.implements(lightbulb.SlashCommand)
async def uncounted_cmd(ctx: lightbulb.SlashContext):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        return
    gd = get_guild_data(ctx.guild_id)
    if ctx.options.action == "show":
        chs = ', '.join([f"<#{c}>" for c in gd['uncounted']]) or "None"
        await ctx.respond(f"🚫 Excluded channels: {chs}", flags=hikari.MessageFlag.EPHEMERAL)
        return
    target_id = ctx.options.channel.id if ctx.options.channel else ctx.channel_id
    if ctx.options.action == "add":
        gd['uncounted'].add(target_id)
    else:
        gd['uncounted'].discard(target_id)
    mark_dirty()
    await ctx.respond("✅ Updated.", flags=hikari.MessageFlag.EPHEMERAL)
    log_id = gd['config'].get('log_channel')
    if log_id:
        try:
            emb = hikari.Embed(title="🚫 Uncounted Channels Updated", color=0x5865F2)
            emb.add_field("Staff", ctx.user.mention, inline=True)
            emb.add_field("Action", ctx.options.action.capitalize(), inline=True)
            emb.add_field("Channel", f"<#{target_id}>", inline=True)
            emb.timestamp = datetime.now(timezone.utc)
            await bot.rest.create_message(log_id, embed=emb)
        except Exception as e:
            logger.warning(f"Log failed for /uncounted: {e}")


@bot.command
@lightbulb.option("role", "Role to manage", type=hikari.Role)
@lightbulb.option("action", "Action", choices=["add", "remove", "show"])
@lightbulb.command("allowedrole", "Restrict coin earning to specific roles")
@lightbulb.implements(lightbulb.SlashCommand)
async def allowed_role_cmd(ctx: lightbulb.SlashContext):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        return
    gd = get_guild_data(ctx.guild_id)
    if ctx.options.action == "show":
        roles = ', '.join([f"<@&{r}>" for r in gd.get('allowed_roles', [])]) or "None (all users can earn)"
        await ctx.respond(f"✅ Allowed roles: {roles}", flags=hikari.MessageFlag.EPHEMERAL)
        return
    roles = gd.setdefault('allowed_roles', [])
    if ctx.options.action == "add":
        if ctx.options.role.id not in roles:
            roles.append(ctx.options.role.id)
    else:
        if ctx.options.role.id in roles:
            roles.remove(ctx.options.role.id)
    mark_dirty()
    await ctx.respond("✅ Updated.", flags=hikari.MessageFlag.EPHEMERAL)
    log_id = gd['config'].get('log_channel')
    if log_id:
        try:
            emb = hikari.Embed(title="✅ Allowed Roles Updated", color=0x5865F2)
            emb.add_field("Staff", ctx.user.mention, inline=True)
            emb.add_field("Action", ctx.options.action.capitalize(), inline=True)
            emb.add_field("Role", f"<@&{ctx.options.role.id}>", inline=True)
            emb.timestamp = datetime.now(timezone.utc)
            await bot.rest.create_message(log_id, embed=emb)
        except Exception as e:
            logger.warning(f"Log failed for /allowedrole: {e}")


@bot.command
@lightbulb.option("role", "Role to ban from bot usage", type=hikari.Role)
@lightbulb.command("bannedrole", "Set the role that cannot use the bot")
@lightbulb.implements(lightbulb.SlashCommand)
async def banned_role_cmd(ctx: lightbulb.SlashContext):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        return
    gd = get_guild_data(ctx.guild_id)
    gd['banned_role'] = ctx.options.role.id
    mark_dirty()
    await ctx.respond(f"✅ Banned role set to <@&{ctx.options.role.id}>.", flags=hikari.MessageFlag.EPHEMERAL)
    log_id = gd['config'].get('log_channel')
    if log_id:
        try:
            emb = hikari.Embed(title="🚫 Banned Role Updated", color=0x5865F2)
            emb.add_field("Staff", ctx.user.mention, inline=True)
            emb.add_field("Role", f"<@&{ctx.options.role.id}>", inline=True)
            emb.timestamp = datetime.now(timezone.utc)
            await bot.rest.create_message(log_id, embed=emb)
        except Exception as e:
            logger.warning(f"Log failed for /bannedrole: {e}")


@bot.command
@lightbulb.option("item", "The gift to reprice", choices=[
    "PayPal", "Steam", "Google Play", "Apple Store",
    "Discord Nitro Basic", "Discord Nitro Boost", "Nintendo Card", "Roblox"
])
@lightbulb.option("price", "Coins per $1 USD", type=int)
@lightbulb.command("setprice", "Set how many coins equal $1 for an item")
@lightbulb.implements(lightbulb.SlashCommand)
async def setprice_cmd(ctx: lightbulb.SlashContext):
    if ctx.user.id != OWNER_ID:
        return
    gd = get_guild_data(ctx.guild_id)
    gd['config'].setdefault('shop_prices', {})[ctx.options.item] = ctx.options.price
    mark_dirty()
    await ctx.respond(f"✅ Price for **{ctx.options.item}** set to **{ctx.options.price} coins/$1**.", flags=hikari.MessageFlag.EPHEMERAL)
    log_id = gd['config'].get('log_channel')
    if log_id:
        try:
            emb = hikari.Embed(title="⚙️ Price Updated", color=0x5865F2)
            emb.add_field("Staff", ctx.user.mention, inline=True)
            emb.add_field("Item", ctx.options.item, inline=True)
            emb.add_field("New Price", f"{ctx.options.price} coins/$1", inline=True)
            emb.timestamp = datetime.now(timezone.utc)
            await bot.rest.create_message(log_id, embed=emb)
        except Exception as e:
            logger.warning(f"Log failed for /setprice: {e}")


@bot.command
@lightbulb.option("channel", "Log channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setlog", "Set the admin log channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def setlog_cmd(ctx: lightbulb.SlashContext):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        return
    gd = get_guild_data(ctx.guild_id)
    gd['config']['log_channel'] = ctx.options.channel.id
    mark_dirty()
    await ctx.respond("✅ Log channel set.", flags=hikari.MessageFlag.EPHEMERAL)
    try:
        emb = hikari.Embed(title="📝 Log Channel Set", color=0x5865F2)
        emb.add_field("Staff", ctx.user.mention, inline=True)
        emb.add_field("Channel", f"<#{ctx.options.channel.id}>", inline=True)
        emb.timestamp = datetime.now(timezone.utc)
        await bot.rest.create_message(ctx.options.channel.id, embed=emb)
    except Exception as e:
        logger.warning(f"Post to log channel failed: {e}")


@bot.command
@lightbulb.option("channel", "Approval channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setapproval", "Set the purchase approval channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def setapproval_cmd(ctx: lightbulb.SlashContext):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        return
    gd = get_guild_data(ctx.guild_id)
    gd['config']['approval_channel'] = ctx.options.channel.id
    mark_dirty()
    await ctx.respond("✅ Approval channel set.", flags=hikari.MessageFlag.EPHEMERAL)
    log_id = gd['config'].get('log_channel')
    if log_id:
        try:
            emb = hikari.Embed(title="📋 Approval Channel Updated", color=0x5865F2)
            emb.add_field("Staff", ctx.user.mention, inline=True)
            emb.add_field("Channel", f"<#{ctx.options.channel.id}>", inline=True)
            emb.timestamp = datetime.now(timezone.utc)
            await bot.rest.create_message(log_id, embed=emb)
        except Exception as e:
            logger.warning(f"Log failed for /setapproval: {e}")


@bot.command
@lightbulb.option("channel", "Channel for auto coin drops", type=hikari.TextableGuildChannel)
@lightbulb.command("setdrop", "Set the auto coin drop channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def setdrop_cmd(ctx: lightbulb.SlashContext):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        return
    gd = get_guild_data(ctx.guild_id)
    gd['config']['drop_channel'] = ctx.options.channel.id
    mark_dirty()
    await ctx.respond(f"✅ Auto coin drop channel set to <#{ctx.options.channel.id}>.", flags=hikari.MessageFlag.EPHEMERAL)
    log_id = gd['config'].get('log_channel')
    if log_id:
        try:
            emb = hikari.Embed(title="🎁 Drop Channel Updated", color=0x5865F2)
            emb.add_field("Staff", ctx.user.mention, inline=True)
            emb.add_field("Channel", f"<#{ctx.options.channel.id}>", inline=True)
            emb.timestamp = datetime.now(timezone.utc)
            await bot.rest.create_message(log_id, embed=emb)
        except Exception as e:
            logger.warning(f"Log failed for /setdrop: {e}")


@bot.command
@lightbulb.option("amount", "Amount of coins to drop (default: random 5–50)", type=int, required=False)
@lightbulb.command("drop", "Drop coins for the first user to claim")
@lightbulb.implements(lightbulb.SlashCommand)
async def drop_cmd(ctx: lightbulb.SlashContext):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        return
    amount = ctx.options.amount or random.randint(5, 50)
    if amount < 1:
        await ctx.respond("❌ Amount must be at least 1.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    view = DropClaimView(ctx.guild_id, amount)
    emb = hikari.Embed(
        title="🎁 Coin Drop!",
        description=f"**{amount:,} coins** are up for grabs!\nFirst one to click wins!",
        color=0xFFD700
    )
    emb.set_footer(text=f"Dropped by {ctx.user.username}")
    emb.timestamp = datetime.now(timezone.utc)
    await ctx.respond(embed=emb, components=view)
    miru_client.start_view(view)


@bot.command
@lightbulb.option("avatar", "New avatar image URL", required=True)
@lightbulb.command("customize", "Change the bot's avatar (Owner only)")
@lightbulb.implements(lightbulb.SlashCommand)
async def customize_cmd(ctx: lightbulb.SlashContext):
    if ctx.user.id != OWNER_ID:
        return
    try:
        async with http_session.get(ctx.options.avatar) as r:
            if r.status == 200:
                await bot.rest.edit_my_user(avatar=await r.read())
                await ctx.respond("✅ Avatar updated!", flags=hikari.MessageFlag.EPHEMERAL)
            else:
                await ctx.respond("❌ Couldn't fetch image from that URL.", flags=hikari.MessageFlag.EPHEMERAL)
                return
    except Exception as e:
        logger.warning(f"Avatar update failed: {e}")
        await ctx.respond(f"❌ Error: {e}", flags=hikari.MessageFlag.EPHEMERAL)
        return
    log_id = get_guild_data(ctx.guild_id)['config'].get('log_channel')
    if log_id:
        try:
            emb = hikari.Embed(title="🖼️ Bot Avatar Updated", color=0x5865F2)
            emb.add_field("Staff", ctx.user.mention, inline=True)
            emb.set_image(ctx.options.avatar)
            emb.timestamp = datetime.now(timezone.utc)
            await bot.rest.create_message(log_id, embed=emb)
        except Exception as e:
            logger.warning(f"Log failed for /customize: {e}")


@bot.command
@lightbulb.command("ping", "Check bot latency")
@lightbulb.implements(lightbulb.SlashCommand)
async def ping_cmd(ctx: lightbulb.SlashContext):
    await ctx.respond(f"🏓 **{bot.heartbeat_latency * 1000:.0f}ms**", flags=hikari.MessageFlag.EPHEMERAL)

# ── Background tasks ──────────────────────────────────────────────────────────

async def auto_drop_loop():
    await asyncio.sleep(10)
    while True:
        wait_time = random.randint(1800, 5400)
        await asyncio.sleep(wait_time)
        for gid, gd in list(guild_data.items()):
            drop_ch = gd['config'].get('drop_channel')
            if not drop_ch:
                continue
            amount = random.randint(5, 50)
            view = DropClaimView(gid, amount)
            emb = hikari.Embed(
                title="🎁 Auto Coin Drop!",
                description=f"**{amount:,} coins** appeared!\nFirst one to click wins!",
                color=0xFFD700
            )
            emb.timestamp = datetime.now(timezone.utc)
            try:
                await bot.rest.create_message(drop_ch, embed=emb, components=view)
                miru_client.start_view(view)
            except Exception as e:
                logger.warning(f"Auto-drop failed for guild {gid}: {e}")

# ── Bot events ────────────────────────────────────────────────────────────────

@bot.listen(hikari.StartedEvent)
async def on_start(_):
    global http_session
    http_session = aiohttp.ClientSession()
    load_data()
    asyncio.create_task(autosave_loop())
    asyncio.create_task(auto_drop_loop())
    for gid_str, gd in guild_data.items():
        gid = int(gid_str)
        for msg_id_str, p in list(gd.get('pending_purchases', {}).items()):
            try:
                msg = await bot.rest.fetch_message(p['channel_id'], int(msg_id_str))
                view = ShopApprovalView(gid, p['user_id'], p['type'], p['amount'], p['coins'])
                miru_client.start_view(view, bind_to=msg)
            except Exception as e:
                logger.warning(f"Re-attach view failed for msg {msg_id_str}: {e}")
                gd.get('pending_purchases', {}).pop(msg_id_str, None)
    await bot.update_presence(
        status=hikari.Status.DO_NOT_DISTURB,
        activity=hikari.Activity(name="Zo's wallet", type=hikari.ActivityType.WATCHING)
    )
    logger.info("Hyper Coin Bot started.")


@bot.listen(hikari.StoppingEvent)
async def on_stop(_):
    await save_data()
    if http_session:
        await http_session.close()
    logger.info("Bot stopped, data saved.")


if __name__ == "__main__":
    bot.run()
