from app.core import AppError
from app.api.routes import _extract_order_constraints
from app.agents.planner import (
    compile_planning_context,
    materialize_itinerary_from_skeleton,
    plan_skeleton_with_llm,
)


def test_compile_planning_context_groups_candidates_and_excludes_unplannable_rows():
    context = compile_planning_context(
        {
            "destination": "成都",
            "days": 2,
            "route_goal": "food_first",
            "constraints": {"physical_intensity": "medium", "must_visit": ["IFS"], "avoid_visit": ["酒吧"]},
        },
        [
            {
                "poi_id": "p1",
                "standard_name": "IFS",
                "district": "锦江区",
                "category": "shopping_mall",
                "match_status": "matched",
                "estimated_duration_min": 90,
                "user_override": "must_include",
                "final_decision": "include",
            },
            {
                "poi_id": "p2",
                "standard_name": "人民公园",
                "district": "青羊区",
                "category": "park",
                "match_status": "matched",
                "estimated_duration_min": 120,
                "user_override": "none",
                "final_decision": "optional",
            },
            {
                "poi_id": "p3",
                "standard_name": "很远的酒吧",
                "district": "武侯区",
                "category": "restaurant",
                "match_status": "matched",
                "estimated_duration_min": 75,
                "user_override": "remove",
                "final_decision": "exclude",
            },
            {
                "poi_id": "p4",
                "standard_name": "晓市集",
                "district": "武侯区",
                "category": "restaurant",
                "match_status": "ambiguous",
                "estimated_duration_min": 75,
                "user_override": "none",
                "final_decision": "unresolved",
            },
        ],
        [],
        uncertain_pois=[{"poi_id": "p4", "standard_name": "晓市集", "decision_reason": "地点还需要确认"}],
        hotel_anchor={"poi_id": "hotel", "standard_name": "春熙路酒店", "location": {"lng": 104.08, "lat": 30.65}},
    )

    assert context["day_budget_min"] == 420
    assert [poi["poi_id"] for poi in context["plannable_pois"]] == ["p1", "p2"]
    assert context["must_poi_ids"] == ["p1"]
    assert context["optional_poi_ids"] == ["p2"]
    assert context["meal_candidate_poi_ids"] == []
    assert context["district_summary"] == [
        {"district": "锦江区", "poi_ids": ["p1"], "count": 1},
        {"district": "青羊区", "poi_ids": ["p2"], "count": 1},
    ]
    assert context["uncertain_pois"][0]["poi_id"] == "p4"


def test_plan_skeleton_with_llm_retries_invalid_candidate_ids():
    class RecordingLLM:
        def __init__(self):
            self.calls = 0
            self.messages = []

        def json_chat(self, messages, step, temperature=0.2):
            self.calls += 1
            self.messages.append(messages)
            if self.calls == 1:
                return {
                    "destination": "北京",
                    "days": [{"day": 1, "poi_ids": ["missing-poi"], "unscheduled_poi_ids": [], "risk_tags": []}],
                    "unscheduled": [],
                    "risk_tags": [],
                }
            return {
                "destination": "北京",
                "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": [], "risk_tags": []}],
                "unscheduled": [],
                "risk_tags": [],
            }

    llm = RecordingLLM()
    context = compile_planning_context(
        {"destination": "北京", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [{"poi_id": "p1", "standard_name": "景山公园", "match_status": "matched", "estimated_duration_min": 90, "final_decision": "include"}],
        [],
    )

    skeleton = plan_skeleton_with_llm(context, llm)

    assert llm.calls == 2
    assert skeleton["days"][0]["poi_ids"] == ["p1"]
    prompt = llm.messages[0][1]["content"]
    assert "禁止生成时间线" in prompt
    assert "不得新增候选集合之外的 poi_id" in prompt


def test_plan_skeleton_with_llm_raises_when_retry_still_invalid():
    class AlwaysInvalidLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {
                "destination": "北京",
                "days": [{"day": 1, "poi_ids": ["missing-poi"], "unscheduled_poi_ids": [], "risk_tags": []}],
                "unscheduled": [],
                "risk_tags": [],
            }

    context = compile_planning_context(
        {"destination": "北京", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [{"poi_id": "p1", "standard_name": "景山公园", "match_status": "matched", "estimated_duration_min": 90, "final_decision": "include"}],
        [],
    )

    try:
        plan_skeleton_with_llm(context, AlwaysInvalidLLM())
    except AppError as exc:
        assert exc.code == "llm_invalid_plan_skeleton"
    else:
        raise AssertionError("invalid skeleton should raise AppError")


def test_plan_skeleton_with_llm_retries_invalid_json_once():
    class FlakyLLM:
        def __init__(self):
            self.calls = 0

        def json_chat(self, messages, step, temperature=0.2):
            self.calls += 1
            if self.calls == 1:
                raise AppError("LLM 返回内容不是有效 JSON。", code="llm_invalid_json", step=step)
            return {
                "destination": "北京",
                "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": [], "risk_tags": []}],
                "unscheduled": [],
                "risk_tags": [],
            }

    context = compile_planning_context(
        {"destination": "北京", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [{"poi_id": "p1", "standard_name": "景山公园", "match_status": "matched", "estimated_duration_min": 90, "final_decision": "include"}],
        [],
    )

    skeleton = plan_skeleton_with_llm(context, FlakyLLM())

    assert skeleton["days"][0]["poi_ids"] == ["p1"]


def test_materialize_itinerary_from_skeleton_keeps_removed_reason_codes_for_copy_stage():
    context = compile_planning_context(
        {"destination": "北京", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {"poi_id": "p1", "standard_name": "景山公园", "match_status": "matched", "estimated_duration_min": 90, "final_decision": "include"},
            {"poi_id": "p2", "standard_name": "前门", "match_status": "matched", "estimated_duration_min": 75, "final_decision": "optional"},
        ],
        [],
        uncertain_pois=[{"poi_id": "p9", "standard_name": "待确认地点"}],
    )

    itinerary = materialize_itinerary_from_skeleton(
        context,
        {
            "destination": "北京",
            "days": [
                {
                    "day": 1,
                    "poi_ids": ["p1"],
                    "unscheduled_poi_ids": ["p2"],
                    "drop_reason_codes": {"p2": ["time_over_budget"]},
                    "risk_tags": ["must_places_dense"],
                }
            ],
            "unscheduled": [{"poi_id": "p2", "reason_codes": ["time_over_budget"]}],
            "risk_tags": ["must_places_dense"],
        },
    )

    assert itinerary["days"][0]["items"][0]["poi_id"] == "p1"
    assert itinerary["days"][0]["removed_pois"] == [{"poi_id": "p2", "name": "前门", "reason_codes": ["time_over_budget"]}]
    assert itinerary["global_risks"] == ["must_places_dense"]
    assert itinerary["uncertain_pois"][0]["poi_id"] == "p9"


def test_compile_planning_context_collects_meal_candidates_with_suitability_hints():
    context = compile_planning_context(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "园里火锅",
                "district": "武侯区",
                "category": "restaurant",
                "match_status": "matched",
                "estimated_duration_min": 90,
                "final_decision": "include",
                "contexts": ["午餐想吃火锅", "晚上也可以去"],
            },
            {
                "poi_id": "p2",
                "standard_name": "成都大熊猫繁育研究基地",
                "district": "成华区",
                "category": "attraction",
                "match_status": "matched",
                "estimated_duration_min": 240,
                "final_decision": "include",
            },
        ],
        [],
    )

    assert context["meal_candidate_poi_ids"] == ["p1"]
    assert context["meal_candidates"] == [
        {
            "poi_id": "p1",
            "name": "园里火锅",
            "district": "武侯区",
            "estimated_duration_min": 90,
            "final_decision": "include",
            "must_keep": False,
            "meal_suitability_hint": ["lunch", "dinner"],
            "route_fit_context": ["午餐想吃火锅", "晚上也可以去"],
        }
    ]


def test_compile_planning_context_filters_light_drink_out_of_lunch_dinner_candidates_and_keeps_nightlife_timing():
    context = compile_planning_context(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "喜茶(IFS店)",
                "district": "锦江区",
                "category": "restaurant",
                "match_status": "matched",
                "estimated_duration_min": 45,
                "final_decision": "include",
                "planning_semantics": {
                    "experience_type": "light_drink",
                    "time_suitability": ["afternoon", "evening"],
                    "outing_role": "filler",
                },
            },
            {
                "poi_id": "p2",
                "standard_name": "兰桂坊",
                "district": "锦江区",
                "category": "restaurant",
                "match_status": "matched",
                "estimated_duration_min": 120,
                "final_decision": "include",
                "planning_semantics": {
                    "experience_type": "nightlife",
                    "time_suitability": ["evening", "night"],
                    "outing_role": "anchor",
                },
            },
            {
                "poi_id": "p3",
                "standard_name": "园里火锅",
                "district": "武侯区",
                "category": "restaurant",
                "match_status": "matched",
                "estimated_duration_min": 90,
                "final_decision": "include",
                "planning_semantics": {
                    "experience_type": "full_meal",
                    "time_suitability": ["midday", "evening"],
                    "outing_role": "anchor",
                },
            },
        ],
        [],
    )

    assert context["meal_candidate_poi_ids"] == ["p3"]
    assert context["poi_lookup"]["p1"]["time_suitability"] == ["afternoon", "evening"]
    assert context["poi_lookup"]["p2"]["time_suitability"] == ["evening", "night"]
    assert context["poi_lookup"]["p2"]["outing_role"] == "anchor"


def test_materialize_itinerary_from_skeleton_keeps_meal_slots():
    context = compile_planning_context(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {"poi_id": "p1", "standard_name": "园里火锅", "match_status": "matched", "estimated_duration_min": 90, "final_decision": "include", "category": "restaurant"},
            {"poi_id": "p2", "standard_name": "东郊记忆", "match_status": "matched", "estimated_duration_min": 120, "final_decision": "include"},
        ],
        [],
    )

    itinerary = materialize_itinerary_from_skeleton(
        context,
        {
            "destination": "成都",
            "days": [
                {
                    "day": 1,
                    "poi_ids": ["p1", "p2"],
                    "meal_slots": [
                        {"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p1"},
                        {"slot": "dinner", "requirement": "required", "source": "fallback_nearby"},
                    ],
                    "unscheduled_poi_ids": [],
                    "drop_reason_codes": {},
                    "risk_tags": [],
                }
            ],
            "unscheduled": [],
            "risk_tags": [],
        },
    )

    assert itinerary["days"][0]["meal_slots"] == [
        {"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p1"},
        {"slot": "dinner", "requirement": "required", "source": "fallback_nearby"},
    ]


def test_materialize_itinerary_from_segmented_skeleton_keeps_hotel_rest_segments():
    context = compile_planning_context(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {"poi_id": "p1", "standard_name": "武侯祠", "match_status": "matched", "estimated_duration_min": 120, "final_decision": "include"},
            {"poi_id": "p2", "standard_name": "兰桂坊", "match_status": "matched", "estimated_duration_min": 90, "final_decision": "include"},
        ],
        [],
    )

    itinerary = materialize_itinerary_from_skeleton(
        context,
        {
            "destination": "成都",
            "days": [
                {
                    "day": 1,
                    "segments": [
                        {"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]},
                        {"kind": "hotel_rest", "duration_min": 180, "reason": "中午回酒店休息"},
                        {"kind": "outing", "segment_time": "night", "poi_ids": ["p2"]},
                    ],
                    "meal_slots": [{"slot": "dinner", "requirement": "required", "source": "fallback_nearby"}],
                    "unscheduled_poi_ids": [],
                    "drop_reason_codes": {},
                    "risk_tags": [],
                }
            ],
            "unscheduled": [],
            "risk_tags": [],
        },
    )

    assert [item["poi_id"] for item in itinerary["days"][0]["items"]] == ["p1", "p2"]
    assert itinerary["days"][0]["segments"] == [
        {"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]},
        {"kind": "hotel_rest", "duration_min": 180, "reason": "中午回酒店休息"},
        {"kind": "outing", "segment_time": "night", "poi_ids": ["p2"]},
    ]


def test_compile_planning_context_excludes_unresolved_chain_from_llm_context():
    context = compile_planning_context(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "喜茶（待选择）",
                "brand_name": "喜茶",
                "match_status": "ambiguous",
                "estimated_duration_min": 45,
                "final_decision": "include",
                "user_override": "none",
                "district": "锦江区",
                "location": {"lng": 104.0805, "lat": 30.6572},
                "planning_semantics": {
                    "experience_type": "light_drink",
                    "time_suitability": ["afternoon", "evening"],
                    "outing_role": "filler",
                    "chain_resolution_mode": "unresolved_chain",
                },
                "chain_status": "unresolved",
            }
        ],
        [],
        order_constraints=[{"before": "IFS", "after": "武侯祠", "strength": "strong_preference", "source": "user_text"}],
    )

    assert context["plannable_pois"] == []
    assert context["order_constraints"][0]["strength"] == "strong_preference"


def test_plan_skeleton_prompt_no_longer_mentions_branch_selection():
    class RecordingLLM:
        def __init__(self):
            self.messages = None

        def json_chat(self, messages, step, temperature=0.2):
            self.messages = messages
            return {
                "destination": "成都",
                "days": [{"day": 1, "poi_ids": ["p1"], "scheduled_roles": {"p1": "quick_stop"}, "unscheduled_poi_ids": [], "risk_tags": []}],
                "unscheduled": [],
                "risk_tags": [],
            }

    llm = RecordingLLM()
    context = compile_planning_context(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "喜茶(IFS店)",
                "brand_name": "喜茶",
                "match_status": "matched",
                "estimated_duration_min": 45,
                "final_decision": "include",
                "user_override": "optional",
                "district": "锦江区",
                "location": {"lng": 104.0805, "lat": 30.6572},
                "planning_semantics": {
                    "experience_type": "light_drink",
                    "time_suitability": ["afternoon", "evening"],
                    "outing_role": "filler",
                },
                "chain_status": "resolved",
            }
        ],
        [],
    )

    plan_skeleton_with_llm(context, llm)

    prompt = llm.messages[0]["content"] + llm.messages[1]["content"]
    assert "route_dependent_chain" not in prompt
    assert "selected_branch_ids" not in prompt


def test_materialize_itinerary_from_skeleton_uses_resolved_chain_as_normal_poi():
    context = compile_planning_context(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "喜茶(IFS店)",
                "brand_name": "喜茶",
                "match_status": "matched",
                "estimated_duration_min": 45,
                "final_decision": "include",
                "user_override": "optional",
                "district": "锦江区",
                "location": {"lng": 104.0805, "lat": 30.6572},
                "planning_semantics": {
                    "experience_type": "light_drink",
                    "time_suitability": ["afternoon", "evening"],
                    "outing_role": "filler",
                },
                "chain_status": "resolved",
            }
        ],
        [],
    )

    itinerary = materialize_itinerary_from_skeleton(
        context,
        {
            "destination": "成都",
            "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": [], "risk_tags": []}],
            "unscheduled": [],
            "risk_tags": [],
        },
    )

    assert itinerary["days"][0]["items"][0]["name"] == "喜茶(IFS店)"
    assert itinerary["days"][0]["items"][0]["selected_branch_id"] is None


def test_materialize_itinerary_from_skeleton_keeps_scheduled_roles_for_resolved_chain_and_meal_points():
    context = compile_planning_context(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "喜茶(IFS店)",
                "brand_name": "喜茶",
                "match_status": "matched",
                "estimated_duration_min": 45,
                "final_decision": "include",
                "user_override": "optional",
                "district": "锦江区",
                "location": {"lng": 104.0805, "lat": 30.6572},
                "planning_semantics": {
                    "experience_type": "light_drink",
                    "time_suitability": ["afternoon", "evening"],
                    "outing_role": "filler",
                },
                "quick_stop_total_cost_min": 25,
                "quick_stop_duration_min": 15,
                "chain_status": "resolved",
            },
            {
                "poi_id": "p2",
                "standard_name": "园里火锅",
                "match_status": "matched",
                "estimated_duration_min": 90,
                "final_decision": "include",
                "category": "restaurant",
                "planning_semantics": {"experience_type": "full_meal", "time_suitability": ["midday", "evening"], "outing_role": "anchor"},
            },
        ],
        [],
    )

    itinerary = materialize_itinerary_from_skeleton(
        context,
        {
            "destination": "成都",
            "days": [
                {
                    "day": 1,
                    "poi_ids": ["p1", "p2"],
                    "scheduled_roles": {"p1": "quick_stop", "p2": "meal_stop"},
                    "meal_slots": [{"slot": "dinner", "requirement": "required", "source": "poi", "poi_id": "p2"}],
                    "unscheduled_poi_ids": [],
                    "risk_tags": [],
                }
            ],
            "unscheduled": [],
            "risk_tags": [],
        },
    )

    assert itinerary["days"][0]["items"][0]["scheduled_role"] == "quick_stop"
    assert itinerary["days"][0]["items"][0]["burden_role"] == "light_detour"
    assert itinerary["days"][0]["items"][1]["scheduled_role"] == "meal_stop"
    assert itinerary["days"][0]["items"][1]["trim_priority"] == "never_trim_before_meal"


def test_materialize_itinerary_from_skeleton_infers_quick_stop_for_fixed_light_drink_role():
    context = compile_planning_context(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "喜茶(IFS黑金店)",
                "match_status": "matched",
                "estimated_duration_min": 45,
                "final_decision": "include",
                "category": "restaurant",
                "planning_semantics": {
                    "experience_type": "light_drink",
                    "time_suitability": ["afternoon", "evening"],
                    "outing_role": "filler",
                    "quick_stop_eligible": True,
                    "base_duration_profiles": {"visit": 45, "quick_stop": 15},
                },
            }
        ],
        [],
    )

    itinerary = materialize_itinerary_from_skeleton(
        context,
        {
            "destination": "成都",
            "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": [], "risk_tags": []}],
            "unscheduled": [],
            "risk_tags": [],
        },
    )

    assert itinerary["days"][0]["items"][0]["scheduled_role"] == "quick_stop"
    assert itinerary["days"][0]["items"][0]["duration_min"] == 15
    assert itinerary["days"][0]["items"][0]["trim_priority"] == "keep_if_low_detour"


def test_compile_planning_context_uses_relaxed_duration_profile_for_light_days_and_intense_for_high_days():
    runtime_poi = {
        "poi_id": "p1",
        "standard_name": "颐和园",
        "match_status": "matched",
        "estimated_duration_min": 240,
        "final_decision": "include",
        "visit_duration_profile": {
            "relaxed_min": 180,
            "intense_min": 240,
            "confidence": "high",
            "reason": "大型皇家园林正常游玩需要半天左右。",
        },
    }

    relaxed = compile_planning_context(
        {"destination": "北京", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [runtime_poi],
        [],
    )
    intense = compile_planning_context(
        {"destination": "北京", "days": 1, "constraints": {"physical_intensity": "high"}},
        [runtime_poi],
        [],
    )

    assert relaxed["plannable_pois"][0]["estimated_duration_min"] == 180
    assert intense["plannable_pois"][0]["estimated_duration_min"] == 240
    assert relaxed["plannable_pois"][0]["visit_duration_profile"]["intense_min"] == 240


def test_materialize_itinerary_from_skeleton_uses_selected_duration_profile_for_anchor_visit():
    context = compile_planning_context(
        {"destination": "北京", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "颐和园",
                "match_status": "matched",
                "estimated_duration_min": 240,
                "final_decision": "include",
                "visit_duration_profile": {
                    "relaxed_min": 180,
                    "intense_min": 240,
                    "confidence": "high",
                    "reason": "大型皇家园林正常游玩需要半天左右。",
                },
            }
        ],
        [],
    )

    itinerary = materialize_itinerary_from_skeleton(
        context,
        {
            "destination": "北京",
            "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": [], "risk_tags": []}],
            "unscheduled": [],
            "risk_tags": [],
        },
    )

    assert itinerary["days"][0]["items"][0]["duration_min"] == 180


def test_extract_order_constraints_marks_user_sequence_as_strong_preference():
    constraints = _extract_order_constraints(
        "先去IFS，再去武侯祠",
        "",
        [
            {"standard_name": "IFS", "raw_name": "IFS", "ugc_evidence": ["先去IFS"]},
            {"standard_name": "武侯祠", "raw_name": "武侯祠", "ugc_evidence": ["再去武侯祠"]},
        ],
    )

    assert constraints == [{"before": "IFS", "after": "武侯祠", "strength": "strong_preference", "source": "user_text"}]
