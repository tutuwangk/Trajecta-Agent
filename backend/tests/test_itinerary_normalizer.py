from app.agents.itinerary_normalizer import normalize_itinerary
from app.agents.intensity import daily_time_minutes


def test_normalize_itinerary_keeps_all_items_and_rebuilds_timing_when_day_is_dense():
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
    assert [item["poi_id"] for item in day["items"]] == ["p1", "p2", "p3"]
    assert day["items"][1]["arrival_time"] == "13:40"
    assert day["removed_pois"] == []
    assert day["total_outing_min"] > 420


def test_normalize_itinerary_preserves_existing_adjacent_transport_when_no_trim_occurs():
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
    assert [item["poi_id"] for item in day["items"]] == ["p1", "p2", "p3"]
    assert day["items"][0]["transport_to_next"]["duration_min"] == 10
    assert day["items"][2]["arrival_time"] == "17:20"


def test_normalize_itinerary_keeps_segments_aligned_when_items_remain_unchanged():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "segments": [
                    {"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]},
                    {"kind": "outing", "segment_time": "afternoon", "poi_ids": ["p2"]},
                    {"kind": "outing", "segment_time": "evening", "poi_ids": ["p3"]},
                ],
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
    assert [item["poi_id"] for item in day["items"]] == ["p1", "p2", "p3"]
    assert day["segments"] == [
        {"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]},
        {"kind": "outing", "segment_time": "afternoon", "poi_ids": ["p2"]},
        {"kind": "outing", "segment_time": "evening", "poi_ids": ["p3"]},
    ]
    assert day["items"][0]["transport_to_next"]["duration_min"] == 10


def test_normalize_itinerary_places_fallback_lunch_before_a_long_visit_crosses_mealtime():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "fallback_nearby"}],
                "items": [{"poi_id": "p1", "name": "成都博物馆", "arrival_time": "10:00", "duration_min": 300}],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {
            "poi_id": "p1",
            "standard_name": "成都博物馆",
            "match_status": "matched",
            "final_decision": "include",
            "category": "museum",
        }
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, [])

    day = itinerary["days"][0]
    assert day["meal_breaks"] == [
        {"label": "午餐", "slot": "lunch", "start_time": "11:30", "duration_min": 60, "source": "fallback_nearby"}
    ]
    assert day["items"][0]["arrival_time"] == "12:30"


def test_normalize_itinerary_keeps_segmented_fallback_lunch_after_compilation():
    itinerary = {
        "days": [
            {
                "day": 1,
                "segments": [{"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]}],
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "fallback_nearby"}],
                "items": [{"poi_id": "p1", "name": "成都博物馆", "duration_min": 150}],
                "removed_pois": [],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都博物馆", "match_status": "matched", "final_decision": "include", "category": "museum"}
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, [])

    day = itinerary["days"][0]
    assert day["meal_slots"] == [{"slot": "lunch", "requirement": "required", "source": "fallback_nearby"}]
    assert day["items"][0]["arrival_time"] == "10:00"
    assert day["meal_breaks"][0]["start_time"] == "12:30"


def test_normalize_itinerary_drops_optional_breakfast_when_day_starts_in_afternoon():
    itinerary = {
        "days": [
            {
                "day": 1,
                "segments": [{"kind": "outing", "segment_time": "afternoon", "poi_ids": ["p1"]}],
                "meal_slots": [{"slot": "breakfast", "requirement": "optional", "source": "fallback_nearby"}],
                "items": [{"poi_id": "p1", "name": "东郊记忆", "duration_min": 90}],
                "removed_pois": [],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "东郊记忆", "match_status": "matched", "final_decision": "include"}
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, [])

    day = itinerary["days"][0]
    assert day["meal_slots"] == []
    assert day["meal_breaks"] == []
    assert day["items"][0]["arrival_time"] == "14:00"


def test_normalize_itinerary_drops_required_lunch_when_first_outing_starts_at_night():
    itinerary = {
        "days": [
            {
                "day": 1,
                "segments": [{"kind": "outing", "segment_time": "night", "poi_ids": ["p1"]}],
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "fallback_nearby"}],
                "items": [{"poi_id": "p1", "name": "九眼桥", "duration_min": 90}],
                "removed_pois": [],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "九眼桥", "match_status": "matched", "final_decision": "include"}
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, [])

    day = itinerary["days"][0]
    assert day["meal_slots"] == []
    assert day["meal_breaks"] == []
    assert day["items"][0]["arrival_time"] == "18:00"


def test_normalize_itinerary_aligns_real_lunch_poi_with_lunch_window():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p2"}],
                "items": [
                    {"poi_id": "p1", "name": "武侯祠", "arrival_time": "09:00", "duration_min": 120},
                    {"poi_id": "p2", "name": "钵钵鸡", "duration_min": 60},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "武侯祠", "match_status": "matched", "final_decision": "include"},
        {
            "poi_id": "p2",
            "standard_name": "钵钵鸡",
            "match_status": "matched",
            "final_decision": "include",
            "category": "restaurant",
            "planning_semantics": {"experience_type": "full_meal", "meal_capability": "lunch_dinner"},
        },
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "walking", "duration_min": 10, "distance_m": 800}
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["items"][1]["arrival_time"] == "11:30"
    assert day["items"][1]["meal_roles"] == ["lunch"]
    assert day["meal_breaks"] == []


def test_normalize_itinerary_reanchors_segment_so_designated_lunch_poi_reaches_lunch_window():
    itinerary = {
        "days": [
            {
                "day": 1,
                "segments": [{"kind": "outing", "segment_time": "evening", "poi_ids": ["p1", "p2"]}],
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p2"}],
                "items": [
                    {"poi_id": "p1", "name": "武侯祠", "duration_min": 90},
                    {"poi_id": "p2", "name": "钵钵鸡", "duration_min": 60},
                ],
                "removed_pois": [],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "武侯祠", "match_status": "matched", "final_decision": "include"},
        {
            "poi_id": "p2",
            "standard_name": "钵钵鸡",
            "match_status": "matched",
            "final_decision": "include",
            "category": "restaurant",
            "planning_semantics": {"experience_type": "full_meal", "meal_capability": "lunch_dinner"},
        },
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "walking", "duration_min": 15, "distance_m": 900}
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["items"][0]["arrival_time"] == "10:45"
    assert day["items"][1]["arrival_time"] == "12:30"
    assert day["items"][1]["meal_roles"] == ["lunch"]


def test_normalize_itinerary_does_not_create_gap_for_soft_later_segment_label():
    itinerary = {
        "days": [
            {
                "day": 1,
                "segments": [
                    {"kind": "outing", "segment_time": "afternoon", "poi_ids": ["p1"]},
                    {"kind": "outing", "segment_time": "evening", "poi_ids": ["p2"]},
                ],
                "items": [
                    {"poi_id": "p1", "name": "博物馆", "duration_min": 120},
                    {"poi_id": "p2", "name": "普通街区", "duration_min": 90},
                ],
                "removed_pois": [],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "match_status": "matched", "final_decision": "include"},
        {
            "poi_id": "p2",
            "match_status": "matched",
            "final_decision": "include",
            "planning_semantics": {"time_suitability": ["morning", "midday", "afternoon", "evening"]},
        },
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "walking", "duration_min": 20, "distance_m": 1400}
    ]

    normalize_itinerary(itinerary, {"constraints": {}}, runtime_pois, route_matrix)

    assert itinerary["days"][0]["items"][0]["arrival_time"] == "14:00"
    assert itinerary["days"][0]["items"][1]["arrival_time"] == "16:20"


def test_normalize_itinerary_does_not_invent_meal_break_without_blueprint_slot():
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
    assert day["meal_breaks"] == []
    assert day["items"][1]["arrival_time"] == "12:00"
    assert daily_time_minutes(day) == 280


def test_normalize_itinerary_does_not_mark_large_place_meals_without_blueprint_slot():
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
    assert day["meal_breaks"] == []
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


def test_normalize_itinerary_keeps_optional_meal_stop_without_inventing_meal_breaks():
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
    assert [item["poi_id"] for item in day["items"]] == ["p1", "p2", "p3"]
    assert day["removed_pois"] == []
    assert day["meal_breaks"] == []


def test_normalize_itinerary_keeps_quick_stop_without_system_trimming():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 80,
                "hotel_return_transport_min": 80,
                "items": [
                    {"poi_id": "p1", "name": "成都大熊猫繁育研究基地", "arrival_time": "10:00", "duration_min": 240},
                    {"poi_id": "p2", "name": "喜茶(IFS店)", "duration_min": 10, "scheduled_role": "quick_stop", "burden_role": "light_detour", "trim_priority": "keep_if_low_detour"},
                    {"poi_id": "p3", "name": "东郊记忆", "duration_min": 120},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都大熊猫繁育研究基地", "match_status": "matched", "user_override": "must_include", "final_decision": "include"},
        {
            "poi_id": "p2",
            "standard_name": "喜茶(IFS店)",
            "match_status": "matched",
            "user_override": "optional",
            "final_decision": "include",
            "category": "restaurant",
            "chain_status": "resolved",
            "planning_semantics": {"experience_type": "light_drink"},
        },
        {"poi_id": "p3", "standard_name": "东郊记忆", "match_status": "matched", "user_override": "none", "final_decision": "optional"},
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "walking", "duration_min": 10, "distance_m": 600},
        {"origin_poi_id": "p2", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 30, "distance_m": 9000},
        {"origin_poi_id": "p1", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 35, "distance_m": 11000},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert [item["poi_id"] for item in day["items"]] == ["p1", "p2", "p3"]
    assert day["removed_pois"] == []


def test_normalize_itinerary_keeps_low_detour_quick_stop_without_dropping_other_optional_items():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 80,
                "hotel_return_transport_min": 80,
                "items": [
                    {"poi_id": "p1", "name": "成都太古里", "arrival_time": "10:00", "duration_min": 120},
                    {
                        "poi_id": "p2",
                        "name": "喜茶(太古里店)",
                        "duration_min": 15,
                        "scheduled_role": "quick_stop",
                        "burden_role": "light_detour",
                        "trim_priority": "keep_if_low_detour",
                        "quick_stop_total_cost_min": 252,
                    },
                    {"poi_id": "p3", "name": "IFS国际金融中心", "duration_min": 120},
                    {"poi_id": "p4", "name": "兰桂坊成都", "duration_min": 120},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都太古里", "match_status": "matched", "user_override": "must_include", "final_decision": "include"},
        {
            "poi_id": "p2",
            "standard_name": "喜茶(太古里店)",
            "match_status": "matched",
            "user_override": "optional",
            "final_decision": "include",
            "category": "restaurant",
            "chain_status": "resolved",
            "planning_semantics": {"experience_type": "light_drink"},
        },
        {"poi_id": "p3", "standard_name": "IFS国际金融中心", "match_status": "matched", "user_override": "must_include", "final_decision": "include"},
        {"poi_id": "p4", "standard_name": "兰桂坊成都", "match_status": "matched", "user_override": "none", "final_decision": "optional"},
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "walking", "duration_min": 5, "distance_m": 393},
        {"origin_poi_id": "p2", "destination_poi_id": "p3", "mode": "walking", "duration_min": 5, "distance_m": 393},
        {"origin_poi_id": "p3", "destination_poi_id": "p4", "mode": "walking", "duration_min": 12, "distance_m": 887},
        {"origin_poi_id": "p1", "destination_poi_id": "p3", "mode": "walking", "duration_min": 10, "distance_m": 767},
        {"origin_poi_id": "p2", "destination_poi_id": "p4", "mode": "walking", "duration_min": 12, "distance_m": 887},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert [item["poi_id"] for item in day["items"]] == ["p1", "p2", "p3", "p4"]
    assert day["removed_pois"] == []


def test_normalize_itinerary_outputs_intensity_minutes_from_trim_pressure_not_total_outing():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 80,
                "items": [
                    {"poi_id": "p1", "name": "大景点", "duration_min": 240, "scheduled_role": "anchor_visit"},
                    {
                        "poi_id": "p2",
                        "name": "园里火锅",
                        "duration_min": 90,
                        "scheduled_role": "meal_stop",
                        "burden_role": "protected_basic",
                    },
                    {
                        "poi_id": "p3",
                        "name": "喜茶(IFS店)",
                        "duration_min": 15,
                        "scheduled_role": "quick_stop",
                        "burden_role": "light_detour",
                        "quick_stop_total_cost_min": 20,
                    },
                ],
                "meal_slots": [{"slot": "dinner", "requirement": "required", "source": "fallback_nearby"}],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "大景点", "match_status": "matched", "final_decision": "include"},
        {"poi_id": "p2", "standard_name": "园里火锅", "match_status": "matched", "final_decision": "include", "category": "restaurant"},
        {"poi_id": "p3", "standard_name": "喜茶(IFS店)", "match_status": "matched", "final_decision": "include", "category": "restaurant"},
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 20, "distance_m": 3000},
        {"origin_poi_id": "p2", "destination_poi_id": "p3", "mode": "walking", "duration_min": 10, "distance_m": 600},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["total_outing_min"] > 420
    assert day["intensity_outing_min"] == 385


def test_normalize_itinerary_keeps_required_meal_stop_without_trimming_other_optional_items():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 80,
                "hotel_return_transport_min": 80,
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p2"}],
                "items": [
                    {"poi_id": "p1", "name": "成都大熊猫繁育研究基地", "arrival_time": "09:00", "duration_min": 240},
                    {"poi_id": "p2", "name": "园里火锅", "duration_min": 90, "scheduled_role": "meal_stop", "burden_role": "protected_basic", "trim_priority": "never_trim_before_meal"},
                    {"poi_id": "p3", "name": "东郊记忆", "duration_min": 120},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都大熊猫繁育研究基地", "match_status": "matched", "user_override": "must_include", "final_decision": "include"},
        {
            "poi_id": "p2",
            "standard_name": "园里火锅",
            "match_status": "matched",
            "user_override": "none",
            "final_decision": "optional",
            "category": "restaurant",
            "planning_semantics": {"experience_type": "full_meal"},
        },
        {"poi_id": "p3", "standard_name": "东郊记忆", "match_status": "matched", "user_override": "none", "final_decision": "optional"},
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 10, "distance_m": 3000},
        {"origin_poi_id": "p2", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 30, "distance_m": 9000},
        {"origin_poi_id": "p1", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 35, "distance_m": 11000},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert [item["poi_id"] for item in day["items"]] == ["p1", "p2", "p3"]
    assert day["items"][1]["meal_roles"] == ["lunch"]
    assert day["removed_pois"] == []


def test_normalize_itinerary_uses_poi_meal_slot_without_duplicate_generic_break():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "meal_slots": [
                    {"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p2"},
                    {"slot": "dinner", "requirement": "required", "source": "fallback_nearby"},
                ],
                "items": [
                    {"poi_id": "p1", "name": "成都大熊猫繁育研究基地", "arrival_time": "09:00", "duration_min": 120},
                    {"poi_id": "p2", "name": "园里火锅", "duration_min": 90},
                    {"poi_id": "p3", "name": "东郊记忆", "duration_min": 90},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都大熊猫繁育研究基地", "match_status": "matched", "final_decision": "include", "category": "attraction"},
        {"poi_id": "p2", "standard_name": "园里火锅", "match_status": "matched", "final_decision": "include", "category": "restaurant"},
        {"poi_id": "p3", "standard_name": "东郊记忆", "match_status": "matched", "final_decision": "include", "category": "attraction"},
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 30, "distance_m": 8000},
        {"origin_poi_id": "p2", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 25, "distance_m": 7000},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["items"][1]["meal_roles"] == ["lunch"]
    assert day["meal_slots"] == [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p2"}]
    assert day["meal_breaks"] == []


def test_normalize_itinerary_drops_stale_dinner_when_hotel_can_be_reached_before_1730():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 70,
                "hotel_return_transport_min": 70,
                "meal_slots": [
                    {"slot": "lunch", "requirement": "required", "source": "fallback_nearby"},
                    {"slot": "dinner", "requirement": "required", "source": "fallback_nearby"},
                ],
                "items": [
                    {"poi_id": "p1", "name": "成都杜甫草堂博物馆", "arrival_time": "10:00", "duration_min": 120},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都杜甫草堂博物馆", "match_status": "matched", "final_decision": "include", "category": "museum"},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, [])

    day = itinerary["days"][0]
    assert day["meal_slots"] == [{"slot": "lunch", "requirement": "required", "source": "fallback_nearby"}]
    assert day["meal_breaks"] == [{"label": "午餐", "slot": "lunch", "start_time": "12:00", "duration_min": 60, "source": "fallback_nearby"}]
    assert day["total_outing_min"] == 320


def test_normalize_itinerary_rebinds_required_meal_slot_to_remaining_real_restaurant():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p2"}],
                "items": [
                    {"poi_id": "p1", "name": "成都博物馆", "arrival_time": "10:00", "duration_min": 90},
                    {"poi_id": "p3", "name": "园里火锅", "duration_min": 90, "scheduled_role": "meal_stop", "burden_role": "protected_basic"},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都博物馆", "match_status": "matched", "final_decision": "include", "category": "museum"},
        {"poi_id": "p3", "standard_name": "园里火锅", "match_status": "matched", "final_decision": "include", "category": "restaurant", "planning_semantics": {"experience_type": "full_meal", "meal_capability": "lunch_dinner"}},
    ]
    route_matrix = [{"origin_poi_id": "p1", "destination_poi_id": "p3", "mode": "walking", "duration_min": 15, "distance_m": 900}]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["meal_slots"] == [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p3"}]
    assert day["items"][1]["meal_roles"] == ["lunch"]
    assert day["meal_breaks"] == []


def test_normalize_itinerary_keeps_required_fallback_meal_as_llm_planned():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "meal_slots": [{"slot": "dinner", "requirement": "required", "source": "fallback_nearby"}],
                "items": [
                    {"poi_id": "p1", "name": "武侯祠", "arrival_time": "15:30", "duration_min": 90, "transport_to_next": {"duration_min": 20}},
                    {"poi_id": "p2", "name": "园里火锅", "duration_min": 90, "scheduled_role": "meal_stop", "burden_role": "protected_basic"},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "武侯祠", "match_status": "matched", "final_decision": "include", "category": "attraction"},
        {"poi_id": "p2", "standard_name": "园里火锅", "match_status": "matched", "final_decision": "include", "category": "restaurant", "planning_semantics": {"experience_type": "full_meal", "meal_capability": "lunch_dinner"}},
    ]
    route_matrix = [{"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 20, "distance_m": 2400}]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["meal_slots"] == [{"slot": "dinner", "requirement": "required", "source": "poi", "poi_id": "p2"}]
    assert day["items"][1]["meal_roles"] == ["dinner"]
    assert day["items"][1]["arrival_time"] == "17:30"
    assert day["meal_breaks"] == []


def test_normalize_itinerary_keeps_fallback_dinner_when_only_restaurant_is_too_early():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "meal_slots": [{"slot": "dinner", "requirement": "required", "source": "fallback_nearby"}],
                "items": [
                    {"poi_id": "p1", "name": "园里火锅", "arrival_time": "12:00", "duration_min": 90, "scheduled_role": "meal_stop", "burden_role": "protected_basic", "transport_to_next": {"duration_min": 20}},
                    {"poi_id": "p2", "name": "东郊记忆", "duration_min": 210},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "园里火锅", "match_status": "matched", "final_decision": "include", "category": "restaurant", "planning_semantics": {"experience_type": "full_meal", "meal_capability": "lunch_dinner"}},
        {"poi_id": "p2", "standard_name": "东郊记忆", "match_status": "matched", "final_decision": "include", "category": "attraction"},
    ]
    route_matrix = [{"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 20, "distance_m": 2400}]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["meal_slots"] == [{"slot": "dinner", "requirement": "required", "source": "fallback_nearby"}]
    assert day["meal_breaks"] == [{"label": "晚餐", "slot": "dinner", "start_time": "17:30", "duration_min": 60, "source": "fallback_nearby"}]
    assert day["items"][0].get("meal_roles") is None


def test_normalize_itinerary_keeps_inside_poi_and_optional_breakfast_slots():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 30,
                "hotel_return_transport_min": 30,
                "meal_slots": [
                    {"slot": "breakfast", "requirement": "optional", "source": "poi", "poi_id": "p1"},
                    {"slot": "lunch", "requirement": "required", "source": "inside_poi", "within_poi_id": "p2"},
                ],
                "items": [
                    {"poi_id": "p1", "name": "赵记豆浆", "arrival_time": "08:00", "duration_min": 45},
                    {"poi_id": "p2", "name": "成都博物馆", "duration_min": 300},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "赵记豆浆", "match_status": "matched", "final_decision": "include", "category": "restaurant"},
        {"poi_id": "p2", "standard_name": "成都博物馆", "match_status": "matched", "final_decision": "include", "category": "museum"},
    ]
    route_matrix = [{"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "walking", "duration_min": 15, "distance_m": 900}]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["items"][0]["meal_roles"] == ["breakfast"]
    assert day["meal_breaks"] == [
        {
            "label": "午餐",
            "slot": "lunch",
            "start_time": "12:00",
            "duration_min": 60,
            "within_poi_id": "p2",
            "included_in_item_duration": True,
            "source": "inside_poi",
        }
    ]


def test_normalize_itinerary_replaces_inside_poi_meal_with_real_restaurant_when_available():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "inside_poi", "within_poi_id": "p1"}],
                "items": [
                    {"poi_id": "p1", "name": "成都博物馆", "arrival_time": "10:30", "duration_min": 90, "transport_to_next": {"duration_min": 10}},
                    {"poi_id": "p2", "name": "园里火锅", "duration_min": 90, "scheduled_role": "meal_stop", "burden_role": "protected_basic"},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都博物馆", "match_status": "matched", "final_decision": "include", "category": "museum", "planning_semantics": {"experience_type": "daytime_visit", "meal_capability": "none"}},
        {"poi_id": "p2", "standard_name": "园里火锅", "match_status": "matched", "final_decision": "include", "category": "restaurant", "planning_semantics": {"experience_type": "full_meal", "meal_capability": "lunch_dinner"}},
    ]
    route_matrix = [{"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "walking", "duration_min": 10, "distance_m": 600}]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["meal_slots"] == [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p2"}]
    assert day["items"][1]["meal_roles"] == ["lunch"]
    assert day["meal_breaks"] == []


def test_normalize_itinerary_rejects_snack_as_lunch_even_if_llm_marked_meal_stop():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p2"}],
                "items": [
                    {"poi_id": "p1", "name": "成都太古里", "arrival_time": "10:00", "duration_min": 120, "transport_to_next": {"duration_min": 5}},
                    {"poi_id": "p2", "name": "TRUFFE BOULANGERIE B&C", "duration_min": 45, "scheduled_role": "meal_stop", "burden_role": "protected_basic"},
                    {"poi_id": "p3", "name": "九眼桥", "duration_min": 60},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都太古里", "match_status": "matched", "final_decision": "include", "category": "shopping_mall"},
        {
            "poi_id": "p2",
            "standard_name": "TRUFFE BOULANGERIE B&C",
            "match_status": "matched",
            "final_decision": "include",
            "category": "restaurant",
            "planning_semantics": {"experience_type": "snack", "meal_capability": "breakfast_lunch"},
            "route_semantics": {"meal_level": "小吃/甜品", "meal_fit": ["仅补给"]},
        },
        {"poi_id": "p3", "standard_name": "九眼桥", "match_status": "matched", "final_decision": "include", "category": "attraction"},
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "walking", "duration_min": 5, "distance_m": 300},
        {"origin_poi_id": "p2", "destination_poi_id": "p3", "mode": "taxi", "duration_min": 15, "distance_m": 3000},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, route_matrix)

    day = itinerary["days"][0]
    assert day["meal_slots"] == [{"slot": "lunch", "requirement": "required", "source": "fallback_nearby"}]
    assert day["items"][1].get("meal_roles") is None
    assert day["items"][1]["scheduled_role"] == "quick_stop"
    assert day["meal_breaks"] == [{"label": "午餐", "slot": "lunch", "start_time": "12:00", "duration_min": 60, "source": "fallback_nearby"}]


def test_normalize_itinerary_starts_nightlife_only_day_in_evening():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "items": [
                    {"poi_id": "p1", "name": "兰桂坊", "duration_min": 120},
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
            "standard_name": "兰桂坊",
            "match_status": "matched",
            "final_decision": "include",
            "category": "restaurant",
            "planning_semantics": {"experience_type": "nightlife", "time_suitability": ["evening", "night"], "outing_role": "anchor"},
        }
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, [])

    assert itinerary["days"][0]["items"][0]["arrival_time"] == "18:00"


def test_normalize_itinerary_supports_hotel_rest_between_morning_and_night_segments():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 25,
                "hotel_rest_breaks": [
                    {
                        "after_poi_id": "p1",
                        "before_poi_id": "p2",
                        "duration_min": 180,
                        "return_to_hotel_transport_min": 20,
                        "depart_from_hotel_transport_min": 25,
                        "reason": "下午回酒店休息",
                    }
                ],
                "segments": [
                    {"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]},
                    {"kind": "hotel_rest", "duration_min": 180, "reason": "下午回酒店休息"},
                    {"kind": "outing", "segment_time": "night", "poi_ids": ["p2"]},
                ],
                "meal_slots": [{"slot": "dinner", "requirement": "required", "source": "inside_poi", "within_poi_id": "p2"}],
                "items": [
                    {"poi_id": "p1", "name": "武侯祠", "duration_min": 120},
                    {"poi_id": "p2", "name": "兰桂坊", "duration_min": 120},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "武侯祠", "match_status": "matched", "final_decision": "include", "category": "attraction", "planning_semantics": {"experience_type": "daytime_visit", "time_suitability": ["morning", "afternoon"], "outing_role": "anchor"}},
        {"poi_id": "p2", "standard_name": "兰桂坊", "match_status": "matched", "final_decision": "include", "category": "restaurant", "planning_semantics": {"experience_type": "nightlife", "time_suitability": ["evening", "night"], "outing_role": "anchor"}},
    ]

    normalize_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, runtime_pois, [])

    day = itinerary["days"][0]
    assert day["items"][0]["arrival_time"] == "10:00"
    assert day["items"][1]["arrival_time"] == "18:00"
    assert day["total_outing_min"] == 330
