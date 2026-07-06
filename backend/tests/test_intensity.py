from app.agents.intensity import daily_time_limit_minutes, daily_time_minutes, sync_day_total_time


def test_daily_time_limit_minutes_uses_requested_intensity_hours():
    assert daily_time_limit_minutes({"constraints": {"physical_intensity": "low"}}) == 420
    assert daily_time_limit_minutes({"constraints": {"physical_intensity": "medium"}}) == 420
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


def test_sync_day_total_time_replaces_llm_total_with_structured_components():
    day = {
        "total_outing_min": 485,
        "hotel_departure_transport_min": 10,
        "hotel_return_transport_min": 20,
        "items": [{"duration_min": 120}],
        "meal_breaks": [{"duration_min": 60}],
    }

    sync_day_total_time(day)

    assert day["total_outing_min"] == 210


def test_sync_day_total_time_uses_timeline_window_as_truth_source():
    day = {
        "hotel_departure_transport_min": 85,
        "hotel_return_transport_min": 85,
        "items": [
            {"poi_id": "p1", "arrival_time": "10:00", "duration_min": 240, "transport_to_next": {"duration_min": 60}},
            {"poi_id": "p2", "arrival_time": "15:00", "duration_min": 120},
        ],
        "meal_breaks": [{"slot": "lunch", "start_time": "14:00", "duration_min": 60, "source": "fallback_nearby"}],
    }

    sync_day_total_time(day)

    assert day["total_outing_min"] == 590
