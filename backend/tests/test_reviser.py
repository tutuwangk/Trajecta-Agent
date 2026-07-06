from app.agents.reviser import revise_from_user_instruction, revise_itinerary


def test_revise_itinerary_removes_unconfirmed_pois_and_trims_relaxed_days():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "IFS"},
                    {"poi_id": "p2", "name": "太古里"},
                    {"poi_id": "p3", "name": "人民公园"},
                    {"poi_id": "p4", "name": "宽窄巷子"},
                    {"poi_id": "p5", "name": "晓市集"},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    verification = {
        "issues": [
            {"type": "daily_time_over_intensity_limit", "message": "当天总耗时过长", "suggestion": "减少当天总耗时"},
            {"type": "unmatched_poi_scheduled", "message": "晓市集 未确认", "suggestion": "移入不确定地点"},
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "IFS", "match_status": "matched", "confidence": 0.9},
        {"poi_id": "p2", "standard_name": "太古里", "match_status": "matched", "confidence": 0.9},
        {"poi_id": "p3", "standard_name": "人民公园", "match_status": "matched", "confidence": 0.8},
        {"poi_id": "p4", "standard_name": "宽窄巷子", "match_status": "matched", "confidence": 0.8},
        {"poi_id": "p5", "standard_name": "晓市集", "match_status": "ambiguous", "confidence": 0.4},
    ]

    revised = revise_itinerary(
        itinerary,
        verification,
        {"constraints": {"avoid_too_tired": True}},
        runtime_pois=runtime_pois,
    )

    items = revised["days"][0]["items"]
    assert len(items) <= 4
    assert all(item["poi_id"] != "p5" for item in items)
    assert any(poi["name"] == "晓市集" for poi in revised["days"][0]["removed_pois"])


def test_revise_itinerary_trims_relaxed_days_by_total_time():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "IFS", "duration_min": 210, "transport_to_next": {"duration_min": 40}},
                    {"poi_id": "p2", "name": "太古里", "duration_min": 210, "transport_to_next": {"duration_min": 40}},
                    {"poi_id": "p3", "name": "人民公园", "duration_min": 180},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "IFS", "match_status": "matched", "confidence": 0.9},
        {"poi_id": "p2", "standard_name": "太古里", "match_status": "matched", "confidence": 0.9},
        {"poi_id": "p3", "standard_name": "人民公园", "match_status": "matched", "confidence": 0.8},
    ]

    revised = revise_itinerary(
        itinerary,
        {"issues": [{"type": "daily_time_over_intensity_limit", "suggestion": "减少当天总耗时"}]},
        {"constraints": {"physical_intensity": "low"}},
        runtime_pois=runtime_pois,
    )

    total_minutes = sum((item.get("duration_min") or 0) + (item.get("transport_to_next") or {}).get("duration_min", 0) for item in revised["days"][0]["items"])
    assert total_minutes <= 420
    assert revised["days"][0]["removed_pois"]


def test_revise_itinerary_keeps_user_confirmed_ambiguous_map_candidate():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [{"poi_id": "p1", "name": "晓市集", "duration_min": 90}],
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

    revised = revise_itinerary(itinerary, {"issues": []}, {"constraints": {"physical_intensity": "medium"}}, runtime_pois=runtime_pois)

    assert [item["poi_id"] for item in revised["days"][0]["items"]] == ["p1"]
    assert revised["days"][0]["removed_pois"] == []


def test_revise_itinerary_keeps_user_marked_must_include_places_when_low_intensity_overflows():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "景山公园", "duration_min": 120, "transport_to_next": {"duration_min": 30}},
                    {"poi_id": "p2", "name": "故宫博物院", "duration_min": 180, "transport_to_next": {"duration_min": 30}},
                    {"poi_id": "p3", "name": "天安门广场", "duration_min": 90, "transport_to_next": {"duration_min": 20}},
                    {"poi_id": "p4", "name": "慕田峪长城", "duration_min": 180},
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
            "standard_name": "景山公园",
            "match_status": "matched",
            "confidence": 0.9,
            "user_override": "must_include",
            "final_decision": "include",
        },
        {
            "poi_id": "p2",
            "standard_name": "故宫博物院",
            "match_status": "matched",
            "confidence": 0.9,
            "user_override": "must_include",
            "final_decision": "include",
        },
        {
            "poi_id": "p3",
            "standard_name": "天安门广场",
            "match_status": "matched",
            "confidence": 0.9,
            "user_override": "must_include",
            "final_decision": "include",
        },
        {
            "poi_id": "p4",
            "standard_name": "慕田峪长城",
            "match_status": "matched",
            "confidence": 0.9,
            "user_override": "must_include",
            "final_decision": "include",
        },
    ]

    revised = revise_itinerary(
        itinerary,
        {"issues": [{"type": "daily_time_over_intensity_limit", "suggestion": "减少当天总耗时"}]},
        {"constraints": {"physical_intensity": "low"}},
        runtime_pois=runtime_pois,
    )

    assert [item["name"] for item in revised["days"][0]["items"]] == ["景山公园", "故宫博物院", "天安门广场", "慕田峪长城"]
    assert revised["days"][0]["removed_pois"] == []
    assert "必去地点较多" in revised["global_risks"][0]


def test_revise_itinerary_keeps_single_large_place_when_low_intensity_overflows():
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "北京环球影城", "duration_min": 600},
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
            "standard_name": "北京环球影城",
            "match_status": "matched",
            "confidence": 0.9,
            "estimated_duration_min": 600,
            "final_decision": "include",
        },
    ]

    revised = revise_itinerary(
        itinerary,
        {"issues": [{"type": "daily_time_over_intensity_limit", "suggestion": "减少当天总耗时"}]},
        {"constraints": {"physical_intensity": "low"}},
        runtime_pois=runtime_pois,
    )

    assert [item["name"] for item in revised["days"][0]["items"]] == ["北京环球影城"]
    assert revised["days"][0]["removed_pois"] == []
    assert any("本身需要较长时间" in risk for risk in revised["global_risks"])


def test_revise_itinerary_preserves_meal_and_quick_stop_roles_before_trimming_normal_visits():
    itinerary = {
        "days": [
            {
                "day": 1,
                "hotel_departure_transport_min": 92,
                "hotel_return_transport_min": 85,
                "items": [
                    {"poi_id": "p1", "name": "成都太古里", "duration_min": 120, "transport_to_next": {"duration_min": 10}},
                    {
                        "poi_id": "p2",
                        "name": "喜茶(成都太古里锦街店)",
                        "duration_min": 15,
                        "scheduled_role": "quick_stop",
                        "burden_role": "light_detour",
                        "trim_priority": "keep_if_low_detour",
                        "transport_to_next": {"duration_min": 5},
                    },
                    {
                        "poi_id": "p3",
                        "name": "园里火锅(万科天荟店)",
                        "duration_min": 60,
                        "scheduled_role": "meal_stop",
                        "burden_role": "protected_basic",
                        "trim_priority": "never_trim_before_meal",
                        "transport_to_next": {"duration_min": 12},
                    },
                    {"poi_id": "p4", "name": "兰桂坊成都", "duration_min": 120},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "成都太古里", "match_status": "matched", "confidence": 0.9, "user_override": "must_include", "final_decision": "include"},
        {"poi_id": "p2", "standard_name": "喜茶(成都太古里锦街店)", "match_status": "matched", "confidence": 0.2, "final_decision": "include"},
        {"poi_id": "p3", "standard_name": "园里火锅(万科天荟店)", "match_status": "matched", "confidence": 0.1, "final_decision": "include"},
        {"poi_id": "p4", "standard_name": "兰桂坊成都", "match_status": "matched", "confidence": 0.95, "final_decision": "optional"},
    ]

    revised = revise_itinerary(
        itinerary,
        {"issues": [{"type": "daily_time_over_intensity_limit", "suggestion": "减少当天总耗时"}]},
        {"constraints": {"physical_intensity": "low", "must_visit": ["成都太古里"]}},
        runtime_pois=runtime_pois,
    )

    assert [item["poi_id"] for item in revised["days"][0]["items"]] == ["p1", "p2", "p3"]
    assert any(poi["name"] == "兰桂坊成都" for poi in revised["days"][0]["removed_pois"])


def test_revise_itinerary_normalizes_llm_risk_objects_before_merging_verification_issues():
    itinerary = {
        "days": [],
        "global_risks": [{"message": "热门地点可能需要排队"}, "热门地点可能需要排队"],
        "revision_notes": [],
    }
    verification = {"issues": [{"message": "当天总耗时偏长", "suggestion": "减少当天总耗时"}]}

    revised = revise_itinerary(itinerary, verification, {"constraints": {}})

    assert revised["global_risks"] == ["热门地点可能需要排队", "当天总耗时偏长"]


def test_revise_itinerary_removes_internal_fields_from_user_visible_text():
    itinerary = {
        "route_summary": {"main_message": "include，已安排"},
        "days": [
            {
                "day": 1,
                "summary": "optional，围绕中轴线安排",
                "items": [
                    {
                        "poi_id": "p1",
                        "name": "景山公园",
                        "reason": "must_include，地标拍照点。",
                        "risk_notes": ["final_decision include，可能排队"],
                    }
                ],
                "removed_pois": [{"name": "前门", "reason": "unresolved，匹配不确定"}],
            }
        ],
        "global_risks": ["system_decision include，热门地点可能需要排队"],
        "revision_notes": [],
    }

    revised = revise_itinerary(itinerary, {"issues": []}, {"constraints": {}})

    assert revised["route_summary"]["main_message"] == "已安排"
    assert revised["days"][0]["summary"] == "围绕中轴线安排"
    assert revised["days"][0]["items"][0]["reason"] == "地标拍照点。"
    assert revised["days"][0]["items"][0]["risk_notes"] == ["可能排队"]
    assert revised["global_risks"] == ["热门地点可能需要排队"]


def test_revise_from_user_instruction_deletes_named_poi_without_llm_call():
    class FailingLLM:
        def json_chat(self, *args, **kwargs):
            raise AssertionError("simple delete instruction should not call llm")

    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "IFS"},
                    {"poi_id": "p2", "name": "建设路"},
                ],
                "removed_pois": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "IFS", "raw_names": ["IFS"], "match_status": "matched"},
        {"poi_id": "p2", "standard_name": "建设路", "raw_names": ["建设路"], "match_status": "matched"},
    ]

    revised = revise_from_user_instruction(
        itinerary,
        "删掉建设路",
        {"constraints": {}},
        runtime_pois,
        [],
        FailingLLM(),
    )

    assert [item["name"] for item in revised["days"][0]["items"]] == ["IFS"]
    assert any(poi["name"] == "建设路" for poi in revised["days"][0]["removed_pois"])


def test_revise_itinerary_adds_summary_and_attention_sections():
    itinerary = {
        "destination": "成都",
        "days": [
            {
                "day": 1,
                "summary": "围绕春熙路安排",
                "items": [{"poi_id": "p1", "name": "IFS"}],
                "removed_pois": [{"name": "都江堰", "reason": "距离较远"}],
            }
        ],
        "uncertain_pois": [{"poi_id": "p2", "standard_name": "晓市集", "decision_reason": "地点匹配不确定"}],
        "global_risks": [],
        "revision_notes": [],
    }

    revised = revise_itinerary(itinerary, {"issues": []}, {"days": 1, "preferences": {"food": 5}, "constraints": {}})

    assert revised["route_summary"]["scheduled_places_count"] == 1
    assert revised["route_summary"]["unscheduled_places_count"] == 1
    assert revised["route_summary"]["attention_required_count"] == 1
    assert revised["unscheduled_places"] == [{"name": "都江堰", "reason": "距离较远"}]
    assert revised["attention_places"][0]["name"] == "晓市集"
