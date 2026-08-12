import logging

logger = logging.getLogger(__name__)

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Google numbers days from Sunday; the contract's keys start at Monday.
_GOOGLE_DAY_TO_KEY = {0: "sun", 1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat"}


def _clock(point: dict) -> str | None:
    hour = point.get("hour")
    minute = point.get("minute", 0)
    if not isinstance(hour, int) or not isinstance(minute, int):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def hours_from_google(regular_opening_hours: dict | None) -> dict[str, dict | None]:
    hours: dict[str, dict | None] = {key: None for key in DAY_KEYS}
    periods = (regular_opening_hours or {}).get("periods") or []

    for period in periods:
        opens = period.get("open") or {}
        key = _GOOGLE_DAY_TO_KEY.get(opens.get("day"))
        open_at = _clock(opens)
        if key is None or open_at is None:
            logger.debug("dropping unusable opening period: %s", period)
            continue
        # No close means open 24 hours; the contract has no way to say that, and
        # midnight-to-midnight is the reading that keeps every value a real time.
        close_at = _clock(period.get("close") or {}) or "00:00"
        hours[key] = {"open": open_at, "close": close_at}

    return hours
