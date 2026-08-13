import logging
import re

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

BASE = "https://places-api.foursquare.com"
API_VERSION = "2025-06-17"
TIP_SORTS = ("POPULAR", "NEWEST")
MATCH_RADIUS_M = 250


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings().fsq_service_key or ''}",
        "X-Places-Api-Version": API_VERSION,
        "accept": "application/json",
    }


def _results(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("results")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _comparable(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


async def match_place(client: httpx.AsyncClient, name: str, lat: float,
                      lng: float) -> dict | None:
    """Matched within 250m of the Google coordinates so a chain's other branches
    do not get attached to this restaurant."""
    response = await client.get(
        f"{BASE}/places/search",
        headers=_headers(),
        params={"query": name, "ll": f"{lat},{lng}", "radius": MATCH_RADIUS_M, "limit": 5},
    )
    if response.status_code != 200:
        raise RuntimeError(f"fsq search {response.status_code}: {response.text[:300]}")

    candidates = _results(response.json())
    wanted = _comparable(name)

    # Foursquare orders by its own relevance, so "Roberta's Pizza Truck" can come
    # back before "Roberta's". An exact name wins before a containing one.
    for candidate in candidates:
        if wanted and _comparable(candidate.get("name", "")) == wanted:
            return candidate
    for candidate in candidates:
        if wanted and wanted in _comparable(candidate.get("name", "")):
            return candidate
    return candidates[0] if candidates else None


async def place_tips(client: httpx.AsyncClient, fsq_id: str, limit: int) -> list[dict]:
    """POPULAR and NEWEST overlap but differ, so both sorts yield more distinct
    prose than either alone."""
    collected: dict[str, dict] = {}
    for sort in TIP_SORTS:
        response = await client.get(
            f"{BASE}/places/{fsq_id}/tips",
            headers=_headers(),
            params={"limit": 50, "sort": sort,
                    "fields": "fsq_tip_id,created_at,text,url,agree_count"},
        )
        if response.status_code != 200:
            raise RuntimeError(f"fsq tips {response.status_code}: {response.text[:300]}")

        for tip in _results(response.json()):
            if tip.get("text"):
                collected.setdefault(tip.get("fsq_tip_id") or tip["text"], tip)

    logger.info("foursquare place %s yielded %d tips", fsq_id, len(collected))
    return list(collected.values())[:limit]