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

_MARKUP = re.compile(r"[*_#>`]|\[(?P<label>[^\]]*)\]\([^)]*\)")
_RATING = re.compile(r"\b\d(?:\.\d)?\s*/\s*5(?:\s*stars?)?\b|\b\d\s*stars?\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

MAX_REVIEW_SENTENCES = 3


def review_content(text: str) -> str:
    without_markup = _MARKUP.sub(lambda m: m.group("label") or "", text or "")
    without_ratings = _RATING.sub("", without_markup)
    collapsed = " ".join(without_ratings.replace("—", "").split())
    sentences = _SENTENCE.split(collapsed)
    return " ".join(sentences[:MAX_REVIEW_SENTENCES]).strip()


def _google_reviews(google_detail: dict) -> list[dict]:
    reviews = []
    for entry in google_detail.get("reviews") or []:
        content = review_content((entry.get("text") or {}).get("text", ""))
        if not content:
            continue
        reviews.append({
            "content": content,
            "source": "google",
            "sourceUrl": entry.get("googleMapsUri"),
            "publishedAt": entry.get("publishTime"),
        })
    return reviews


def _foursquare_reviews(fsq_tips: list[dict]) -> list[dict]:
    reviews = []
    for tip in fsq_tips or []:
        content = review_content(tip.get("text", ""))
        if not content:
            continue
        reviews.append({
            "content": content,
            "source": "foursquare",
            "sourceUrl": tip.get("url"),
            "publishedAt": tip.get("created_at"),
        })
    return reviews


def build_restaurant(neighborhood: str, google_detail: dict, fsq_place: dict | None,
                     fsq_tips: list[dict], page: dict | None, max_reviews: int) -> dict:
    name = (google_detail.get("displayName") or {}).get("text", "").strip()
    if not name:
        raise ValueError(f"place {google_detail.get('id')!r} has no display name")

    page_markdown = (page or {}).get("markdown")
    reviews = (_google_reviews(google_detail) + _foursquare_reviews(fsq_tips))[:max_reviews]

    return {
        "slug": restaurant_slug(neighborhood, name),
        "name": name,
        "neighborhood": neighborhood,
        "cuisine": cuisine_label(google_detail, fsq_place),
        "priceTier": price_tier(google_detail.get("priceLevel"), (fsq_place or {}).get("price")),
        "address": google_detail.get("formattedAddress", ""),
        "dietary": dietary_tags(google_detail, page_markdown),
        "hours": hours_from_google(google_detail.get("regularOpeningHours")),
        "reviews": reviews,
        "raw": {
            "google": google_detail,
            "foursquare": {"place": fsq_place, "tips": fsq_tips},
            "website": page,
        },
    }


def derive_source_status(outcomes: list[tuple[str, bool]]) -> dict[str, str]:
    by_source: dict[str, list[bool]] = {}
    for source, succeeded in outcomes:
        by_source.setdefault(source, []).append(succeeded)

    status = {}
    for source, results in by_source.items():
        if all(results):
            status[source] = "ok"
        elif any(results):
            status[source] = "partial"
        else:
            status[source] = "failed"
    return status

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