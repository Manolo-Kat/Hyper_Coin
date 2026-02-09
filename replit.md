# replit.md

## Overview

This is a Discord bot built with Python using the Hikari ecosystem. The bot uses Hikari as the Discord API wrapper, Lightbulb as the command framework, and Miru for interactive components (buttons, modals, selects, etc.). The bot is currently in early development with the core setup in place but minimal commands implemented. It includes image generation capabilities (Pillow, matplotlib) and async HTTP/file operations.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Core Framework
- **Hikari** (`hikari`): Low-level Discord API wrapper chosen for performance and async-first design
- **Lightbulb** (`hikari-lightbulb`): Command handler framework built on top of Hikari, providing slash command support and command management
- **Miru** (`hikari-miru`): Component handler for interactive UI elements like buttons, select menus, and modals

### Bot Configuration
- The bot is initialized with `ALL` intents enabled, meaning it receives all gateway events from Discord
- `default_enabled_guilds=()` means slash commands are registered globally (not limited to specific guilds)
- Bot token is loaded from a `.env` file using `python-dotenv`
- There's an owner ID (`867623049741860874`) and a moderator role ID (`1373312465626202222`) hardcoded for permission management

### Application Structure
- **Single-file architecture**: Everything lives in `main.py` currently. As the bot grows, commands should ideally be split into extensions/plugins using Lightbulb's extension system
- **Logging**: Dual logging to both `bot.log` file and stdout console
- **Async-first**: Uses `asyncio`, `aiohttp` for HTTP requests, and `aiofiles` for file I/O — all non-blocking

### Image & Data Visualization
- **Pillow (PIL)**: Used for image generation and manipulation
- **Matplotlib**: Used with the `Agg` (non-interactive) backend for generating charts and graphs as images

### Key Design Decisions
- **No database currently**: The bot has no persistent storage set up yet. If data persistence is needed, a database (like SQLite or PostgreSQL with an ORM) should be added
- **No command extensions loaded**: The bot has the framework wired up but no commands or event listeners are defined yet in the visible code
- **Environment-based config**: Secrets are managed via `.env` file rather than hardcoded values

### Running the Bot
- Entry point is `main.py`
- Requires a `.env` file with `BOT_TOKEN` set to a valid Discord bot token
- Python 3.10+ recommended due to Hikari requirements

## External Dependencies

### Python Packages
| Package | Purpose |
|---------|---------|
| `hikari` (2.0.0.dev124) | Discord API wrapper |
| `hikari-lightbulb` (2.3.5) | Command framework |
| `hikari-miru` (4.1.0) | Interactive components (buttons, menus, modals) |
| `aiohttp` (>=3.9) | Async HTTP client for API calls |
| `aiofiles` | Async file I/O |
| `Pillow` (>=10.1.0) | Image generation and manipulation |
| `matplotlib` (>=3.8.0) | Chart/graph generation |
| `python-dotenv` (>=1.0.0) | Loading environment variables from `.env` |

### External Services
- **Discord API**: The bot connects to Discord's gateway and REST API. Requires a valid bot token from the Discord Developer Portal
- **No other external APIs** are currently integrated, though `aiohttp` is imported and ready for making HTTP requests to external services

### Environment Variables
| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | Yes | Discord bot authentication token |