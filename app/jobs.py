import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from .config import enabled_sources, settings
from .models import CrawlRequest, Restaurant
from .normalize import build_restaurant, derive_source_status, restaurant_slug
from .sources import foursquare, google, website

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")


@dataclass
class Job:
    id: str
    neighborhood: str
    status: str = "queued"
    progress: dict = field(default_factory=lambda: {"found": 0, "completed": 0, "total": 0})
    restaurants: list = field(default_factory=list)
    source_status: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    crawled_at: str | None = None


_jobs: dict[str, Job] = {}


def reset_registry() -> None:
    _jobs.clear()


def create_job(neighborhood: str) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], neighborhood=neighborhood)
    _jobs[job.id] = job
    logger.info("job %s created for neighborhood %r", job.id, neighborhood)
    return job


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def find_active(neighborhood: str) -> Job | None:
    wanted = neighborhood.strip().lower()
    for job in _jobs.values():
        if job.status in ACTIVE_STATUSES and job.neighborhood.strip().lower() == wanted:
            return job
    return None


def job_payload(job: Job) -> dict:
    if job.status in ACTIVE_STATUSES:
        return {"jobId": job.id, "status": job.status, "progress": job.progress}
    return {
        "jobId": job.id,
        "status": job.status,
        "neighborhood": job.neighborhood,
        "crawledAt": job.crawled_at,
        "restaurants": job.restaurants,
        "sourceStatus": job.source_status,
        "errors": job.errors,
    }

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _enrich(client: httpx.AsyncClient, job: Job, request: CrawlRequest,
                  place: dict, active: list[str]) -> tuple[dict | None, list[tuple[str, bool]]]:
    outcomes: list[tuple[str, bool]] = []
    started = time.monotonic()

    try:
        detail = await google.place_details(client, place["id"])
        outcomes.append(("google", True))
    except (httpx.HTTPError, RuntimeError) as error:
        outcomes.append(("google", False))
        job.errors.append({"source": "google", "slug": None, "message": str(error)})
        logger.warning("job %s place %s google failed: %s", job.id, place.get("id"), error)
        return None, outcomes

    name = (detail.get("displayName") or {}).get("text", place["id"])
    slug = restaurant_slug(job.neighborhood, name)
    location = detail.get("location") or {}
    fsq_place, fsq_tips, page = None, [], None

    if "foursquare" in active:
        try:
            fsq_place = await foursquare.match_place(
                client, name, location.get("latitude"), location.get("longitude"))
            if fsq_place:
                fsq_id = fsq_place.get("fsq_place_id") or fsq_place.get("fsq_id")
                fsq_tips = await foursquare.place_tips(
                    client, fsq_id, request.max_reviews_per_restaurant)
            outcomes.append(("foursquare", True))
        except (httpx.HTTPError, RuntimeError) as error:
            outcomes.append(("foursquare", False))
            job.errors.append({"source": "foursquare", "slug": slug, "message": str(error)})
            logger.warning("job %s %s foursquare failed: %s", job.id, slug, error)

    site = detail.get("websiteUri")
    if "website" in active and website.crawlable(site):
        try:
            page = await website.fetch_page(site)
            outcomes.append(("website", True))
        except (RuntimeError, asyncio.TimeoutError) as error:
            outcomes.append(("website", False))
            job.errors.append({"source": "website", "slug": slug, "message": str(error)})
            logger.warning("job %s %s website failed: %s", job.id, slug, error)

    try:
        record = build_restaurant(job.neighborhood, detail, fsq_place, fsq_tips, page,
                                  request.max_reviews_per_restaurant)
        Restaurant.model_validate(record)
    except (ValueError, KeyError) as error:
        job.errors.append({"source": "assembly", "slug": slug, "message": str(error)})
        logger.warning("job %s dropped %s: %s", job.id, slug, error)
        return None, outcomes

    job.progress["completed"] += 1
    logger.info("job %s completed %s in %.1fs", job.id, record["slug"],
                time.monotonic() - started)
    return record, outcomes


async def run_crawl(job: Job, request: CrawlRequest) -> None:
    config = settings()
    active = enabled_sources(config)
    if request.sources:
        active = [name for name in active if name in request.sources]

    job.status = "running"
    logger.info("job %s running with sources %s", job.id, active)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            places = await google.search_restaurants(
                client, request.neighborhood, request.city, request.limit)
        except (httpx.HTTPError, RuntimeError) as error:
            job.status = "failed"
            job.crawled_at = _utc_now()
            job.errors.append({"source": "google", "slug": None, "message": str(error)})
            logger.error("job %s discovery failed: %s", job.id, error)
            return

        # asyncio.wait raises on an empty set, and there is nothing to enrich.
        if not places:
            job.status = "failed"
            job.crawled_at = _utc_now()
            logger.info("job %s found no restaurants in %r", job.id, request.neighborhood)
            return

        job.progress = {"found": len(places), "completed": 0, "total": len(places)}
        semaphore = asyncio.Semaphore(config.crawl_concurrency)

        async def guarded(place):
            async with semaphore:
                return await _enrich(client, job, request, place, active)

        tasks = [asyncio.create_task(guarded(place)) for place in places]
        done, pending = await asyncio.wait(tasks, timeout=config.job_timeout_seconds)

        for task in pending:
            task.cancel()
            job.errors.append({"source": "job", "slug": None,
                               "message": f"cancelled at the {config.job_timeout_seconds}s deadline"})

    outcomes: list[tuple[str, bool]] = []
    for task in done:
        record, task_outcomes = task.result()
        outcomes.extend(task_outcomes)
        if record:
            job.restaurants.append(record)

    job.source_status = derive_source_status(outcomes)
    job.crawled_at = _utc_now()
    job.status = "succeeded" if job.restaurants else "failed"
    logger.info("job %s %s with %d restaurants", job.id, job.status, len(job.restaurants))