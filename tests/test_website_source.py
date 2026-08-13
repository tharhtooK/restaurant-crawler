from app.sources.website import combine_pages, crawlable


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

def _page(url, markdown, title="Ayat"):
    return {"url": url, "title": title, "markdown": markdown}


def test_combine_concatenates_pages_and_records_which_were_crawled():
    combined = combine_pages([
        _page("https://ayatnyc.com/", "All halal, all delicious. " * 10),
        _page("https://ayatnyc.com/main-menu", "Falafel and hummus. " * 10),
    ])
    assert "All halal" in combined["markdown"]
    assert "Falafel" in combined["markdown"]
    assert combined["pages"] == ["https://ayatnyc.com/", "https://ayatnyc.com/main-menu"]


def test_combine_identifies_the_site_by_its_first_page():
    combined = combine_pages([
        _page("https://ayatnyc.com/", "x " * 200, title="Ayat Bushwick"),
        _page("https://ayatnyc.com/catering", "y " * 200, title="Catering"),
    ])
    assert combined["url"] == "https://ayatnyc.com/"
    assert combined["title"] == "Ayat Bushwick"


def test_combine_treats_a_trailing_slash_as_the_same_page():
    """A live crawl fetched /reservation and /reservation/ as two pages, spending
    the budget twice on one page."""
    combined = combine_pages([
        _page("https://ayatnyc.com/reservation", "book a table " * 40),
        _page("https://ayatnyc.com/reservation/", "book a table " * 40),
    ])
    assert combined["pages"] == ["https://ayatnyc.com/reservation"]


def test_combine_ignores_a_site_with_too_little_text():
    assert combine_pages([_page("https://tiny.com/", "hi")]) is None


def test_combine_returns_nothing_when_no_page_was_crawled():
    assert combine_pages([]) is None
