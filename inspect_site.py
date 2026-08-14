"""Dump what website.fetch_site() actually returns for a URL, so a disappointing
crawl can be read instead of guessed at. Run inside the container."""
import argparse
import asyncio
import logging

from app.normalize import dietary_tags
from app.sources import website

DIETARY_WORDS = ("vegan", "vegetarian", "plant-based", "halal", "kosher", "gluten", "dairy")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--chars", type=int, default=1500,
                        help="markdown characters to print; 0 prints everything")
    parser.add_argument("--log", default="WARNING")
    return parser.parse_args()


def report(url: str, result: dict | None) -> None:
    if result is None:
        print("no usable content (no pages, or under MIN_USEFUL_CHARS)")
        return

    markdown = result["markdown"]
    print(f"title   : {result['title']!r}")
    print(f"chars   : {len(markdown)}")
    print(f"dietary : {dietary_tags({}, markdown) or 'NONE'}")
    print("pages   :")
    for page_url in result["pages"]:
        print(f"          {page_url}")

    lowered = markdown.lower()
    present = [word for word in DIETARY_WORDS if word in lowered]
    print(f"keywords: {present or 'none present anywhere in the text'}")


async def main() -> None:
    args = parse_args()
    logging.basicConfig(level=args.log, format="%(levelname)s %(name)s %(message)s")

    for url in args.urls:
        print(f"\n{'=' * 70}\n{url}\n{'=' * 70}")
        if not website.crawlable(url):
            print("crawlable() rejected this URL")
            continue
        try:
            result = await website.fetch_site(url)
        except Exception as error:
            print(f"raised {type(error).__name__}: {error}")
            continue

        report(url, result)
        if result:
            markdown = result["markdown"]
            body = markdown if args.chars == 0 else markdown[:args.chars]
            print(f"\n----- markdown -----\n{body}")


asyncio.run(main())
