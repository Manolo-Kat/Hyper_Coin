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
        logging.FileHandler("bot.log"),
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
            'config': {
                'leaderboard_channel': None,
                'leaderboard_msg': None,
                'leaderboard_color': 0x5865F2,
                'redeem_channel': None,
                'redeem_msg': None,
                'redeem_color': 0x5865F2,
                'redeem_button_color': 'PRIMARY',
                'redeem_button_text': 'Redeem',
                'approval_channel': None,
                'price_per_usd': 100,
                'log_channel': None,
                'price_history': []  # Store price changes
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
                        'config': gdata.get('config', {})
                    }
                    # Default config values
                    config = guild_data[guild_id]['config']
                    config.setdefault('leaderboard_color', 0x5865F2)
                    config.setdefault('redeem_color', 0x5865F2)
                    config.setdefault('redeem_button_color', 'PRIMARY')
                    config.setdefault('redeem_button_text', 'Redeem')
                    config.setdefault('price_per_usd', 100)
                    config.setdefault('price_history', [])
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
    """Check if member is boosting the server"""
    if not member:
        return False
    return member.premium_since is not None

async def add_coins(guild_id, user_id, amount, member=None):
    gdata = get_guild_data(guild_id)
    if user_id not in gdata['users']:
        gdata['users'][user_id] = 0

    today = datetime.now(timezone.utc).date()
    daily_key = f"{user_id}_{today}"

    # Check if booster for higher daily limit
    is_boosting = is_booster(member) if member else False
    daily_limit = 400 if is_boosting else 200

    if gdata['daily_earnings'][daily_key] >= daily_limit:
        return False

    # Apply streak multiplier
    mult = get_streak_mult(guild_id, user_id)

    # Apply booster 2x multiplier (stacks with streak)
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

    # Remove # if present
    if color_input.startswith('#'):
        color_input = color_input[1:]

    # Try hex
    try:
        return int(color_input, 16)
    except:
        pass

    # Color names
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
    current_price = gdata['config']['price_per_usd']

    # If no history, create initial entry
    if not price_history:
        price_history = [{'price': current_price, 'timestamp': datetime.now(timezone.utc).isoformat()}]
        gdata['config']['price_history'] = price_history

    # Extract prices and timestamps
    prices = [entry['price'] for entry in price_history]
    timestamps = [datetime.fromisoformat(entry['timestamp']) for entry in price_history]

    # Add current price if it's different from last
    if prices[-1] != current_price:
        timestamps.append(datetime.now(timezone.utc))
        prices.append(current_price)

    # Create line chart
    ax.plot(timestamps, prices, color='#5865F2', linewidth=2.5, marker='o', markersize=8)
    ax.fill_between(timestamps, prices, alpha=0.2, color='#5865F2')

    # Calculate change
    if len(prices) > 1:
        change = prices[-1] - prices[0]
        change_percent = (change / prices[0]) * 100 if prices[0] != 0 else 0
        change_color = '#00FF00' if change >= 0 else '#FF0000'
        change_symbol = '▲' if change >= 0 else '▼'

        # Add change indicator
        ax.text(0.02, 0.98, f'{change_symbol} {change:+.0f} ({change_percent:+.1f}%)',
                transform=ax.transAxes, fontsize=14, fontweight='bold',
                color=change_color, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    # Styling
    ax.set_xlabel('Time', fontsize=12)
    ax.set_ylabel('Coins per $1 USD', fontsize=12)
    ax.set_title(f'Price History - Current: {current_price} coins/$1', fontsize=16, fontweight='bold')
    ax.grid(True, alpha=0.3, linestyle='--')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # Format x-axis
    if len(timestamps) > 1:
        plt.xticks(rotation=45, ha='right')

    # Add horizontal line at current price
    ax.axhline(y=current_price, color='gray', linestyle='--', alpha=0.5, linewidth=1)

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

    @miru.button(label="Open Shop Menu", style=hikari.ButtonStyle.PRIMARY)
    async def open_shop(self, ctx: miru.ViewContext):
        # This button is just to satisfy the view if needed, but the select is the main part
        await ctx.respond("Please use the dropdown menu below to select your reward.", flags=hikari.MessageFlag.EPHEMERAL)

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
        self.amount_input = miru.TextInput(label="Amount per USD", placeholder="Enter USD amount (e.g. 5)", required=True)
        self.add_item(self.amount_input)

    async def callback(self, ctx: miru.ModalContext):
        gdata = get_guild_data(self.guild_id)
        try:
            usd_amount = int(list(self.amount_input.values.values())[0])
        except ValueError:
            await ctx.respond("Please enter a valid number for the amount.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        price_per_usd = gdata['config'].get('price_per_usd', 100)
        required_coins = usd_amount * price_per_usd
        user_balance = gdata['users'].get(ctx.user.id, 0)

        if user_balance < required_coins:
            await ctx.respond(f"Insufficient balance. You need {required_coins} coins for ${usd_amount} USD.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        # Deduct balance
        gdata['users'][ctx.user.id] -= required_coins
        await save_data()

        # Send to approval channel
        approval_channel_id = gdata['config'].get('approval_channel')
        if not approval_channel_id:
            # Refund if no approval channel
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

        # Auto-refund on rejection
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

class RedeemButton(miru.Button):
    def __init__(self, guild_id):
        gdata = get_guild_data(guild_id)
        style_map = {
            'PRIMARY': hikari.ButtonStyle.PRIMARY,
            'SUCCESS': hikari.ButtonStyle.SUCCESS,
            'DANGER': hikari.ButtonStyle.DANGER,
            'SECONDARY': hikari.ButtonStyle.SECONDARY
        }
        config_style = gdata['config'].get('redeem_button_color', 'PRIMARY')
        button_style = style_map.get(config_style, hikari.ButtonStyle.PRIMARY)

        super().__init__(
            style=button_style,
            label=gdata['config'].get('redeem_button_text', 'Redeem')
        )
        self.guild_id = guild_id

    async def callback(self, ctx: miru.ViewContext):
        gdata = get_guild_data(self.guild_id)
        member = ctx.member
        if member and is_banned(self.guild_id, member):
            await ctx.respond("You're banned from using this bot.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        balance = gdata['users'].get(ctx.user.id, 0)
        required = gdata['config']['price_per_usd'] * 5

        if balance < required:
            await ctx.respond(f"You need at least {required} coins ($5) to redeem.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        modal = RedeemModal(self.guild_id)
        await ctx.respond_with_modal(modal)

class RedeemModal(miru.Modal):
    def __init__(self, guild_id):
        super().__init__("Redeem Reward")
        self.guild_id = guild_id
        self.reward_type_input = miru.TextInput(label="Reward Type", placeholder="e.g., Discord Nitro, PayPal, etc.")
        self.reward_details_input = miru.TextInput(label="Reward Details", placeholder="Amount/details", style=hikari.TextInputStyle.PARAGRAPH)
        self.add_item(self.reward_type_input)
        self.add_item(self.reward_details_input)

    async def callback(self, ctx: miru.ModalContext):
        gdata = get_guild_data(self.guild_id)
        reward_type = self.reward_type_input.value
        reward_details = self.reward_details_input.value

        view = ApprovalView(self.guild_id, ctx.user.id, reward_type, reward_details)

        embed = hikari.Embed(
            title="💰 Reward Redemption Request",
            color=0xFFD700
        )
        embed.add_field("User", f"<@{ctx.user.id}>", inline=True)
        embed.add_field("Balance", f"{gdata['users'].get(ctx.user.id, 0)} coins", inline=True)
        embed.add_field("Reward Type", reward_type, inline=False)
        embed.add_field("Reward Details", reward_details, inline=False)
        embed.timestamp = datetime.now(timezone.utc)

        channel = gdata['config'].get('approval_channel')
        if channel:
            msg = await bot.rest.create_message(channel, embed=embed, components=view)
            miru_client.start_view(view, bind_to=msg)

        await ctx.respond("Your redemption request has been submitted!", flags=hikari.MessageFlag.EPHEMERAL)

class ApprovalView(miru.View):
    def __init__(self, guild_id, user_id, reward_type, reward_details):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.user_id = user_id
        self.reward_type = reward_type
        self.reward_details = reward_details

    @miru.button(label="Reject w/ Response", style=hikari.ButtonStyle.DANGER)
    async def reject_btn(self, ctx: miru.ViewContext, button: miru.Button):
        member = await bot.rest.fetch_member(self.guild_id, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("You don't have permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        modal = ResponseModal(self.guild_id, self.user_id, False, self.reward_type, self.reward_details)
        await ctx.respond_with_modal(modal)

    @miru.button(label="Accept (No Response)", style=hikari.ButtonStyle.SUCCESS)
    async def accept_no_response(self, ctx: miru.ViewContext, button: miru.Button):
        gdata = get_guild_data(self.guild_id)
        member = await bot.rest.fetch_member(self.guild_id, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("You don't have permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        amount = self.calculate_cost()
        if gdata['users'].get(self.user_id, 0) >= amount:
            gdata['users'][self.user_id] -= amount
            await save_data()

            embed = hikari.Embed(title="✅ Redemption Approved", color=0x00FF00)
            embed.description = f"Approved by <@{ctx.user.id}>"
            await ctx.edit_response(embed=embed, components=[])
            await ctx.respond("Request approved.", flags=hikari.MessageFlag.EPHEMERAL)

    @miru.button(label="Accept w/ Response", style=hikari.ButtonStyle.SUCCESS)
    async def accept_response(self, ctx: miru.ViewContext, button: miru.Button):
        member = await bot.rest.fetch_member(self.guild_id, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("You don't have permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        modal = ResponseModal(self.guild_id, self.user_id, True, self.reward_type, self.reward_details)
        await ctx.respond_with_modal(modal)

    def calculate_cost(self):
        gdata = get_guild_data(self.guild_id)
        details = self.reward_details.lower()
        matches = re.findall(r'\d+', details)
        if matches:
            amount_val = int(matches[0])
            return gdata['config']['price_per_usd'] * amount_val
        return gdata['config']['price_per_usd'] * 5

class ResponseModal(miru.Modal):
    def __init__(self, guild_id, user_id, is_accept, reward_type, reward_details):
        title = "Acceptance Message" if is_accept else "Rejection Reason"
        super().__init__(title)
        self.guild_id = guild_id
        self.user_id = user_id
        self.is_accept = is_accept
        self.reward_type = reward_type
        self.reward_details = reward_details

        label = "Message to user" if is_accept else "Rejection reason"
        self.message_input = miru.TextInput(label=label, style=hikari.TextInputStyle.PARAGRAPH)
        self.add_item(self.message_input)

    async def callback(self, ctx: miru.ModalContext):
        gdata = get_guild_data(self.guild_id)
        message = self.message_input.value

        try:
            user = await bot.rest.fetch_user(self.user_id)
            dm = await user.fetch_dm_channel()

            if self.is_accept:
                amount = self.calculate_cost()
                if gdata['users'].get(self.user_id, 0) >= amount:
                    gdata['users'][self.user_id] -= amount
                    await save_data()

                embed = hikari.Embed(title="✅ Redemption Approved", color=0x00FF00)
                embed.add_field("Message", message)
                await dm.send(embed=embed)

                response_embed = hikari.Embed(title="✅ Redemption Approved", color=0x00FF00)
                response_embed.description = f"Approved by <@{ctx.user.id}>"
            else:
                embed = hikari.Embed(title="❌ Redemption Rejected", color=0xFF0000)
                embed.add_field("Reason", message)
                await dm.send(embed=embed)

                response_embed = hikari.Embed(title="❌ Redemption Rejected", color=0xFF0000)
                response_embed.description = f"Rejected by <@{ctx.user.id}>"

            await ctx.edit_response(embed=response_embed, components=[])
        except Exception as e:
            logger.error(f"Error sending DM/updating response: {e}")

        await ctx.respond("Response sent.", flags=hikari.MessageFlag.EPHEMERAL)

    def calculate_cost(self):
        gdata = get_guild_data(self.guild_id)
        details = self.reward_details.lower()
        matches = re.findall(r'\d+', details)
        if matches:
            amount_val = int(matches[0])
            return gdata['config']['price_per_usd'] * amount_val
        return gdata['config']['price_per_usd'] * 5

@bot.listen(hikari.StartedEvent)
async def on_start(event):
    load_data()
    bot.d.session = aiohttp.ClientSession()

    await bot.update_presence(
        status=hikari.Status.DO_NOT_DISTURB,
        activity=hikari.Activity(
            name="Zo's wallet",
            type=hikari.ActivityType.WATCHING
        )
    )
    logger.info("Bot started successfully")

@bot.listen(hikari.StoppingEvent)
async def on_stop(event):
    if hasattr(bot.d, 'session'):
        await bot.d.session.close()
    await save_data()
    logger.info("Bot stopped and data saved")

@bot.listen(hikari.GuildMessageCreateEvent)
async def on_message(event):
    if event.author.is_bot:
        return

    gdata = get_guild_data(event.guild_id)

    if event.channel_id in gdata['uncounted']:
        return

    member = event.member
    if member and is_banned(event.guild_id, member):
        return

    user_id = event.author.id
    now = datetime.now(timezone.utc)

    if user_id in gdata['cooldowns']:
        if (now - gdata['cooldowns'][user_id]).total_seconds() < 25:
            return

    gdata['cooldowns'][user_id] = now
    check_streak(event.guild_id, user_id)
    await add_coins(event.guild_id, user_id, 5, member)

@bot.command
@lightbulb.command("leaderboard", "Show the coin leaderboard")
@lightbulb.implements(lightbulb.SlashCommand)
async def leaderboard_cmd(ctx):
    gdata = get_guild_data(ctx.guild_id)
    sorted_users = sorted(gdata['users'].items(), key=lambda x: x[1], reverse=True)
    
    if not sorted_users:
        await ctx.respond("The leaderboard is empty.")
        return

    pages = []
    users_per_page = 10
    total_pages = (len(sorted_users) + users_per_page - 1) // users_per_page
    
    user_rank = -1
    for i, (uid, _) in enumerate(sorted_users):
        if uid == ctx.user.id:
            user_rank = i + 1
            break

    for p in range(total_pages):
        embed = hikari.Embed(title="🏆 Coin Leaderboard", color=0xFFD700)
        start = p * users_per_page
        end = start + users_per_page
        page_users = sorted_users[start:end]
        
        desc = ""
        for i, (uid, coins) in enumerate(page_users):
            rank = start + i + 1
            desc += f"**#{rank}** <@{uid}>: `{coins}` coins\n"
        
        embed.description = desc
        
        # Check if user is on this page
        user_on_page = any(uid == ctx.user.id for uid, _ in page_users)
        if not user_on_page and user_rank != -1:
            embed.set_footer(text=f"Your position: #{user_rank}")
        
        pages.append(embed)

    view = LeaderboardView(ctx.guild_id, pages)
    msg = await ctx.respond(embed=pages[0], components=view)
    miru_client.start_view(view, bind_to=msg)

@bot.command
@lightbulb.command("shop", "Open the reward shop")
@lightbulb.implements(lightbulb.SlashCommand)
async def shop_cmd(ctx):
    embed = hikari.Embed(title="🛒 Reward Shop", description="Select a reward from the menu below to redeem your coins.", color=0x5865F2)
    view = ShopView(ctx.guild_id)
    msg = await ctx.respond(embed=embed, components=view)
    miru_client.start_view(view, bind_to=msg)

@bot.command
@lightbulb.option("price", "Price to give or take", type=int)
@lightbulb.option("channel", "Channel to drop in", type=hikari.TextableGuildChannel)
@lightbulb.option("action", "take or give", choices=["take", "give"])
@lightbulb.command("drop", "Drop coins in a channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def drop_cmd(ctx):
    if ctx.user.id != OWNER_ID:
        await ctx.respond("Owner only.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    action = ctx.options.action
    channel = ctx.options.channel
    price = ctx.options.price

    class DropView(miru.View):
        def __init__(self, action, price, guild_id):
            super().__init__(timeout=180 if action == "take" else None)
            self.action = action
            self.price = price
            self.guild_id = guild_id
            self.claimed = False
            self._drop_message = None

        async def on_timeout(self):
            if not self.claimed and self.action == "take" and self._drop_message:
                try:
                    await self._drop_message.delete()
                except:
                    pass

        @miru.button(label="Free Coins", style=hikari.ButtonStyle.SUCCESS if action == "give" else hikari.ButtonStyle.DANGER)
        async def claim_btn(self, ctx: miru.ViewContext, button: miru.Button):
            if self.claimed:
                return

            if is_banned(self.guild_id, ctx.member):
                await ctx.respond("You are banned from using this bot.", flags=hikari.MessageFlag.EPHEMERAL)
                return

            self.claimed = True
            self.stop()
            
            gdata = get_guild_data(self.guild_id)
            if self.action == "give":
                gdata['users'][ctx.user.id] = gdata['users'].get(ctx.user.id, 0) + self.price
                response_msg = f"Congrats <@{ctx.user.id}>! You won **{self.price} coins**!"
            else:
                gdata['users'][ctx.user.id] = max(0, gdata['users'].get(ctx.user.id, 0) - self.price)
                response_msg = f"Too bad <@{ctx.user.id}>! You lost **{self.price} coins**. Focus next time!"

            await save_data()
            
            embed = ctx.message.embeds[0]
            embed.description = f"Action: **{self.action.upper()}**\nAmount: **{self.price}** coins\n\nClaimed by: <@{ctx.user.id}>"
            await ctx.edit_response(content=response_msg, embed=embed, components=[])

    view = DropView(action, price, ctx.guild_id)
    embed = hikari.Embed(
        title="💰 Coin Drop!",
        description=f"Action: **{action.upper()}**\nAmount: **{price}** coins\n\nBe the first to click the button below!",
        color=0x00FF00 if action == "give" else 0xFF0000
    )
    
    msg = await bot.rest.create_message(channel.id, embed=embed, components=view)
    view._drop_message = msg
    miru_client.start_view(view, bind_to=msg)
    await ctx.respond(f"Drop created in {channel.mention}!", flags=hikari.MessageFlag.EPHEMERAL)

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

    # Apply booster 2x multiplier
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

    embed = hikari.Embed(
        title="🎁 Daily Reward",
        description=f"You received **{amount} coins**!" + (" <:Boost_Bot:1470167258603982900> **(Booster Bonus!)**" if is_boosting else ""),
        color=0xFFD700
    )
    embed.add_field("Current Streak", f"{gdata['streaks'][user_id]} days", inline=True)
    embed.add_field("Multiplier", f"x{get_streak_mult(ctx.guild_id, user_id)}", inline=True)
    embed.set_footer(text=f"Come back tomorrow to keep your streak!")

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
        await log_action(ctx, f"Added {channel.mention} to uncounted channels")
    else:
        gdata['uncounted'].discard(channel.id)
        await save_data()
        await ctx.respond(f"Removed {channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)
        await log_action(ctx, f"Removed {channel.mention} from uncounted channels")

@bot.command
@lightbulb.option("color", "Hex color or name (e.g., FF5733, red, blue)", required=False, default="5865F2")
@lightbulb.option("channel", "Channel for leaderboard", type=hikari.TextableGuildChannel)
@lightbulb.command("setleaderboard", "Set leaderboard channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_leaderboard(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    gdata = get_guild_data(ctx.guild_id)
    gdata['config']['leaderboard_channel'] = ctx.options.channel.id
    gdata['config']['leaderboard_color'] = parse_color(ctx.options.color)

    await save_data()
    await ctx.respond(f"Leaderboard set to {ctx.options.channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)
    await update_leaderboard(ctx.guild_id)
    await log_action(ctx, f"Set leaderboard channel to {ctx.options.channel.mention}")

async def update_leaderboard(guild_id):
    gdata = get_guild_data(guild_id)
    if not gdata['config'].get('leaderboard_channel'):
        return

    top = sorted(gdata['users'].items(), key=lambda x: x[1], reverse=True)[:10]

    embed = hikari.Embed(
        title="💰 Top 10 Richest Users",
        color=gdata['config']['leaderboard_color']
    )

    desc = ""
    for i, (uid, bal) in enumerate(top, 1):
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        medal = medals.get(i, f"{i}.")
        desc += f"{medal} <@{uid}> - **{bal}** coins\n"

    embed.description = desc if desc else "No data yet"
    embed.timestamp = datetime.now(timezone.utc)

    channel = gdata['config']['leaderboard_channel']

    try:
        if gdata['config'].get('leaderboard_msg'):
            try:
                await bot.rest.edit_message(channel, gdata['config']['leaderboard_msg'], embed=embed)
                return
            except:
                pass

        msg = await bot.rest.create_message(channel, embed=embed)
        gdata['config']['leaderboard_msg'] = msg.id
        await save_data()
    except Exception as e:
        logger.error(f"Error updating leaderboard: {e}")

async def update_all_redeems():
    # Only run once on start to ensure views are bound
    for guild_id in list(guild_data.keys()):
        try:
            await update_redeem(guild_id)
        except:
            pass

async def update_all_leaderboards():
    while True:
        for guild_id in list(guild_data.keys()):
            try:
                await update_leaderboard(guild_id)
            except:
                pass
        await asyncio.sleep(1200)

@bot.command
@lightbulb.option("price", "Coins per 1 USD", type=int)
@lightbulb.command("setprice", "Set reward price")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_price(ctx):
    if ctx.user.id != OWNER_ID:
        await ctx.respond("Owner only.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    gdata = get_guild_data(ctx.guild_id)
    old_price = gdata['config'].get('price_per_usd', 100)
    new_price = ctx.options.price

    # Add to price history if different
    if old_price != new_price:
        gdata['config']['price_per_usd'] = new_price
        if 'price_history' not in gdata['config']:
            gdata['config']['price_history'] = []

        gdata['config']['price_history'].append({
            'price': new_price,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

        # Keep only last 20 price changes for performance
        if len(gdata['config']['price_history']) > 20:
            gdata['config']['price_history'] = gdata['config']['price_history'][-20:]

        await save_data()
        await update_redeem(ctx.guild_id)
        change = new_price - old_price
        await ctx.respond(
            f"Price updated: {old_price} → {new_price} coins per $1 ({change:+d} coins)",
            flags=hikari.MessageFlag.EPHEMERAL
        )
    else:
        await ctx.respond("Price is already set to this value.", flags=hikari.MessageFlag.EPHEMERAL)
    return

@bot.command
@lightbulb.option("button_text", "Button text", required=False, default="Redeem")
@lightbulb.option("button_color", "Button color", choices=["blue", "green", "red", "gray"], required=False, default="blue")
@lightbulb.option("embed_color", "Hex color or name", required=False, default="5865F2")
@lightbulb.option("channel", "Channel for redeem", type=hikari.TextableGuildChannel)
@lightbulb.command("setredeem", "Set redeem channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_redeem(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    gdata = get_guild_data(ctx.guild_id)
    gdata['config']['redeem_channel'] = ctx.options.channel.id
    gdata['config']['redeem_button_text'] = ctx.options.button_text

    colors = {
        "blue": "PRIMARY",
        "green": "SUCCESS",
        "red": "DANGER",
        "gray": "SECONDARY"
    }
    gdata['config']['redeem_button_color'] = colors.get(ctx.options.button_color, "PRIMARY")
    gdata['config']['redeem_color'] = parse_color(ctx.options.embed_color)

    await save_data()
    await ctx.respond(f"Redeem set to {ctx.options.channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)
    await update_redeem(ctx.guild_id)
    await log_action(ctx, f"Set redeem channel to {ctx.options.channel.mention}")

async def update_redeem(guild_id, force_new=False):
    gdata = get_guild_data(guild_id)
    if not gdata['config'].get('redeem_channel'):
        return

    try:
        chart_buf = create_chart(guild_id)

        embed = hikari.Embed(
            title="💎 Redeem Rewards",
            description=f"Current rate: **{gdata['config']['price_per_usd']}** coins = $1 USD\n\nClick the button below to redeem your coins for rewards!",
            color=gdata['config']['redeem_color']
        )
        embed.set_image(hikari.Bytes(chart_buf.read(), "price_chart.png"))
        embed.timestamp = datetime.now(timezone.utc)

        view = miru.View()
        view.add_item(RedeemButton(guild_id))

        channel = gdata['config']['redeem_channel']
        msg_id = gdata['config'].get('redeem_msg')

        if force_new or not msg_id:
            if msg_id:
                try:
                    await bot.rest.delete_message(channel, msg_id)
                except:
                    pass
            msg = await bot.rest.create_message(channel, embed=embed, components=view)
            gdata['config']['redeem_msg'] = msg.id
        else:
            try:
                msg = await bot.rest.edit_message(channel, msg_id, embed=embed, components=view)
            except hikari.NotFoundError:
                msg = await bot.rest.create_message(channel, embed=embed, components=view)
                gdata['config']['redeem_msg'] = msg.id

        miru_client.start_view(view, bind_to=gdata['config']['redeem_msg'])
        await save_data()
    except Exception as e:
        logger.error(f"Error updating redeem: {e}")

async def update_all_redeems():
    # Only run once on start to ensure views are bound, then it's triggered by price changes
    for guild_id in list(guild_data.keys()):
        try:
            await update_redeem(guild_id)
        except:
            pass

@bot.command
@lightbulb.option("currency", "Currency to convert to (Type to search, e.g., EGP, SAR, EUR, BRL, CNY)", required=True)
@lightbulb.option("amount", "Amount of coins to convert", type=int)
@lightbulb.command("exchange", "Convert coins to real-world currency value")
@lightbulb.implements(lightbulb.SlashCommand)
async def exchange_cmd(ctx):
    amount = ctx.options.amount
    target_currency = ctx.options.currency.upper()
    gdata = get_guild_data(ctx.guild_id)
    
    price_per_usd = gdata['config'].get('price_per_usd', 100)
    usd_value = amount / price_per_usd if price_per_usd != 0 else 0
    
    try:
        # First try Frankfurter API
        async with bot.d.session.get(f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={target_currency}") as resp:
            if resp.status == 200:
                data = await resp.json()
                rate = data['rates'].get(target_currency)
                if rate:
                    await process_exchange_result(ctx, amount, usd_value, target_currency, rate)
                    return
            
        # Fallback to ExchangeRate-API (better support for EGP/SAR)
        async with bot.d.session.get(f"https://open.er-api.com/v6/latest/USD") as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("result") == "success":
                    rate = data['rates'].get(target_currency)
                    if rate:
                        await process_exchange_result(ctx, amount, usd_value, target_currency, rate)
                        return
        
        await ctx.respond(f"Could not find exchange rate for **{target_currency}**. Please check the currency code (e.g., USD, EGP, SAR, EUR).", flags=hikari.MessageFlag.EPHEMERAL)
    except Exception as e:
        logger.error(f"Exchange error: {e}")
        await ctx.respond("An error occurred while fetching exchange rates.", flags=hikari.MessageFlag.EPHEMERAL)

async def process_exchange_result(ctx, amount, usd_value, target_currency, rate):
    converted_value = usd_value * rate
    # Common symbols
    symbols = {
        "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", 
        "EGP": "EGP ", "SAR": "SAR ", "BRL": "R$", "CAD": "C$", 
        "AUD": "A$", "CHF": "CHF ", "HKD": "HK$", "INR": "₹",
        "TRY": "₺", "ZAR": "R", "ILS": "₪", "KRW": "₩"
    }
    symbol = symbols.get(target_currency, f"{target_currency} ")
    
    embed = hikari.Embed(
        title="💱 Currency Exchange",
        color=0x00FF00,
        description=f"**{amount} coins** is approximately:"
    )
    embed.add_field("USD Value", f"${usd_value:.2f}", inline=True)
    embed.add_field(f"{target_currency} Value", f"{symbol}{converted_value:.2f}", inline=True)
    embed.set_footer(text=f"Live Rate: 1 USD = {rate} {target_currency}")
    await ctx.respond(embed=embed)

@bot.command
@lightbulb.option("channel", "Channel for approvals", type=hikari.TextableGuildChannel)
@lightbulb.command("setapproval", "Set approval channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_approval(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    gdata = get_guild_data(ctx.guild_id)
    gdata['config']['approval_channel'] = ctx.options.channel.id
    await save_data()
    await ctx.respond(f"Approval set to {ctx.options.channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)
    await log_action(ctx, f"Set approval channel to {ctx.options.channel.mention}")

@bot.command
@lightbulb.option("amount", "Amount to add/remove", type=int)
@lightbulb.option("user", "User", type=hikari.User)
@lightbulb.option("action", "add or remove", choices=["add", "remove"])
@lightbulb.command("coins", "Manage user coins")
@lightbulb.implements(lightbulb.SlashCommand)
async def manage_coins(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    gdata = get_guild_data(ctx.guild_id)
    user = ctx.options.user
    amount = ctx.options.amount
    action = ctx.options.action

    if user.id not in gdata['users']:
        gdata['users'][user.id] = 0

    if action == "add":
        gdata['users'][user.id] += amount
        await ctx.respond(f"Added {amount} coins to {user.mention}", flags=hikari.MessageFlag.EPHEMERAL)
        await log_action(ctx, f"Added {amount} coins to {user.mention}")
    else:
        gdata['users'][user.id] = max(0, gdata['users'][user.id] - amount)
        await ctx.respond(f"Removed {amount} coins from {user.mention}", flags=hikari.MessageFlag.EPHEMERAL)
        await log_action(ctx, f"Removed {amount} coins from {user.mention}")

    await save_data()

@bot.command
@lightbulb.option("role", "Role to ban", type=hikari.Role)
@lightbulb.command("banrole", "Set banned role")
@lightbulb.implements(lightbulb.SlashCommand)
async def ban_role(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    gdata = get_guild_data(ctx.guild_id)
    gdata['banned_role'] = ctx.options.role.id

    try:
        guild = await bot.rest.fetch_guild(ctx.guild_id)
        members = bot.cache.get_members_view_for_guild(guild.id)

        for member_id in list(members.keys()):
            member_obj = members[member_id]
            if gdata['banned_role'] in member_obj.role_ids:
                if member_id in gdata['users']:
                    del gdata['users'][member_id]
    except Exception as e:
        logger.error(f"Error checking members for ban role: {e}")

    await save_data()
    await ctx.respond(f"Banned role set to {ctx.options.role.mention}", flags=hikari.MessageFlag.EPHEMERAL)
    await log_action(ctx, f"Set banned role to {ctx.options.role.mention}")

@bot.command
@lightbulb.option("type", "What to restore", choices=["leaderboard", "redeem"])
@lightbulb.command("restore", "Restore deleted embeds")
@lightbulb.implements(lightbulb.SlashCommand)
async def restore(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    type_choice = ctx.options.type

    if type_choice == "leaderboard":
        await update_leaderboard(ctx.guild_id)
        await ctx.respond("Leaderboard restored.", flags=hikari.MessageFlag.EPHEMERAL)
    else:
        await update_redeem(ctx.guild_id)
        await ctx.respond("Redeem embed restored.", flags=hikari.MessageFlag.EPHEMERAL)

    await log_action(ctx, f"Restored {type_choice} embed")

def image_to_data_uri(image_data):
    """Convert image bytes to Discord-compatible base64 data URI"""
    encoded = base64.b64encode(image_data).decode('ascii')
    # Detect image type
    if image_data.startswith(b'\x89PNG'):
        mime = 'image/png'
    elif image_data.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    elif image_data.startswith(b'GIF'):
        mime = 'image/gif'
    elif image_data.startswith(b'WEBP', 8):
        mime = 'image/webp'
    else:
        mime = 'image/png'  # default
    return f'data:{mime};base64,{encoded}'

@bot.command
@lightbulb.option("user", "User to copy avatar from", type=hikari.User, required=False)
@lightbulb.option("image", "Image URL or attachment", type=hikari.Attachment, required=False)
@lightbulb.option("action", "What to do", choices=["set_from_attachment", "set_from_user", "remove"])
@lightbulb.command("setpfp", "Change bot avatar (Owner only)")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_pfp(ctx):
    if ctx.user.id != OWNER_ID:
        await ctx.respond("Owner only.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    action = ctx.options.action

    if action == "remove":
        await bot.rest.edit_my_user(avatar=None)
        await ctx.respond("Avatar removed.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    try:
        if action == "set_from_user":
            user = ctx.options.user
            if not user:
                await ctx.respond("Please specify a user.", flags=hikari.MessageFlag.EPHEMERAL)
                return

            avatar_url = user.avatar_url or user.default_avatar_url
            async with bot.d.session.get(str(avatar_url)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    data_uri = image_to_data_uri(data)
                    await bot.rest.edit_my_user(avatar=data_uri)
                    await ctx.respond(f"Avatar set from {user.mention}", flags=hikari.MessageFlag.EPHEMERAL)
                else:
                    await ctx.respond("Failed to download avatar.", flags=hikari.MessageFlag.EPHEMERAL)
        else:
            attachment = ctx.options.image
            if not attachment:
                await ctx.respond("Please provide an image.", flags=hikari.MessageFlag.EPHEMERAL)
                return

            async with bot.d.session.get(attachment.url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    data_uri = image_to_data_uri(data)
                    await bot.rest.edit_my_user(avatar=data_uri)
                    await ctx.respond("Avatar updated.", flags=hikari.MessageFlag.EPHEMERAL)
                else:
                    await ctx.respond("Failed to download image.", flags=hikari.MessageFlag.EPHEMERAL)
    except Exception as e:
        logger.error(f"Error setting bot avatar: {e}")
        await ctx.respond("An error occurred while updating the avatar.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("user", "User to copy banner from", type=hikari.User, required=False)
@lightbulb.option("image", "Image URL or attachment", type=hikari.Attachment, required=False)
@lightbulb.option("action", "What to do", choices=["set_from_attachment", "set_from_user", "remove"])
@lightbulb.command("setbanner", "Change bot banner (Owner only)")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_banner(ctx):
    if ctx.user.id != OWNER_ID:
        await ctx.respond("Owner only.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    action = ctx.options.action

    if action == "remove":
        await bot.rest.edit_my_user(banner=None)
        await ctx.respond("Banner removed.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    try:
        if action == "set_from_user":
            user = ctx.options.user
            if not user:
                await ctx.respond("Please specify a user.", flags=hikari.MessageFlag.EPHEMERAL)
                return

            user_full = await bot.rest.fetch_user(user.id)
            if not user_full.banner_url:
                await ctx.respond("User doesn't have a banner.", flags=hikari.MessageFlag.EPHEMERAL)
                return

            async with bot.d.session.get(str(user_full.banner_url)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    data_uri = image_to_data_uri(data)
                    await bot.rest.edit_my_user(banner=data_uri)
                    await ctx.respond(f"Banner set from {user.mention}", flags=hikari.MessageFlag.EPHEMERAL)
                else:
                    await ctx.respond("Failed to download banner.", flags=hikari.MessageFlag.EPHEMERAL)
        else:
            attachment = ctx.options.image
            if not attachment:
                await ctx.respond("Please provide an image.", flags=hikari.MessageFlag.EPHEMERAL)
                return

            async with bot.d.session.get(attachment.url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    data_uri = image_to_data_uri(data)
                    await bot.rest.edit_my_user(banner=data_uri)
                    await ctx.respond("Banner updated.", flags=hikari.MessageFlag.EPHEMERAL)
                else:
                    await ctx.respond("Failed to download image.", flags=hikari.MessageFlag.EPHEMERAL)
    except Exception as e:
        logger.error(f"Error setting bot banner: {e}")
        await ctx.respond("An error occurred while updating the banner.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.command("help", "Show commands")
@lightbulb.implements(lightbulb.SlashCommand)
async def help_cmd(ctx):
    member = ctx.member
    is_owner = ctx.user.id == OWNER_ID
    is_mod = member and MOD_ROLE_ID in member.role_ids

    embed = hikari.Embed(title="📚 Commands", color=0x5865F2)

    user_cmds = "• `/daily` - Claim daily reward\n• `/balance` - Check your coins\n• `/exchange` - Convert coins to currency\n• `/leaderboard` - Show leaderboard\n• `/shop` - Open shop\n• `/help` - Show this menu"

    if is_owner or is_mod:
        mod_cmds = "• `/uncounted` - Manage uncounted channels\n• `/coins` - Manage user coins\n• `/banrole` - Set banned role\n• `/setlog` - Set log channel\n• `/ping` - Check latency"
        embed.add_field("User Commands", user_cmds, inline=False)
        embed.add_field("Moderator Commands", mod_cmds, inline=False)

        if is_owner:
            owner_cmds = "• `/setprice` - Set reward prices\n• `/setpfp` - Change bot avatar\n• `/setbanner` - Change bot banner"
            embed.add_field("Owner Commands", owner_cmds, inline=False)
    else:
        embed.description = user_cmds

    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("channel", "Log channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setlog", "Set log channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_log(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    gdata = get_guild_data(ctx.guild_id)
    gdata['config']['log_channel'] = ctx.options.channel.id
    await save_data()
    await ctx.respond(f"Log channel set to {ctx.options.channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)

async def log_action(ctx, action):
    gdata = get_guild_data(ctx.guild_id)
    log_channel_id = gdata['config'].get('log_channel')
    if not log_channel_id:
        return

    embed = hikari.Embed(
        title="📝 Moderator Action",
        description=action,
        color=0xFFAA00
    )
    embed.add_field("Moderator", f"<@{ctx.user.id}>", inline=True)
    embed.timestamp = datetime.now(timezone.utc)

    try:
        await bot.rest.create_message(log_channel_id, embed=embed)
    except Exception as e:
        logger.error(f"Failed to log action: {e}")

@bot.command
@lightbulb.command("ping", "Check bot latency")
@lightbulb.implements(lightbulb.SlashCommand)
async def ping_cmd(ctx):
    latency = bot.heartbeat_latency * 1000
    await ctx.respond(f"🏓 Pong! {latency:.0f}ms", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.option("user", "User to check", type=hikari.User, required=False)
@lightbulb.command("balance", "Check balance")
@lightbulb.implements(lightbulb.SlashCommand)
async def balance_cmd(ctx):
    user = ctx.options.user or ctx.user
    gdata = get_guild_data(ctx.guild_id)

    if user.id != ctx.user.id:
        member = ctx.member
        if member and is_banned(ctx.guild_id, member):
            await ctx.respond("You're banned.", flags=hikari.MessageFlag.EPHEMERAL)
            return

    # Fetch member to check booster status
    try:
        target_member = await bot.rest.fetch_member(ctx.guild_id, user.id)
    except:
        target_member = None

    balance = gdata['users'].get(user.id, 0)
    streak = gdata['streaks'].get(user.id, 0)
    streak_mult = get_streak_mult(ctx.guild_id, user.id)
    price_per_usd = gdata['config'].get('price_per_usd', 100)
    usd_value = balance / price_per_usd if price_per_usd != 0 else 0
    is_boosting = is_booster(target_member)

    # Calculate total multiplier
    total_mult = streak_mult * (2 if is_boosting else 1)

    # Get today's earnings and limit
    today = datetime.now(timezone.utc).date()
    daily_key = f"{user.id}_{today}"
    today_earned = gdata['daily_earnings'].get(daily_key, 0)
    daily_limit = 400 if is_boosting else 200

    embed = hikari.Embed(title=f"💰 {user.username}'s Wallet", color=0xFFD700)
    embed.add_field("Balance", f"{balance} coins", inline=True)
    embed.add_field("USD Value", f"${usd_value:.2f}", inline=True)

    multiplier_text = f"{streak} days (x{streak_mult})"
    if is_boosting:
        multiplier_text += f"\n**Booster Bonus: x2**\n**Total: x{total_mult}**"
    embed.add_field("Streak", multiplier_text, inline=True)

    embed.add_field("Today's Earnings", f"{today_earned}/{daily_limit} coins", inline=True)

    # Set footer with boost status (use emoji icon URL for custom emojis)
    if is_boosting:
        embed.set_footer(
            text=f"Server Booster | {price_per_usd} coins = $1 USD",
            icon="https://cdn.discordapp.com/emojis/1040304561066119188.png"
        )
    else:
        embed.set_footer(
            text=f"Not Booster | {price_per_usd} coins = $1 USD",
            icon="https://cdn.discordapp.com/emojis/1040304561066119188.png"
        )

    embed.set_thumbnail(user.avatar_url or user.default_avatar_url)

    await ctx.respond(embed=embed)

if __name__ == "__main__":
    bot.run()
