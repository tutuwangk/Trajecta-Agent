from app.api.routes import (
    _clean_final_messages,
    _planning_grounded_pois,
    _sync_hotel_transport_edges,
    _sync_transport_edges,
    _uncertain_grounded_pois,
)


def test_planning_grounded_pois_uses_only_accepted_matched_rows():
    rows = [
        {"final_decision": "include", "grounded_poi": {"raw_name": "IFS", "match_status": "matched"}},
        {"final_decision": "exclude", "grounded_poi": {"raw_name": "太古里", "match_status": "matched"}},
        {"final_decision": "unresolved", "grounded_poi": {"raw_name": "晓市集", "match_status": "ambiguous"}},
        {"final_decision": "optional", "grounded_poi": {"raw_name": "人民公园", "match_status": "matched"}},
    ]

    accepted = _planning_grounded_pois(rows)
    uncertain = _uncertain_grounded_pois(rows)

    assert [poi["raw_name"] for poi in accepted] == ["IFS", "人民公园"]
    assert [poi["raw_name"] for poi in uncertain] == ["晓市集"]


def test_planning_grounded_pois_keeps_legacy_decisions_compatible():
    rows = [
        {"decision": "keep", "grounded_poi": {"raw_name": "IFS", "match_status": "matched"}},
        {"decision": "delete", "grounded_poi": {"raw_name": "太古里", "match_status": "matched"}},
        {"decision": "must_visit", "grounded_poi": {"raw_name": "人民公园", "match_status": "matched"}},
    ]

    accepted = _planning_grounded_pois(rows)

    assert [poi["raw_name"] for poi in accepted] == ["IFS", "人民公园"]


def test_planning_grounded_pois_includes_user_confirmed_ambiguous_candidate():
    rows = [
        {
            "final_decision": "include",
            "user_override": "must_include",
            "grounded_poi": {
                "raw_name": "晓市集",
                "match_status": "ambiguous",
                "amap_id": "B004",
                "location": {"lng": 104.1, "lat": 30.6},
            },
        },
        {
            "final_decision": "include",
            "user_override": "must_include",
            "grounded_poi": {
                "raw_name": "没有坐标的地点",
                "match_status": "unmatched",
                "amap_id": "",
                "location": {"lng": None, "lat": None},
            },
        },
    ]

    accepted = _planning_grounded_pois(rows)

    assert [poi["raw_name"] for poi in accepted] == ["晓市集"]


def test_sync_transport_edges_overwrites_llm_transport_with_route_matrix():
    itinerary = {
        "days": [
            {
                "items": [
                    {
                        "poi_id": "p1",
                        "name": "天安门广场",
                        "transport_to_next": {"mode": "walking", "duration_min": 20, "distance_m": 1700},
                    },
                    {"poi_id": "p2", "name": "景山公园", "transport_to_next": {"mode": "walking", "duration_min": 99}},
                ]
            }
        ]
    }
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 8, "distance_m": 3200}
    ]

    _sync_transport_edges(itinerary, route_matrix)

    items = itinerary["days"][0]["items"]
    assert items[0]["transport_to_next"] == {"mode": "taxi", "duration_min": 8, "distance_m": 3200}
    assert "transport_to_next" not in items[1]


def test_sync_hotel_transport_edges_overwrites_llm_hotel_times_with_amap():
    class FakeAmap:
        def geocode(self, address, city=None):
            return {"location": "116.40,39.90"}

        def driving_direction(self, origin, destination):
            durations = {
                ("116.4,39.9", "116.3,39.99"): 39,
                ("116.3,39.99", "116.4,39.9"): 45,
            }
            duration = durations[(origin, destination)]
            return {"route": {"paths": [{"duration": str(duration * 60), "distance": "23000"}]}}

        def walking_direction(self, origin, destination):
            return None

        def transit_direction(self, origin, destination, city):
            return None

    itinerary = {
        "days": [
            {
                "items": [{"poi_id": "p1", "name": "颐和园"}],
                "hotel_departure_transport_min": 15,
                "hotel_return_transport_min": 20,
            }
        ]
    }
    runtime_pois = [
        {
            "poi_id": "p1",
            "standard_name": "颐和园",
            "city": "北京",
            "location": {"lng": 116.30, "lat": 39.99},
        }
    ]

    _sync_hotel_transport_edges(
        itinerary,
        runtime_pois,
        {"destination": "北京", "hotel_name": "北京王府井希尔顿酒店", "transport_preference": ["taxi"]},
        FakeAmap(),
    )

    day = itinerary["days"][0]
    assert day["hotel_departure_transport_min"] == 39
    assert day["hotel_return_transport_min"] == 45


def test_clean_final_messages_removes_stale_technical_and_pre_revision_warnings():
    itinerary = {
        "days": [{"items": [{"name": "景山公园"}]}],
        "global_risks": [
            "酒店往返时间未提供矩阵数据，按20分钟估算，实际可能不同。",
            "Day 1 预计总耗时约 355 分钟，超过当前强度上限。",
            "每日总外出时间超上限约30-50分钟，需要步行节奏放慢。",
            "酒店到景点交通时间基于估算（10分钟），实际可能略长，请预留弹性。",
            "景山公园本身需要较长时间，当前路线会超过所选行程强度，建议当天少安排其他项目。",
        ],
        "revision_notes": [
            "缩短停留时间，减少移动距离，或把部分地点拆到其他天。",
            "五道营胡同的estimated_duration_min为120分钟，实际可能较短，若结束早可提前返回。",
            "已将不适合直接执行的地点移入未安排地点。",
        ],
        "unscheduled_places": [{"name": "五道营胡同", "reason": "为控制当天总耗时，已从路线中后置。"}],
    }

    _clean_final_messages(itinerary, {"issues": []})

    assert itinerary["global_risks"] == []
    assert itinerary["revision_notes"] == ["已将不适合直接执行的地点移入未安排地点。"]
