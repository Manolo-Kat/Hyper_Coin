# Hyper_Coin Beta - Project Documentation

## Overview
A Discord coin/reward bot built with Python (hikari, lightbulb, miru). Users earn coins by chatting and daily rewards, then redeem them for real-world gifts via an approval workflow.

## Commands
### User Commands
- `/daily`: Claim daily reward (1-20 coins, 2x for boosters).
- `/balance`: Check coin balance and real-world value.
- `/leaderboard`: See top earners.
- `/buy`: Purchase gifts (PayPal, Steam, etc.).
- `/currency`: Set preferred display currency.
- `/help`: List all available commands.

### Staff Commands (Moderator Role/Owner Only)
- `/coins`: Add or remove coins from a user.
- `/uncounted`: Exclude channels from coin earning.
- `/allowedroles`: Restrict coin earning to specific roles.
- `/banrole`: Set a role that prevents users from using the bot.
- `/setapproval`: Set the channel for purchase requests.
- `/setlog`: Set the channel for administrative logs.
- `/setprice`: Set the coin price for specific gift items.
- `/customize`: Change the bot's avatar (Owner only).

## Configuration
- **Owner ID**: 823310792291385424
- **Mod Role ID**: 1373312465626202222
- **Data Storage**: `data.json`
- **Environment**: `.env` (requires `BOT_TOKEN`)

## Features
- Multi-currency support via Frankfurter API.
- Streak system (up to 2.5x multiplier).
- Server Booster perks (2x coins, higher daily limits).
- Full logging for administrative actions.
