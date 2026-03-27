import os
from dotenv import load_dotenv
load_dotenv(override=True)

OWNER_ID    = int(os.environ.get('OWNER_ID',    '823310792291385424'))
MOD_ROLE_ID = int(os.environ.get('MOD_ROLE_ID', '1373312465626202222'))
WEEKLY_LIMIT_USD = 20
COIN_COOLDOWN_SECONDS = 25

DEFAULT_SHOP_PRICES = {
    'PayPal': 100, 'Steam': 100, 'Google Play': 100,
    'Apple Store': 100, 'Discord Nitro Basic': 100,
    'Discord Nitro Boost': 100, 'Nintendo Card': 100, 'Roblox': 100
}
