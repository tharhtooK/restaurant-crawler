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