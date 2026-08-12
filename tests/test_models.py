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