from app.agents.intensity import daily_time_limit_minutes, daily_time_minutes


def test_daily_time_limit_minutes_uses_requested_intensity_hours():
    assert daily_time_limit_minutes({"constraints": {"physical_intensity": "low"}}) == 300
    assert daily_time_limit_minutes({"constraints": {"physical_intensity": "medium"}}) == 540
    assert daily_time_limit_minutes({"constraints": {"physical_intensity": "high"}}) == 840


def test_daily_time_minutes_counts_full_outing_time_when_available():
    day = {
        "hotel_departure_transport_min": 25,
        "hotel_return_transport_min": 35,
        "meal_breaks": [{"duration_min": 45}, {"duration_min": 60}],
        "items": [
            {"duration_min": 90, "transport_to_next": {"duration_min": 20}},
            {"duration_min": 120},
        ],
    }

    assert daily_time_minutes(day) == 395
