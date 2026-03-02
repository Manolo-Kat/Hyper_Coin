# Hyper_Coin Beta

A Discord coin and reward bot built with Python (Hikari, Lightbulb, and Miru).

## Features
- **Earning**: Users earn coins by chatting and claiming daily rewards.
- **Shop**: Redeem coins for various real-world gift cards (PayPal, Steam, etc.).
- **Currency**: Support for multiple display currencies with live exchange rates.
- **Streaks**: Daily login streaks with coin multipliers.
- **Moderation**: Staff commands to manage balances, allowed roles, and bot settings.

## Commands
### User
- `/daily`: Claim daily reward.
- `/balance`: Check your coin balance and value.
- `/leaderboard`: See top users.
- `/buy`: Purchase items from the shop.
- `/currency`: Set preferred display currency.
- `/help`: Show command list.

### Staff
- `/coins`: Add/remove coins.
- `/setprice`: Set item prices.
- `/setapproval`: Set shop approval channel.
- `/setlog`: Set purchase log channel.
- `/banrole`: Restrict users from the bot.
- `/allowedroles`: Restrict earning to specific roles.
- `/customize`: Change bot avatar.

## Setup
1. Create a `.env` file with `BOT_TOKEN`.
2. Install dependencies: `pip install -r requirements.txt`.
3. Run the bot: `python main.py`.
