from app.agents.verifier import verify_itinerary


def test_verify_itinerary_flags_unmatched_and_daily_time_over_low_intensity_limit():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "A", "duration_min": 220, "transport_to_next": {"duration_min": 25}},
                    {"poi_id": "p2", "name": "B", "duration_min": 190, "transport_to_next": {"duration_min": 25}},
                    {"poi_id": "p3", "name": "C", "duration_min": 120},
                    {"poi_id": "p5", "name": "E", "duration_min": 10},
                ],
            }
        ],
        "uncertain_pois": [],
    }
    user_profile = {"constraints": {"avoid_too_tired": True, "must_visit": ["太古里"], "avoid_visit": ["都江堰"]}}
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "A", "match_status": "matched"},
        {"poi_id": "p2", "standard_name": "B", "match_status": "matched"},
        {"poi_id": "p3", "standard_name": "C", "match_status": "matched"},
        {"poi_id": "p4", "standard_name": "D", "match_status": "matched"},
        {"poi_id": "p5", "standard_name": "E", "match_status": "unmatched"},
    ]

    result = verify_itinerary(itinerary, user_profile, [], runtime_pois)

    issue_types = {issue["type"] for issue in result["issues"]}
    assert result["passed"] is False
    assert "daily_time_over_intensity_limit" in issue_types
    assert "unmatched_poi_scheduled" in issue_types
    assert "must_visit_missing" in issue_types


def test_verify_itinerary_flags_daily_time_over_relaxed_limit():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                        {"poi_id": "p1", "name": "A", "duration_min": 220, "transport_to_next": {"duration_min": 40}},
                        {"poi_id": "p2", "name": "B", "duration_min": 220, "transport_to_next": {"duration_min": 40}},
                        {"poi_id": "p3", "name": "C", "duration_min": 90},
                ],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "A", "match_status": "matched", "category": "restaurant"},
        {"poi_id": "p2", "standard_name": "B", "match_status": "matched"},
        {"poi_id": "p3", "standard_name": "C", "match_status": "matched"},
    ]

    relaxed_result = verify_itinerary(
        itinerary,
        {"constraints": {"physical_intensity": "medium"}},
        [],
        runtime_pois,
    )
    high_result = verify_itinerary(
        itinerary,
        {"constraints": {"physical_intensity": "high"}},
        [],
        runtime_pois,
    )

    assert "daily_time_over_intensity_limit" in {issue["type"] for issue in relaxed_result["issues"]}
    assert "daily_time_over_intensity_limit" not in {issue["type"] for issue in high_result["issues"]}


def test_verify_itinerary_counts_hotel_transport_and_meals_as_outing_time():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 30,
                "hotel_return_transport_min": 30,
                "meal_breaks": [{"duration_min": 60}],
                "items": [
                    {"poi_id": "p1", "name": "A", "duration_min": 320, "transport_to_next": {"duration_min": 30}},
                    {"poi_id": "p2", "name": "B", "duration_min": 120},
                ],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "A", "match_status": "matched", "category": "restaurant"},
        {"poi_id": "p2", "standard_name": "B", "match_status": "matched"},
    ]

    result = verify_itinerary(itinerary, {"constraints": {"physical_intensity": "low"}}, [], runtime_pois)

    assert "daily_time_over_intensity_limit" in {issue["type"] for issue in result["issues"]}


def test_verify_itinerary_uses_intensity_outing_minutes_for_intensity_classification():
    itinerary = {
        "days": [
            {
                "day": 1,
                "total_outing_min": 530,
                "intensity_outing_min": 305,
                "items": [
                    {"poi_id": "p1", "name": "大景点", "duration_min": 240},
                    {"poi_id": "p2", "name": "园里火锅", "duration_min": 90, "scheduled_role": "meal_stop", "burden_role": "protected_basic"},
                    {"poi_id": "p3", "name": "喜茶(IFS店)", "duration_min": 15, "scheduled_role": "quick_stop", "burden_role": "light_detour"},
                ],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "大景点", "match_status": "matched"},
        {"poi_id": "p2", "standard_name": "园里火锅", "match_status": "matched", "category": "restaurant"},
        {"poi_id": "p3", "standard_name": "喜茶(IFS店)", "match_status": "matched", "category": "restaurant"},
    ]

    result = verify_itinerary(
        itinerary,
        {"constraints": {"physical_intensity": "medium"}},
        [{"origin_poi_id": "p1", "destination_poi_id": "p2", "duration_min": 20}, {"origin_poi_id": "p2", "destination_poi_id": "p3", "duration_min": 10}],
        runtime_pois,
    )

    assert "daily_time_over_intensity_limit" not in {issue["type"] for issue in result["issues"]}


def test_verify_itinerary_allows_relaxed_day_to_exceed_limit_for_must_places():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "故宫博物院", "duration_min": 260, "transport_to_next": {"duration_min": 40}},
                    {"poi_id": "p2", "name": "颐和园", "duration_min": 260},
                ],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "故宫博物院", "match_status": "matched", "user_override": "must_include"},
        {"poi_id": "p2", "standard_name": "颐和园", "match_status": "matched", "user_override": "must_include"},
    ]

    result = verify_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, [], runtime_pois)

    assert "daily_time_over_intensity_limit" not in {issue["type"] for issue in result["issues"]}


def test_verify_itinerary_allows_user_confirmed_ambiguous_map_candidate():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [{"poi_id": "p1", "name": "晓市集", "duration_min": 90}],
            }
        ]
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

    result = verify_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, [], runtime_pois)

    assert "unmatched_poi_scheduled" not in {issue["type"] for issue in result["issues"]}


def test_verify_itinerary_only_flags_long_transfer_for_adjacent_items():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "IFS"},
                    {"poi_id": "p2", "name": "太古里"},
                ],
            }
        ]
    }
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "relation": "nearby", "duration_min": 12},
        {"origin_poi_id": "p1", "destination_poi_id": "p3", "relation": "separate_day", "duration_min": 70},
    ]
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "IFS", "match_status": "matched", "district": "锦江区"},
        {"poi_id": "p2", "standard_name": "太古里", "match_status": "matched", "district": "锦江区"},
        {"poi_id": "p3", "standard_name": "都江堰", "match_status": "matched", "district": "都江堰市"},
    ]

    result = verify_itinerary(itinerary, {"constraints": {}}, route_matrix, runtime_pois)

    issue_types = {issue["type"] for issue in result["issues"]}
    assert "long_transfer" not in issue_types


def test_verify_itinerary_flags_adjacent_long_transfer_and_ambiguous_poi():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "IFS"},
                    {"poi_id": "p2", "name": "晓市集"},
                ],
            }
        ]
    }
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "relation": "separate_day", "duration_min": 65}
    ]
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "IFS", "match_status": "matched", "district": "锦江区"},
        {"poi_id": "p2", "standard_name": "晓市集", "match_status": "ambiguous", "district": "武侯区"},
    ]

    result = verify_itinerary(itinerary, {"constraints": {}}, route_matrix, runtime_pois)

    issue_types = {issue["type"] for issue in result["issues"]}
    assert "long_transfer" in issue_types
    assert "unmatched_poi_scheduled" in issue_types


def test_verify_itinerary_flags_removed_or_unresolved_decision_scheduled():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "太古里"},
                    {"poi_id": "p2", "name": "晓市集"},
                ],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "太古里", "match_status": "matched", "final_decision": "exclude"},
        {"poi_id": "p2", "standard_name": "晓市集", "match_status": "matched", "final_decision": "unresolved"},
    ]

    result = verify_itinerary(itinerary, {"constraints": {}}, [], runtime_pois)

    issue_types = {issue["type"] for issue in result["issues"]}
    assert "excluded_place_scheduled" in issue_types
    assert "unresolved_place_scheduled" in issue_types


def test_verify_itinerary_flags_missing_required_meal_slot_when_day_crosses_dinner():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 20,
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p2"}],
                "items": [
                    {"poi_id": "p1", "name": "成都大熊猫繁育研究基地", "arrival_time": "09:00", "duration_min": 240, "transport_to_next": {"duration_min": 30}},
                    {"poi_id": "p2", "name": "园里火锅", "duration_min": 90, "meal_roles": ["lunch"], "transport_to_next": {"duration_min": 30}},
                    {"poi_id": "p3", "name": "东郊记忆", "duration_min": 150},
                ],
                "meal_breaks": [],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都大熊猫繁育研究基地", "match_status": "matched", "category": "attraction"},
        {"poi_id": "p2", "standard_name": "园里火锅", "match_status": "matched", "category": "restaurant"},
        {"poi_id": "p3", "standard_name": "东郊记忆", "match_status": "matched", "category": "attraction"},
    ]

    result = verify_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, [], runtime_pois)

    issue_types = {issue["type"] for issue in result["issues"]}
    assert "meal_slot_missing" in issue_types


def test_verify_itinerary_uses_hotel_return_time_for_required_dinner_threshold():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 20,
                "hotel_return_transport_min": 50,
                "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "fallback_nearby"}],
                "items": [
                    {"poi_id": "p1", "name": "成都博物馆", "arrival_time": "10:00", "duration_min": 180, "transport_to_next": {"duration_min": 20}},
                    {"poi_id": "p2", "name": "东郊记忆", "arrival_time": "13:20", "duration_min": 210},
                ],
                "meal_breaks": [{"label": "午餐", "slot": "lunch", "duration_min": 60, "source": "fallback_nearby"}],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都博物馆", "match_status": "matched", "category": "museum"},
        {"poi_id": "p2", "standard_name": "东郊记忆", "match_status": "matched", "category": "attraction"},
    ]

    result = verify_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, [], runtime_pois)

    assert "meal_slot_missing" in {issue["type"] for issue in result["issues"]}


def test_verify_itinerary_accepts_required_slots_satisfied_by_poi_and_fallback():
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
                    {"poi_id": "p1", "name": "成都大熊猫繁育研究基地", "arrival_time": "09:00", "duration_min": 180, "transport_to_next": {"duration_min": 30}},
                    {"poi_id": "p2", "name": "园里火锅", "duration_min": 90, "meal_roles": ["lunch"], "transport_to_next": {"duration_min": 25}},
                    {"poi_id": "p3", "name": "东郊记忆", "duration_min": 120},
                ],
                "meal_breaks": [{"label": "晚餐", "slot": "dinner", "duration_min": 60, "source": "fallback_nearby"}],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都大熊猫繁育研究基地", "match_status": "matched", "category": "attraction"},
        {"poi_id": "p2", "standard_name": "园里火锅", "match_status": "matched", "category": "restaurant"},
        {"poi_id": "p3", "standard_name": "东郊记忆", "match_status": "matched", "category": "attraction"},
    ]

    result = verify_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, [], runtime_pois)

    assert "meal_slot_missing" not in {issue["type"] for issue in result["issues"]}


def test_verify_itinerary_does_not_treat_light_drink_as_formal_meal_stop():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "成都博物馆", "arrival_time": "10:00", "duration_min": 120, "transport_to_next": {"duration_min": 20}},
                    {"poi_id": "p2", "name": "喜茶(IFS店)", "duration_min": 45, "transport_to_next": {"duration_min": 20}},
                    {"poi_id": "p3", "name": "太古里", "duration_min": 150},
                ],
                "meal_breaks": [],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都博物馆", "match_status": "matched", "category": "attraction"},
        {
            "poi_id": "p2",
            "standard_name": "喜茶(IFS店)",
            "match_status": "matched",
            "category": "restaurant",
            "planning_semantics": {"experience_type": "light_drink"},
        },
        {"poi_id": "p3", "standard_name": "太古里", "match_status": "matched", "category": "shopping_mall"},
    ]

    result = verify_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, [], runtime_pois)

    assert "meal_stop_missing" in {issue["type"] for issue in result["issues"]}


def test_verify_itinerary_flags_explicit_evening_time_constraint_when_scheduled_too_early():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "成都太古里", "arrival_time": "10:00", "duration_min": 120, "transport_to_next": {"duration_min": 15}},
                    {"poi_id": "p2", "name": "九眼桥", "arrival_time": "12:15", "duration_min": 60},
                ],
                "meal_breaks": [],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都太古里", "match_status": "matched", "category": "shopping_mall"},
        {"poi_id": "p2", "standard_name": "九眼桥", "match_status": "matched", "category": "attraction"},
    ]

    result = verify_itinerary(
        itinerary,
        {"constraints": {"physical_intensity": "medium"}},
        [],
        runtime_pois,
        time_constraints=[
            {"poi_id": "p2", "preferred_window": "evening", "strength": "quasi_hard", "source_text": "晚上去九眼桥"}
        ],
    )

    issue_types = {issue["type"] for issue in result["issues"]}
    assert "time_constraint_violated" in issue_types


def test_verify_itinerary_respects_hotel_rest_segments_for_transfers_but_still_requires_lunch():
    itinerary = {
        "days": [
            {
                "day": 1,
                "segments": [
                    {"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]},
                    {"kind": "hotel_rest", "duration_min": 180, "reason": "中午回酒店休息"},
                    {"kind": "outing", "segment_time": "evening", "poi_ids": ["p2"]},
                ],
                "meal_slots": [{"slot": "dinner", "requirement": "required", "source": "inside_poi", "within_poi_id": "p2"}],
                "meal_breaks": [{"slot": "dinner", "within_poi_id": "p2", "included_in_item_duration": True}],
                "items": [
                    {"poi_id": "p1", "name": "武侯祠", "arrival_time": "10:00", "duration_min": 120},
                    {"poi_id": "p2", "name": "兰桂坊", "arrival_time": "18:00", "duration_min": 120},
                ],
                "hotel_rest_breaks": [{"after_poi_id": "p1", "before_poi_id": "p2", "duration_min": 180}],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "武侯祠", "match_status": "matched", "category": "attraction"},
        {"poi_id": "p2", "standard_name": "兰桂坊", "match_status": "matched", "category": "restaurant", "planning_semantics": {"experience_type": "nightlife"}},
    ]

    result = verify_itinerary(itinerary, {"constraints": {"physical_intensity": "medium"}}, [], runtime_pois)

    issue_types = {issue["type"] for issue in result["issues"]}
    assert "missing_transfer" not in issue_types
    assert "meal_slot_missing" in issue_types
