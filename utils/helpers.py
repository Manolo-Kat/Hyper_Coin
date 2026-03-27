import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import hikari

logger = logging.getLogger("HyperCoin")

STREAK_MULT = {0: 1.0, 1: 1.25, 2: 1.5, 3: 1.75, 4: 2.0, 5: 2.25, 6: 2.25, 7: 2.5}


def get_streak_mult(streak: int) -> float:
    return STREAK_MULT.get(min(streak, 7), 1.0)


def is_booster(member) -> bool:
    return bool(member and getattr(member, 'premium_since', None) is not None)


def is_banned_member(banned_role_id, member) -> bool:
    if not banned_role_id or not member:
        return False
    return banned_role_id in member.role_ids


def is_staff(ctx) -> bool:
    """Return True if the slash-context user has the mod role or is the owner."""
    from utils.config import MOD_ROLE_ID, OWNER_ID
    return MOD_ROLE_ID in ctx.member.role_ids or ctx.user.id == OWNER_ID


async def send_log_embed(bot, channel_id, embed: hikari.Embed) -> None:
    """Send an embed to the log channel. Logs a warning if sending fails."""
    if not channel_id:
        return
    try:
        await bot.rest.create_message(channel_id, embed=embed)
    except Exception as e:
        logger.warning(f"Failed to send log embed to channel {channel_id}: {e}")


# ── Currency helpers ──────────────────────────────────────────────────────────

COUNTRY_CURRENCY_MAP: dict[str, str] = {
    # Reset sentinel
    'coins': 'COINS', 'coin': 'COINS', 'default': 'COINS', 'reset': 'COINS',

    # Middle East
    'egypt': 'EGP', 'egyptian': 'EGP', 'egyptian pound': 'EGP',
    'saudi': 'SAR', 'saudi arabia': 'SAR', 'ksa': 'SAR', 'riyal': 'SAR',
    'uae': 'AED', 'emirates': 'AED', 'dirham': 'AED', 'dubai': 'AED', 'abu dhabi': 'AED',
    'kuwait': 'KWD', 'kuwaiti': 'KWD', 'kuwaiti dinar': 'KWD',
    'qatar': 'QAR', 'qatari': 'QAR', 'qatari riyal': 'QAR',
    'bahrain': 'BHD', 'bahraini': 'BHD',
    'oman': 'OMR', 'omani': 'OMR',
    'jordan': 'JOD', 'jordanian': 'JOD', 'jordanian dinar': 'JOD',
    'turkey': 'TRY', 'turkish': 'TRY', 'lira': 'TRY', 'turkish lira': 'TRY',
    'israel': 'ILS', 'israeli': 'ILS', 'shekel': 'ILS',
    'iran': 'IRR', 'iranian': 'IRR', 'rial': 'IRR',
    'iraq': 'IQD', 'iraqi': 'IQD', 'iraqi dinar': 'IQD',
    'lebanon': 'LBP', 'lebanese': 'LBP',
    'syria': 'SYP', 'syrian': 'SYP',
    'yemen': 'YER', 'yemeni': 'YER',

    # Europe
    'euro': 'EUR', 'europe': 'EUR', 'european': 'EUR', 'eu': 'EUR', 'eurozone': 'EUR',
    'uk': 'GBP', 'britain': 'GBP', 'england': 'GBP', 'pound': 'GBP',
    'sterling': 'GBP', 'british': 'GBP', 'great britain': 'GBP',
    'switzerland': 'CHF', 'swiss': 'CHF', 'franc': 'CHF',
    'sweden': 'SEK', 'swedish': 'SEK', 'krona': 'SEK',
    'norway': 'NOK', 'norwegian': 'NOK', 'krone': 'NOK',
    'denmark': 'DKK', 'danish': 'DKK',
    'poland': 'PLN', 'polish': 'PLN', 'zloty': 'PLN',
    'czech': 'CZK', 'czech republic': 'CZK', 'koruna': 'CZK',
    'hungary': 'HUF', 'hungarian': 'HUF', 'forint': 'HUF',
    'romania': 'RON', 'romanian': 'RON', 'leu': 'RON',
    'russia': 'RUB', 'russian': 'RUB', 'ruble': 'RUB',
    'ukraine': 'UAH', 'ukrainian': 'UAH', 'hryvnia': 'UAH',
    'serbia': 'RSD', 'serbian': 'RSD', 'dinar': 'RSD',
    'bulgaria': 'BGN', 'bulgarian': 'BGN', 'lev': 'BGN',
    'croatia': 'HRK', 'croatian': 'HRK', 'kuna': 'HRK',

    # Americas
    'usa': 'USD', 'us': 'USD', 'united states': 'USD', 'america': 'USD',
    'dollar': 'USD', 'usd': 'USD',
    'canada': 'CAD', 'canadian': 'CAD', 'canadian dollar': 'CAD',
    'mexico': 'MXN', 'mexican': 'MXN', 'peso': 'MXN',
    'brazil': 'BRL', 'brazilian': 'BRL', 'real': 'BRL',
    'argentina': 'ARS', 'argentinian': 'ARS',
    'chile': 'CLP', 'chilean': 'CLP',
    'colombia': 'COP', 'colombian': 'COP',
    'peru': 'PEN', 'peruvian': 'PEN', 'sol': 'PEN',
    'venezuela': 'VES', 'venezuelan': 'VES',

    # Asia Pacific
    'japan': 'JPY', 'japanese': 'JPY', 'yen': 'JPY',
    'china': 'CNY', 'chinese': 'CNY', 'yuan': 'CNY', 'renminbi': 'CNY', 'rmb': 'CNY',
    'hong kong': 'HKD', 'hongkong': 'HKD', 'hk': 'HKD',
    'taiwan': 'TWD', 'taiwanese': 'TWD',
    'south korea': 'KRW', 'korea': 'KRW', 'korean': 'KRW', 'won': 'KRW',
    'india': 'INR', 'indian': 'INR', 'rupee': 'INR',
    'singapore': 'SGD', 'singaporean': 'SGD', 'singapore dollar': 'SGD',
    'australia': 'AUD', 'australian': 'AUD', 'aussie': 'AUD',
    'new zealand': 'NZD', 'nz': 'NZD', 'kiwi': 'NZD',
    'philippines': 'PHP', 'philippine': 'PHP', 'philippine peso': 'PHP',
    'thailand': 'THB', 'thai': 'THB', 'baht': 'THB',
    'vietnam': 'VND', 'vietnamese': 'VND', 'dong': 'VND',
    'indonesia': 'IDR', 'indonesian': 'IDR', 'rupiah': 'IDR',
    'malaysia': 'MYR', 'malaysian': 'MYR', 'ringgit': 'MYR',
    'pakistan': 'PKR', 'pakistani': 'PKR', 'pakistani rupee': 'PKR',
    'bangladesh': 'BDT', 'bangladeshi': 'BDT', 'taka': 'BDT',
    'sri lanka': 'LKR', 'sri lankan': 'LKR',
    'myanmar': 'MMK', 'burmese': 'MMK',
    'cambodia': 'KHR', 'cambodian': 'KHR', 'riel': 'KHR',
    'laos': 'LAK', 'lao': 'LAK',

    # Africa
    'nigeria': 'NGN', 'nigerian': 'NGN', 'naira': 'NGN',
    'ghana': 'GHS', 'ghanaian': 'GHS', 'cedi': 'GHS',
    'kenya': 'KES', 'kenyan': 'KES', 'shilling': 'KES',
    'south africa': 'ZAR', 'south african': 'ZAR', 'rand': 'ZAR',
    'ethiopia': 'ETB', 'ethiopian': 'ETB', 'birr': 'ETB',
    'morocco': 'MAD', 'moroccan': 'MAD',
    'tunisia': 'TND', 'tunisian': 'TND',
    'algeria': 'DZD', 'algerian': 'DZD',
    'tanzania': 'TZS', 'tanzanian': 'TZS',
    'uganda': 'UGX', 'ugandan': 'UGX',
    'zimbabwe': 'ZWL', 'zimbabwean': 'ZWL',
    'senegal': 'XOF', 'cfa': 'XOF',

    # Other
    'bitcoin': 'BTC', 'btc': 'BTC',
}


def normalize_currency(inp: str) -> str | None:
    clean = inp.strip().lower()
    if clean in COUNTRY_CURRENCY_MAP:
        return COUNTRY_CURRENCY_MAP[clean]
    code = inp.strip().upper()
    if 2 <= len(code) <= 5 and code.isalpha():
        return code
    return None


async def get_exchange_rate(bot, currency: str) -> float | None:
    """Fetch exchange rate from USD. Lock is held for the entire fetch to prevent duplicate requests."""
    if currency in ('USD', 'COINS'):
        return 1.0 if currency == 'USD' else None

    async with bot.d.rate_lock:
        # Check cache inside the lock
        cached = bot.d.rate_cache.get(currency)
        if cached is not None:
            rate, ts = cached
            if (datetime.now(timezone.utc) - ts).total_seconds() < 3600:
                return rate

        # Fetch while holding the lock — prevents two coroutines both firing requests
        try:
            async with bot.d.http.get("https://open.er-api.com/v6/latest/USD") as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get('result') == 'success':
                        rates = data.get('rates', {})
                        now   = datetime.now(timezone.utc)
                        for c, v in rates.items():
                            bot.d.rate_cache[c] = (v, now)
                        return rates.get(currency)
        except Exception as e:
            logger.warning(f"Exchange rate fetch failed: {e}")

    return None


# ── Spam Tracker ──────────────────────────────────────────────────────────────

class SpamTracker:
    WINDOW          = 30
    RATE_LIMIT      = 5
    BURST_THRESHOLD = 4
    BURST_SPAN      = 6
    SIMILAR_RATIO   = 0.80
    SIMILAR_MIN     = 3
    SIMILAR_MAX_LEN = 150   # skip similarity check for long messages

    def __init__(self):
        self._history: dict[int, deque]    = defaultdict(lambda: deque(maxlen=20))
        self._penalty: dict[int, datetime] = {}

    def detect(self, user_id: int, content: str, now: datetime) -> bool:
        exp = self._penalty.get(user_id)
        if exp is not None:
            if now < exp:
                return True
            del self._penalty[user_id]  # expired — remove so dict doesn't grow forever

        history = self._history[user_id]
        cutoff  = now.timestamp() - self.WINDOW

        while history and history[0][0] < cutoff:
            history.popleft()

        if len(history) >= self.RATE_LIMIT:
            self._penalty[user_id] = now + timedelta(minutes=5)
            logger.info(f"[spam] {user_id} rate-limited (5 min)")
            return True

        if len(history) >= self.BURST_THRESHOLD:
            ts_slice = [ts for ts, _ in list(history)[-self.BURST_THRESHOLD:]]
            if (ts_slice[-1] - ts_slice[0]) < self.BURST_SPAN:
                self._penalty[user_id] = now + timedelta(minutes=10)
                logger.info(f"[spam] {user_id} burst-banned (10 min)")
                return True

        if content.strip():
            norm = content.strip().lower()
            same = sum(1 for _, c in history if c.strip().lower() == norm)
            if same >= 2:
                self._penalty[user_id] = now + timedelta(minutes=2)
                logger.info(f"[spam] {user_id} repeated message (2 min)")
                return True

        # Skip similarity check for long messages (too expensive and rarely spam)
        if content.strip() and len(content) <= self.SIMILAR_MAX_LEN and len(history) >= self.SIMILAR_MIN - 1:
            recent = [c for _, c in list(history)[-(self.SIMILAR_MIN - 1):] if c.strip()]
            if len(recent) >= self.SIMILAR_MIN - 1:
                cl = content.lower()
                if all(SequenceMatcher(None, cl, c.lower()).ratio() > self.SIMILAR_RATIO
                       for c in recent):
                    self._penalty[user_id] = now + timedelta(minutes=3)
                    logger.info(f"[spam] {user_id} similar cluster (3 min)")
                    return True

        history.append((now.timestamp(), content))
        return False
