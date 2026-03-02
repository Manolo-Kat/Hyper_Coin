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
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
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
                    'PayPal': 100,
                    'Steam': 100,
                    'Google Play': 100,
                    'Apple Store': 100,
                    'Discord Nitro Basic': 100,
                    'Discord Nitro Boost': 100,
                    'Nintendo Card': 100,
                    'Roblox': 100
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
                    config.setdefault('price_history', [])
                    config.setdefault('shop_prices', {
                        'PayPal': 100,
                        'Steam': 100,
                        'Google Play': 100,
                        'Apple Store': 100,
                        'Discord Nitro Basic': 100,
                        'Discord Nitro Boost': 100,
                        'Nintendo Card': 100,
                        'Roblox': 100
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

def check_streak(guild_id, user_id):
    gdata = get_guild_data(guild_id)
    now = datetime.now(timezone.utc)
    if user_id in gdata['last_activity']:
        diff = (now - gdata['last_activity'][user_id]).total_seconds() / 3600
        if diff > 24:
            gdata['streaks'][user_id] = 0
    gdata['last_activity'][user_id] = now

def is_banned(guild_id, member):
    gdata = get_guild_data(guild_id)
    if not gdata['banned_role']:
        return False
    return gdata['banned_role'] in member.role_ids

def is_booster(member):
    if not member:
        return False
    return member.premium_since is not None

async def add_coins(guild_id, user_id, amount, member=None):
    gdata = get_guild_data(guild_id)
    allowed_roles = gdata.get('allowed_roles', [])
    if allowed_roles and member:
        has_role = any(role_id in member.role_ids for role_id in allowed_roles)
        if not has_role:
            return False

    if user_id not in gdata['users']:
        gdata['users'][user_id] = 0

    today = datetime.now(timezone.utc).date()
    daily_key = f"{user_id}_{today}"
    is_boosting = is_booster(member) if member else False
    daily_limit = 400 if is_boosting else 200

    if gdata['daily_earnings'][daily_key] >= daily_limit:
        return False

    mult = get_streak_mult(guild_id, user_id)
    if is_boosting:
        mult *= 2
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
        self.guild_id = guild_id
        self.pages = pages
        self.current_page = current_page

    async def update_message(self, ctx: miru.ViewContext):
        embed = self.pages[self.current_page]
        await ctx.edit_response(embed=embed, components=self)

    @miru.button(label="<<<", style=hikari.ButtonStyle.DANGER)
    async def first_page(self, ctx: miru.ViewContext, button: miru.Button):
        self.current_page = 0
        await self.update_message(ctx)

    @miru.button(label="<<", style=hikari.ButtonStyle.SUCCESS)
    async def prev_5(self, ctx: miru.ViewContext, button: miru.Button):
        self.current_page = max(0, self.current_page - 5)
        await self.update_message(ctx)

    @miru.button(label="<", style=hikari.ButtonStyle.PRIMARY)
    async def prev_1(self, ctx: miru.ViewContext, button: miru.Button):
        self.current_page = max(0, self.current_page - 1)
        await self.update_message(ctx)

    @miru.button(label=">", style=hikari.ButtonStyle.PRIMARY)
    async def next_1(self, ctx: miru.ViewContext, button: miru.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        await self.update_message(ctx)

    @miru.button(label=">>", style=hikari.ButtonStyle.SUCCESS)
    async def next_5(self, ctx: miru.ViewContext, button: miru.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 5)
        await self.update_message(ctx)

    @miru.button(label=">>>", style=hikari.ButtonStyle.DANGER)
    async def last_page(self, ctx: miru.ViewContext, button: miru.Button):
        self.current_page = len(self.pages) - 1
        await self.update_message(ctx)

@bot.command
@lightbulb.option("gmail", "Your PayPal Gmail (PayPal only)", required=False)
@lightbulb.option("region", "Gift Card Region (Steam, Google Play, Apple, Nitro, Nintendo, Roblox)", required=False)
@lightbulb.option("amount", "Amount in USD (Minimum $5)", type=int)
@lightbulb.option("type", "Gift Type", choices=["PayPal", "Steam", "Google Play", "Apple Store", "Discord Nitro Basic", "Discord Nitro Boost", "Nintendo Card", "Roblox"])
@lightbulb.command("buy", "Purchase a gift with your coins")
@lightbulb.implements(lightbulb.SlashCommand)
async def buy_cmd(ctx):
    if is_banned(ctx.guild_id, ctx.member):
        await ctx.respond("Banned.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    gdata = get_guild_data(ctx.guild_id)
    gift_type = ctx.options.type
    usd_amount = ctx.options.amount
    region = ctx.options.region
    gmail = ctx.options.gmail

    if usd_amount < 5:
        await ctx.respond("Minimum purchase is $5.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    mapped_type = gift_type
    
    prefs = gdata.get('user_prefs', {}).get(ctx.user.id, {})
    pref_currency = prefs.get('currency', 'USD').upper()
    
    region_str = "N/A"
    contact_str = "N/A"
    
    if mapped_type == "PayPal":
        if not gmail or not re.match(r"[^@]+@[^@]+\.[^@]+", gmail):
            await ctx.respond("Please provide a valid Gmail for PayPal.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        contact_str = f"Gmail: {gmail}"
    else:
        if not region or len(region) < 2:
            await ctx.respond(f"Please provide a valid region for {mapped_type}.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        region_str = region

    shop_prices = gdata['config'].get('shop_prices', {})
    price_per_usd = shop_prices.get(mapped_type, gdata['config'].get('price_per_usd', 100))
    required_coins = usd_amount * price_per_usd
    
    user_balance = gdata['users'].get(ctx.user.id, 0)
    if user_balance < required_coins:
        await ctx.respond(f"Insufficient balance. You need {required_coins} coins.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    approval_channel_id = gdata['config'].get('approval_channel')
    if not approval_channel_id:
        await ctx.respond("Shop is not configured (no approval channel).", flags=hikari.MessageFlag.EPHEMERAL)
        return

    gdata['users'][ctx.user.id] -= required_coins
    await save_data()

    embed = hikari.Embed(title="🛒 New Purchase Request", color=0x5865F2)
    embed.set_thumbnail(ctx.user.avatar_url or ctx.user.default_avatar_url)
    embed.add_field("User", f"{ctx.user.username} (<@{ctx.user.id}>)", inline=True)
    embed.add_field("Type", mapped_type, inline=True)
    embed.add_field("Amount", f"${usd_amount} USD", inline=True)
    
    coin_display = f"{required_coins}"
    if pref_currency != 'USD':
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={pref_currency}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rate = data['rates'].get(pref_currency)
                        if rate:
                            val = usd_amount * rate
                            coin_display += f" ({val:.2f} {pref_currency})"
        except:
            pass
            
    embed.add_field("Coins", coin_display, inline=True)
    embed.add_field("Region", region_str, inline=True)
    embed.add_field("Contact", contact_str, inline=True)
    embed.timestamp = datetime.now(timezone.utc)

    view = ShopApprovalView(ctx.guild_id, ctx.user.id, mapped_type, usd_amount, required_coins)
    await bot.rest.create_message(approval_channel_id, embed=embed, components=view)
    miru_client.start_view(view)

    await ctx.respond("Your purchase request has been submitted. Coins deducted temporarily.", flags=hikari.MessageFlag.EPHEMERAL)

class ShopApprovalView(miru.View):
    def __init__(self, guild_id, user_id, reward_type, usd_amount, coin_amount):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.user_id = user_id
        self.reward_type = reward_type
        self.usd_amount = usd_amount
        self.coin_amount = coin_amount

    @miru.button(label="Accept", style=hikari.ButtonStyle.SUCCESS)
    async def approve(self, ctx: miru.ViewContext, button: miru.Button):
        member = await bot.rest.fetch_member(self.guild_id, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        modal = ShopAcceptModal(self.user_id, self.reward_type, self.usd_amount)
        await ctx.respond_with_modal(modal)
        miru_client.start_modal(modal)

    @miru.button(label="Reject", style=hikari.ButtonStyle.DANGER)
    async def reject(self, ctx: miru.ViewContext, button: miru.Button):
        member = await bot.rest.fetch_member(self.guild_id, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        
        gdata = get_guild_data(self.guild_id)
        gdata['users'][self.user_id] = gdata['users'].get(self.user_id, 0) + self.coin_amount
        await save_data()

        try:
            user = await bot.rest.fetch_user(self.user_id)
            await user.send(f"Your shop request for {self.reward_type} (${self.usd_amount}) was rejected. Your coins have been refunded.")
        except:
            pass
        
        await ctx.edit_response(content="Request rejected and user notified.", embed=None, components=[])

class ShopAcceptModal(miru.Modal):
    def __init__(self, user_id, reward_type, usd_amount):
        super().__init__("Accept Request - Enter Code")
        self.user_id = user_id
        self.reward_type = reward_type
        self.usd_amount = usd_amount
        self.code_input = miru.TextInput(label="Gift Code / Details", placeholder="Enter the code or delivery details here", style=hikari.TextInputStyle.PARAGRAPH, required=True)
        self.add_item(self.code_input)

    async def callback(self, ctx: miru.ModalContext):
        code = self.code_input.value
        try:
            user = await bot.rest.fetch_user(self.user_id)
            embed = hikari.Embed(title="✅ Shop Request Approved", color=0x00FF00)
            embed.add_field("Item", f"{self.reward_type} (${self.usd_amount})", inline=False)
            embed.add_field("Your Code", f"```\n{code}\n```", inline=False)
            await user.send(embed=embed)
            await ctx.respond("Code sent to user.", flags=hikari.MessageFlag.EPHEMERAL)
            await ctx.edit_response(content=f"Request approved and code sent to <@{self.user_id}>.", embed=None, components=[])
            
            gdata = get_guild_data(ctx.guild_id)
            log_channel_id = gdata['config'].get('log_channel')
            if log_channel_id:
                log_embed = hikari.Embed(title="✅ Purchase Completed", color=0x00FF00)
                log_embed.add_field("User", f"<@{self.user_id}>", inline=True)
                log_embed.add_field("Item", f"{self.reward_type} (${self.usd_amount})", inline=True)
                log_embed.timestamp = datetime.now(timezone.utc)
                await bot.rest.create_message(log_channel_id, embed=log_embed)
        except:
            await ctx.respond("Failed to DM user. Please contact them manually.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.listen(hikari.GuildMessageCreateEvent)
async def on_message(event):
    if event.is_bot or not event.guild_id:
        return
    gdata = get_guild_data(event.guild_id)
    if event.channel_id in gdata['uncounted']:
        return
    if is_banned(event.guild_id, event.member):
        return
    await add_coins(event.guild_id, event.author_id, 1, event.member)

@bot.command
@lightbulb.command("daily", "Claim your daily reward")
@lightbulb.implements(lightbulb.SlashCommand)
async def daily_cmd(ctx):
    gdata = get_guild_data(ctx.guild_id)
    member = ctx.member
    if member and is_banned(ctx.guild_id, member):
        await ctx.respond("You're banned from using this bot.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    user_id = ctx.user.id
    now = datetime.now(timezone.utc)
    if user_id in gdata['last_daily']:
        diff = (now - gdata['last_daily'][user_id]).total_seconds()
        if diff < 86400:
            remaining = 86400 - diff
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            await ctx.respond(f"Already claimed! Wait {hours}h {minutes}m", flags=hikari.MessageFlag.EPHEMERAL)
            return
        if diff > 172800:
            gdata['streaks'][user_id] = 0
    amount = random.randint(1, 20)
    is_boosting = is_booster(member)
    if is_boosting:
        amount *= 2
    gdata['users'][user_id] = gdata['users'].get(user_id, 0) + amount
    gdata['last_daily'][user_id] = now
    if user_id not in gdata['streaks']:
        gdata['streaks'][user_id] = 0
    else:
        gdata['streaks'][user_id] = min(gdata['streaks'][user_id] + 1, 7)
    check_streak(ctx.guild_id, user_id)
    await save_data()
    embed = hikari.Embed(title="🎁 Daily Reward", description=f"You received **{amount} coins**!" + (" (Booster Bonus!)" if is_boosting else ""), color=0xFFD700)
    embed.add_field("Streak", f"{gdata['streaks'][user_id]} days", inline=True)
    await ctx.respond(embed=embed)

@bot.command
@lightbulb.option("channel", "Channel to exclude", type=hikari.TextableGuildChannel, required=False)
@lightbulb.option("action", "add/remove/show", choices=["add", "remove", "show"])
@lightbulb.command("uncounted", "Manage uncounted channels")
@lightbulb.implements(lightbulb.SlashCommand)
async def uncounted_cmd(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gdata = get_guild_data(ctx.guild_id)
    action = ctx.options.action
    if action == "show":
        if gdata['uncounted']:
            channels = ", ".join([f"<#{c}>" for c in gdata['uncounted']])
            await ctx.respond(f"Uncounted: {channels}", flags=hikari.MessageFlag.EPHEMERAL)
        else:
            await ctx.respond("No uncounted channels.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    channel = ctx.options.channel
    if not channel:
        await ctx.respond("Specify a channel.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    if action == "add":
        gdata['uncounted'].add(channel.id)
        await save_data()
        await ctx.respond(f"Added {channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)
    else:
        gdata['uncounted'].discard(channel.id)
        await save_data()
        await ctx.respond(f"Removed {channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("price", "New price per $1 USD", type=int)
@lightbulb.option("item", "Shop item", choices=["PayPal", "Steam", "Google Play", "Apple Store", "Discord Nitro Basic", "Discord Nitro Boost", "Nintendo Card", "Roblox"])
@lightbulb.command("setprice", "Set reward price for an item")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_price(ctx):
    if ctx.user.id != OWNER_ID:
        await ctx.respond("Owner only.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gdata = get_guild_data(ctx.guild_id)
    item = ctx.options.item
    new_price = ctx.options.price
    gdata['config'].setdefault('shop_prices', {})[item] = new_price
    await save_data()
    await ctx.respond(f"Price for **{item}** set to **{new_price}** coins per $1 USD.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("amount", "Amount of coins", type=int)
@lightbulb.option("user", "User to manage", type=hikari.User)
@lightbulb.option("action", "add/remove", choices=["add", "remove"])
@lightbulb.command("coins", "Add or remove coins from a user")
@lightbulb.implements(lightbulb.SlashCommand)
async def manage_coins(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gdata = get_guild_data(ctx.guild_id)
    u_id = ctx.options.user.id
    if ctx.options.action == "add":
        gdata['users'][u_id] = gdata['users'].get(u_id, 0) + ctx.options.amount
    else:
        gdata['users'][u_id] = max(0, gdata['users'].get(u_id, 0) - ctx.options.amount)
    await save_data()
    await ctx.respond("Updated.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("role", "Banned role", type=hikari.Role)
@lightbulb.command("banrole", "Set banned role")
@lightbulb.implements(lightbulb.SlashCommand)
async def ban_role_cmd(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gdata = get_guild_data(ctx.guild_id)
    gdata['banned_role'] = ctx.options.role.id
    await save_data()
    await ctx.respond(f"Banned role set to {ctx.options.role.mention}", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.command("help", "Show commands")
@lightbulb.implements(lightbulb.SlashCommand)
async def help_cmd(ctx):
    is_mod = MOD_ROLE_ID in ctx.member.role_ids or ctx.user.id == OWNER_ID
    embed = hikari.Embed(title="📚 Commands", color=0x5865F2)
    u_cmds = "• `/daily` • `/balance` • `/leaderboard` • `/buy` • `/currency` • `/help`"
    embed.add_field("User Commands", u_cmds)
    if is_mod:
        m_cmds = "• `/uncounted` • `/coins` • `/banrole` • `/allowedroles` • `/setapproval` • `/setlog` • `/setprice` • `/customize`"
        embed.add_field("Staff Commands", m_cmds)
    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("user", "User", type=hikari.User, required=False)
@lightbulb.command("balance", "Check balance")
@lightbulb.implements(lightbulb.SlashCommand)
async def balance_cmd(ctx):
    user = ctx.options.user or ctx.user
    gdata = get_guild_data(ctx.guild_id)
    bal = gdata['users'].get(user.id, 0)
    mult = get_streak_mult(ctx.guild_id, user.id)
    
    prefs = gdata.get('user_prefs', {}).get(user.id, {})
    currency = prefs.get('currency', 'USD').upper()
    
    price_per_usd = gdata['config'].get('price_per_usd', 100)
    usd_val = bal / price_per_usd
    currency_str = f"(${usd_val:.2f} USD)"
    
    if currency != 'USD':
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={currency}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rate = data['rates'].get(currency)
                        if rate:
                            val = usd_val * rate
                            currency_str = f"({val:.2f} {currency})"
        except:
            pass

    embed = hikari.Embed(title=f"💰 {user.username}'s Balance", color=0xFFD700)
    embed.set_thumbnail(user.avatar_url or user.default_avatar_url)
    embed.add_field("Coins", f"{bal}", inline=True)
    embed.add_field("Value", currency_str, inline=True)
    embed.add_field("Multiplier", f"x{mult}", inline=True)
    await ctx.respond(embed=embed)

@bot.command
@lightbulb.option("currency", "Currency code (e.g. EUR, GBP, JPY)", required=True)
@lightbulb.command("currency", "Change your preferred display currency")
@lightbulb.implements(lightbulb.SlashCommand)
async def change_currency_cmd(ctx):
    gdata = get_guild_data(ctx.guild_id)
    currency = ctx.options.currency.upper()
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.frankfurter.dev/v1/latest?symbols={currency}") as resp:
                if resp.status != 200:
                    await ctx.respond(f"Invalid currency code: {currency}", flags=hikari.MessageFlag.EPHEMERAL)
                    return
    except:
        await ctx.respond("Error validating currency. Please try again later.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if 'user_prefs' not in gdata:
        gdata['user_prefs'] = {}
    if ctx.user.id not in gdata['user_prefs']:
        gdata['user_prefs'][ctx.user.id] = {}
        
    gdata['user_prefs'][ctx.user.id]['currency'] = currency
    await save_data()
    await ctx.respond(f"Your preferred currency has been set to **{currency}**", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("banner", "New banner URL", required=False)
@lightbulb.option("avatar", "New avatar URL", required=False)
@lightbulb.command("customize", "Change bot's appearance (Owner only)")
@lightbulb.implements(lightbulb.SlashCommand)
async def customize_bot_cmd(ctx):
    if ctx.user.id != OWNER_ID:
        await ctx.respond("Owner only.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    
    avatar_url = ctx.options.avatar
    
    if not avatar_url:
        await ctx.respond("Please provide an avatar URL.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url) as resp:
                if resp.status == 200:
                    await bot.rest.edit_my_user(avatar=await resp.read())
        
        await ctx.respond("Bot appearance updated.", flags=hikari.MessageFlag.EPHEMERAL)
    except Exception as e:
        await ctx.respond(f"Error: {e}", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("channel", "Log channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setlog", "Set the log channel for purchases")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_log_cmd(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gdata = get_guild_data(ctx.guild_id)
    gdata['config']['log_channel'] = ctx.options.channel.id
    await save_data()
    await ctx.respond(f"Log channel set to {ctx.options.channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.command("leaderboard", "Show coin leaderboard")
@lightbulb.implements(lightbulb.SlashCommand)
async def leaderboard_cmd(ctx):
    gdata = get_guild_data(ctx.guild_id)
    sorted_users = sorted(gdata['users'].items(), key=lambda x: x[1], reverse=True)
    if not sorted_users:
        await ctx.respond("No users yet.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    
    pages = []
    for i in range(0, len(sorted_users), 10):
        embed = hikari.Embed(title="🏆 Leaderboard", color=0xFFD700)
        desc = ""
        for rank, (u_id, coins) in enumerate(sorted_users[i:i+10], start=i+1):
            desc += f"**#{rank}** <@{u_id}>: {coins} coins\n"
        embed.description = desc
        embed.set_footer(text=f"Page {i//10 + 1} of {(len(sorted_users)-1)//10 + 1}")
        pages.append(embed)
    
    view = LeaderboardView(ctx.guild_id, pages)
    await ctx.respond(embed=pages[0], components=view)
    miru_client.start_view(view)

@bot.command
@lightbulb.option("channel", "Approval channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setapproval", "Set the channel for purchase approvals")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_approval_cmd(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gdata = get_guild_data(ctx.guild_id)
    gdata['config']['approval_channel'] = ctx.options.channel.id
    await save_data()
    await ctx.respond(f"Approval channel set to {ctx.options.channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("role", "Role to allow", type=hikari.Role)
@lightbulb.option("action", "add/remove/show", choices=["add", "remove", "show"])
@lightbulb.command("allowedroles", "Manage roles allowed to earn coins")
@lightbulb.implements(lightbulb.SlashCommand)
async def allowed_roles_cmd(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gdata = get_guild_data(ctx.guild_id)
    action = ctx.options.action
    if action == "show":
        roles = ", ".join([f"<@&{r}>" for r in gdata.get('allowed_roles', [])]) or "None"
        await ctx.respond(f"Allowed roles: {roles}", flags=hikari.MessageFlag.EPHEMERAL)
        return
    role = ctx.options.role
    if action == "add":
        if 'allowed_roles' not in gdata: gdata['allowed_roles'] = []
        if role.id not in gdata['allowed_roles']:
            gdata['allowed_roles'].append(role.id)
            await save_data()
            await ctx.respond(f"Added {role.mention} to allowed roles.", flags=hikari.MessageFlag.EPHEMERAL)
    else:
        if 'allowed_roles' in gdata and role.id in gdata['allowed_roles']:
            gdata['allowed_roles'].remove(role.id)
            await save_data()
            await ctx.respond(f"Removed {role.mention} from allowed roles.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.command("ping", "Check latency")
@lightbulb.implements(lightbulb.SlashCommand)
async def ping_cmd(ctx):
    await ctx.respond(f"🏓 {bot.heartbeat_latency*1000:.0f}ms", flags=hikari.MessageFlag.EPHEMERAL)

@bot.listen(hikari.StartedEvent)
async def on_start(event):
    load_data()
    logger.info("Bot started")

if __name__ == "__main__":
    bot.run()
