# Hyper_Coin Beta — Project Documentation

## Overview
A Discord coin/reward bot (Hyper_Coin Beta#0594) built with Python using hikari, lightbulb, and miru.
Users earn coins by chatting and claiming daily rewards, then redeem them for real-world gifts via a staff approval workflow.

## File Structure
```
main.py                 — Bot entry point, lifecycle hooks, auto-drop loop
utils/
  config.py             — Constants (OWNER_ID, MOD_ROLE_ID, defaults)
  db.py                 — SQLite database layer (all CRUD, migration)
  helpers.py            — SpamTracker, exchange rate, streak/booster helpers
extensions/
  economy.py            — /daily, /balance, /leaderboard, /currency, /help, on_message
  shop.py               — /buy, /drop, /setdrop, ShopApprovalView, DropView
  admin.py              — /coins, /setprice, /bannedrole, /allowedrole, /uncounted,
                          /setapproval, /setlog, /customize, /ping
```

## Database
- **Engine**: SQLite via aiosqlite (`hyper_coin.db`)
- **Tables**: users, user_prefs, daily_claims, daily_earnings,
              weekly_spending, pending_purchases, guild_config
- **Migration**: runs once on startup from `data.json` → renames to `data.json.migrated`

## Configuration
- **OWNER_ID**: 823310792291385424 (env var)
- **MOD_ROLE_ID**: 1373312465626202222 (env var)
- **BOT_TOKEN**: Replit Secret
- **Weekly limit**: $20/week per user (Monday reset)
- **Coin cooldown**: 25 seconds per message

## Commands
### User
- `/daily` — Claim daily coins (1-20, 2× for boosters, streak multiplier up to 2.5×)
- `/balance [user]` — Coins, USD value, streak, booster, daily progress bar, weekly spend
- `/leaderboard` — Paginated top earners
- `/buy <item> <amount>` — Redeem coins for gift (pending staff approval)
- `/currency <code>` — Set display currency (EUR, GBP, EGP, etc.)
- `/help` — All commands

### Staff (MOD_ROLE or OWNER)
- `/coins <user> <amount>` — Add/remove coins
- `/uncounted <channel>` — Toggle channel from earning coins
- `/bannedrole <role>` — Set role blocked from using the bot
- `/allowedrole <role>` — Toggle role allowed to earn coins (empty = everyone)
- `/setapproval <channel>` — Set purchase approval channel
- `/setlog <channel>` — Set admin log channel
- `/setprice [item] <price>` — Set coins-per-$1 (global or per item)
- `/drop <amount>` — Manual coin drop with Claim button
- `/setdrop <channel>` — Set auto-drop channel
- `/ping` — Latency check

### Owner Only
- `/customize <avatar_url>` — Change bot avatar

## Features
- **Anti-spam**: Multi-tier detection (rate limit, burst, identical repeat, high similarity via difflib)
- **Streak system**: Up to 2.5× multiplier at 7-day streak
- **Booster perks**: 2× coins, 400/day limit (vs 200)
- **Daily cap**: 200/400 coins from chat per day (tracked per user)
- **Weekly purchase limit**: $20/week, resets Monday
- **Pending purchases**: Coins held until staff approves/rejects
- **Persistent buttons**: Approval buttons survive bot restarts
- **Auto-drop**: Random 50–500 coin drops every 30–90 min
- **Exchange rates**: 1-hour cache via Frankfurter API

## Environment
- Python 3.11, hikari 2.0.0.dev124, hikari-lightbulb 2.3.5, hikari-miru 4.1.0
- aiosqlite 0.21.0 for async SQLite
