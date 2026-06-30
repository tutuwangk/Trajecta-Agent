from app.agents.verifier import verify_itinerary


def test_verify_itinerary_flags_unmatched_and_daily_time_over_low_intensity_limit():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "A", "duration_min": 140, "transport_to_next": {"duration_min": 25}},
                    {"poi_id": "p2", "name": "B", "duration_min": 120, "transport_to_next": {"duration_min": 25}},
                    {"poi_id": "p3", "name": "C", "duration_min": 60},
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


def test_verify_itinerary_flags_daily_time_over_intensity_limit():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "A", "duration_min": 180, "transport_to_next": {"duration_min": 40}},
                    {"poi_id": "p2", "name": "B", "duration_min": 180, "transport_to_next": {"duration_min": 40}},
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

    low_result = verify_itinerary(
        itinerary,
        {"constraints": {"physical_intensity": "low"}},
        [],
        runtime_pois,
    )
    medium_result = verify_itinerary(
        itinerary,
        {"constraints": {"physical_intensity": "medium"}},
        [],
        runtime_pois,
    )

    assert "daily_time_over_intensity_limit" in {issue["type"] for issue in low_result["issues"]}
    assert "daily_time_over_intensity_limit" not in {issue["type"] for issue in medium_result["issues"]}


def test_verify_itinerary_counts_hotel_transport_and_meals_as_outing_time():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 30,
                "hotel_return_transport_min": 30,
                "meal_breaks": [{"duration_min": 60}],
                "items": [
                    {"poi_id": "p1", "name": "A", "duration_min": 160, "transport_to_next": {"duration_min": 30}},
                    {"poi_id": "p2", "name": "B", "duration_min": 30},
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
