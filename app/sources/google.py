import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

BASE = "https://places.googleapis.com/v1"

# Text Search masks are places.-prefixed; Place Details masks are not. Mixing them
# returns a 400 whose message does not explain why.
SEARCH_FIELDS = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.businessStatus",
])

DETAIL_FIELDS = ",".join([
    "id",
    "displayName",
    "formattedAddress",
    "location",
    "primaryTypeDisplayName",
    "priceLevel",
    "websiteUri",
    "regularOpeningHours",
    "servesVegetarianFood",
    "reviews.text",
    "reviews.publishTime",
    "reviews.googleMapsUri",
])


def _headers(field_mask: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings().google_maps_api_key or "",
        "X-Goog-FieldMask": field_mask,
    }


async def search_restaurants(client: httpx.AsyncClient, neighborhood: str, city: str,
                             limit: int) -> list[dict]:
    query = f"restaurants in {neighborhood}, {city}"
    response = await client.post(
        f"{BASE}/places:searchText",
        headers=_headers(SEARCH_FIELDS),
        json={"textQuery": query, "languageCode": "en", "pageSize": min(limit, 20)},
    )
    if response.status_code != 200:
        raise RuntimeError(f"searchText {response.status_code}: {response.text[:300]}")

    places = response.json().get("places", [])
    logger.info("discovery found %d places for %r", len(places), query)
    return places[:limit]


async def place_details(client: httpx.AsyncClient, place_id: str) -> dict:
    response = await client.get(
        f"{BASE}/places/{place_id}",
        headers=_headers(DETAIL_FIELDS),
        params={"languageCode": "en"},
    )
    if response.status_code != 200:
        raise RuntimeError(f"placeDetails {response.status_code}: {response.text[:300]}")
    return response.json()