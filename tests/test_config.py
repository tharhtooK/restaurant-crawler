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