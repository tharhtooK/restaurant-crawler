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