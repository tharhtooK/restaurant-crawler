import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    api_key: str
    google_maps_api_key: str | None
    job_timeout_seconds: int
    crawl_concurrency: int


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit():
        raise RuntimeError(f"{name} must be a positive integer, got {raw!r}")
    return int(raw)


def load_settings() -> Settings:
    api_key = os.environ.get("CRAWLER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("CRAWLER_API_KEY is not set; refusing to start")
    return Settings(
        api_key=api_key,
        google_maps_api_key=_optional("GOOGLE_MAPS_API_KEY"),
        job_timeout_seconds=_int_env("JOB_TIMEOUT_SECONDS", 300),
        crawl_concurrency=_int_env("CRAWL_CONCURRENCY", 4),
    )


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def enabled_sources(s: Settings) -> list[str]:
    names = []
    if s.google_maps_api_key:
        names.append("google")
    names.append("website")
    return names