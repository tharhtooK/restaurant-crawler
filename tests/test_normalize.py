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
    assert price_tier("PRICE_LEVEL_INEXPENSIVE") == 1
    assert price_tier("PRICE_LEVEL_MODERATE") == 2
    assert price_tier("PRICE_LEVEL_EXPENSIVE") == 3
    assert price_tier("PRICE_LEVEL_VERY_EXPENSIVE") == 4
    assert price_tier("PRICE_LEVEL_FREE") == 1


def test_no_price_defaults_to_two_and_never_zero_or_none():
    assert price_tier(None) == 2
    assert price_tier("PRICE_LEVEL_UNSPECIFIED") == 2


def test_cuisine_drops_the_trailing_word_restaurant():
    assert cuisine_label({"primaryTypeDisplayName": {"text": "Pizza restaurant"}}) == "Pizza"
    assert cuisine_label({"primaryTypeDisplayName": {"text": "Korean restaurant"}}) == "Korean"


def test_a_generically_typed_place_keeps_the_plain_default():
    """Google types many restaurants as bare "Restaurant"; that is all we know."""
    assert cuisine_label({"primaryTypeDisplayName": {"text": "Restaurant"}}) == "Restaurant"
    assert cuisine_label({}) == "Restaurant"


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

def test_dietary_recognises_a_plain_first_party_claim():
    """Ayat's own site says "all halal, all delicious" — a first-party statement,
    which §4 accepts as evidence just as it accepts a certification."""
    page = "Middle Eastern favorites and NYC spirit. All halal, all delicious."
    assert dietary_tags({}, page) == ["halal"]


def test_dietary_recognises_common_affirmative_phrasings():
    assert dietary_tags({}, "our halal kitchen since 1998") == ["halal"]
    assert dietary_tags({}, "a fully plant-based menu") == ["vegan"]
    assert dietary_tags({}, "we are vegetarian-friendly") == ["vegetarian"]
    assert dietary_tags({}, "gluten free options available") == ["gluten-free"]
