import re
import logging

logger = logging.getLogger(__name__)

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Google numbers days from Sunday; the contract's keys start at Monday.
_GOOGLE_DAY_TO_KEY = {0: "sun", 1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat"}

NEIGHBORHOOD_CODES = {
    "east village": "ev",
    "flushing": "fl",
    "williamsburg": "wb",
    "harlem": "hl",
    "astoria": "as",
    "bushwick": "bw",
}

_GOOGLE_PRICE_TIERS = {
    "PRICE_LEVEL_FREE": 1,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}

DEFAULT_PRICE_TIER = 2

# Only phrases a restaurant states about itself. Never a cuisine, a dish, or a
# category name.
_DIETARY_PHRASES = {
    "vegan": ("vegan menu", "vegan options", "fully vegan", "100% vegan", "all vegan"),
    "vegetarian": ("vegetarian menu", "vegetarian options", "fully vegetarian"),
    "halal": ("certified halal", "halal certified", "100% halal", "halal meat"),
    "kosher": ("certified kosher", "kosher certified", "glatt kosher"),
    "gluten-free": ("gluten-free menu", "gluten free menu", "gluten-free options"),
    "dairy-free": ("dairy-free menu", "dairy free menu", "dairy-free options"),
}


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

def neighborhood_code(neighborhood: str) -> str:
    cleaned = " ".join((neighborhood or "").lower().split())
    known = NEIGHBORHOOD_CODES.get(cleaned)
    if known:
        return known
    letters = re.sub(r"[^a-z]", "", cleaned)
    code = letters[:2]
    logger.info("no code for neighborhood %r; using fallback %r", neighborhood, code)
    return code


def restaurant_slug(neighborhood: str, name: str) -> str:
    stripped = re.sub(r"[’']", "", (name or "").lower())
    words = re.sub(r"[^a-z0-9]+", "-", stripped).strip("-")
    return f"{neighborhood_code(neighborhood)}-{words}"


def price_tier(google_price_level: str | None, fsq_price: int | None) -> int:
    tier = _GOOGLE_PRICE_TIERS.get(google_price_level or "")
    if tier:
        return tier
    if isinstance(fsq_price, int) and 1 <= fsq_price <= 4:
        return fsq_price
    return DEFAULT_PRICE_TIER


def cuisine_label(google_detail: dict, fsq_place: dict | None) -> str:
    google_label = (google_detail.get("primaryTypeDisplayName") or {}).get("text", "")
    categories = (fsq_place or {}).get("categories") or []
    fsq_label = categories[0].get("name", "") if categories else ""

    for label in (google_label, fsq_label):
        trimmed = re.sub(r"\s*restaurant$", "", label.strip(), flags=re.IGNORECASE)
        if trimmed:
            return trimmed
    return "Restaurant"


def dietary_tags(google_detail: dict, page_markdown: str | None) -> list[str]:
    tags = set()
    if google_detail.get("servesVegetarianFood") is True:
        tags.add("vegetarian")

    page = (page_markdown or "").lower()
    for tag, phrases in _DIETARY_PHRASES.items():
        if any(phrase in page for phrase in phrases):
            tags.add(tag)

    return sorted(tags)