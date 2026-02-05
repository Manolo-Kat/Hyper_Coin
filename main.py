import hikari
import lightbulb
import miru
import asyncio
import aiohttp
import random
from datetime import datetime, timedelta
from collections import defaultdict
import json
import os
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')
from dotenv import dotenv_values

config_env = dotenv_values(".env")
BOT_TOKEN = config_env.get('BOT_TOKEN')

if not BOT_TOKEN:
    print("=" * 60)
    print("ERROR: BOT_TOKEN not found in .env file!")
    print("=" * 60)
    print("Please create a .env file with:")
    print("BOT_TOKEN=your_bot_token_here")
    print("=" * 60)
    exit(1)

bot = lightbulb.BotApp(
    token=BOT_TOKEN,
    intents=hikari.Intents.ALL,
    default_enabled_guilds=()
)
miru.install(bot)

OWNER_ID = 867623049741860874
MOD_ROLE_ID = 1373312465626202222

user_data = {}
cooldowns = {}
voice_join_times = {}
daily_earnings = defaultdict(int)
last_daily = {}
streaks = {}
last_activity = {}
uncounted_channels = set()
banned_role_id = None
config = {
    'leaderboard_channel': None,
    'leaderboard_msg': None,
    'leaderboard_color': 0x5865F2,
    'redeem_channel': None,
    'redeem_msg': None,
    'redeem_color': 0x5865F2,
    'redeem_button_color': hikari.ButtonStyle.PRIMARY,
    'redeem_button_text': 'Redeem',
    'approval_channel': None,
    'price_per_usd': 100,
    'log_channel': None
}

def load_data():
    global user_data, uncounted_channels, config, last_daily, streaks, last_activity, banned_role_id
    try:
        with open('data.json', 'r') as f:
            data = json.load(f)
            user_data = {int(k): v for k, v in data.get('users', {}).items()}
            uncounted_channels = set(data.get('uncounted', []))
            config.update(data.get('config', {}))
            last_daily = {int(k): datetime.fromisoformat(v) for k, v in data.get('daily', {}).items()}
            streaks = {int(k): v for k, v in data.get('streaks', {}).items()}
            last_activity = {int(k): datetime.fromisoformat(v) for k, v in data.get('activity', {}).items()}
            banned_role_id = data.get('banned_role')
    except:
        pass

def save_data():
    data = {
        'users': user_data,
        'uncounted': list(uncounted_channels),
        'config': config,
        'daily': {k: v.isoformat() for k, v in last_daily.items()},
        'streaks': streaks,
        'activity': {k: v.isoformat() for k, v in last_activity.items()},
        'banned_role': banned_role_id
    }
    with open('data.json', 'w') as f:
        json.dump(data, f)

def get_streak_mult(user_id):
    streak = streaks.get(user_id, 0)
    mults = {0: 1, 1: 1.25, 2: 1.5, 3: 1.75, 4: 2, 5: 2.25, 6: 2.25, 7: 2.5}
    return mults.get(min(streak, 7), 1)

def check_streak(user_id):
    now = datetime.now()
    if user_id in last_activity:
        diff = (now - last_activity[user_id]).total_seconds() / 3600
        if diff > 24:
            streaks[user_id] = 0
    last_activity[user_id] = now

def is_banned(member):
    if not banned_role_id:
        return False
    return banned_role_id in member.role_ids

async def add_coins(user_id, amount):
    if user_id not in user_data:
        user_data[user_id] = 0

    today = datetime.now().date()
    daily_key = f"{user_id}_{today}"

    if daily_earnings[daily_key] >= 200:
        return False

    mult = get_streak_mult(user_id)
    actual = int(amount * mult)

    if daily_earnings[daily_key] + actual > 200:
        actual = 200 - daily_earnings[daily_key]

    user_data[user_id] += actual
    daily_earnings[daily_key] += actual
    save_data()
    return True

def create_chart():
    fig, ax = plt.subplots(figsize=(8, 5))

    price = config['price_per_usd']
    amounts = [1, 5, 10, 20, 50]
    coins = [price * x for x in amounts]

    bars = ax.bar([f'${x}' for x in amounts], coins, color='#5865F2')
    ax.set_ylabel('Coins', fontsize=12)
    ax.set_title('Reward Prices', fontsize=14, fontweight='bold')
    ax.grid(axis='y', alpha=0.3)

    for bar, coin in zip(bars, coins):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{int(coin)}', ha='center', va='bottom', fontsize=10)

    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    buf.seek(0)
    plt.close()
    return buf

class RedeemButton(miru.Button):
    def __init__(self):
        super().__init__(
            style=config['redeem_button_color'],
            label=config['redeem_button_text']
        )

    async def callback(self, ctx: miru.ViewContext):
        member = ctx.guild_id and await bot.rest.fetch_member(ctx.guild_id, ctx.user.id)
        if member and is_banned(member):
            await ctx.respond("You're banned from using this bot.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        balance = user_data.get(ctx.user.id, 0)
        required = config['price_per_usd'] * 5

        if balance < required:
            await ctx.respond(f"You need at least {required} coins (5$) to redeem.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        modal = RedeemModal()
        await ctx.respond_with_modal(modal)

class RedeemModal(miru.Modal):
    def __init__(self):
        super().__init__("Redeem Reward")
        self.add_item(miru.TextInput(label="Reward Type", placeholder="e.g., Discord Nitro, PayPal, etc."))
        self.add_item(miru.TextInput(label="Reward Details", placeholder="Amount/details", style=hikari.TextInputStyle.PARAGRAPH))

    async def callback(self, ctx: miru.ModalContext):
        reward_type = self.children[0].value
        reward_details = self.children[1].value

        view = ApprovalView(ctx.user.id, reward_type, reward_details)

        embed = hikari.Embed(
            title="💰 Reward Redemption Request",
            color=0xFFD700
        )
        embed.add_field("User", f"<@{ctx.user.id}>", inline=True)
        embed.add_field("Balance", f"{user_data.get(ctx.user.id, 0)} coins", inline=True)
        embed.add_field("Reward Type", reward_type, inline=False)
        embed.add_field("Reward Details", reward_details, inline=False)
        embed.timestamp = datetime.now()

        channel = config.get('approval_channel')
        if channel:
            msg = await bot.rest.create_message(channel, embed=embed, components=view)
            view.start(msg)

        await ctx.respond("Your redemption request has been submitted!", flags=hikari.MessageFlag.EPHEMERAL)

class ApprovalView(miru.View):
    def __init__(self, user_id, reward_type, reward_details):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.reward_type = reward_type
        self.reward_details = reward_details

    @miru.button(label="Reject w/ Response", style=hikari.ButtonStyle.DANGER)
    async def reject_btn(self, btn: miru.Button, ctx: miru.ViewContext):
        member = await bot.rest.fetch_member(ctx.guild_id, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("You don't have permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        modal = ResponseModal(self.user_id, False, self.reward_type, self.reward_details)
        await ctx.respond_with_modal(modal)

    @miru.button(label="Accept (No Response)", style=hikari.ButtonStyle.SUCCESS)
    async def accept_no_response(self, btn: miru.Button, ctx: miru.ViewContext):
        member = await bot.rest.fetch_member(ctx.guild_id, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("You don't have permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        amount = self.calculate_cost()
        if user_data.get(self.user_id, 0) >= amount:
            user_data[self.user_id] -= amount
            save_data()

            embed = hikari.Embed(title="✅ Redemption Approved", color=0x00FF00)
            embed.description = f"Approved by <@{ctx.user.id}>"
            await ctx.edit_response(embed=embed, components=[])
            await ctx.respond("Request approved.", flags=hikari.MessageFlag.EPHEMERAL)

    @miru.button(label="Accept w/ Response", style=hikari.ButtonStyle.SUCCESS)
    async def accept_response(self, btn: miru.Button, ctx: miru.ViewContext):
        member = await bot.rest.fetch_member(ctx.guild_id, ctx.user.id)
        if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
            await ctx.respond("You don't have permission.", flags=hikari.MessageFlag.EPHEMERAL)
            return

        modal = ResponseModal(self.user_id, True, self.reward_type, self.reward_details)
        await ctx.respond_with_modal(modal)

    def calculate_cost(self):
        details = self.reward_details.lower()
        for i in range(1, 1000):
            if str(i) in details or f"${i}" in details:
                return config['price_per_usd'] * i
        return config['price_per_usd'] * 5

class ResponseModal(miru.Modal):
    def __init__(self, user_id, is_accept, reward_type, reward_details):
        title = "Acceptance Message" if is_accept else "Rejection Reason"
        super().__init__(title)
        self.user_id = user_id
        self.is_accept = is_accept
        self.reward_type = reward_type
        self.reward_details = reward_details

        label = "Message to user" if is_accept else "Rejection reason"
        self.add_item(miru.TextInput(label=label, style=hikari.TextInputStyle.PARAGRAPH))

    async def callback(self, ctx: miru.ModalContext):
        message = self.children[0].value

        try:
            user = await bot.rest.fetch_user(self.user_id)
            dm = await user.fetch_dm_channel()

            if self.is_accept:
                amount = self.calculate_cost()
                if user_data.get(self.user_id, 0) >= amount:
                    user_data[self.user_id] -= amount
                    save_data()

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
        except:
            pass

        await ctx.respond("Response sent.", flags=hikari.MessageFlag.EPHEMERAL)

    def calculate_cost(self):
        details = self.reward_details.lower()
        for i in range(1, 1000):
            if str(i) in details or f"${i}" in details:
                return config['price_per_usd'] * i
        return config['price_per_usd'] * 5

@bot.listen(hikari.StartedEvent)
async def on_start(event):
    load_data()
    bot.d.session = aiohttp.ClientSession()
    asyncio.create_task(update_leaderboard_loop())
    asyncio.create_task(update_redeem_loop())

    await bot.update_presence(
        status=hikari.Status.DO_NOT_DISTURB,
        activity=hikari.Activity(
            name="Zo's wallet",
            type=hikari.ActivityType.WATCHING
        )
    )

@bot.listen(hikari.StoppingEvent)
async def on_stop(event):
    await bot.d.session.close()

@bot.listen(hikari.GuildMessageCreateEvent)
async def on_message(event):
    if event.author.is_bot:
        return

    if event.channel_id in uncounted_channels:
        return

    member = event.member
    if member and is_banned(member):
        return

    user_id = event.author.id
    now = datetime.now()

    if user_id in cooldowns:
        if (now - cooldowns[user_id]).total_seconds() < 25:
            return

    cooldowns[user_id] = now
    check_streak(user_id)
    await add_coins(user_id, 5)

@bot.listen(hikari.VoiceStateUpdateEvent)
async def on_voice(event):
    if event.state.member.is_bot:
        return

    if event.state.channel_id in uncounted_channels:
        return

    if is_banned(event.state.member):
        return

    user_id = event.state.user_id

    if event.state.channel_id and not event.old_state:
        voice_join_times[user_id] = datetime.now()
    elif not event.state.channel_id and event.old_state:
        if user_id in voice_join_times:
            duration = (datetime.now() - voice_join_times[user_id]).total_seconds()
            hours = duration / 3600
            coins = int(hours * 20)
            check_streak(user_id)
            await add_coins(user_id, coins)
            del voice_join_times[user_id]

@bot.command
@lightbulb.command("daily", "Claim your daily reward")
@lightbulb.implements(lightbulb.SlashCommand)
async def daily_cmd(ctx):
    member = ctx.member
    if member and is_banned(member):
        await ctx.respond("You're banned from using this bot.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    user_id = ctx.user.id
    now = datetime.now()

    if user_id in last_daily:
        diff = (now - last_daily[user_id]).total_seconds()
        if diff < 86400:
            remaining = 86400 - diff
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            await ctx.respond(f"Already claimed! Wait {hours}h {minutes}m", flags=hikari.MessageFlag.EPHEMERAL)
            return

        if diff > 172800:
            streaks[user_id] = 0

    amount = random.randint(1, 20)
    user_data[user_id] = user_data.get(user_id, 0) + amount
    last_daily[user_id] = now

    if user_id not in streaks:
        streaks[user_id] = 0
    else:
        streaks[user_id] = min(streaks[user_id] + 1, 7)

    check_streak(user_id)
    save_data()

    await ctx.respond(f"You got {amount} coins! Streak: {streaks[user_id]} days", flags=hikari.MessageFlag.EPHEMERAL)

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

    action = ctx.options.action

    if action == "show":
        if uncounted_channels:
            channels = ", ".join([f"<#{c}>" for c in uncounted_channels])
            await ctx.respond(f"Uncounted: {channels}", flags=hikari.MessageFlag.EPHEMERAL)
        else:
            await ctx.respond("No uncounted channels.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    channel = ctx.options.channel
    if not channel:
        await ctx.respond("Specify a channel.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if action == "add":
        uncounted_channels.add(channel.id)
        save_data()
        await ctx.respond(f"Added {channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)
        await log_action(ctx, f"Added {channel.mention} to uncounted channels")
    else:
        uncounted_channels.discard(channel.id)
        save_data()
        await ctx.respond(f"Removed {channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)
        await log_action(ctx, f"Removed {channel.mention} from uncounted channels")

@bot.command
@lightbulb.option("color", "Hex color (e.g., FF5733)", required=False, default="5865F2")
@lightbulb.option("channel", "Channel for leaderboard", type=hikari.TextableGuildChannel)
@lightbulb.command("setleaderboard", "Set leaderboard channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_leaderboard(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    config['leaderboard_channel'] = ctx.options.channel.id

    try:
        config['leaderboard_color'] = int(ctx.options.color, 16)
    except:
        config['leaderboard_color'] = 0x5865F2

    save_data()
    await ctx.respond(f"Leaderboard set to {ctx.options.channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)
    await update_leaderboard()
    await log_action(ctx, f"Set leaderboard channel to {ctx.options.channel.mention}")

async def update_leaderboard():
    if not config.get('leaderboard_channel'):
        return

    top = sorted(user_data.items(), key=lambda x: x[1], reverse=True)[:10]

    embed = hikari.Embed(
        title="💰 Top 10 Richest Users",
        color=config['leaderboard_color']
    )

    desc = ""
    for i, (uid, bal) in enumerate(top, 1):
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        medal = medals.get(i, f"{i}.")
        desc += f"{medal} <@{uid}> - **{bal}** coins\n"

    embed.description = desc if desc else "No data yet"
    embed.timestamp = datetime.now()

    channel = config['leaderboard_channel']

    try:
        if config.get('leaderboard_msg'):
            try:
                await bot.rest.edit_message(channel, config['leaderboard_msg'], embed=embed)
                return
            except:
                pass

        if config.get('leaderboard_msg'):
            try:
                await bot.rest.delete_message(channel, config['leaderboard_msg'])
            except:
                pass

        msg = await bot.rest.create_message(channel, embed=embed)
        config['leaderboard_msg'] = msg.id
        save_data()
    except:
        pass

async def update_leaderboard_loop():
    while True:
        await asyncio.sleep(300)
        await update_leaderboard()

@bot.command
@lightbulb.option("price", "Coins per 1 USD", type=int)
@lightbulb.command("setprice", "Set reward price")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_price(ctx):
    if ctx.user.id != OWNER_ID:
        await ctx.respond("Owner only.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    config['price_per_usd'] = ctx.options.price
    save_data()
    await ctx.respond(f"Price set to {ctx.options.price} coins per $1", flags=hikari.MessageFlag.EPHEMERAL)
    await update_redeem()

@bot.command
@lightbulb.option("button_text", "Button text", required=False, default="Redeem")
@lightbulb.option("button_color", "Button color", choices=["blue", "green", "red", "gray"], required=False, default="blue")
@lightbulb.option("embed_color", "Hex color", required=False, default="5865F2")
@lightbulb.option("channel", "Channel for redeem", type=hikari.TextableGuildChannel)
@lightbulb.command("setredeem", "Set redeem channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_redeem(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    config['redeem_channel'] = ctx.options.channel.id
    config['redeem_button_text'] = ctx.options.button_text

    colors = {
        "blue": hikari.ButtonStyle.PRIMARY,
        "green": hikari.ButtonStyle.SUCCESS,
        "red": hikari.ButtonStyle.DANGER,
        "gray": hikari.ButtonStyle.SECONDARY
    }
    config['redeem_button_color'] = colors.get(ctx.options.button_color, hikari.ButtonStyle.PRIMARY)

    try:
        config['redeem_color'] = int(ctx.options.embed_color, 16)
    except:
        config['redeem_color'] = 0x5865F2

    save_data()
    await ctx.respond(f"Redeem set to {ctx.options.channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)
    await update_redeem()
    await log_action(ctx, f"Set redeem channel to {ctx.options.channel.mention}")

async def update_redeem():
    if not config.get('redeem_channel'):
        return

    chart = create_chart()

    embed = hikari.Embed(
        title="💎 Redeem Rewards",
        description=f"Current rate: **{config['price_per_usd']}** coins = $1 USD\n\nClick the button below to redeem your coins for rewards!",
        color=config['redeem_color']
    )
    embed.set_image("attachment://chart.png")
    embed.timestamp = datetime.now()

    view = miru.View()
    view.add_item(RedeemButton())

    channel = config['redeem_channel']

    try:
        if config.get('redeem_msg'):
            try:
                await bot.rest.delete_message(channel, config['redeem_msg'])
            except:
                pass

        msg = await bot.rest.create_message(
            channel,
            embed=embed,
            attachment=hikari.Bytes(chart, "chart.png"),
            components=view
        )
        config['redeem_msg'] = msg.id
        view.start(msg)
        save_data()
    except:
        pass

async def update_redeem_loop():
    while True:
        await asyncio.sleep(300)
        await update_redeem()

@bot.command
@lightbulb.option("channel", "Channel for approvals", type=hikari.TextableGuildChannel)
@lightbulb.command("setapproval", "Set approval channel")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_approval(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    config['approval_channel'] = ctx.options.channel.id
    save_data()
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

    user = ctx.options.user
    amount = ctx.options.amount
    action = ctx.options.action

    if user.id not in user_data:
        user_data[user.id] = 0

    if action == "add":
        user_data[user.id] += amount
        await ctx.respond(f"Added {amount} coins to {user.mention}", flags=hikari.MessageFlag.EPHEMERAL)
        await log_action(ctx, f"Added {amount} coins to {user.mention}")
    else:
        user_data[user.id] = max(0, user_data[user.id] - amount)
        await ctx.respond(f"Removed {amount} coins from {user.mention}", flags=hikari.MessageFlag.EPHEMERAL)
        await log_action(ctx, f"Removed {amount} coins from {user.mention}")

    save_data()

@bot.command
@lightbulb.option("role", "Role to ban", type=hikari.Role)
@lightbulb.command("banrole", "Set banned role")
@lightbulb.implements(lightbulb.SlashCommand)
async def ban_role(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    global banned_role_id
    banned_role_id = ctx.options.role.id

    guild = await bot.rest.fetch_guild(ctx.guild_id)
    members = bot.cache.get_members_view_for_guild(guild.id)

    for member_id in list(members.keys()):
        member = members[member_id]
        if banned_role_id in member.role_ids:
            if member_id in user_data:
                del user_data[member_id]

    save_data()
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
        await update_leaderboard()
        await ctx.respond("Leaderboard restored.", flags=hikari.MessageFlag.EPHEMERAL)
    else:
        await update_redeem()
        await ctx.respond("Redeem embed restored.", flags=hikari.MessageFlag.EPHEMERAL)

    await log_action(ctx, f"Restored {type_choice} embed")

@bot.command
@lightbulb.option("image", "Image URL or attachment", type=hikari.Attachment)
@lightbulb.command("setpfp", "Change bot avatar")
@lightbulb.implements(lightbulb.SlashCommand)
async def set_pfp(ctx):
    member = ctx.member
    if MOD_ROLE_ID not in member.role_ids and ctx.user.id != OWNER_ID:
        await ctx.respond("No permission.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    attachment = ctx.options.image

    async with bot.d.session.get(attachment.url) as resp:
        if resp.status == 200:
            data = await resp.read()
            await bot.rest.edit_my_user(avatar=data)
            await ctx.respond("Avatar updated.", flags=hikari.MessageFlag.EPHEMERAL)
            await log_action(ctx, "Changed bot avatar")
        else:
            await ctx.respond("Failed to download image.", flags=hikari.MessageFlag.EPHEMERAL)

@bot.command
@lightbulb.command("help", "Show commands")
@lightbulb.implements(lightbulb.SlashCommand)
async def help_cmd(ctx):
    member = ctx.member
    is_owner = ctx.user.id == OWNER_ID
    is_mod = member and MOD_ROLE_ID in member.role_ids

    embed = hikari.Embed(title="📚 Commands", color=0x5865F2)

    user_cmds = "• `/daily` - Claim daily reward\n• `/balance` - Check your coins\n• `/help` - Show this menu"

    if is_owner or is_mod:
        mod_cmds = "• `/uncounted` - Manage uncounted channels\n• `/setleaderboard` - Set leaderboard\n• `/setredeem` - Set redeem embed\n• `/setapproval` - Set approval channel\n• `/coins` - Manage user coins\n• `/banrole` - Set banned role\n• `/restore` - Restore embeds\n• `/setpfp` - Change bot avatar\n• `/setlog` - Set log channel"
        embed.add_field("User Commands", user_cmds, inline=False)
        embed.add_field("Moderator Commands", mod_cmds, inline=False)

        if is_owner:
            owner_cmds = "• `/setprice` - Set reward prices"
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

    config['log_channel'] = ctx.options.channel.id
    save_data()
    await ctx.respond(f"Log channel set to {ctx.options.channel.mention}", flags=hikari.MessageFlag.EPHEMERAL)

async def log_action(ctx, action):
    if not config.get('log_channel'):
        return

    embed = hikari.Embed(
        title="📝 Moderator Action",
        description=action,
        color=0xFFAA00
    )
    embed.add_field("Moderator", f"<@{ctx.user.id}>", inline=True)
    embed.timestamp = datetime.now()

    try:
        await bot.rest.create_message(config['log_channel'], embed=embed)
    except:
        pass

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

    if user.id != ctx.user.id:
        member = ctx.member
        if member and is_banned(member):
            await ctx.respond("You're banned.", flags=hikari.MessageFlag.EPHEMERAL)
            return

    balance = user_data.get(user.id, 0)
    streak = streaks.get(user.id, 0)
    mult = get_streak_mult(user.id)

    embed = hikari.Embed(title=f"💰 {user.username}'s Wallet", color=0xFFD700)
    embed.add_field("Balance", f"{balance} coins", inline=True)
    embed.add_field("Streak", f"{streak} days (x{mult})", inline=True)
    embed.set_thumbnail(user.avatar_url or user.default_avatar_url)

    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)

if __name__ == "__main__":
    bot.run()