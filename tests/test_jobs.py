import httpx
import pytest

from app import jobs
from app.models import CrawlRequest

GOOGLE_PLACE = {"id": "abc", "displayName": {"text": "Roberta's"},
                "formattedAddress": "261 Moore St", "location": {"latitude": 40.7, "longitude": -73.9}}

GOOGLE_DETAIL = {
    "id": "abc",
    "displayName": {"text": "Roberta's"},
    "formattedAddress": "261 Moore St, Brooklyn, NY 11206",
    "primaryTypeDisplayName": {"text": "Pizza restaurant"},
    "priceLevel": "PRICE_LEVEL_MODERATE",
    "websiteUri": "https://robertaspizza.com",
    "regularOpeningHours": {"periods": [
        {"open": {"day": 1, "hour": 11, "minute": 0},
         "close": {"day": 1, "hour": 23, "minute": 0}}]},
    "reviews": [{"text": {"text": "The wood-fired pizza is the draw."},
                 "publishTime": "2026-07-01T10:00:00Z"}],
}


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    monkeypatch.setenv("CRAWLER_API_KEY", "secret")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "google-key")
    import app.config
    monkeypatch.setattr(app.config, "_settings", None)
    jobs.reset_registry()


def _stub_sources(monkeypatch, *, site_fails=False):
    async def search(client, neighborhood, city, limit):
        return [GOOGLE_PLACE] * limit

    async def details(client, place_id):
        return GOOGLE_DETAIL

    async def page(url):
        if site_fails:
            raise RuntimeError("crawl failed: navigation timeout")
        return {"url": url, "title": "Roberta's", "markdown": "We have a vegan menu."}

    monkeypatch.setattr(jobs.google, "search_restaurants", search)
    monkeypatch.setattr(jobs.google, "place_details", details)
    monkeypatch.setattr(jobs.website, "fetch_site", page)


def test_a_new_job_starts_queued_with_a_unique_id():
    first = jobs.create_job("Bushwick")
    second = jobs.create_job("Red Hook")
    assert first.status == "queued"
    assert first.id != second.id


def test_find_active_returns_a_queued_or_running_job_for_the_same_neighborhood():
    job = jobs.create_job("Bushwick")
    assert jobs.find_active("bushwick").id == job.id
    assert jobs.find_active("Red Hook") is None


def test_find_active_ignores_finished_jobs():
    job = jobs.create_job("Bushwick")
    job.status = "succeeded"
    assert jobs.find_active("Bushwick") is None


def test_a_running_job_reports_progress_and_omits_restaurants():
    job = jobs.create_job("Bushwick")
    job.status = "running"
    job.progress = {"found": 7, "completed": 3, "total": 10}
    payload = jobs.job_payload(job)
    assert payload["status"] == "running"
    assert payload["progress"] == {"found": 7, "completed": 3, "total": 10}
    assert "restaurants" not in payload


async def test_a_successful_crawl_produces_records_and_source_status(monkeypatch):
    _stub_sources(monkeypatch)
    job = jobs.create_job("Bushwick")
    await jobs.run_crawl(job, CrawlRequest(neighborhood="Bushwick", limit=3))

    payload = jobs.job_payload(job)
    assert payload["status"] == "succeeded"
    assert len(payload["restaurants"]) == 3
    assert payload["sourceStatus"] == {"google": "ok", "website": "ok"}
    assert payload["neighborhood"] == "Bushwick"
    assert payload["crawledAt"].endswith("Z")
    assert payload["restaurants"][0]["dietary"] == ["vegan"]


async def test_one_failing_source_still_succeeds_and_is_reported(monkeypatch):
    _stub_sources(monkeypatch, site_fails=True)
    job = jobs.create_job("Bushwick")
    await jobs.run_crawl(job, CrawlRequest(neighborhood="Bushwick", limit=2))

    payload = jobs.job_payload(job)
    assert payload["status"] == "succeeded"
    assert len(payload["restaurants"]) == 2
    assert payload["sourceStatus"]["website"] == "failed"
    assert payload["sourceStatus"]["google"] == "ok"
    assert any(error["source"] == "website" for error in payload["errors"])


async def test_a_job_that_finds_nothing_fails(monkeypatch):
    _stub_sources(monkeypatch)

    async def nothing(client, neighborhood, city, limit):
        return []

    monkeypatch.setattr(jobs.google, "search_restaurants", nothing)
    job = jobs.create_job("Nowhere")
    await jobs.run_crawl(job, CrawlRequest(neighborhood="Nowhere", limit=3))

    assert jobs.job_payload(job)["status"] == "failed"


async def test_re_running_the_same_crawl_produces_the_same_slugs(monkeypatch):
    _stub_sources(monkeypatch)
    slugs = []
    for _ in range(2):
        job = jobs.create_job("Bushwick")
        await jobs.run_crawl(job, CrawlRequest(neighborhood="Bushwick", limit=2))
        slugs.append([r["slug"] for r in jobs.job_payload(job)["restaurants"]])

    assert slugs[0] == slugs[1] == ["bw-robertas", "bw-robertas"]


async def test_a_source_the_request_excluded_is_absent_from_source_status(monkeypatch):
    _stub_sources(monkeypatch)
    job = jobs.create_job("Bushwick")
    await jobs.run_crawl(
        job, CrawlRequest(neighborhood="Bushwick", limit=2, sources=["google"]))

    payload = jobs.job_payload(job)
    assert "website" not in payload["sourceStatus"]
    assert payload["status"] == "succeeded"

async def test_error_entries_identify_the_restaurant_by_slug(monkeypatch):
    """The contract's errors carry a slug, and the consumer correlates on it."""
    _stub_sources(monkeypatch, site_fails=True)
    job = jobs.create_job("Bushwick")
    await jobs.run_crawl(job, CrawlRequest(neighborhood="Bushwick", limit=1))

    errors = jobs.job_payload(job)["errors"]
    assert [error["slug"] for error in errors] == ["bw-robertas"]
