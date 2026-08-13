import httpx
import pytest

from app.sources.foursquare import match_place, place_tips


@pytest.fixture(autouse=True)
def fsq_key(monkeypatch):
    monkeypatch.setenv("CRAWLER_API_KEY", "secret")
    monkeypatch.setenv("FSQ_SERVICE_KEY", "fsq-key")
    import app.config
    monkeypatch.setattr(app.config, "_settings", None)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_match_searches_near_the_coordinates_and_prefers_a_name_match():
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": [
            {"fsq_place_id": "wrong", "name": "Roberta's Pizza Truck"},
            {"fsq_place_id": "right", "name": "Roberta's"},
        ]})

    async with _client(handler) as client:
        place = await match_place(client, "Roberta's", 40.705, -73.933)

    assert place["fsq_place_id"] == "right"
    assert seen["params"]["ll"] == "40.705,-73.933"
    assert seen["params"]["radius"] == "250"


async def test_match_returns_none_when_nothing_is_nearby():
    def handler(request):
        return httpx.Response(200, json={"results": []})

    async with _client(handler) as client:
        assert await match_place(client, "Nowhere", 40.0, -73.0) is None


async def test_tips_merge_both_sorts_and_deduplicate():
    def handler(request):
        sort = request.url.params["sort"]
        shared = {"fsq_tip_id": "shared", "text": "Same tip."}
        unique = {"fsq_tip_id": sort, "text": f"Tip from {sort}."}
        return httpx.Response(200, json={"results": [shared, unique]})

    async with _client(handler) as client:
        tips = await place_tips(client, "abc", 10)

    ids = [tip["fsq_tip_id"] for tip in tips]
    assert ids.count("shared") == 1
    assert set(ids) == {"shared", "POPULAR", "NEWEST"}


async def test_tips_stop_at_the_requested_limit():
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"fsq_tip_id": str(n), "text": f"Tip {n}."} for n in range(30)
        ]})

    async with _client(handler) as client:
        assert len(await place_tips(client, "abc", 4)) == 4


async def test_a_non_200_raises_rather_than_returning_nothing():
    def handler(request):
        return httpx.Response(401, text="bad key")

    async with _client(handler) as client:
        with pytest.raises(RuntimeError, match="401"):
            await place_tips(client, "abc", 10)