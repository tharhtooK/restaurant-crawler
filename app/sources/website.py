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
MAX_PAGES_PER_SITE = 3
MAX_CRAWL_DEPTH = 1


def crawlable(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.netloc or "").lower()
    return bool(host) and host not in SKIP_HOSTS


def combine_pages(pages: list[dict]) -> dict | None:
    seen: set[str] = set()
    kept: list[dict] = []
    for page in pages:
        # A live crawl returned /reservation and /reservation/ as two pages and
        # spent the budget twice on one of them.
        key = page["url"].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        kept.append(page)

    if not kept:
        return None

    markdown = "\n\n".join(page["markdown"] for page in kept)
    if len(markdown) < MIN_USEFUL_CHARS:
        logger.info("site %s produced only %d chars; ignoring", kept[0]["url"], len(markdown))
        return None

    return {
        "url": kept[0]["url"],
        "title": kept[0]["title"],
        "markdown": markdown,
        "pages": [page["url"] for page in kept],
    }


def _page_text(result) -> str:
    markdown = result.markdown
    return getattr(markdown, "fit_markdown", None) or getattr(markdown, "raw_markdown", "") or ""


async def fetch_site(url: str) -> dict | None:
    """Imported inside the function so the service still boots and answers /health
    on a host where Chromium is missing."""
    from crawl4ai import (AsyncWebCrawler, BFSDeepCrawlStrategy, BrowserConfig,
                          CacheMode, CrawlerRunConfig, DefaultMarkdownGenerator,
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
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=MAX_CRAWL_DEPTH,
            max_pages=MAX_PAGES_PER_SITE,
            include_external=False,
        ),
    )

    async with AsyncWebCrawler(config=browser) as crawler:
        results = await crawler.arun(url=url, config=run)

    if not isinstance(results, list):
        results = [results]

    crawled = [result for result in results if result.success]
    if not crawled:
        reason = results[0].error_message if results else "no result returned"
        raise RuntimeError(f"crawl {url} failed: {reason}")

    logger.info("crawled %d page(s) from %s", len(crawled), url)
    return combine_pages([
        {"url": result.url,
         "title": (result.metadata or {}).get("title"),
         "markdown": _page_text(result)}
        for result in crawled
    ])