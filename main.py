import hikari
import lightbulb
import miru
import asyncio
import aiohttp
import aiofiles
import random
import logging
import re
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import json
import os
from io import BytesIO
from dotenv import dotenv_values

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("DiscordBot")

config_env = dotenv_values(".env")
BOT_TOKEN = config_env.get('BOT_TOKEN')

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in .env file!")
    exit(1)

bot = lightbulb.BotApp(
    token=BOT_TOKEN,
    intents=hikari.Intents.ALL,
    default_enabled_guilds=()
)

miru_client = miru.Client(bot)

OWNER_ID = 823310792291385424
MOD_ROLE_ID = 1373312465626202222

guild_data = {}

def get_guild_data(guild_id):
    if guild_id not in guild_data:
        guild_data[guild_id] = {
            'users': {},
            'user_prefs': {},
            'cooldowns': {},
            'daily_earnings': defaultdict(int),
            'last_daily': {},
            'streaks': {},
            'last_activity': {},
            'uncounted': set(),
            'banned_role': None,
            'allowed_roles': [],
            'config': {
                'approval_channel': None,
                'price_per_usd': 100,
                'shop_prices': {
                    'PayPal': 100, 'Steam': 100, 'Google Play': 100,
                    'Apple Store': 100, 'Discord Nitro Basic': 100,
                    'Discord Nitro Boost': 100, 'Nintendo Card': 100, 'Roblox': 100
                },
                'log_channel': None,
                'price_history': []
            }
        }
    return guild_data[guild_id]

def load_data():
    global guild_data
    try:
        if os.path.exists('data.json'):
            with open('data.json', 'r') as f:
                data = json.load(f)
                for gid, gdata in data.items():
                    guild_id = int(gid)
                    guild_data[guild_id] = {
                        'users': {int(k): v for k, v in gdata.get('users', {}).items()},
                        'user_prefs': {int(k): v for k, v in gdata.get('user_prefs', {}).items()},
                        'cooldowns': {},
                        'daily_earnings': defaultdict(int),
                        'last_daily': {int(k): datetime.fromisoformat(v) for k, v in gdata.get('daily', {}).items()},
                        'streaks': {int(k): v for k, v in gdata.get('streaks', {}).items()},
                        'last_activity': {int(k): datetime.fromisoformat(v) for k, v in gdata.get('activity', {}).items()},
                        'uncounted': set(gdata.get('uncounted', [])),
                        'banned_role': gdata.get('banned_role'),
                        'allowed_roles': gdata.get('allowed_roles', []),
                        'config': gdata.get('config', {})
                    }
                    config = guild_data[guild_id]['config']
                    config.setdefault('price_per_usd', 100)
                    config.setdefault('shop_prices', {
                        'PayPal': 100, 'Steam': 100, 'Google Play': 100,
                        'Apple Store': 100, 'Discord Nitro Basic': 100,
                        'Discord Nitro Boost': 100, 'Nintendo Card': 100, 'Roblox': 100
                    })
        logger.info("Data loaded successfully")
    except Exception as e:
        logger.error(f"Error loading data: {e}")

async def save_data():
    data = {}
    for gid, gdata in guild_data.items():
        data[str(gid)] = {
            'users': gdata['users'],
            'user_prefs': gdata.get('user_prefs', {}),
            'daily': {k: v.isoformat() for k, v in gdata['last_daily'].items()},
            'streaks': gdata['streaks'],
            'activity': {k: v.isoformat() for k, v in gdata['last_activity'].items()},
            'uncounted': list(gdata['uncounted']),
            'banned_role': gdata['banned_role'],
            'allowed_roles': gdata['allowed_roles'],
            'config': gdata['config']
        }
    try:
        async with aiofiles.open('data.json', 'w') as f:
            await f.write(json.dumps(data, indent=4))
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def get_streak_mult(guild_id, user_id):
    gdata = get_guild_data(guild_id)
    streak = gdata['streaks'].get(user_id, 0)
    mults = {0: 1, 1: 1.25, 2: 1.5, 3: 1.75, 4: 2, 5: 2.25, 6: 2.25, 7: 2.5}
    return mults.get(min(streak, 7), 1)

def is_banned(guild_id, member):
    gdata = get_guild_data(guild_id)
    if not gdata['banned_role']: return False
    return gdata['banned_role'] in member.role_ids

def is_booster(member):
    if not member: return False
    return member.premium_since is not None

async def add_coins(guild_id, user_id, amount, member=None):
    gdata = get_guild_data(guild_id)
    allowed_roles = gdata.get('allowed_roles', [])
    if allowed_roles and member:
        if not any(role_id in member.role_ids for role_id in allowed_roles): return False
    if user_id not in gdata['users']: gdata['users'][user_id] = 0
    today = datetime.now(timezone.utc).date()
    daily_key = f"{user_id}_{today}"
    is_boosting = is_booster(member) if member else False
    daily_limit = 400 if is_boosting else 200
    if gdata['daily_earnings'][daily_key] >= daily_limit: return False
    mult = get_streak_mult(guild_id, user_id)
    if is_boosting: mult *= 2
    actual = int(amount * mult)
    if gdata['daily_earnings'][daily_key] + actual > daily_limit:
        actual = daily_limit - gdata['daily_earnings'][daily_key]
    gdata['users'][user_id] += actual
    gdata['daily_earnings'][daily_key] += actual
    await save_data()
    return True

class LeaderboardView(miru.View):
    def __init__(self, guild_id, pages, current_page=0):
        super().__init__(timeout=60.0)
        self.guild_id, self.pages, self.current_page = guild_id, pages, current_page
    async def update_message(self, ctx: miru.ViewContext):
        await ctx.edit_response(embed=self.pages[self.current_page], components=self)
    @miru.button(label="<<<", style=hikari.ButtonStyle.DANGER)
    async def first_page(self, ctx: miru.ViewContext, _):
        self.current_page = 0; await self.update_message(ctx)
    @miru.button(label="<", style=hikari.ButtonStyle.PRIMARY)
    async def prev_1(self, ctx: miru.ViewContext, _):
        self.current_page = max(0, self.current_page - 1); await self.update_message(ctx)
    @miru.button(label=">", style=hikari.ButtonStyle.PRIMARY)
    async def next_1(self, ctx: miru.ViewContext, _):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1); await self.update_message(ctx)
    @miru.button(label=">>>", style=hikari.ButtonStyle.DANGER)
    async def last_page(self, ctx: miru.ViewContext, _):
        self.current_page = len(self.pages) - 1; await self.update_message(ctx)

@bot.command
@lightbulb.option("gmail", "PayPal Gmail", required=False)
@lightbulb.option("region", "Gift Card Region", required=False)
@lightbulb.option("amount", "USD Amount (Min $5)", type=int)
@lightbulb.option("type", "Gift Type", choices=["PayPal", "Steam", "Google Play", "Apple Store", "Discord Nitro Basic", "Discord Nitro Boost", "Nintendo Card", "Roblox"])
@lightbulb.command("buy", "Purchase a gift")
@lightbulb.implements(lightbulb.SlashCommand)
async def buy_cmd(ctx):
    if is_banned(ctx.guild_id, ctx.member):
        await ctx.respond("Banned.", flags=hikari.MessageFlag.EPHEMERAL); return
    gdata = get_guild_data(ctx.guild_id)
    if ctx.options.amount < 5:
        await ctx.respond("Min $5.", flags=hikari.MessageFlag.EPHEMERAL); return
    if ctx.options.type == "PayPal" and (not ctx.options.gmail or "@" not in ctx.options.gmail):
        await ctx.respond("Valid Gmail needed.", flags=hikari.MessageFlag.EPHEMERAL); return
    if ctx.options.type != "PayPal" and (not ctx.options.region or len(ctx.options.region) < 2):
        await ctx.respond("Valid region needed.", flags=hikari.MessageFlag.EPHEMERAL); return
    price = gdata['config'].get('shop_prices', {}).get(ctx.options.type, 100)
    req = ctx.options.amount * price
    if gdata['users'].get(ctx.user.id, 0) < req:
        await ctx.respond(f"Need {req} coins.", flags=hikari.MessageFlag.EPHEMERAL); return
    app_id = gdata['config'].get('approval_channel')
    if not app_id:
        await ctx.respond("Shop not set up.", flags=hikari.MessageFlag.EPHEMERAL); return
    gdata['users'][ctx.user.id] -= req; await save_data()
    emb = hikari.Embed(title="🛒 New Purchase Request", color=0x5865F2)
    emb.add_field("User", f"{ctx.user.username} (<@{ctx.user.id}>)", inline=True)
    emb.add_field("Item", f"{ctx.options.type} (${ctx.options.amount})", inline=True)
    coin_str = f"{req}"
    pref = gdata.get('user_prefs', {}).get(ctx.user.id, {}).get('currency', 'USD').upper()
    if pref != 'USD':
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={pref}") as r:
                    if r.status == 200:
                        rate = (await r.json())['rates'].get(pref)
                        if rate: coin_str += f" ({ctx.options.amount * rate:.2f} {pref})"
        except: pass
    emb.add_field("Coins", coin_str, inline=True)
    emb.add_field("Region", ctx.options.region or "N/A", inline=True)
    emb.add_field("Contact", f"Gmail: {ctx.options.gmail}" if ctx.options.type == "PayPal" else "N/A", inline=True)
    emb.timestamp = datetime.now(timezone.utc)
    view = ShopApprovalView(ctx.guild_id, ctx.user.id, ctx.options.type, ctx.options.amount, req)
    await bot.rest.create_message(app_id, embed=emb, components=view); miru_client.start_view(view)
    await ctx.respond("Request sent.", flags=hikari.MessageFlag.EPHEMERAL)

class ShopApprovalView(miru.View):
    def __init__(self, gid, uid, rtype, amt, coins):
        super().__init__(timeout=None)
        self.gid, self.uid, self.rtype, self.amt, self.coins = gid, uid, rtype, amt, coins
    @miru.button(label="Accept", style=hikari.ButtonStyle.SUCCESS)
    async def approve(self, ctx: miru.ViewContext, _):
        member = await bot.rest.fetch_member(self.gid, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL); return
        modal = ShopAcceptModal(self.uid, self.rtype, self.amt, ctx.message.id)
        await ctx.respond_with_modal(modal); miru_client.start_modal(modal)
    @miru.button(label="Reject", style=hikari.ButtonStyle.DANGER)
    async def reject(self, ctx: miru.ViewContext, _):
        member = await bot.rest.fetch_member(self.gid, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL); return
        gdata = get_guild_data(self.gid)
        gdata['users'][self.uid] = gdata['users'].get(self.uid, 0) + self.coins; await save_data()
        try:
            u = await bot.rest.fetch_user(self.uid)
            await u.send(f"Your request for {self.rtype} (${self.amt}) was rejected. Coins refunded.")
        except: pass
        await ctx.edit_response(content=f"❌ Rejected by {ctx.user.mention}.", embed=None, components=[])
        log_id = gdata['config'].get('log_channel')
        if log_id:
            emb = hikari.Embed(title="❌ Purchase Rejected", color=0xFF0000)
            emb.add_field("User", f"<@{self.uid}>", inline=True); emb.add_field("Staff", ctx.user.mention, inline=True)
            emb.timestamp = datetime.now(timezone.utc); await bot.rest.create_message(log_id, embed=emb)

class ShopAcceptModal(miru.Modal):
    def __init__(self, uid, rtype, amt, mid):
        super().__init__("Accept Request")
        self.uid, self.rtype, self.amt, self.mid = uid, rtype, amt, mid
        self.details = miru.TextInput(label="Gift Code / Details", style=hikari.TextInputStyle.PARAGRAPH, required=True)
        self.add_item(self.details)
    async def callback(self, ctx: miru.ModalContext):
        try:
            u = await bot.rest.fetch_user(self.uid)
            await u.send(embed=hikari.Embed(title="✅ Approved", color=0x00FF00).add_field("Item", f"{self.rtype} (${self.amt})").add_field("Details", f"```\n{self.details.value}\n```"))
            edit_emb = hikari.Embed(title="✅ Request Approved", color=0x00FF00)
            edit_emb.description = f"Sent to <@{self.uid}>!\n\n**Staff:** {ctx.user.mention}\n**Item:** {self.rtype} (${self.amt})"
            await bot.rest.edit_message(ctx.channel_id, self.mid, embed=edit_emb, components=[])
            await ctx.respond("Done.", flags=hikari.MessageFlag.EPHEMERAL)
            gdata = get_guild_data(ctx.guild_id); log_id = gdata['config'].get('log_channel')
            if log_id:
                emb = hikari.Embed(title="✅ Purchase Completed", color=0x00FF00)
                emb.add_field("User", f"<@{self.uid}>", inline=True); emb.add_field("Staff", ctx.user.mention, inline=True)
                emb.timestamp = datetime.now(timezone.utc); await bot.rest.create_message(log_id, embed=emb)
        except Exception as e: logger.error(f"Modal error: {e}")

@bot.listen(hikari.GuildMessageCreateEvent)
async def on_message(event):
    if event.is_bot or not event.guild_id: return
    gdata = get_guild_data(event.guild_id)
    if event.channel_id in gdata['uncounted'] or is_banned(event.guild_id, event.member): return
    await add_coins(event.guild_id, event.author_id, 1, event.member)

@bot.command
@lightbulb.command("daily", "Claim daily reward")
@lightbulb.implements(lightbulb.SlashCommand)
async def daily_cmd(ctx):
    if is_banned(ctx.guild_id, ctx.member): return
    gdata, now = get_guild_data(ctx.guild_id), datetime.now(timezone.utc)
    if ctx.user.id in gdata['last_daily'] and (now - gdata['last_daily'][ctx.user.id]).total_seconds() < 86400:
        await ctx.respond("Wait tomorrow.", flags=hikari.MessageFlag.EPHEMERAL); return
    amt = random.randint(1, 20) * (2 if is_booster(ctx.member) else 1)
    gdata['users'][ctx.user.id] = gdata['users'].get(ctx.user.id, 0) + amt
    gdata['last_daily'][ctx.user.id] = now
    gdata['streaks'][ctx.user.id] = min(gdata['streaks'].get(ctx.user.id, 0) + 1, 7)
    await save_data()
    emb = hikari.Embed(title="🎁 Daily Reward", description=f"Received **{amt} coins**!", color=0xFFD700)
    emb.add_field("Streak", f"{gdata['streaks'][ctx.user.id]} days", inline=True)
    await ctx.respond(embed=emb)

@bot.command
@lightbulb.option("channel", "Channel", type=hikari.TextableGuildChannel, required=False)
@lightbulb.option("action", "Action", choices=["add", "remove", "show"])
@lightbulb.command("uncounted", "Exclude channels")
@lightbulb.implements(lightbulb.SlashCommand)
async def uncounted_cmd(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID: return
    gdata = get_guild_data(ctx.guild_id)
    if ctx.options.action == "show":
        await ctx.respond(f"Uncounted: {', '.join([f'<#{c}>' for c in gdata['uncounted']]) or 'None'}", flags=hikari.MessageFlag.EPHEMERAL); return
    if ctx.options.action == "add": gdata['uncounted'].add(ctx.options.channel.id)
    else: gdata['uncounted'].discard(ctx.options.channel.id)
    await save_data(); await ctx.respond("Updated.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("price", "Price per $1", type=int)
@lightbulb.option("item", "Item", choices=["PayPal", "Steam", "Google Play", "Apple Store", "Discord Nitro Basic", "Discord Nitro Boost", "Nintendo Card", "Roblox"])
@lightbulb.command("setprice", "Set item price")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_price(ctx):
    if ctx.user.id != OWNER_ID: return
    get_guild_data(ctx.guild_id)['config'].setdefault('shop_prices', {})[ctx.options.item] = ctx.options.price
    await save_data(); await ctx.respond("Price set.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("amount", "Amount", type=int)
@lightbulb.option("user", "User", type=hikari.User)
@lightbulb.option("action", "Action", choices=["add", "remove"])
@lightbulb.command("coins", "Manage coins")
@lightbulb.implements(lightbulb.SlashCommand)
async def manage_coins(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID: return
    gdata = get_guild_data(ctx.guild_id)
    bal = gdata['users'].get(ctx.options.user.id, 0)
    gdata['users'][ctx.options.user.id] = (bal + ctx.options.amount) if ctx.options.action == "add" else max(0, bal - ctx.options.amount)
    await save_data(); await ctx.respond("Updated.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.command("help", "Commands")
@lightbulb.implements(lightbulb.SlashCommand)
async def help_cmd(ctx):
    is_mod = MOD_ROLE_ID in ctx.member.role_ids or ctx.user.id == OWNER_ID
    emb = hikari.Embed(title="📚 Commands", color=0x5865F2)
    emb.add_field("User", "• `/daily` • `/balance` • `/leaderboard` • `/buy` • `/currency` • `/help`")
    if is_mod: emb.add_field("Staff", "• `/uncounted` • `/coins` • `/banrole` • `/allowedroles` • `/setapproval` • `/setlog` • `/setprice` • `/customize`")
    await ctx.respond(embed=emb, flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("user", "User", type=hikari.User, required=False)
@lightbulb.command("balance", "Check balance")
@lightbulb.implements(lightbulb.SlashCommand)
async def balance_cmd(ctx):
    user = ctx.options.user or ctx.user; gdata = get_guild_data(ctx.guild_id)
    bal = gdata['users'].get(user.id, 0); pref = gdata.get('user_prefs', {}).get(user.id, {}).get('currency', 'USD').upper()
    usd = bal / gdata['config'].get('price_per_usd', 100); val_str = f"(${usd:.2f} USD)"
    if pref != 'USD':
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={pref}") as r:
                    if r.status == 200:
                        rate = (await r.json())['rates'].get(pref)
                        if rate: val_str = f"({usd * rate:.2f} {pref})"
        except: pass
    emb = hikari.Embed(title=f"💰 {user.username}", color=0xFFD700).set_thumbnail(user.avatar_url or user.default_avatar_url)
    emb.add_field("Coins", str(bal), inline=True).add_field("Value", val_str, inline=True).add_field("Multiplier", f"x{get_streak_mult(ctx.guild_id, user.id)}", inline=True)
    await ctx.respond(embed=emb)

@bot.command
@lightbulb.option("currency", "Currency code", required=True)
@lightbulb.command("currency", "Set display currency")
@lightbulb.implements(lightbulb.SlashCommand)
async def change_currency_cmd(ctx):
    curr = ctx.options.currency.upper()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.frankfurter.dev/v1/latest?symbols={curr}") as r:
                if r.status != 200: await ctx.respond(f"Invalid currency: {curr}", flags=hikari.MessageFlag.EPHEMERAL); return
    except: return
    get_guild_data(ctx.guild_id).setdefault('user_prefs', {}).setdefault(ctx.user.id, {})['currency'] = curr
    await save_data(); await ctx.respond(f"Currency set to {curr}.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("avatar", "New avatar URL", required=True)
@lightbulb.command("customize", "Change bot avatar (Owner only)")
@lightbulb.implements(lightbulb.SlashCommand)
async def customize_bot_cmd(ctx):
    if ctx.user.id != OWNER_ID: return
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(ctx.options.avatar) as r:
                if r.status == 200: await bot.rest.edit_my_user(avatar=await r.read())
        await ctx.respond("Avatar updated.", flags=hikari.MessageFlag.EPHEMERAL)
    except Exception as e: await ctx.respond(f"Error: {e}", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("channel", "Log channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setlog", "Set log channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_log_cmd(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID: return
    get_guild_data(ctx.guild_id)['config']['log_channel'] = ctx.options.channel.id
    await save_data(); await ctx.respond("Log channel set.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.command("leaderboard", "Top users")
@lightbulb.implements(lightbulb.SlashCommand)
async def leaderboard_cmd(ctx):
    users = sorted(get_guild_data(ctx.guild_id)['users'].items(), key=lambda x: x[1], reverse=True)
    if not users: await ctx.respond("No data.", flags=hikari.MessageFlag.EPHEMERAL); return
    pages = []
    for i in range(0, len(users), 10):
        emb = hikari.Embed(title="🏆 Leaderboard", color=0xFFD700)
        emb.description = "\n".join([f"**#{r}** <@{u}>: {c} coins" for r, (u, c) in enumerate(users[i:i+10], start=i+1)])
        emb.set_footer(text=f"Page {i//10 + 1}"); pages.append(emb)
    view = LeaderboardView(ctx.guild_id, pages)
    await ctx.respond(embed=pages[0], components=view); miru_client.start_view(view)

@bot.command
@lightbulb.option("channel", "Approval channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setapproval", "Set approval channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_approval_cmd(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID: return
    get_guild_data(ctx.guild_id)['config']['approval_channel'] = ctx.options.channel.id
    await save_data(); await ctx.respond("Approval channel set.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("role", "Role", type=hikari.Role)
@lightbulb.option("action", "Action", choices=["add", "remove", "show"])
@lightbulb.command("allowedroles", "Allowed roles")
@lightbulb.implements(lightbulb.SlashCommand)
async def allowed_roles_cmd(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID: return
    gdata = get_guild_data(ctx.guild_id)
    if ctx.options.action == "show":
        await ctx.respond(f"Allowed: {', '.join([f'<@&{r}>' for r in gdata.get('allowed_roles', [])]) or 'None'}", flags=hikari.MessageFlag.EPHEMERAL); return
    roles = gdata.setdefault('allowed_roles', [])
    if ctx.options.action == "add":
        if ctx.options.role.id not in roles: roles.append(ctx.options.role.id)
    else:
        if ctx.options.role.id in roles: roles.remove(ctx.options.role.id)
    await save_data(); await ctx.respond("Updated.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("role", "Role", type=hikari.Role)
@lightbulb.command("banrole", "Set banned role")
@lightbulb.implements(lightbulb.SlashCommand)
async def ban_role_cmd(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID: return
    get_guild_data(ctx.guild_id)['banned_role'] = ctx.options.role.id
    await save_data(); await ctx.respond("Banned role set.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.command("ping", "Latency")
@lightbulb.implements(lightbulb.SlashCommand)
async def ping_cmd(ctx): await ctx.respond(f"🏓 {bot.heartbeat_latency*1000:.0f}ms", flags=hikari.MessageFlag.EPHEMERAL)

@bot.listen(hikari.StartedEvent)
async def on_start(_):
    load_data()
    await bot.update_presence(status=hikari.Status.DO_NOT_DISTURB, activity=hikari.Activity(name="Zo's wallet", type=hikari.ActivityType.WATCHING))
    logger.info("Bot started")

if __name__ == "__main__": bot.run()
