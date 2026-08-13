import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, status

from . import jobs
from .config import enabled_sources, settings
from .models import CrawlRequest

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("sources enabled: %s", ", ".join(enabled_sources(settings())))
    yield


app = FastAPI(title="restaurant-crawler", lifespan=lifespan)


def require_bearer(authorization: str = Header(default="")) -> None:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, settings().api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/crawl", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_bearer)])
async def start_crawl(request: CrawlRequest) -> dict[str, str]:
    existing = jobs.find_active(request.neighborhood)
    if existing:
        logger.info("returning in-flight job %s for %r", existing.id, request.neighborhood)
        return {"jobId": existing.id, "status": existing.status}

    job = jobs.create_job(request.neighborhood)
    asyncio.create_task(jobs.run_crawl(job, request))
    return {"jobId": job.id, "status": job.status}


@app.get("/crawl/{job_id}", dependencies=[Depends(require_bearer)])
def poll_crawl(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no job {job_id}")
    return jobs.job_payload(job)