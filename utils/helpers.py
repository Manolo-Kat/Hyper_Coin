import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

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


async def get_exchange_rate(bot, currency: str) -> float | None:
    async with bot.d.rate_lock:
        cached = bot.d.rate_cache.get(currency)
        if cached:
            rate, ts = cached
            if (datetime.now(timezone.utc) - ts).total_seconds() < 3600:
                return rate
    try:
        async with bot.d.http.get(
            f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={currency}"
        ) as r:
            if r.status == 200:
                data = await r.json()
                rate = data.get('rates', {}).get(currency)
                if rate:
                    async with bot.d.rate_lock:
                        bot.d.rate_cache[currency] = (rate, datetime.now(timezone.utc))
                return rate
    except Exception as e:
        logger.warning(f"Exchange rate fetch failed for {currency}: {e}")
    return None


class SpamTracker:
    """
    Advanced in-memory spam detector for coin earning.
    Tracks message patterns per user and flags spam behaviour.
    """
    WINDOW          = 30    # seconds to look back
    RATE_LIMIT      = 5     # max distinct messages in WINDOW before soft ban
    BURST_THRESHOLD = 4     # messages in BURST_SPAN seconds triggers hard ban
    BURST_SPAN      = 6     # seconds for burst window
    SIMILAR_RATIO   = 0.80  # similarity ratio to flag as duplicate
    SIMILAR_MIN     = 3     # how many similar messages to trigger

    def __init__(self):
        self._history: dict[int, deque]    = defaultdict(lambda: deque(maxlen=20))
        self._penalty: dict[int, datetime] = {}

    def detect(self, user_id: int, content: str, now: datetime) -> bool:
        """
        Returns True if the message looks like spam and coins should be denied.
        Does NOT add to history when spam is detected so the record stays clean
        after the penalty expires.
        """
        # ── Existing penalty check ──────────────────────────────────────────
        exp = self._penalty.get(user_id)
        if exp:
            if now < exp:
                return True
            del self._penalty[user_id]

        history = self._history[user_id]
        cutoff  = now.timestamp() - self.WINDOW

        # Prune old entries
        while history and history[0][0] < cutoff:
            history.popleft()

        # ── Rule 1: Rapid-fire rate limit ──────────────────────────────────
        if len(history) >= self.RATE_LIMIT:
            self._penalty[user_id] = now + timedelta(minutes=5)
            logger.info(f"[spam] {user_id} rate-limited (5 min)")
            return True

        # ── Rule 2: Ultra-burst — N messages in BURST_SPAN seconds ─────────
        if len(history) >= self.BURST_THRESHOLD:
            ts_slice = [ts for ts, _ in list(history)[-self.BURST_THRESHOLD:]]
            if (ts_slice[-1] - ts_slice[0]) < self.BURST_SPAN:
                self._penalty[user_id] = now + timedelta(minutes=10)
                logger.info(f"[spam] {user_id} burst-banned (10 min)")
                return True

        # ── Rule 3: Identical content repeated ─────────────────────────────
        if content.strip():
            norm = content.strip().lower()
            same = sum(1 for _, c in history if c.strip().lower() == norm)
            if same >= 2:
                self._penalty[user_id] = now + timedelta(minutes=2)
                logger.info(f"[spam] {user_id} repeated message (2 min)")
                return True

        # ── Rule 4: High-similarity cluster ────────────────────────────────
        if content.strip() and len(history) >= self.SIMILAR_MIN - 1:
            recent = [c for _, c in list(history)[-(self.SIMILAR_MIN - 1):] if c.strip()]
            if len(recent) >= self.SIMILAR_MIN - 1:
                cl = content.lower()
                if all(SequenceMatcher(None, cl, c.lower()).ratio() > self.SIMILAR_RATIO
                       for c in recent):
                    self._penalty[user_id] = now + timedelta(minutes=3)
                    logger.info(f"[spam] {user_id} similar cluster (3 min)")
                    return True

        # Not spam — record and allow
        history.append((now.timestamp(), content))
        return False
