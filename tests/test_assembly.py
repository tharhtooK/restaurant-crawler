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