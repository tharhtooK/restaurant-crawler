# restaurant-crawler v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An HTTP service that takes a neighborhood name, crawls Google Places, Foursquare, and restaurant websites, and returns structured restaurant records plus review text in the exact JSON shape `restaurant-rag` expects.

**Architecture:** FastAPI with three routes. `POST /crawl` validates, dedupes against in-flight jobs, and hands the work to `asyncio.create_task` before responding `202`. The pipeline runs one Google discovery call, then enriches each candidate concurrently behind a semaphore. All I/O lives in `app/sources/`, which returns untouched upstream JSON; all normalization lives in `app/normalize.py`, which is pure and testable with no credentials.

**Tech Stack:** Python 3.12 (from the Playwright base image), FastAPI, pydantic v2, httpx, crawl4ai, pytest, Docker Compose.

## Change log

**2026-08-13 — menu pages are now reached.** The deep crawl uses
`BestFirstCrawlingStrategy` with a `KeywordRelevanceScorer`, and
`remove_overlay_elements` was removed: it stripped the markup holding the menu
link, so the crawler never discovered the menu page. Isolated by running the same
crawl with each flag toggled.

**2026-08-13 — the website source crawls a site, not a page.** `fetch_page`
became `fetch_site`, using crawl4ai's `BFSDeepCrawlStrategy` at depth 1, capped
at 3 pages, same-domain only, with the pages' markdown concatenated. Task 6 below
shows the single-page version that was executed first. Measured caveat: on the
one crawlable site in the test set the extra pages produced no additional dietary
tags, and a 3-page cap can miss the menu page.

**2026-08-13 — dietary phrases widened.** A live crawl found Ayat's own site
saying "all halal, all delicious" and the original phrase list missed it, losing
a true first-party claim. Task 3's list below is the corrected one. Bare-word
matching was rejected: it would also match "not halal" and a page mentioning a
halal place down the street, and a false positive here is the harm §4 warns about.

**2026-08-13 — Foursquare removed.** Tasks 3, 4, 6 and 7 below still show the
Foursquare code as it was built, because they were executed that way. It was then
removed: the tips endpoint needs purchased credits, and `match_place` was a fuzzy
name lookup with no confidence signal that can silently attach the wrong venue's
data. v1 is Google Places plus the restaurant's own website. See the commit that
carries this note for the diff.

## Global Constraints

Copied from `docs/superpowers/specs/2026-08-12-restaurant-crawler-design.md` and `restaurant-rag/docs/coding-guidelines.md`. Every task's requirements implicitly include this section.

- **Pinned versions** — `fastapi==0.141.*`, `uvicorn[standard]==0.52.*`, `pydantic==2.13.*`, `httpx>=0.28,<1.0`, `crawl4ai==0.9.*`, `playwright==1.62.0`, `pytest==9.1.*`, `pytest-asyncio==1.4.*`.
- **`playwright==1.62.0` must stay exactly equal to the base image tag** `mcr.microsoft.com/playwright/python:v1.62.0-noble`. crawl4ai only requires `>=1.49.0`, so without the pin pip may install a Playwright whose browsers are not in the image, and every crawl fails with "Executable doesn't exist".
- **Wire format is camelCase**, matching the consumer's Prisma schema exactly: `priceTier`, `sourceUrl`, `publishedAt`, `crawledAt`, `jobId`, `maxReviewsPerRestaurant`.
- **`hours` has exactly seven keys** `mon,tue,wed,thu,fri,sat,sun`. Each is `null` or `{"open":"HH:MM","close":"HH:MM"}` in 24-hour time.
- **`priceTier` is an integer 1–4.** Never `0`, never `null`. Default `2` when no source gives a price.
- **Never infer a dietary tag from cuisine.** Only an explicit source statement produces a tag.
- **No bare `except`.** Catch the specific error. Never swallow one silently.
- **No `TODO`, `pass`, or placeholder returns** in delivered code. No mock or fallback data when a real call fails.
- **No hardcoded keys, URLs, ports, or absolute paths.** Env vars only.
- **Comments explain WHY, never WHAT.** One line, and only for something non-obvious.
- **~40 lines per function, ~200 lines per file.**
- **Logging is stdlib, one logger per module.** Never log a secret.
- **Every task ends with a command the user runs and an output they can check.**

## File Structure

| File | Responsibility |
|---|---|
| `app/config.py` | env loading, startup validation, which sources have credentials |
| `app/models.py` | pydantic request/response models, camelCase on the wire |
| `app/normalize.py` | the pure rules: hours, slug, price tier, dietary, cuisine, assembly |
| `app/jobs.py` | in-memory job registry + the discovery → fan-out pipeline |
| `app/main.py` | FastAPI app: three routes and the bearer check |
| `app/sources/google.py` | Places text search + place details |
| `app/sources/foursquare.py` | place match + tips |
| `app/sources/website.py` | crawl4ai over the restaurant's own site |
| `tests/` | pure-logic and route tests, no network |
| `fixtures/` | recorded raw payloads |

---

### Task 1: Skeleton, config, `/health`, and a container that runs

**Files:**
- Create: `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `Makefile`, `.env.example`, `app/__init__.py`, `app/sources/__init__.py`, `app/config.py`, `app/main.py`, `tests/__init__.py`, `tests/test_config.py`, `README.md`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Settings` (frozen dataclass with fields `api_key: str`, `google_maps_api_key: str | None`, `fsq_service_key: str | None`, `job_timeout_seconds: int`, `crawl_concurrency: int`); `load_settings() -> Settings`; `settings() -> Settings` (lazy singleton); `enabled_sources(s: Settings) -> list[str]`; the FastAPI app object `app` in `app/main.py`

**Note on ordering:** the container is built before the first test runs, because
every later task runs `pytest` inside it and there is no Python environment on
the host. `app/main.py`'s `/health` route has no logic to unit-test — it is
verified by the `curl` in Step 12 — so TDD here applies to `app/config.py`.

- [X] **Step 1: Create the package directories**

```bash
mkdir -p app/sources tests fixtures
touch app/__init__.py app/sources/__init__.py tests/__init__.py fixtures/.gitkeep
```

- [X] **Step 2: Write the failing test**

```python
# tests/test_config.py
import pytest

from app.config import Settings, enabled_sources, load_settings


def test_missing_api_key_refuses_to_start(monkeypatch):
    monkeypatch.delenv("CRAWLER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CRAWLER_API_KEY"):
        load_settings()


def test_defaults_apply_when_optional_vars_are_absent(monkeypatch):
    monkeypatch.setenv("CRAWLER_API_KEY", "secret")
    monkeypatch.delenv("JOB_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("CRAWL_CONCURRENCY", raising=False)
    s = load_settings()
    assert s.job_timeout_seconds == 300
    assert s.crawl_concurrency == 4


def test_enabled_sources_reflects_which_credentials_are_present():
    both = Settings("k", "google-key", "fsq-key", 300, 4)
    assert enabled_sources(both) == ["google", "foursquare", "website"]

    google_only = Settings("k", "google-key", None, 300, 4)
    assert enabled_sources(google_only) == ["google", "website"]

    no_keys = Settings("k", None, None, 300, 4)
    assert enabled_sources(no_keys) == ["website"]
```

- [X] **Step 3: Write `requirements.txt`**

```
fastapi==0.141.*
uvicorn[standard]==0.52.*
pydantic==2.13.*
httpx>=0.28,<1.0
crawl4ai==0.9.*
playwright==1.62.0
pytest==9.1.*
pytest-asyncio==1.4.*
```

- [X] **Step 4: Write `Dockerfile`**

```dockerfile
# The tag's Playwright version must equal the pin in requirements.txt, or crawl4ai
# installs a Playwright whose browsers are not in this image.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# One worker on purpose: job state is an in-process dict, so a second worker would
# answer polls for jobs it cannot see.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

- [X] **Step 5: Write `docker-compose.yml`**

```yaml
services:
  crawler:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./app:/srv/app
      - ./tests:/srv/tests
      - ./fixtures:/srv/fixtures
      - ./pytest.ini:/srv/pytest.ini
    command: >
      uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --reload
```

- [X] **Step 6: Write `pytest.ini`**

`asyncio_mode = auto` lets the async tests in Tasks 5–7 run without a decorator
on every one of them.

```ini
[pytest]
asyncio_mode = auto
```

- [X] **Step 7: Write `.env.example`**

```
# Shared secret the consumer sends as `Authorization: Bearer <key>`.
# Generate with: openssl rand -hex 32
CRAWLER_API_KEY=

# Optional. Absent means that source is disabled and omitted from sourceStatus.
GOOGLE_MAPS_API_KEY=
FSQ_SERVICE_KEY=

JOB_TIMEOUT_SECONDS=300
CRAWL_CONCURRENCY=4
LOG_LEVEL=INFO
```

- [X] **Step 8: Write `Makefile`**

```makefile
.PHONY: up down logs test shell

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f crawler

test:
	docker compose exec crawler pytest -q

shell:
	docker compose exec crawler bash
```

- [X] **Step 9: Write a placeholder `app/main.py` so the container can start**

The `/health` route is the whole file at this point; the crawl routes arrive in
Task 8.

```python
import logging
import os

from fastapi import FastAPI

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="restaurant-crawler")


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
```

- [X] **Step 10: Create `.env` and build the container**

Run:
```bash
cp .env.example .env
sed -i '' "s|^CRAWLER_API_KEY=$|CRAWLER_API_KEY=$(openssl rand -hex 32)|" .env
make up
```
Expected: the build completes and `docker compose ps` shows `crawler` running.
The first build pulls a ~2GB base image, so expect several minutes.

- [X] **Step 11: Run the test to verify it fails**

Run: `docker compose exec crawler pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`

- [X] **Step 12: Check `/health`**

Run: `curl -s localhost:8000/health`
Expected: `{"ok":true}`

- [X] **Step 13: Write `app/config.py`**

```python
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    api_key: str
    google_maps_api_key: str | None
    fsq_service_key: str | None
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
        fsq_service_key=_optional("FSQ_SERVICE_KEY"),
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
    if s.fsq_service_key:
        names.append("foursquare")
    names.append("website")
    return names
```

- [X] **Step 14: Run the test to verify it passes**

Run: `docker compose exec crawler pytest tests/test_config.py -v`
Expected: 3 passed

- [X] **Step 15: Log the enabled sources at startup**

Replace `app/main.py` with the version that reports its configuration. FastAPI's
`on_event` hooks are deprecated; `lifespan` is the current form.

```python
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
```

- [X] **Step 16: Confirm the startup log**

Run: `docker compose restart crawler && sleep 3 && docker compose logs crawler | grep "sources enabled"`
Expected: `sources enabled: website` — Google and Foursquare are absent because no
keys are set yet, which is the correct behaviour, not a failure.

- [X] **Step 17: Write `README.md`**

````markdown
# restaurant-crawler

An HTTP service that takes a neighborhood name, crawls public sources for
restaurants there, and returns structured records plus review text. It stores
nothing and knows nothing about its consumer beyond the JSON shape.

The contract is `restaurant-rag/docs/crawler-service-spec.md`. The v1 design and
what it deliberately leaves out is in `docs/superpowers/specs/`.

## Running

```bash
cp .env.example .env        # set CRAWLER_API_KEY at minimum
make up
curl localhost:8000/health
```

## Job state is in memory

Jobs live in a dict inside the process. A restart loses them, and a poll for a
job created before the restart returns 404. That is an accepted tradeoff at this
scale, not an oversight.
````

- [X] **Step 18: Run the whole suite**

Run: `make test`
Expected: 3 passed

- [X] **Step 19: Commit**

```bash
git add -A
git commit -m "feat: service skeleton, config validation, and /health"
```

---

### Task 2: Opening hours normalization

The contract calls §4 "where the bugs live". Hours are the worst of it, so they get their own task.

**Files:**
- Create: `app/normalize.py`, `tests/test_hours.py`
- Test: `tests/test_hours.py`

**Interfaces:**
- Consumes: nothing
- Produces: `hours_from_google(regular_opening_hours: dict | None) -> dict[str, dict | None]` returning exactly the seven keys `mon,tue,wed,thu,fri,sat,sun`, each `None` or `{"open": "HH:MM", "close": "HH:MM"}`

- [X] **Step 1: Write the failing test**

Google Places (New) returns `regularOpeningHours.periods`, each `{"open": {"day": 0-6, "hour": h, "minute": m}, "close": {...}}`, where day 0 is Sunday. A 24-hour place returns an `open` with no `close`.

```python
# tests/test_hours.py
from app.normalize import DAY_KEYS, hours_from_google


def _period(open_day, open_hour, close_day, close_hour, open_min=0, close_min=0):
    return {
        "open": {"day": open_day, "hour": open_hour, "minute": open_min},
        "close": {"day": close_day, "hour": close_hour, "minute": close_min},
    }


def test_all_seven_days_are_present_even_when_google_sends_none():
    hours = hours_from_google(None)
    assert list(hours.keys()) == list(DAY_KEYS)
    assert all(value is None for value in hours.values())


def test_a_day_google_omits_is_null_rather_than_missing():
    hours = hours_from_google({"periods": [_period(1, 11, 1, 23)]})
    assert hours["mon"] == {"open": "11:00", "close": "23:00"}
    assert hours["sun"] is None
    assert len(hours) == 7


def test_times_are_zero_padded_24_hour():
    hours = hours_from_google({"periods": [_period(2, 9, 2, 17, open_min=5)]})
    assert hours["tue"] == {"open": "09:05", "close": "17:00"}


def test_close_after_midnight_belongs_to_the_day_it_opened():
    hours = hours_from_google({"periods": [_period(5, 18, 6, 2)]})
    assert hours["fri"] == {"open": "18:00", "close": "02:00"}
    assert hours["sat"] is None


def test_day_zero_is_sunday():
    hours = hours_from_google({"periods": [_period(0, 10, 0, 22)]})
    assert hours["sun"] == {"open": "10:00", "close": "22:00"}


def test_a_24_hour_place_has_no_close_and_reads_as_midnight_to_midnight():
    hours = hours_from_google(
        {"periods": [{"open": {"day": 3, "hour": 0, "minute": 0}}]}
    )
    assert hours["wed"] == {"open": "00:00", "close": "00:00"}


def test_a_malformed_period_is_dropped_rather_than_half_written():
    hours = hours_from_google({"periods": [{"open": {"hour": 11}}, _period(4, 12, 4, 20)]})
    assert hours["thu"] == {"open": "12:00", "close": "20:00"}
    assert sum(1 for value in hours.values() if value) == 1
```

- [X] **Step 2: Run the test to verify it fails**

Run: `docker compose exec crawler pytest tests/test_hours.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.normalize'`

- [X] **Step 3: Write `app/normalize.py`**

```python
import logging

logger = logging.getLogger(__name__)

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Google numbers days from Sunday; the contract's keys start at Monday.
_GOOGLE_DAY_TO_KEY = {0: "sun", 1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat"}


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
```

- [X] **Step 4: Run the test to verify it passes**

Run: `docker compose exec crawler pytest tests/test_hours.py -v`
Expected: 7 passed

- [X] **Step 5: Commit**

```bash
git add app/normalize.py tests/test_hours.py
git commit -m "feat: normalize Google opening hours into the seven-key structure"
```

---

### Task 3: Slug, price tier, cuisine, and dietary tags

**Files:**
- Modify: `app/normalize.py`
- Create: `tests/test_normalize.py`
- Test: `tests/test_normalize.py`

**Interfaces:**
- Consumes: `app/normalize.py` from Task 2
- Produces: `neighborhood_code(neighborhood: str) -> str`; `restaurant_slug(neighborhood: str, name: str) -> str`; `price_tier(google_price_level: str | None, fsq_price: int | None) -> int`; `cuisine_label(google_detail: dict, fsq_place: dict | None) -> str`; `dietary_tags(google_detail: dict, page_markdown: str | None) -> list[str]`

- [X] **Step 1: Write the failing test**

```python
# tests/test_normalize.py
from app.normalize import (
    cuisine_label,
    dietary_tags,
    neighborhood_code,
    price_tier,
    restaurant_slug,
)


def test_known_neighborhood_codes_match_the_consumers_existing_slugs():
    assert neighborhood_code("East Village") == "ev"
    assert neighborhood_code("Flushing") == "fl"
    assert neighborhood_code("Williamsburg") == "wb"
    assert neighborhood_code("Harlem") == "hl"
    assert neighborhood_code("Astoria") == "as"
    assert neighborhood_code("Bushwick") == "bw"


def test_known_codes_are_matched_regardless_of_casing_and_spacing():
    assert neighborhood_code("  east village ") == "ev"
    assert neighborhood_code("BUSHWICK") == "bw"


def test_unknown_neighborhood_falls_back_to_its_first_two_letters():
    assert neighborhood_code("Red Hook") == "re"


def test_slug_is_stable_across_identical_calls():
    first = restaurant_slug("Bushwick", "Roberta's")
    second = restaurant_slug("Bushwick", "Roberta's")
    assert first == second == "bw-robertas"


def test_slug_is_lowercase_kebab_case_without_punctuation():
    assert restaurant_slug("Flushing", "Tian Jin Dumpling House") == "fl-tian-jin-dumpling-house"
    assert restaurant_slug("Harlem", "Sylvia's  Restaurant!") == "hl-sylvias-restaurant"


def test_google_price_levels_map_to_tiers():
    assert price_tier("PRICE_LEVEL_INEXPENSIVE", None) == 1
    assert price_tier("PRICE_LEVEL_MODERATE", None) == 2
    assert price_tier("PRICE_LEVEL_EXPENSIVE", None) == 3
    assert price_tier("PRICE_LEVEL_VERY_EXPENSIVE", None) == 4
    assert price_tier("PRICE_LEVEL_FREE", None) == 1


def test_foursquare_price_is_used_when_google_has_none():
    assert price_tier(None, 3) == 3


def test_no_price_anywhere_defaults_to_two_and_never_zero_or_none():
    assert price_tier(None, None) == 2
    assert price_tier("PRICE_LEVEL_UNSPECIFIED", None) == 2
    assert price_tier(None, 0) == 2
    assert price_tier(None, 9) == 2


def test_cuisine_prefers_googles_display_name_without_the_word_restaurant():
    detail = {"primaryTypeDisplayName": {"text": "Pizza restaurant"}}
    assert cuisine_label(detail, None) == "Pizza"


def test_cuisine_falls_back_to_foursquare_category_then_to_a_plain_default():
    fsq = {"categories": [{"name": "Korean BBQ Restaurant"}]}
    assert cuisine_label({}, fsq) == "Korean BBQ"
    assert cuisine_label({}, None) == "Restaurant"


def test_dietary_tag_comes_from_an_explicit_google_attribute():
    assert dietary_tags({"servesVegetarianFood": True}, None) == ["vegetarian"]
    assert dietary_tags({"servesVegetarianFood": False}, None) == []


def test_dietary_tags_come_from_explicit_phrases_on_the_restaurants_own_page():
    page = "Our fully vegan kitchen also offers a gluten-free menu."
    assert dietary_tags({}, page) == ["gluten-free", "vegan"]


def test_dietary_phrases_are_matched_regardless_of_casing():
    assert dietary_tags({}, "CERTIFIED HALAL since 1998") == ["halal"]


def test_dietary_tags_are_never_inferred_from_cuisine():
    """A wrong halal or kosher tag can cause someone real harm, so an unstated
    tag must read as unknown."""
    page = "Authentic Middle Eastern cuisine. Our deli serves pastrami."
    detail = {"primaryTypeDisplayName": {"text": "Middle Eastern restaurant"}}
    assert dietary_tags(detail, page) == []


def test_dietary_tags_are_deduplicated_and_sorted():
    page = "vegan options and a vegan menu, plus vegetarian options"
    assert dietary_tags({}, page) == ["vegan", "vegetarian"]
```

- [X] **Step 2: Run the test to verify it fails**

Run: `docker compose exec crawler pytest tests/test_normalize.py -v`
Expected: FAIL with `ImportError: cannot import name 'cuisine_label' from 'app.normalize'`

- [X] **Step 3: Add the imports and tables to `app/normalize.py`**

Add at the top, below the existing imports:

```python
import re
```

Add below `_GOOGLE_DAY_TO_KEY`:

```python
# Not derivable: wb for Williamsburg and hl for Harlem follow no rule that also
# produces ev and as. The consumer upserts on slug, so these must never change.
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
    "vegan": ("vegan menu", "vegan options", "vegan friendly", "vegan-friendly",
              "fully vegan", "100% vegan", "all vegan", "entirely vegan",
              "plant-based menu", "fully plant-based"),
    "vegetarian": ("vegetarian menu", "vegetarian options", "vegetarian friendly",
                   "vegetarian-friendly", "fully vegetarian", "all vegetarian"),
    "halal": ("certified halal", "halal certified", "100% halal", "halal meat",
              "all halal", "fully halal", "halal kitchen", "halal restaurant",
              "serves halal"),
    "kosher": ("certified kosher", "kosher certified", "glatt kosher",
               "all kosher", "fully kosher", "kosher kitchen", "kosher restaurant"),
    "gluten-free": ("gluten-free menu", "gluten free menu", "gluten-free options",
                    "gluten free options", "gluten-free kitchen", "gluten free kitchen"),
    "dairy-free": ("dairy-free menu", "dairy free menu", "dairy-free options",
                   "dairy free options"),
}
```

- [X] **Step 4: Add the functions to `app/normalize.py`**

```python
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
```

- [X] **Step 5: Run the test to verify it passes**

Run: `docker compose exec crawler pytest tests/test_normalize.py -v`
Expected: 15 passed

- [X] **Step 6: Run the whole suite**

Run: `make test`
Expected: 25 passed

- [X] **Step 7: Commit**

```bash
git add app/normalize.py tests/test_normalize.py
git commit -m "feat: slug, price tier, cuisine, and dietary normalization"
```

---

### Task 4: Wire models and record assembly

**Files:**
- Create: `app/models.py`, `tests/test_models.py`, `tests/test_assembly.py`
- Modify: `app/normalize.py`
- Test: `tests/test_models.py`, `tests/test_assembly.py`

**Interfaces:**
- Consumes: everything from Tasks 2 and 3
- Produces: `CrawlRequest`, `Review`, `Restaurant`, `JobStatus` in `app/models.py`; `review_content(text: str) -> str`, `build_restaurant(neighborhood, google_detail, fsq_place, fsq_tips, page, max_reviews) -> dict`, and `derive_source_status(outcomes: list[tuple[str, bool]]) -> dict[str, str]` in `app/normalize.py`

- [X] **Step 1: Write the failing model test**

```python
# tests/test_models.py
import pytest
from pydantic import ValidationError

from app.models import CrawlRequest, Restaurant


def test_request_defaults_match_the_contract():
    request = CrawlRequest(neighborhood="Bushwick")
    assert request.city == "New York"
    assert request.limit == 10
    assert request.max_reviews_per_restaurant == 10
    assert request.sources is None


def test_request_accepts_camel_case_from_the_wire():
    request = CrawlRequest.model_validate(
        {"neighborhood": "Bushwick", "maxReviewsPerRestaurant": 3}
    )
    assert request.max_reviews_per_restaurant == 3


def test_request_rejects_a_limit_outside_the_allowed_range():
    with pytest.raises(ValidationError):
        CrawlRequest(neighborhood="Bushwick", limit=0)


def _valid_restaurant(**overrides):
    payload = {
        "slug": "bw-robertas",
        "name": "Roberta's",
        "neighborhood": "Bushwick",
        "cuisine": "Pizza",
        "priceTier": 2,
        "address": "261 Moore St, Brooklyn, NY 11206",
        "dietary": ["vegetarian"],
        "hours": {day: None for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")},
        "reviews": [],
        "raw": {},
    }
    payload.update(overrides)
    return payload


def test_restaurant_serializes_to_camel_case():
    restaurant = Restaurant.model_validate(_valid_restaurant())
    dumped = restaurant.model_dump(by_alias=True)
    assert dumped["priceTier"] == 2
    assert "price_tier" not in dumped


def test_restaurant_rejects_a_missing_day():
    six_days = {day: None for day in ("mon", "tue", "wed", "thu", "fri", "sat")}
    with pytest.raises(ValidationError, match="seven"):
        Restaurant.model_validate(_valid_restaurant(hours=six_days))


def test_restaurant_rejects_a_price_tier_outside_one_to_four():
    with pytest.raises(ValidationError):
        Restaurant.model_validate(_valid_restaurant(priceTier=0))
```

- [X] **Step 2: Run the test to verify it fails**

Run: `docker compose exec crawler pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models'`

- [X] **Step 3: Write `app/models.py`**

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from .normalize import DAY_KEYS

WireModel = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CrawlRequest(BaseModel):
    model_config = WireModel

    neighborhood: str = Field(min_length=1)
    city: str = "New York"
    limit: int = Field(default=10, ge=1, le=20)
    max_reviews_per_restaurant: int = Field(default=10, ge=1, le=50)
    sources: list[str] | None = None


class HoursWindow(BaseModel):
    model_config = WireModel

    open: str = Field(pattern=r"^\d{2}:\d{2}$")
    close: str = Field(pattern=r"^\d{2}:\d{2}$")


class Review(BaseModel):
    model_config = WireModel

    content: str = Field(min_length=1)
    source: str
    source_url: str | None = None
    published_at: str | None = None


class Restaurant(BaseModel):
    model_config = WireModel

    slug: str
    name: str
    neighborhood: str
    cuisine: str
    price_tier: int = Field(ge=1, le=4)
    address: str
    dietary: list[str]
    hours: dict[str, HoursWindow | None]
    reviews: list[Review]
    raw: dict

    @field_validator("hours")
    @classmethod
    def exactly_seven_days(cls, value: dict) -> dict:
        if set(value) != set(DAY_KEYS):
            raise ValueError(f"hours needs exactly the seven keys {DAY_KEYS}, got {sorted(value)}")
        return value


class JobStatus(BaseModel):
    model_config = WireModel

    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
```

- [X] **Step 4: Run the model test to verify it passes**

Run: `docker compose exec crawler pytest tests/test_models.py -v`
Expected: 6 passed

- [X] **Step 5: Write the failing assembly test**

```python
# tests/test_assembly.py
from app.models import Restaurant
from app.normalize import build_restaurant, derive_source_status, review_content

GOOGLE_DETAIL = {
    "id": "places/abc123",
    "displayName": {"text": "Roberta's"},
    "formattedAddress": "261 Moore St, Brooklyn, NY 11206",
    "primaryTypeDisplayName": {"text": "Pizza restaurant"},
    "priceLevel": "PRICE_LEVEL_MODERATE",
    "regularOpeningHours": {
        "periods": [
            {"open": {"day": 1, "hour": 11, "minute": 0},
             "close": {"day": 1, "hour": 23, "minute": 0}}
        ]
    },
    "reviews": [
        {"text": {"text": "The wood-fired pizza is the draw."},
         "publishTime": "2026-07-01T10:00:00Z",
         "googleMapsUri": "https://maps.google.com/review1"}
    ],
}


def test_review_content_is_plain_text_without_markup_or_ratings():
    raw = "**Great** spot!\n\n5/5 stars — the pizza is [excellent](http://x.com)."
    assert review_content(raw) == "Great spot! the pizza is excellent."


def test_review_content_keeps_at_most_three_sentences():
    raw = "One. Two. Three. Four. Five."
    assert review_content(raw) == "One. Two. Three."


def test_assembled_record_validates_against_the_wire_model():
    record = build_restaurant("Bushwick", GOOGLE_DETAIL, None, [], None, 10)
    Restaurant.model_validate(record)
    assert record["slug"] == "bw-robertas"
    assert record["priceTier"] == 2
    assert record["cuisine"] == "Pizza"
    assert record["hours"]["mon"] == {"open": "11:00", "close": "23:00"}
    assert record["hours"]["sun"] is None


def test_reviews_carry_their_source_and_are_capped():
    tips = [{"text": f"Tip number {n}.", "created_at": "2026-05-01T00:00:00",
             "url": f"https://fsq/{n}"} for n in range(5)]
    record = build_restaurant("Bushwick", GOOGLE_DETAIL, None, tips, None, 3)
    assert len(record["reviews"]) == 3
    assert {review["source"] for review in record["reviews"]} <= {"google", "foursquare"}
    assert record["reviews"][0]["source"] == "google"


def test_raw_keeps_each_sources_untouched_payload():
    record = build_restaurant("Bushwick", GOOGLE_DETAIL, {"fsq_place_id": "x"}, [], None, 10)
    assert record["raw"]["google"] == GOOGLE_DETAIL
    assert record["raw"]["foursquare"]["place"] == {"fsq_place_id": "x"}


def test_source_status_is_ok_partial_or_failed():
    assert derive_source_status([("google", True), ("google", True)]) == {"google": "ok"}
    assert derive_source_status([("google", True), ("google", False)]) == {"google": "partial"}
    assert derive_source_status([("website", False)]) == {"website": "failed"}


def test_a_source_that_never_ran_is_absent_from_source_status():
    """The contract has no 'skipped' value, and the consumer reads this as a dict,
    so an absent key is how a source without credentials is reported."""
    assert "foursquare" not in derive_source_status([("google", True)])
```

- [X] **Step 6: Run the test to verify it fails**

Run: `docker compose exec crawler pytest tests/test_assembly.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_restaurant' from 'app.normalize'`

- [X] **Step 7: Add assembly to `app/normalize.py`**

```python
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
```

- [X] **Step 8: Run the test to verify it passes**

Run: `docker compose exec crawler pytest tests/test_assembly.py -v`
Expected: 7 passed

- [X] **Step 9: Run the whole suite**

Run: `make test`
Expected: 38 passed

- [X] **Step 10: Commit**

```bash
git add app/models.py app/normalize.py tests/test_models.py tests/test_assembly.py
git commit -m "feat: wire models and restaurant record assembly"
```

---

### Task 5: Google Places source

**Files:**
- Create: `app/sources/__init__.py`, `app/sources/google.py`, `tests/test_google_source.py`
- Test: `tests/test_google_source.py`

Tests use `httpx.MockTransport`, so they assert the exact request this code builds without a key and without network.

**Interfaces:**
- Consumes: `settings()` from Task 1
- Produces: `search_restaurants(client: httpx.AsyncClient, neighborhood: str, city: str, limit: int) -> list[dict]`; `place_details(client: httpx.AsyncClient, place_id: str) -> dict`; the module constants `SEARCH_FIELDS` and `DETAIL_FIELDS`

- [X] **Step 1: Write the failing test**

```python
# tests/test_google_source.py
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
```

- [X] **Step 2: Run the test to verify it fails**

Run: `docker compose exec crawler pytest tests/test_google_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sources'`

- [X] **Step 3: Write `app/sources/google.py`**

```python
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
```

- [X] **Step 4: Run the test to verify it passes**

Run: `docker compose exec crawler pytest tests/test_google_source.py -v`
Expected: 5 passed

- [X] **Step 5: Commit**

```bash
git add app/sources tests/test_google_source.py
git commit -m "feat: Google Places search and details source"
```

---

### Task 6: Foursquare and website sources

**Files:**
- Create: `app/sources/foursquare.py`, `app/sources/website.py`, `tests/test_foursquare_source.py`, `tests/test_website_source.py`
- Test: `tests/test_foursquare_source.py`, `tests/test_website_source.py`

**Interfaces:**
- Consumes: `settings()` from Task 1
- Produces: `match_place(client, name, lat, lng) -> dict | None` and `place_tips(client, fsq_id, limit) -> list[dict]` in `foursquare.py`; `crawlable(url: str | None) -> bool` and `fetch_page(url: str) -> dict | None` in `website.py`, where a page is `{"url": str, "title": str | None, "markdown": str}`

- [X] **Step 1: Write the failing Foursquare test**

```python
# tests/test_foursquare_source.py
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
```

- [X] **Step 2: Run the test to verify it fails**

Run: `docker compose exec crawler pytest tests/test_foursquare_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sources.foursquare'`

- [X] **Step 3: Write `app/sources/foursquare.py`**

```python
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
```

- [X] **Step 4: Run the test to verify it passes**

Run: `docker compose exec crawler pytest tests/test_foursquare_source.py -v`
Expected: 5 passed

- [X] **Step 5: Write the failing website test**

```python
# tests/test_website_source.py
from app.sources.website import crawlable


def test_a_restaurants_own_domain_is_crawlable():
    assert crawlable("https://robertaspizza.com/menu") is True


def test_social_and_aggregator_hosts_are_not_crawled():
    """Instagram and Linktree are not the restaurant's site, and Yelp and Google
    Maps forbid it."""
    for url in ("https://www.instagram.com/robertas",
                "https://linktr.ee/robertas",
                "https://www.yelp.com/biz/robertas",
                "https://maps.google.com/?cid=1",
                "https://www.opentable.com/robertas"):
        assert crawlable(url) is False


def test_a_missing_or_malformed_url_is_not_crawlable():
    assert crawlable(None) is False
    assert crawlable("") is False
    assert crawlable("not-a-url") is False
```

- [X] **Step 6: Run the test to verify it fails**

Run: `docker compose exec crawler pytest tests/test_website_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sources.website'`

- [X] **Step 7: Write `app/sources/website.py`**

```python
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Hosts that are never the restaurant's own site: social profiles, link shims, and
# aggregators whose terms forbid crawling.
SKIP_HOSTS = {
    "facebook.com", "www.facebook.com", "instagram.com", "www.instagram.com",
    "twitter.com", "x.com", "linktr.ee", "linktree.com",
    "google.com", "www.google.com", "maps.google.com", "goo.gl",
    "yelp.com", "www.yelp.com", "opentable.com", "www.opentable.com", "resy.com",
}

MIN_USEFUL_CHARS = 200
PAGE_TIMEOUT_MS = 30000


def crawlable(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    return bool(host) and host not in SKIP_HOSTS


async def fetch_page(url: str) -> dict | None:
    """Imported inside the function so the service still boots and answers /health
    on a host where Chromium is missing."""
    from crawl4ai import (AsyncWebCrawler, BrowserConfig, CacheMode,
                          CrawlerRunConfig, DefaultMarkdownGenerator,
                          PruningContentFilter)

    browser = BrowserConfig(headless=True, text_mode=True, light_mode=True, verbose=False)
    run = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.45, threshold_type="dynamic")),
        excluded_tags=["nav", "footer", "header", "form", "script", "style", "aside"],
        remove_overlay_elements=True,
        exclude_external_links=True,
        word_count_threshold=15,
        page_timeout=PAGE_TIMEOUT_MS,
        check_robots_txt=True,
        verbose=False,
    )

    async with AsyncWebCrawler(config=browser) as crawler:
        result = await crawler.arun(url=url, config=run)

    if not result.success:
        raise RuntimeError(f"crawl {url} failed: {result.error_message}")

    markdown = result.markdown
    text = getattr(markdown, "fit_markdown", None) or getattr(markdown, "raw_markdown", "") or ""
    if len(text) < MIN_USEFUL_CHARS:
        logger.info("page %s produced only %d chars; ignoring", url, len(text))
        return None

    return {"url": result.url, "title": (result.metadata or {}).get("title"), "markdown": text}
```

- [X] **Step 8: Run the test to verify it passes**

Run: `docker compose exec crawler pytest tests/test_website_source.py -v`
Expected: 3 passed

- [X] **Step 9: Verify Chromium actually launches in the container**

This is the step that catches a Playwright/base-image version mismatch, and it is the only reason to run a live crawl this early.

Run:
```bash
docker compose exec crawler python -c "
import asyncio
from app.sources.website import fetch_page
page = asyncio.run(fetch_page('https://example.com'))
print('crawl returned:', 'a page' if page else 'too little text (expected for example.com)')
"
```
Expected: either line prints without an exception. An `Executable doesn't exist` error means the `playwright` pin and the base image tag have drifted apart — fix the pin, do not install a browser by hand.

- [X] **Step 10: Commit**

```bash
git add app/sources tests/test_foursquare_source.py tests/test_website_source.py
git commit -m "feat: Foursquare tips and restaurant website sources"
```

---

### Task 7: Job registry and the crawl pipeline

**Files:**
- Create: `app/jobs.py`, `tests/test_jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: everything from Tasks 2–6
- Produces: `Job` dataclass with fields `id: str`, `neighborhood: str`, `status: str`, `progress: dict`, `restaurants: list`, `source_status: dict`, `errors: list`, `crawled_at: str | None`; `create_job(neighborhood: str) -> Job`; `find_active(neighborhood: str) -> Job | None`; `get_job(job_id: str) -> Job | None`; `job_payload(job: Job) -> dict`; `async run_crawl(job: Job, request: CrawlRequest) -> None`; `reset_registry() -> None` for tests

- [X] **Step 1: Write the failing test**

```python
# tests/test_jobs.py
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
    monkeypatch.setenv("FSQ_SERVICE_KEY", "fsq-key")
    import app.config
    monkeypatch.setattr(app.config, "_settings", None)
    jobs.reset_registry()


def _stub_sources(monkeypatch, *, fsq_fails=False, site_fails=False):
    async def search(client, neighborhood, city, limit):
        return [GOOGLE_PLACE] * limit

    async def details(client, place_id):
        return GOOGLE_DETAIL

    async def match(client, name, lat, lng):
        if fsq_fails:
            raise RuntimeError("fsq search 401: bad key")
        return {"fsq_place_id": "fsq1", "price": 2}

    async def tips(client, fsq_id, limit):
        return [{"fsq_tip_id": "t1", "text": "Go on a weeknight."}]

    async def page(url):
        if site_fails:
            raise RuntimeError("crawl failed: navigation timeout")
        return {"url": url, "title": "Roberta's", "markdown": "We have a vegan menu."}

    monkeypatch.setattr(jobs.google, "search_restaurants", search)
    monkeypatch.setattr(jobs.google, "place_details", details)
    monkeypatch.setattr(jobs.foursquare, "match_place", match)
    monkeypatch.setattr(jobs.foursquare, "place_tips", tips)
    monkeypatch.setattr(jobs.website, "fetch_page", page)


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
    assert payload["sourceStatus"] == {"google": "ok", "foursquare": "ok", "website": "ok"}
    assert payload["neighborhood"] == "Bushwick"
    assert payload["crawledAt"].endswith("Z")
    assert payload["restaurants"][0]["dietary"] == ["vegan"]


async def test_one_failing_source_still_succeeds_and_is_reported(monkeypatch):
    _stub_sources(monkeypatch, fsq_fails=True)
    job = jobs.create_job("Bushwick")
    await jobs.run_crawl(job, CrawlRequest(neighborhood="Bushwick", limit=2))

    payload = jobs.job_payload(job)
    assert payload["status"] == "succeeded"
    assert len(payload["restaurants"]) == 2
    assert payload["sourceStatus"]["foursquare"] == "failed"
    assert payload["sourceStatus"]["google"] == "ok"
    assert any(error["source"] == "foursquare" for error in payload["errors"])


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


async def test_a_source_without_credentials_is_absent_from_source_status(monkeypatch):
    _stub_sources(monkeypatch)
    monkeypatch.delenv("FSQ_SERVICE_KEY")
    import app.config
    monkeypatch.setattr(app.config, "_settings", None)

    job = jobs.create_job("Bushwick")
    await jobs.run_crawl(job, CrawlRequest(neighborhood="Bushwick", limit=2))

    payload = jobs.job_payload(job)
    assert "foursquare" not in payload["sourceStatus"]
    assert payload["status"] == "succeeded"
```

- [X] **Step 2: Run the test to verify it fails**

Run: `docker compose exec crawler pytest tests/test_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.jobs'`

- [X] **Step 3: Write the registry half of `app/jobs.py`**

```python
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
```

- [X] **Step 4: Write the pipeline half of `app/jobs.py`**

Append to the same file:

```python
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
```

- [X] **Step 5: Run the test to verify it passes**

Run: `docker compose exec crawler pytest tests/test_jobs.py -v`
Expected: 9 passed

- [X] **Step 6: Run the whole suite**

Run: `make test`
Expected: 60 passed

- [X] **Step 7: Commit**

```bash
git add app/jobs.py tests/test_jobs.py
git commit -m "feat: job registry and the discovery to fan-out crawl pipeline"
```

---

### Task 8: The three routes and bearer auth

**Files:**
- Modify: `app/main.py`
- Create: `tests/test_routes.py`
- Test: `tests/test_routes.py`

**Interfaces:**
- Consumes: `app/jobs.py` from Task 7, `CrawlRequest` from Task 4
- Produces: `POST /crawl` returning `202 {"jobId", "status"}`; `GET /crawl/{job_id}` returning `job_payload`; `GET /health` unchanged and unauthenticated

- [X] **Step 1: Write the failing test**

```python
# tests/test_routes.py
import pytest
from fastapi.testclient import TestClient

from app import jobs

AUTH = {"Authorization": "Bearer secret"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CRAWLER_API_KEY", "secret")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "google-key")
    import app.config
    monkeypatch.setattr(app.config, "_settings", None)
    jobs.reset_registry()

    from app.main import app

    async def never_runs(job, request):
        """The pipeline is exercised in test_jobs.py; these tests are about HTTP."""
        job.status = "running"

    monkeypatch.setattr(jobs, "run_crawl", never_runs)
    with TestClient(app) as test_client:
        yield test_client


def test_health_needs_no_token(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_crawl_rejects_a_missing_token(client):
    assert client.post("/crawl", json={"neighborhood": "Bushwick"}).status_code == 401


def test_crawl_rejects_a_wrong_token(client):
    response = client.post("/crawl", json={"neighborhood": "Bushwick"},
                           headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_polling_rejects_a_missing_token(client):
    assert client.get("/crawl/anything").status_code == 401


def test_crawl_returns_202_with_a_job_id(client):
    response = client.post("/crawl", json={"neighborhood": "Bushwick", "limit": 3},
                           headers=AUTH)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["jobId"]


def test_a_second_request_for_the_same_neighborhood_returns_the_same_job(client):
    """Clicks double-fire, so a duplicate must not start a second crawl."""
    first = client.post("/crawl", json={"neighborhood": "Bushwick"}, headers=AUTH).json()
    second = client.post("/crawl", json={"neighborhood": "bushwick"}, headers=AUTH).json()
    assert first["jobId"] == second["jobId"]


def test_a_different_neighborhood_gets_its_own_job(client):
    first = client.post("/crawl", json={"neighborhood": "Bushwick"}, headers=AUTH).json()
    second = client.post("/crawl", json={"neighborhood": "Red Hook"}, headers=AUTH).json()
    assert first["jobId"] != second["jobId"]


def test_an_empty_neighborhood_is_rejected(client):
    assert client.post("/crawl", json={"neighborhood": ""}, headers=AUTH).status_code == 422


def test_polling_an_unknown_job_is_404(client):
    assert client.get("/crawl/does-not-exist", headers=AUTH).status_code == 404


def test_polling_a_known_job_returns_its_payload(client):
    job_id = client.post("/crawl", json={"neighborhood": "Bushwick"}, headers=AUTH).json()["jobId"]
    response = client.get(f"/crawl/{job_id}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["jobId"] == job_id
```

- [X] **Step 2: Run the test to verify it fails**

Run: `docker compose exec crawler pytest tests/test_routes.py -v`
Expected: FAIL — the `/crawl` routes return 404 because they do not exist

- [X] **Step 3: Rewrite `app/main.py`**

```python
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
```

- [X] **Step 4: Run the test to verify it passes**

Run: `docker compose exec crawler pytest tests/test_routes.py -v`
Expected: 10 passed

- [X] **Step 5: Run the whole suite**

Run: `make test`
Expected: 70 passed

- [X] **Step 6: Check the running service by hand**

Run:
```bash
KEY=$(grep '^CRAWLER_API_KEY=' .env | cut -d= -f2)
curl -s localhost:8000/health
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/crawl \
  -H 'Content-Type: application/json' -d '{"neighborhood":"Bushwick"}'
curl -s -X POST localhost:8000/crawl -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"neighborhood":"Bushwick","limit":3}'
```
Expected: `{"ok":true}`, then `401`, then a `jobId`. The job itself will fail without a Google key — that is Task 9.

- [X] **Step 7: Commit**

```bash
git add app/main.py tests/test_routes.py
git commit -m "feat: crawl and poll routes behind bearer auth"
```

---

### Task 9: Live verification against the real APIs

**Requires** a Google Places key in `.env`. Everything before this point runs
without credentials; nothing here can.

**Files:**
- Create: `fixtures/google-place-detail.json`, `tests/test_fixtures_match_reality.py`
- Modify: `Makefile`, `README.md`
- Test: `tests/test_fixtures_match_reality.py`

**Interfaces:**
- Consumes: everything
- Produces: a committed fixture recorded from a real response, and a `make smoke` target

- [ ] **Step 1: Add the key and recreate the container**

```bash
# edit .env, filling GOOGLE_MAPS_API_KEY
docker compose up -d
docker compose logs crawler --since 30s | grep "sources enabled"
```
Expected: `Recreated`, then `sources enabled: google, website`.

`up -d`, never `restart`: `env_file` is read when the container is created, so
`restart` reuses the environment the container already had and the new key is
invisible. The symptom is `sources enabled: website` after you have filled the
key in.

- [ ] **Step 2: Add the smoke target to `Makefile`**

```makefile
smoke:
	@KEY=$$(grep '^CRAWLER_API_KEY=' .env | cut -d= -f2); \
	JOB=$$(curl -s -X POST localhost:8000/crawl -H "Authorization: Bearer $$KEY" \
		-H 'Content-Type: application/json' \
		-d '{"neighborhood":"Bushwick","limit":3}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["jobId"])'); \
	echo "job $$JOB"; \
	for i in $$(seq 1 60); do \
		sleep 5; \
		STATUS=$$(curl -s localhost:8000/crawl/$$JOB -H "Authorization: Bearer $$KEY" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])'); \
		echo "  $$STATUS"; \
		case $$STATUS in succeeded|failed) break;; esac; \
	done; \
	curl -s localhost:8000/crawl/$$JOB -H "Authorization: Bearer $$KEY" > /tmp/crawl-result.json; \
	python3 -c 'import json; d=json.load(open("/tmp/crawl-result.json")); print(d["status"], len(d.get("restaurants",[])), "restaurants"); print("sourceStatus:", d.get("sourceStatus")); print("errors:", d.get("errors"))'
```

Add `smoke` to the `.PHONY` line.

- [ ] **Step 3: Run a real crawl**

Run: `make smoke`
Expected: the status line walks `running` → `succeeded`, then prints
`succeeded 3 restaurants` and a `sourceStatus` map. This is acceptance criteria
#2 and #3.

Google Places bills by SKU according to the fields the mask requests, and
`DETAIL_FIELDS` asks for `reviews` and `regularOpeningHours`, which sit in the
pricier tiers. Keep `limit` at 3 and set a budget cap in the Cloud console.

- [ ] **Step 4: Check the returned records by eye**

Run:
```bash
python3 -c "
import json
data = json.load(open('/tmp/crawl-result.json'))
for r in data['restaurants']:
    print(r['slug'], '|', r['cuisine'], '| tier', r['priceTier'], '|', len(r['reviews']), 'reviews', '|', r['dietary'])
    assert sorted(r['hours']) == ['fri','mon','sat','sun','thu','tue','wed'], r['hours']
    assert 1 <= r['priceTier'] <= 4
print('every record has seven hours keys and a tier in 1-4')
"
```
Expected: one line per restaurant and the final confirmation. A failed assertion
here is a real bug in `normalize.py`, not a flaky API.

Google caps reviews at 5 per place permanently, so `reviews` will read 5 or
fewer. Restaurants Google types generically come back with cuisine
`"Restaurant"`; that is all Google knows about them.

- [ ] **Step 5: Record a fixture from the real payload**

The unit-test fixtures so far were written from the documented response shapes.
This replaces guesswork with what the API actually returned.

```bash
python3 -c "
import json
data = json.load(open('/tmp/crawl-result.json'))
json.dump(data['restaurants'][0]['raw']['google'],
          open('fixtures/google-place-detail.json','w'), indent=2)
print('wrote fixtures/google-place-detail.json')
"
```

- [ ] **Step 6: Write a test that runs the real payload through normalization**

```python
# tests/test_fixtures_match_reality.py
"""The other tests use payloads written by hand from the documented shapes. These
run the same code over a response the live API actually returned."""
import json
import pathlib

from app.models import Restaurant
from app.normalize import build_restaurant

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def test_a_real_google_detail_assembles_into_a_valid_record():
    record = build_restaurant("Bushwick", _load("google-place-detail.json"), None, 10)
    Restaurant.model_validate(record)
    assert record["slug"].startswith("bw-")
    assert 1 <= record["priceTier"] <= 4


def test_a_real_google_detail_yields_reviews_within_googles_cap():
    record = build_restaurant("Bushwick", _load("google-place-detail.json"), None, 10)
    assert 1 <= len(record["reviews"]) <= 5
    assert all(review["source"] == "google" for review in record["reviews"])
```

- [ ] **Step 7: Run the fixture test**

Run: `docker compose exec crawler pytest tests/test_fixtures_match_reality.py -v`
Expected: 2 passed. A failure here means a hand-written assumption about the
response shape was wrong — fix `normalize.py`, and do not edit the fixture to
make it pass.

- [ ] **Step 8: Verify slug stability across two real crawls**

Run:
```bash
cp /tmp/crawl-result.json /tmp/crawl-first.json
make smoke
python3 -c "
import json
first = [r['slug'] for r in json.load(open('/tmp/crawl-first.json'))['restaurants']]
second = [r['slug'] for r in json.load(open('/tmp/crawl-result.json'))['restaurants']]
print('first :', sorted(first))
print('second:', sorted(second))
print('stable overlap:', sorted(set(first) & set(second)))
"
```
Expected: the slugs appearing in both runs are identical strings. This is
acceptance criterion #4. Google may return a slightly different place set between
runs; what matters is that a restaurant present in both has the same slug.

- [ ] **Step 9: Confirm a failing source does not fail the job**

Acceptance criterion #5. With Foursquare removed there is no credential to break
on purpose, so this is verified two ways:

- `test_one_failing_source_still_succeeds_and_is_reported` in `tests/test_jobs.py`
  covers it deterministically, with the website source raising.
- The live crawl usually demonstrates it for real: restaurant sites time out,
  navigate mid-capture, or sit behind anti-bot protection.

Run:
```bash
python3 -c "
import json
d = json.load(open('/tmp/crawl-result.json'))
print('status      :', d['status'])
print('sourceStatus:', d['sourceStatus'])
for e in d['errors']:
    print(' -', e['source'], e['slug'], e['message'][:80])
"
```
Expected: `status: succeeded` regardless of what `sourceStatus.website` says. If
every site crawled cleanly, the unit test is the coverage and that is fine — do
not break something to manufacture a failure.

Do not add crawl4ai's `magic` or `simulate_user` to get past a site's anti-bot
protection. A site running bot protection is declining to be crawled, and
`partial` with the reason in `errors` is the contract working.

- [ ] **Step 10: Finish the README**

Add to `README.md`:

````markdown
## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/health` | — | `{"ok": true}`. Open, so platform health checks work. |
| `POST` | `/crawl` | Bearer | `202` with a jobId. A neighborhood already in flight returns that job. |
| `GET` | `/crawl/{jobId}` | Bearer | Progress while running, the full payload when finished. |

```bash
curl -X POST localhost:8000/crawl \
  -H "Authorization: Bearer $CRAWLER_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"neighborhood":"Bushwick","limit":3}'
```

## Sources

Google Places discovers restaurants and supplies facts and reviews, capped by
Google at 5 reviews per place. The restaurant's own site is crawled for
explicitly stated dietary claims.

Foursquare was removed on 2026-08-13: its tips endpoint needs purchased credits,
and its place match is a fuzzy name lookup with no confidence signal, which can
silently attach the wrong venue's data. Reddit is in the contract and not built.

A source without a credential is disabled and **absent from `sourceStatus`**
rather than reported as failed. Enabled sources are logged at startup.

## Partial success is normal

A job is `failed` only when it produced no restaurants at all. Anything else is
`succeeded` with the damage recorded in `sourceStatus` and `errors`.
````

- [ ] **Step 11: Run the whole suite one last time**

Run: `make test`
Expected: 67 passed

- [ ] **Step 12: Commit**

```bash
git add fixtures tests/test_fixtures_match_reality.py Makefile README.md
git commit -m "test: verify against the live API and record a real fixture"
```

---

## Acceptance summary

| Criterion | Verified by |
|---|---|
| #1 jobId in under a second | Task 8 Step 4 (`test_crawl_returns_202_with_a_job_id`) |
| #2 polling reaches succeeded with 3 restaurants | Task 9 Step 3 (`make smoke`) |
| #3 every record validates | Task 9 Step 4, plus `Restaurant.model_validate` in the pipeline |
| #4 re-running produces the same slugs | Task 9 Step 8, plus `test_re_running_the_same_crawl_produces_the_same_slugs` |
| #5 a failing source still succeeds | `test_one_failing_source_still_succeeds_and_is_reported`, and Task 9 Step 9 against real site failures |
| #6 nothing but `/health` is open | Task 8 Step 4 (four auth tests) |
