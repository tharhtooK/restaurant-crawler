import httpx
import pytest

from app.sources.google import place_details, search_restaurants


@pytest.fixture(autouse=True)
def google_key(monkeypatch):
    monkeypatch.setenv("CRAWLER_API_KEY", "secret")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "google-key")
    import app.config
    monkeypatch.setattr(app.config, "_settings", None)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_search_sends_the_key_and_a_places_prefixed_field_mask():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["mask"] = request.headers["X-Goog-FieldMask"]
        seen["key"] = request.headers["X-Goog-Api-Key"]
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"places": [{"id": "a"}, {"id": "b"}]})

    async with _client(handler) as client:
        places = await search_restaurants(client, "Bushwick", "New York", 5)

    assert [p["id"] for p in places] == ["a", "b"]
    assert seen["url"].endswith("/places:searchText")
    assert seen["key"] == "google-key"
    assert all(field.startswith("places.") for field in seen["mask"].split(","))
    assert "restaurants in Bushwick, New York" in seen["body"]


@pytest.mark.asyncio
async def test_search_truncates_to_the_requested_limit():
    def handler(request):
        return httpx.Response(200, json={"places": [{"id": str(n)} for n in range(10)]})

    async with _client(handler) as client:
        places = await search_restaurants(client, "Bushwick", "New York", 3)

    assert len(places) == 3


@pytest.mark.asyncio
async def test_details_uses_an_unprefixed_field_mask():
    """Text Search masks are places.-prefixed and Details masks are not. Mixing
    them returns a 400 whose message does not say so."""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["mask"] = request.headers["X-Goog-FieldMask"]
        return httpx.Response(200, json={"id": "abc", "displayName": {"text": "X"}})

    async with _client(handler) as client:
        detail = await place_details(client, "abc")

    assert detail["id"] == "abc"
    assert "/places/abc" in seen["url"]
    assert not any(field.startswith("places.") for field in seen["mask"].split(","))


@pytest.mark.asyncio
async def test_details_asks_for_the_fields_the_records_need():
    seen = {}

    def handler(request):
        seen["mask"] = request.headers["X-Goog-FieldMask"].split(",")
        return httpx.Response(200, json={"id": "abc"})

    async with _client(handler) as client:
        await place_details(client, "abc")

    for required in ("regularOpeningHours", "priceLevel", "servesVegetarianFood",
                     "primaryTypeDisplayName", "formattedAddress", "websiteUri",
                     "reviews.text"):
        assert required in seen["mask"]


@pytest.mark.asyncio
async def test_a_non_200_raises_with_the_status_and_body():
    def handler(request):
        return httpx.Response(400, text="field mask is malformed")

    async with _client(handler) as client:
        with pytest.raises(RuntimeError, match="400"):
            await place_details(client, "abc")