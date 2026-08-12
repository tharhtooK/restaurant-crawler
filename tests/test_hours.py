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