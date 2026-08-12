import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import enabled_sources, settings

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


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}