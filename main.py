import hikari
import lightbulb
import miru
import asyncio
import aiosqlite
import random
from datetime import datetime, timedelta
from typing import Optional
import io
from PIL import Image, ImageDraw, ImageFont
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

if not TOKEN:
    print("Add DISCORD_BOT_TOKEN to .env file")
    exit()

bot = lightbulb.BotApp(
    token=TOKEN,
    intents=hikari.Intents.ALL,
    banner=None
)
miru_client = miru.Client(bot)

DB_FILE = "economy.db"
user_cooldowns = {}
voice_tracking = {}

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                daily_earned INTEGER DEFAULT 0,
                last_daily TEXT,
                last_message TEXT,
                last_voice TEXT,
                streak INTEGER DEFAULT 0,
                last_activity TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER PRIMARY KEY,
                price_per_usd REAL DEFAULT 1.0,
                lb_channel INTEGER,
                lb_message INTEGER,
                lb_color TEXT DEFAULT '#5865F2',
                redeem_channel INTEGER,
                redeem_message INTEGER,
                redeem_color TEXT DEFAULT '#5865F2',
                redeem_button_color TEXT DEFAULT 'green',
                redeem_button_text TEXT DEFAULT 'Redeem',
                approval_channel INTEGER,
                banned_role INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ignored_channels (
                channel_id INTEGER PRIMARY KEY
            )
        """)
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                await db.execute(
                    "INSERT INTO users (user_id, last_activity) VALUES (?, ?)",
                    (user_id, datetime.utcnow().isoformat())
                )
                await db.commit()
                return {
                    'user_id': user_id, 'balance': 0, 'daily_earned': 0,
                    'last_daily': None, 'last_message': None, 'last_voice': None,
                    'streak': 0, 'last_activity': datetime.utcnow().isoformat()
                }
            return {
                'user_id': row[0], 'balance': row[1], 'daily_earned': row[2],
                'last_daily': row[3], 'last_message': row[4], 'last_voice': row[5],
                'streak': row[6], 'last_activity': row[7]
            }

async def update_user(user_id: int, **kwargs):
    async with aiosqlite.connect(DB_FILE) as db:
        fields = ", ".join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [user_id]
        await db.execute(f"UPDATE users SET {fields} WHERE user_id = ?", values)
        await db.commit()

async def add_balance(user_id: int, amount: int):
    user = await get_user(user_id)
    await update_user(user_id, balance=user['balance'] + amount)

async def get_setting(guild_id: int, key: str):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            f"SELECT {key} FROM settings WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_setting(guild_id: int, **kwargs):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            f"INSERT OR IGNORE INTO settings (guild_id) VALUES (?)", (guild_id,)
        )
        fields = ", ".join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [guild_id]
        await db.execute(f"UPDATE settings SET {fields} WHERE guild_id = ?", values)
        await db.commit()

async def is_ignored(channel_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT 1 FROM ignored_channels WHERE channel_id = ?", (channel_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

async def check_streak(user_id: int):
    user = await get_user(user_id)
    if not user['last_activity']:
        return 0

    last = datetime.fromisoformat(user['last_activity'])
    now = datetime.utcnow()
    diff = (now - last).total_seconds() / 3600

    if diff > 24:
        await update_user(user_id, streak=0)
        return 0

    return user['streak']

async def get_multiplier(streak: int):
    multipliers = {
        0: 1.0, 1: 1.25, 2: 1.5, 3: 1.75,
        4: 2.0, 5: 2.25, 6: 2.25, 7: 2.5
    }
    return multipliers.get(min(streak, 7), 1.0)

async def generate_chart(price: float):
    img = Image.new('RGB', (400, 200), color='#2b2d31')
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 24)
        small_font = ImageFont.truetype("arial.ttf", 16)
    except:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    draw.text((200, 80), f"{price} coins", fill='white', anchor='mm', font=font)
    draw.text((200, 120), "per 1 USD", fill='gray', anchor='mm', font=small_font)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf

class RedeemModal(miru.Modal):
    def __init__(self, user_balance: int, price: float) -> None:
        super().__init__(title="Redeem Rewards")
        self.user_balance = user_balance
        self.price = price

        self.reward_type = miru.TextInput(
            label="Reward Type",
            placeholder="e.g., Nitro, Role, etc.",
            required=True
        )
        self.add_item(self.reward_type)

        self.reward_details = miru.TextInput(
            label="Reward Details",
            placeholder="Specify what you want...",
            style=hikari.TextInputStyle.PARAGRAPH,
            required=True
        )
        self.add_item(self.reward_details)

    async def callback(self, ctx: miru.ModalContext) -> None:
        approval_channel = await get_setting(ctx.guild_id, 'approval_channel')

        if not approval_channel:
            await ctx.respond("Setup incomplete. Contact mods.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        embed = hikari.Embed(
            title="Reward Request",
            color=0x5865F2
        )
        embed.add_field("User", f"<@{ctx.user.id}>", inline=True)
        embed.add_field("Balance", f"{self.user_balance} coins", inline=True)
        embed.add_field("Reward Type", self.reward_type.value, inline=False)
        embed.add_field("Details", self.reward_details.value, inline=False)

        view = ApprovalView(ctx.user.id, self.price, self.reward_type.value, self.reward_details.value)
        msg = await ctx.bot.rest.create_message(
            approval_channel, embed=embed, components=view
        )
        await ctx.respond("Request submitted!", flags=hikari.MessageFlag.EPHEMERAL)

class ResponseModal(miru.Modal):
    def __init__(self, user_id: int, action: str, price: float, reward_type: str, reward_details: str) -> None:
        super().__init__(title=f"{action.capitalize()} Response")
        self.user_id = user_id
        self.action = action
        self.price = price
        self.reward_type = reward_type
        self.reward_details = reward_details

        self.response = miru.TextInput(
            label="Response Message",
            placeholder="Type your message...",
            style=hikari.TextInputStyle.PARAGRAPH,
            required=True
        )
        self.add_item(self.response)

    async def callback(self, ctx: miru.ModalContext) -> None:
        user = await get_user(self.user_id)
        usd_value = user['balance'] / self.price

        if self.action == "accept":
            if user['balance'] >= self.price * 5:
                coins_to_deduct = int(usd_value * self.price)
                await update_user(self.user_id, balance=user['balance'] - coins_to_deduct)

                try:
                    dm = await ctx.bot.rest.fetch_channel(
                        await ctx.bot.rest.create_dm_channel(self.user_id)
                    )
                    await ctx.bot.rest.create_message(
                        dm,
                        f"Your request for **{self.reward_type}** was accepted!\n\n"
                        f"Response: {self.response.value}\n\n"
                        f"Coins deducted: {coins_to_deduct}"
                    )
                except:
                    pass
        else:
            try:
                dm = await ctx.bot.rest.fetch_channel(
                    await ctx.bot.rest.create_dm_channel(self.user_id)
                )
                await ctx.bot.rest.create_message(
                    dm,
                    f"Your request for **{self.reward_type}** was rejected.\n\n"
                    f"Reason: {self.response.value}"
                )
            except:
                pass

        embed = ctx.interaction.message.embeds[0]
        embed.title = f"{'✅ Accepted' if self.action == 'accept' else '❌ Rejected'}"

        await ctx.edit_response(embed=embed, components=[])
        await ctx.respond("Done", flags=hikari.MessageFlag.EPHEMERAL)

class ApprovalView(miru.View):
    def __init__(self, user_id: int, price: float, reward_type: str, reward_details: str) -> None:
        super().__init__(timeout=None)
        self.user_id = user_id
        self.price = price
        self.reward_type = reward_type
        self.reward_details = reward_details

    @miru.button(label="Reject", style=hikari.ButtonStyle.DANGER)
    async def reject_respond(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        modal = ResponseModal(self.user_id, "reject", self.price, self.reward_type, self.reward_details)
        await ctx.respond_with_modal(modal)

    @miru.button(label="Accept", style=hikari.ButtonStyle.SUCCESS)
    async def accept_no_respond(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        user = await get_user(self.user_id)
        usd_value = user['balance'] / self.price

        if user['balance'] >= self.price * 5:
            coins_to_deduct = int(usd_value * self.price)
            await update_user(self.user_id, balance=user['balance'] - coins_to_deduct)

            embed = ctx.interaction.message.embeds[0]
            embed.title = "✅ Accepted"
            await ctx.edit_response(embed=embed, components=[])
            await ctx.respond("Accepted without message", flags=hikari.MessageFlag.EPHEMERAL)
        else:
            await ctx.respond("Insufficient balance", flags=hikari.MessageFlag.EPHEMERAL)

    @miru.button(label="Accept + Respond", style=hikari.ButtonStyle.SUCCESS)
    async def accept_respond(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        modal = ResponseModal(self.user_id, "accept", self.price, self.reward_type, self.reward_details)
        await ctx.respond_with_modal(modal)

class RedeemButton(miru.View):
    def __init__(self, label: str, color: str) -> None:
        super().__init__(timeout=None)
        style_map = {
            'green': hikari.ButtonStyle.SUCCESS,
            'blue': hikari.ButtonStyle.PRIMARY,
            'red': hikari.ButtonStyle.DANGER,
            'gray': hikari.ButtonStyle.SECONDARY
        }
        self.children[0].label = label
        self.children[0].style = style_map.get(color, hikari.ButtonStyle.SUCCESS)

    @miru.button(label="Redeem", style=hikari.ButtonStyle.SUCCESS, custom_id="redeem_persistent")
    async def redeem_btn(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        banned_role = await get_setting(ctx.guild_id, 'banned_role')
        if banned_role and banned_role in ctx.member.role_ids:
            await ctx.respond("You're banned from using this", flags=hikari.MessageFlag.EPHEMERAL)
            return

        user = await get_user(ctx.user.id)
        price = await get_setting(ctx.guild_id, 'price_per_usd') or 1.0
        usd_value = user['balance'] / price

        if usd_value < 5:
            await ctx.respond(
                f"Need at least 5 USD ({int(price * 5)} coins). You have {usd_value:.2f} USD",
                flags=hikari.MessageFlag.EPHEMERAL
            )
            return

        modal = RedeemModal(user['balance'], price)
        await ctx.respond_with_modal(modal)

@bot.listen(hikari.StartedEvent)
async def on_started(event):
    await init_db()
    print("Bot ready")

    asyncio.create_task(update_leaderboards())
    asyncio.create_task(track_voice())

async def track_voice():
    while True:
        await asyncio.sleep(3600)

        current_time = datetime.utcnow()
        remove_list = []

        for user_id, data in voice_tracking.items():
            if await is_ignored(data['channel_id']):
                continue

            banned_role = await get_setting(data['guild_id'], 'banned_role')
            member = bot.cache.get_member(data['guild_id'], user_id)
            if member and banned_role and banned_role in member.role_ids:
                remove_list.append(user_id)
                continue

            user = await get_user(user_id)
            if user['daily_earned'] >= 200:
                continue

            streak = await check_streak(user_id)
            multiplier = await get_multiplier(streak)
            coins = int(20 * multiplier)

            new_total = min(user['daily_earned'] + coins, 200)
            actual_coins = new_total - user['daily_earned']

            await update_user(
                user_id,
                balance=user['balance'] + actual_coins,
                daily_earned=new_total,
                last_voice=current_time.isoformat(),
                last_activity=current_time.isoformat()
            )

        for user_id in remove_list:
            voice_tracking.pop(user_id, None)

async def update_leaderboards():
    while True:
        await asyncio.sleep(300)

        for guild in bot.cache.get_guilds_view().values():
            lb_channel = await get_setting(guild.id, 'lb_channel')
            lb_message = await get_setting(guild.id, 'lb_message')

            if not lb_channel:
                continue

            async with aiosqlite.connect(DB_FILE) as db:
                async with db.execute(
                    "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10"
                ) as cursor:
                    rows = await cursor.fetchall()

            if not rows:
                continue

            color_hex = await get_setting(guild.id, 'lb_color') or '#5865F2'
            color = int(color_hex.replace('#', ''), 16)

            embed = hikari.Embed(title="💰 Leaderboard", color=color)
            desc = ""
            for i, (uid, bal) in enumerate(rows, 1):
                medals = {1: "🥇", 2: "🥈", 3: "🥉"}
                medal = medals.get(i, f"{i}.")
                desc += f"{medal} <@{uid}> - {bal} coins\n"

            embed.description = desc
            embed.set_footer(text="Updates every 5 mins")

            try:
                if lb_message:
                    try:
                        await bot.rest.edit_message(lb_channel, lb_message, embed=embed)
                    except:
                        msg = await bot.rest.create_message(lb_channel, embed=embed)
                        await set_setting(guild.id, lb_message=msg.id)
                else:
                    msg = await bot.rest.create_message(lb_channel, embed=embed)
                    await set_setting(guild.id, lb_message=msg.id)
            except:
                pass

@bot.listen(hikari.VoiceStateUpdateEvent)
async def on_voice(event):
    if event.state.member.is_bot:
        return

    user_id = event.state.user_id
    guild_id = event.state.guild_id

    if event.state.channel_id:
        voice_tracking[user_id] = {
            'channel_id': event.state.channel_id,
            'guild_id': guild_id,
            'joined': datetime.utcnow()
        }
    else:
        voice_tracking.pop(user_id, None)

@bot.listen(hikari.GuildMessageCreateEvent)
async def on_message(event):
    if event.is_bot or not event.guild_id:
        return

    if await is_ignored(event.channel_id):
        return

    banned_role = await get_setting(event.guild_id, 'banned_role')
    if banned_role and banned_role in event.member.role_ids:
        return

    user_id = event.author_id
    now = datetime.utcnow()

    if user_id in user_cooldowns:
        last_time = user_cooldowns[user_id]
        if (now - last_time).total_seconds() < 25:
            return

    user_cooldowns[user_id] = now

    user = await get_user(user_id)

    last_daily = user['last_daily']
    if last_daily:
        last_date = datetime.fromisoformat(last_daily).date()
        if last_date != now.date():
            await update_user(user_id, daily_earned=0)
            user['daily_earned'] = 0

    if user['daily_earned'] >= 200:
        return

    streak = await check_streak(user_id)
    multiplier = await get_multiplier(streak)
    coins = int(5 * multiplier)

    new_total = min(user['daily_earned'] + coins, 200)
    actual_coins = new_total - user['daily_earned']

    await update_user(
        user_id,
        balance=user['balance'] + actual_coins,
        daily_earned=new_total,
        last_message=now.isoformat(),
        last_activity=now.isoformat()
    )

@bot.command
@lightbulb.command("daily", "Claim daily reward")
@lightbulb.implements(lightbulb.SlashCommand)
async def daily_cmd(ctx):
    banned_role = await get_setting(ctx.guild_id, 'banned_role')
    if banned_role and banned_role in ctx.member.role_ids:
        await ctx.respond("Banned", flags=hikari.MessageFlag.EPHEMERAL)
        return

    user = await get_user(ctx.user.id)
    now = datetime.utcnow()

    if user['last_daily']:
        last = datetime.fromisoformat(user['last_daily'])
        if (now - last).total_seconds() < 86400:
            remaining = 86400 - (now - last).total_seconds()
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            await ctx.respond(
                f"Wait {hours}h {minutes}m",
                flags=hikari.MessageFlag.EPHEMERAL
            )
            return

    streak = user['streak']
    if user['last_daily']:
        last = datetime.fromisoformat(user['last_daily'])
        diff = (now - last).total_seconds() / 3600
        if diff <= 48:
            streak = min(streak + 1, 7)
        else:
            streak = 0
    else:
        streak = 0

    coins = random.randint(1, 20)
    await update_user(
        ctx.user.id,
        balance=user['balance'] + coins,
        last_daily=now.isoformat(),
        streak=streak,
        last_activity=now.isoformat()
    )

    await ctx.respond(f"Got {coins} coins! Streak: {streak}")

@bot.command
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.MANAGE_MESSAGES))
@lightbulb.option("channel", "Channel", type=hikari.TextableGuildChannel)
@lightbulb.command("ignorechannel", "Toggle ignored channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def ignore_channel(ctx):
    channel_id = ctx.options.channel.id

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            "SELECT 1 FROM ignored_channels WHERE channel_id = ?", (channel_id,)
        ) as cursor:
            exists = await cursor.fetchone()

        if exists:
            await db.execute("DELETE FROM ignored_channels WHERE channel_id = ?", (channel_id,))
            action = "removed from"
        else:
            await db.execute("INSERT INTO ignored_channels VALUES (?)", (channel_id,))
            action = "added to"

        await db.commit()

    await ctx.respond(f"Channel {action} ignored list")

@bot.command
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.MANAGE_MESSAGES))
@lightbulb.option("color", "Hex color (e.g. #FF5733)", type=str, default="#5865F2")
@lightbulb.option("channel", "Channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setleaderboard", "Setup leaderboard")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_lb(ctx):
    old_msg = await get_setting(ctx.guild_id, 'lb_message')
    old_ch = await get_setting(ctx.guild_id, 'lb_channel')

    if old_msg and old_ch:
        try:
            await bot.rest.delete_message(old_ch, old_msg)
        except:
            pass

    await set_setting(
        ctx.guild_id,
        lb_channel=ctx.options.channel.id,
        lb_color=ctx.options.color,
        lb_message=None
    )

    await ctx.respond(f"Leaderboard set to {ctx.options.channel.mention}")

@bot.command
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.ADMINISTRATOR))
@lightbulb.option("price", "Coins per 1 USD", type=float)
@lightbulb.command("setprice", "Change reward price")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_price(ctx):
    await set_setting(ctx.guild_id, price_per_usd=ctx.options.price)

    redeem_ch = await get_setting(ctx.guild_id, 'redeem_channel')
    redeem_msg = await get_setting(ctx.guild_id, 'redeem_message')

    if redeem_ch and redeem_msg:
        try:
            chart = await generate_chart(ctx.options.price)

            color_hex = await get_setting(ctx.guild_id, 'redeem_color') or '#5865F2'
            color = int(color_hex.replace('#', ''), 16)

            embed = hikari.Embed(
                title="💎 Redeem Rewards",
                description=f"Current rate: **{ctx.options.price}** coins per 1 USD\n\n"
                           "Minimum: 5 USD to redeem",
                color=color
            )

            btn_color = await get_setting(ctx.guild_id, 'redeem_button_color') or 'green'
            btn_text = await get_setting(ctx.guild_id, 'redeem_button_text') or 'Redeem'
            view = RedeemButton(btn_text, btn_color)

            await bot.rest.edit_message(
                redeem_ch, redeem_msg,
                embed=embed,
                attachment=hikari.Bytes(chart, "chart.png"),
                components=view
            )
        except:
            pass

    await ctx.respond(f"Price updated to {ctx.options.price} coins per USD")

@bot.command
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.MANAGE_MESSAGES))
@lightbulb.option("button_text", "Button text", type=str, default="Redeem")
@lightbulb.option("button_color", "Button color", type=str, default="green", choices=["green", "blue", "red", "gray"])
@lightbulb.option("embed_color", "Embed color", type=str, default="#5865F2")
@lightbulb.option("channel", "Channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setredeem", "Setup redeem message")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_redeem(ctx):
    old_msg = await get_setting(ctx.guild_id, 'redeem_message')
    old_ch = await get_setting(ctx.guild_id, 'redeem_channel')

    if old_msg and old_ch:
        try:
            await bot.rest.delete_message(old_ch, old_msg)
        except:
            pass

    price = await get_setting(ctx.guild_id, 'price_per_usd') or 1.0
    chart = await generate_chart(price)

    color = int(ctx.options.embed_color.replace('#', ''), 16)
    embed = hikari.Embed(
        title="💎 Redeem Rewards",
        description=f"Current rate: **{price}** coins per 1 USD\n\n"
                   "Minimum: 5 USD to redeem",
        color=color
    )

    view = RedeemButton(ctx.options.button_text, ctx.options.button_color)

    msg = await bot.rest.create_message(
        ctx.options.channel,
        embed=embed,
        attachment=hikari.Bytes(chart, "chart.png"),
        components=view
    )

    await set_setting(
        ctx.guild_id,
        redeem_channel=ctx.options.channel.id,
        redeem_message=msg.id,
        redeem_color=ctx.options.embed_color,
        redeem_button_color=ctx.options.button_color,
        redeem_button_text=ctx.options.button_text
    )

    await ctx.respond(f"Redeem message created in {ctx.options.channel.mention}")

@bot.command
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.MANAGE_MESSAGES))
@lightbulb.option("channel", "Approval channel", type=hikari.TextableGuildChannel)
@lightbulb.command("setapproval", "Set approval channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_approval(ctx):
    await set_setting(ctx.guild_id, approval_channel=ctx.options.channel.id)
    await ctx.respond(f"Approval channel set to {ctx.options.channel.mention}")

@bot.command
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.MANAGE_MESSAGES))
@lightbulb.option("amount", "Amount", type=int)
@lightbulb.option("user", "User", type=hikari.User)
@lightbulb.command("addcoins", "Add coins to user")
@lightbulb.implements(lightbulb.SlashCommand)
async def add_coins(ctx):
    user = await get_user(ctx.options.user.id)
    await update_user(ctx.options.user.id, balance=user['balance'] + ctx.options.amount)
    await ctx.respond(f"Added {ctx.options.amount} coins to {ctx.options.user.mention}")

@bot.command
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.MANAGE_MESSAGES))
@lightbulb.option("amount", "Amount", type=int)
@lightbulb.option("user", "User", type=hikari.User)
@lightbulb.command("removecoins", "Remove coins from user")
@lightbulb.implements(lightbulb.SlashCommand)
async def remove_coins(ctx):
    user = await get_user(ctx.options.user.id)
    new_balance = max(0, user['balance'] - ctx.options.amount)
    await update_user(ctx.options.user.id, balance=new_balance)
    await ctx.respond(f"Removed {ctx.options.amount} coins from {ctx.options.user.mention}")

@bot.command
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.MANAGE_MESSAGES))
@lightbulb.option("role", "Banned role", type=hikari.Role)
@lightbulb.command("setbanned", "Set banned role")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_banned(ctx):
    await set_setting(ctx.guild_id, banned_role=ctx.options.role.id)

    async with aiosqlite.connect(DB_FILE) as db:
        for member in bot.cache.get_members_view_for_guild(ctx.guild_id).values():
            if ctx.options.role.id in member.role_ids:
                await db.execute("DELETE FROM users WHERE user_id = ?", (member.id,))
        await db.commit()

    await ctx.respond(f"Banned role set to {ctx.options.role.mention}")

@bot.command
@lightbulb.add_checks(lightbulb.has_guild_permissions(hikari.Permissions.MANAGE_MESSAGES))
@lightbulb.option("type", "Type", type=str, choices=["leaderboard", "redeem"])
@lightbulb.command("restore", "Restore deleted message")
@lightbulb.implements(lightbulb.SlashCommand)
async def restore(ctx):
    if ctx.options.type == "leaderboard":
        lb_ch = await get_setting(ctx.guild_id, 'lb_channel')
        if not lb_ch:
            await ctx.respond("Not configured", flags=hikari.MessageFlag.EPHEMERAL)
            return

        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10"
            ) as cursor:
                rows = await cursor.fetchall()

        color_hex = await get_setting(ctx.guild_id, 'lb_color') or '#5865F2'
        color = int(color_hex.replace('#', ''), 16)

        embed = hikari.Embed(title="💰 Leaderboard", color=color)
        desc = ""
        for i, (uid, bal) in enumerate(rows, 1):
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            medal = medals.get(i, f"{i}.")
            desc += f"{medal} <@{uid}> - {bal} coins\n"

        embed.description = desc
        embed.set_footer(text="Updates every 5 mins")

        msg = await bot.rest.create_message(lb_ch, embed=embed)
        await set_setting(ctx.guild_id, lb_message=msg.id)
        await ctx.respond("Leaderboard restored")

    else:
        redeem_ch = await get_setting(ctx.guild_id, 'redeem_channel')
        if not redeem_ch:
            await ctx.respond("Not configured", flags=hikari.MessageFlag.EPHEMERAL)
            return

        price = await get_setting(ctx.guild_id, 'price_per_usd') or 1.0
        chart = await generate_chart(price)

        color_hex = await get_setting(ctx.guild_id, 'redeem_color') or '#5865F2'
        color = int(color_hex.replace('#', ''), 16)

        embed = hikari.Embed(
            title="💎 Redeem Rewards",
            description=f"Current rate: **{price}** coins per 1 USD\n\n"
                       "Minimum: 5 USD to redeem",
            color=color
        )

        btn_color = await get_setting(ctx.guild_id, 'redeem_button_color') or 'green'
        btn_text = await get_setting(ctx.guild_id, 'redeem_button_text') or 'Redeem'
        view = RedeemButton(btn_text, btn_color)

        msg = await bot.rest.create_message(
            redeem_ch,
            embed=embed,
            attachment=hikari.Bytes(chart, "chart.png"),
            components=view
        )

        await set_setting(ctx.guild_id, redeem_message=msg.id)
        await ctx.respond("Redeem message restored")

@bot.command
@lightbulb.option("user", "User", type=hikari.User, default=None)
@lightbulb.command("balance", "Check balance")
@lightbulb.implements(lightbulb.SlashCommand)
async def balance(ctx):
    target = ctx.options.user or ctx.user
    user = await get_user(target.id)

    price = await get_setting(ctx.guild_id, 'price_per_usd') or 1.0
    usd = user['balance'] / price

    embed = hikari.Embed(
        title=f"{target.username}'s Balance",
        color=0x5865F2
    )
    embed.add_field("Coins", str(user['balance']), inline=True)
    embed.add_field("USD Value", f"${usd:.2f}", inline=True)
    embed.add_field("Streak", str(user['streak']), inline=True)

    await ctx.respond(embed=embed)

@bot.command
@lightbulb.command("ping", "Check if the bot is alive")
@lightbulb.implements(lightbulb.SlashCommand)
async def ping(ctx):
    await ctx.respond(f"Pong! Latency: {round(bot.heartbeat_latency * 1000)}ms")

@bot.command
@lightbulb.command("help", "See all available commands")
@lightbulb.implements(lightbulb.SlashCommand)
async def help_cmd(ctx):
    embed = hikari.Embed(
        title="🤖 Bot Help Menu",
        description="Here is a list of all commands you can use:",
        color=0x5865F2
    )
    
    # Economy Commands
    embed.add_field("💰 Economy", "`/balance` - Check your wallet\n`/daily` - Claim daily coins", inline=False)
    
    # Staff Commands
    embed.add_field("🛠️ Management", "`/addcoins` - Give coins\n`/removecoins` - Take coins\n`/setprice` - Set coin value\n`/ignorechannel` - Toggle channel tracking", inline=False)
    
    # Setup Commands
    embed.add_field("⚙️ Setup", "`/setleaderboard` - Setup leaderboard\n`/setredeem` - Setup redeem shop\n`/setapproval` - Set staff logs\n`/setbanned` - Set blacklisted role", inline=False)
    
    # Misc
    embed.add_field("✨ Misc", "`/ping` - Check connection\n`/help` - This menu", inline=False)
    
    await ctx.respond(embed=embed)

if __name__ == "__main__":
    bot.run(
        activity=hikari.Activity(
            name="Zo's wallet",
            type=hikari.ActivityType.WATCHING
        ),
        status=hikari.Status.DO_NOT_DISTURB
    )