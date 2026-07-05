from app.agents.itinerary_normalizer import normalize_itinerary
from app.agents.intensity import daily_time_minutes


def test_normalize_itinerary_keeps_must_places_over_relaxed_limit_and_drops_optional():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 30,
                "hotel_return_transport_min": 30,
                "items": [
                    {"poi_id": "p1", "name": "故宫博物院", "arrival_time": "09:00", "duration_min": 240},
                    {"poi_id": "p2", "name": "颐和园", "duration_min": 240},
                    {"poi_id": "p3", "name": "南锣鼓巷", "duration_min": 120},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "故宫博物院", "match_status": "matched", "user_override": "must_include", "final_decision": "include"},
        {"poi_id": "p2", "standard_name": "颐和园", "match_status": "matched", "user_override": "must_include", "final_decision": "include"},
        {"poi_id": "p3", "standard_name": "南锣鼓巷", "match_status": "matched", "user_override": "none", "final_decision": "optional"},
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 40, "distance_m": 15000},
        {"origin_poi_id": "p2", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 35, "distance_m": 12000},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert [item["poi_id"] for item in day["items"]] == ["p1", "p2"]
    assert day["items"][1]["arrival_time"] == "13:40"
    assert any(poi["name"] == "南锣鼓巷" for poi in day["removed_pois"])
    assert any("必去地点较多" in risk for risk in itinerary["global_risks"])


def test_normalize_itinerary_reconnects_transport_after_optional_place_removed():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "items": [
                    {"poi_id": "p1", "name": "A", "arrival_time": "10:00", "duration_min": 240},
                    {"poi_id": "p2", "name": "B", "duration_min": 180},
                    {"poi_id": "p3", "name": "C", "duration_min": 240},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "A", "match_status": "matched", "user_override": "must_include", "final_decision": "include"},
        {"poi_id": "p2", "standard_name": "B", "match_status": "matched", "user_override": "none", "final_decision": "optional"},
        {"poi_id": "p3", "standard_name": "C", "match_status": "matched", "user_override": "must_include", "final_decision": "include"},
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 10, "distance_m": 1000},
        {"origin_poi_id": "p2", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 10, "distance_m": 1000},
        {"origin_poi_id": "p1", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 30, "distance_m": 5000},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert [item["poi_id"] for item in day["items"]] == ["p1", "p3"]
    assert day["items"][0]["transport_to_next"]["duration_min"] == 30
    assert day["items"][1]["arrival_time"] == "14:30"


def test_normalize_itinerary_adds_meal_break_and_counts_it_once():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "items": [
                    {"poi_id": "p1", "name": "景山公园", "arrival_time": "10:00", "duration_min": 90},
                    {"poi_id": "p2", "name": "天坛公园", "duration_min": 120},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "景山公园", "match_status": "matched", "final_decision": "include"},
        {"poi_id": "p2", "standard_name": "天坛公园", "match_status": "matched", "final_decision": "include"},
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 30, "distance_m": 8000},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["meal_breaks"] == [{"label": "午餐", "start_time": "11:30", "duration_min": 60}]
    assert day["items"][1]["arrival_time"] == "13:00"
    assert daily_time_minutes(day) == 340


def test_normalize_itinerary_marks_large_place_meal_inside_without_double_counting():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 30,
                "hotel_return_transport_min": 30,
                "items": [
                    {"poi_id": "p1", "name": "北京环球影城", "arrival_time": "09:30", "duration_min": 600},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "北京环球影城", "match_status": "matched", "final_decision": "include", "category": "attraction"},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, [])

    day = itinerary["days"][0]
    assert day["meal_breaks"] == [
        {"label": "午餐", "start_time": "12:00", "duration_min": 60, "within_poi_id": "p1", "included_in_item_duration": True},
        {"label": "晚餐", "start_time": "18:00", "duration_min": 60, "within_poi_id": "p1", "included_in_item_duration": True},
    ]
    assert daily_time_minutes(day) == 660


def test_normalize_itinerary_keeps_user_confirmed_ambiguous_map_candidate():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "items": [
                    {"poi_id": "p1", "name": "晓市集", "arrival_time": "10:00", "duration_min": 90},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {
            "poi_id": "p1",
            "standard_name": "晓市集",
            "match_status": "ambiguous",
            "amap_id": "B004",
            "location": {"lng": 104.1, "lat": 30.6},
            "user_override": "must_include",
            "final_decision": "include",
        },
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, [])

    day = itinerary["days"][0]
    assert [item["poi_id"] for item in day["items"]] == ["p1"]
    assert day["removed_pois"] == []


def test_normalize_itinerary_drops_optional_meal_when_not_nearby_and_over_limit():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 30,
                "hotel_return_transport_min": 30,
                "items": [
                    {"poi_id": "p1", "name": "故宫博物院", "arrival_time": "09:00", "duration_min": 240},
                    {"poi_id": "p2", "name": "很远的餐厅", "duration_min": 90},
                    {"poi_id": "p3", "name": "颐和园", "duration_min": 240},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "故宫博物院", "match_status": "matched", "user_override": "must_include", "final_decision": "include"},
        {"poi_id": "p2", "standard_name": "很远的餐厅", "match_status": "matched", "user_override": "none", "final_decision": "optional", "category": "restaurant"},
        {"poi_id": "p3", "standard_name": "颐和园", "match_status": "matched", "user_override": "must_include", "final_decision": "include"},
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 50, "distance_m": 18000},
        {"origin_poi_id": "p2", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 50, "distance_m": 18000},
        {"origin_poi_id": "p1", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 40, "distance_m": 15000},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert [item["poi_id"] for item in day["items"]] == ["p1", "p3"]
    assert any(poi["name"] == "很远的餐厅" for poi in day["removed_pois"])
    assert day["meal_breaks"] == [
        {"label": "午餐", "start_time": "12:00", "duration_min": 60, "within_poi_id": "p1", "included_in_item_duration": True},
        {"label": "晚餐", "start_time": "18:00", "duration_min": 60, "within_poi_id": "p3", "included_in_item_duration": True},
    ]
