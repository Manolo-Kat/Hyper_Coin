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
import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
from dotenv import dotenv_values

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
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
                    'Discord Nitro Basic': 100,
                    'Discord Nitro Gaming': 100,
                    'Apple Pay': 100
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
                        'Discord Nitro Basic': 100,
                        'Discord Nitro Gaming': 100,
                        'Apple Pay': 100
                    })
        logger.info("Data loaded successfully")
    except Exception as e:
        logger.error(f"Error loading data: {e}")

async def save_data():
    data = {}
    for gid, gdata in guild_data.items():
        data[str(gid)] = {
            'users': gdata['users'],
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

def parse_color(color_input):
    if not color_input:
        return 0x5865F2
    color_input = str(color_input).strip().lower()
    if color_input.startswith('#'):
        color_input = color_input[1:]
    try:
        return int(color_input, 16)
    except:
        pass
    colors = {
        'red': 0xFF0000, 'green': 0x00FF00, 'blue': 0x0000FF,
        'yellow': 0xFFFF00, 'purple': 0x800080, 'pink': 0xFFC0CB,
        'orange': 0xFFA500, 'gold': 0xFFD700, 'teal': 0x008080,
        'cyan': 0x00FFFF, 'magenta': 0xFF00FF, 'lime': 0x00FF00,
        'navy': 0x000080, 'maroon': 0x800000, 'olive': 0x808000,
        'white': 0xFFFFFF, 'black': 0x000000, 'gray': 0x808080,
        'grey': 0x808080, 'silver': 0xC0C0C0
    }
    return colors.get(color_input, 0x5865F2)

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

def create_chart(guild_id):
    gdata = get_guild_data(guild_id)
    fig, ax = plt.subplots(figsize=(10, 6))
    price_history = gdata['config'].get('price_history', [])
    if not price_history:
        price_history = [{'price': gdata['config']['price_per_usd'], 'timestamp': datetime.now(timezone.utc).isoformat()}]
    prices = [entry['price'] for entry in price_history]
    timestamps = [datetime.fromisoformat(entry['timestamp']) for entry in price_history]
    ax.plot(timestamps, prices, color='#5865F2', linewidth=2.5, marker='o', markersize=8)
    ax.fill_between(timestamps, prices, alpha=0.2, color='#5865F2')
    if len(prices) > 1:
        change = prices[-1] - prices[0]
        change_percent = (change / prices[0]) * 100 if prices[0] != 0 else 0
        change_color = '#00FF00' if change >= 0 else '#FF0000'
        change_symbol = '▲' if change >= 0 else '▼'
        ax.text(0.02, 0.98, f'{change_symbol} {change:+.0f} ({change_percent:+.1f}%)',
                transform=ax.transAxes, fontsize=14, fontweight='bold',
                color=change_color, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
    ax.set_xlabel('Time', fontsize=12)
    ax.set_ylabel('Price', fontsize=12)
    ax.set_title(f'Price History', fontsize=16, fontweight='bold')
    ax.grid(True, alpha=0.3, linestyle='--')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    if len(timestamps) > 1:
        plt.xticks(rotation=45, ha='right')
    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight', facecolor='white')
    buf.seek(0)
    plt.close(fig)
    return buf

class ShopView(miru.View):
    def __init__(self, guild_id):
        super().__init__(timeout=60.0)
        self.guild_id = guild_id

    @miru.text_select(
        placeholder="Choose your reward",
        options=[
            miru.SelectOption(label="PayPal", value="PayPal"),
            miru.SelectOption(label="Steam", value="Steam"),
            miru.SelectOption(label="Discord Nitro (Basic)", value="Discord Nitro Basic"),
            miru.SelectOption(label="Discord Nitro (Gaming)", value="Discord Nitro Gaming"),
            miru.SelectOption(label="Google Play", value="Google Play"),
            miru.SelectOption(label="Apple Pay", value="Apple Pay"),
        ]
    )
    async def reward_select(self, ctx: miru.ViewContext, select: miru.TextSelect):
        modal = ShopModal(self.guild_id, select.values[0])
        await ctx.respond_with_modal(modal)

class ShopModal(miru.Modal):
    def __init__(self, guild_id, reward_type):
        super().__init__(f"Shop - {reward_type}")
        self.guild_id = guild_id
        self.reward_type = reward_type
        self.amount_input = miru.TextInput(label="Amount (USD)", placeholder="Enter USD amount (e.g. 5)", required=True)
        self.add_item(self.amount_input)

    async def callback(self, ctx: miru.ModalContext):
        gdata = get_guild_data(self.guild_id)
        try:
            usd_amount = int(list(self.amount_input.values.values())[0])
        except ValueError:
            await ctx.respond("Please enter a valid number for the amount.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        shop_prices = gdata['config'].get('shop_prices', {})
        price_per_usd = shop_prices.get(self.reward_type, 100)
        required_coins = usd_amount * price_per_usd
        user_balance = gdata['users'].get(ctx.user.id, 0)
        if user_balance < required_coins:
            await ctx.respond(f"Insufficient balance. You need {required_coins} coins for ${usd_amount} USD worth of {self.reward_type}.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        gdata['users'][ctx.user.id] -= required_coins
        await save_data()
        approval_channel_id = gdata['config'].get('approval_channel')
        if not approval_channel_id:
            gdata['users'][ctx.user.id] += required_coins
            await save_data()
            await ctx.respond("Shop is not configured (no approval channel). Refunded.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        embed = hikari.Embed(title="🛒 New Shop Request", color=0x5865F2)
        embed.add_field("User", f"<@{ctx.user.id}>", inline=True)
        embed.add_field("Reward", self.reward_type, inline=True)
        embed.add_field("Amount", f"${usd_amount} USD ({required_coins} coins)", inline=True)
        embed.timestamp = datetime.now(timezone.utc)
        view = ShopApprovalView(self.guild_id, ctx.user.id, self.reward_type, usd_amount, required_coins)
        msg = await bot.rest.create_message(approval_channel_id, embed=embed, components=view)
        miru_client.start_view(view, bind_to=msg)
        await ctx.respond("Your request has been submitted and balance deducted.", flags=hikari.MessageFlag.EPHEMERAL)

class ShopApprovalView(miru.View):
    def __init__(self, guild_id, user_id, reward_type, usd_amount, coin_amount):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.user_id = user_id
        self.reward_type = reward_type
        self.usd_amount = usd_amount
        self.coin_amount = coin_amount

    @miru.button(label="Approve", style=hikari.ButtonStyle.SUCCESS)
    async def approve(self, ctx: miru.ViewContext, button: miru.Button):
        member = await bot.rest.fetch_member(self.guild_id, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        modal = ShopResponseModal(self.guild_id, self.user_id, True, self.reward_type, self.usd_amount, self.coin_amount)
        await ctx.respond_with_modal(modal)

    @miru.button(label="Reject", style=hikari.ButtonStyle.DANGER)
    async def reject(self, ctx: miru.ViewContext, button: miru.Button):
        member = await bot.rest.fetch_member(self.guild_id, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return
        gdata = get_guild_data(self.guild_id)
        gdata['users'][self.user_id] = gdata['users'].get(self.user_id, 0) + self.coin_amount
        await save_data()
        modal = ShopResponseModal(self.guild_id, self.user_id, False, self.reward_type, self.usd_amount, self.coin_amount)
        await ctx.respond_with_modal(modal)

class ShopResponseModal(miru.Modal):
    def __init__(self, guild_id, user_id, is_approve, reward_type, usd_amount, coin_amount):
        super().__init__("Shop Response")
        self.guild_id = guild_id
        self.user_id = user_id
        self.is_approve = is_approve
        self.reward_type = reward_type
        self.usd_amount = usd_amount
        self.coin_amount = coin_amount
        self.msg_input = miru.TextInput(label="Message to User", style=hikari.TextInputStyle.PARAGRAPH, required=True)
        self.add_item(self.msg_input)

    async def callback(self, ctx: miru.ModalContext):
        status = "Approved" if self.is_approve else "Rejected"
        color = 0x00FF00 if self.is_approve else 0xFF0000
        embed = hikari.Embed(title=f"🛒 Shop Request {status}", color=color)
        embed.add_field("Reward", self.reward_type, inline=True)
        embed.add_field("Amount", f"${self.usd_amount} USD", inline=True)
        embed.add_field("Message", list(self.msg_input.values.values())[0], inline=False)
        try:
            user = await bot.rest.fetch_user(self.user_id)
            await user.send(embed=embed)
        except:
            pass
        await ctx.edit_response(embed=hikari.Embed(title=f"Request {status}", description=f"Handled by <@{ctx.user.id}>", color=color), components=[])
        await ctx.respond("Response sent.", flags=hikari.MessageFlag.EPHEMERAL)

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
@lightbulb.option("item", "Shop item", choices=["PayPal", "Steam", "Google Play", "Discord Nitro Basic", "Discord Nitro Gaming", "Apple Pay"])
@lightbulb.command("setprice", "Set reward price for an item")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_price(ctx):
    if ctx.user.id != OWNER_ID:
        await ctx.respond("Owner only.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gdata = get_guild_data(ctx.guild_id)
    item = ctx.options.item
    new_price = ctx.options.price
    shop_prices = gdata['config'].setdefault('shop_prices', {})
    old_price = shop_prices.get(item, 100)
    shop_prices[item] = new_price
    gdata['config'].setdefault('price_history', []).append({
        'item': item,
        'price': new_price,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })
    await save_data()
    await ctx.respond(f"Price for **{item}** updated: {old_price} -> {new_price}", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.command("shop", "Open rewards shop")
@lightbulb.implements(lightbulb.SlashCommand)
async def shop_cmd(ctx):
    if is_banned(ctx.guild_id, ctx.member):
        await ctx.respond("Banned.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    view = ShopView(ctx.guild_id)
    await ctx.respond("Select a reward:", components=view, flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("role", "Role to allow", type=hikari.Role)
@lightbulb.option("action", "add or remove", choices=["add", "remove"])
@lightbulb.command("allowedroles", "Manage roles allowed to earn coins")
@lightbulb.implements(lightbulb.SlashCommand)
async def allowed_roles_cmd(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gdata = get_guild_data(ctx.guild_id)
    role = ctx.options.role
    if ctx.options.action == "add":
        if role.id not in gdata['allowed_roles']:
            gdata['allowed_roles'].append(role.id)
        await ctx.respond(f"Added {role.mention}", flags=hikari.MessageFlag.EPHEMERAL)
    else:
        if role.id in gdata['allowed_roles']:
            gdata['allowed_roles'].remove(role.id)
        await ctx.respond(f"Removed {role.mention}", flags=hikari.MessageFlag.EPHEMERAL)
    await save_data()

@bot.command
@lightbulb.command("leaderboard", "Show leaderboard")
@lightbulb.implements(lightbulb.SlashCommand)
async def leaderboard_cmd(ctx):
    gdata = get_guild_data(ctx.guild_id)
    users = sorted(gdata['users'].items(), key=lambda x: x[1], reverse=True)
    if not users:
        await ctx.respond("No data.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    pages = []
    for i in range(0, len(users), 10):
        chunk = users[i:i+10]
        desc = "\n".join([f"**#{j+i+1}** <@{u[0]}> - {u[1]} coins" for j, u in enumerate(chunk)])
        pages.append(hikari.Embed(title="🏆 Leaderboard", description=desc, color=0x5865F2))
    view = LeaderboardView(ctx.guild_id, pages)
    await ctx.respond(embed=pages[0], components=view, flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("currency", "Currency code", required=True)
@lightbulb.option("amount", "Amount of coins", type=int)
@lightbulb.command("exchange", "Convert coins to currency")
@lightbulb.implements(lightbulb.SlashCommand)
async def exchange_cmd(ctx):
    gdata = get_guild_data(ctx.guild_id)
    usd = ctx.options.amount / gdata['config'].get('price_per_usd', 100)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={ctx.options.currency.upper()}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rate = data['rates'].get(ctx.options.currency.upper())
                    if rate:
                        val = usd * rate
                        await ctx.respond(f"**{ctx.options.amount} coins** is approx **{val:.2f} {ctx.options.currency.upper()}**", flags=hikari.MessageFlag.EPHEMERAL)
                        return
        await ctx.respond("Currency not found.", flags=hikari.MessageFlag.EPHEMERAL)
    except:
        await ctx.respond("Error fetching rates.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("channel", "Approval channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setapproval", "Set approval channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_approval(ctx):
    if MOD_ROLE_ID not in ctx.member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    gdata = get_guild_data(ctx.guild_id)
    gdata['config']['approval_channel'] = ctx.options.channel.id
    await save_data()
    await ctx.respond(f"Approval set to {ctx.options.channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("amount", "Amount", type=int)
@lightbulb.option("user", "User", type=hikari.User)
@lightbulb.option("action", "add or remove", choices=["add", "remove"])
@lightbulb.command("coins", "Manage coins")
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
    u_cmds = "• `/daily` • `/balance` • `/exchange` • `/leaderboard` • `/shop` • `/help`"
    embed.add_field("User Commands", u_cmds)
    if is_mod:
        m_cmds = "• `/uncounted` • `/coins` • `/banrole` • `/allowedroles` • `/setapproval` • `/setprice`"
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
    embed = hikari.Embed(title=f"💰 {user.username}'s Balance", color=0xFFD700)
    embed.add_field("Coins", f"{bal}", inline=True)
    embed.add_field("Multiplier", f"x{mult}", inline=True)
    await ctx.respond(embed=embed)

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
