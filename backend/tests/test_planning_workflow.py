from app.agents.planning_workflow import run_planning_workflow


def test_run_planning_workflow_replans_after_hard_issue_and_generates_copy():
    class PlanningLLM:
        def __init__(self):
            self.calls = 0

        def json_chat(self, messages, step, temperature=0.2):
            self.calls += 1
            if step == "plan_poi_semantics":
                return {"semantics": []}
            if step == "plan_itinerary_blueprint":
                return {
                    "destination": "成都",
                    "days": [{"day": 1, "poi_ids": ["p2"], "unscheduled_poi_ids": ["p1"], "risk_tags": []}],
                    "unscheduled": [{"poi_id": "p1", "reason_codes": ["time_over_budget"]}],
                    "risk_tags": [],
                }
            if step == "replan_itinerary_blueprint":
                return {
                    "destination": "成都",
                    "days": [{"day": 1, "poi_ids": ["p1", "p2"], "unscheduled_poi_ids": [], "risk_tags": []}],
                    "unscheduled": [],
                    "risk_tags": [],
                }
            raise AssertionError(f"unexpected step: {step}")

    class CopyLLM:
        def json_chat(self, messages, step, temperature=0.2):
            if step == "review_itinerary_soft_quality":
                return {"issues": []}
            assert step == "generate_itinerary_copy"
            return {
                "route_summary": {"main_message": "已整理出 1 天路线。"},
                "days": [
                    {
                        "day": 1,
                        "summary": "围绕核心城区安排。",
                        "items": [
                            {"poi_id": "p1", "reason": "先安排必去地点。"},
                            {"poi_id": "p2", "reason": "顺路加入可选地点。"},
                        ],
                        "removed_pois": [],
                        "risk_notes": [],
                    }
                ],
                "global_risks": [],
            }

    itinerary, verification, debug = run_planning_workflow(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium", "must_visit": ["IFS"]}},
        [
            {
                "poi_id": "p1",
                "standard_name": "IFS",
                "match_status": "matched",
                "estimated_duration_min": 90,
                "final_decision": "include",
                "user_override": "must_include",
                "district": "锦江区",
            },
            {
                "poi_id": "p2",
                "standard_name": "人民公园",
                "match_status": "matched",
                "estimated_duration_min": 120,
                "final_decision": "optional",
                "district": "青羊区",
            },
        ],
        [{"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 15, "distance_m": 3200, "relation": "same_day_possible"}],
        PlanningLLM(),
        CopyLLM(),
        uncertain_pois=[],
        hotel_anchor=None,
    )

    assert [item["poi_id"] for item in itinerary["days"][0]["items"]] == ["p1", "p2"]
    assert itinerary["route_summary"]["main_message"] == "已整理出 1 天路线。"
    assert itinerary["days"][0]["items"][0]["reason"] == "先安排必去地点。"
    assert verification["passed"] is True
    assert len(debug["skeleton_versions"]) == 2
    assert debug["preference_issue_history"][0][0]["type"] == "must_visit_missing"


def test_run_planning_workflow_replans_deterministic_night_constraint_before_soft_review():
    class PlanningLLM:
        def __init__(self):
            self.steps = []

        def json_chat(self, messages, step, temperature=0.2):
            self.steps.append(step)
            if step == "plan_poi_semantics":
                return {
                    "semantics": [
                        {
                            "poi_id": "p1",
                            "visit_role": "主目的地",
                            "meal_level": "非餐饮",
                            "meal_fit": ["不可承接正餐"],
                            "time_fit": ["上午", "下午"],
                        },
                        {
                            "poi_id": "p2",
                            "visit_role": "夜间体验",
                            "meal_level": "非餐饮",
                            "meal_fit": ["不可承接正餐"],
                            "time_fit": ["夜间"],
                        },
                    ]
                }
            if step == "plan_itinerary_blueprint":
                return {
                    "destination": "成都",
                    "days": [{"day": 1, "poi_ids": ["p1", "p2"], "unscheduled_poi_ids": [], "risk_tags": []}],
                    "unscheduled": [],
                    "risk_tags": [],
                }
            if step == "replan_itinerary_blueprint":
                return {
                    "destination": "成都",
                    "days": [
                        {
                            "day": 1,
                                "segments": [
                                {"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]},
                                {"kind": "hotel_rest", "duration_min": 180, "reason": "下午回酒店休息"},
                                    {"kind": "outing", "segment_time": "night", "poi_ids": ["p2"]},
                                ],
                                    "meal_slots": [
                                        {"slot": "lunch", "requirement": "required", "source": "inside_poi", "within_poi_id": "p1"}
                                    ],
                                "unscheduled_poi_ids": [],
                            "risk_tags": [],
                        }
                    ],
                    "unscheduled": [],
                    "risk_tags": [],
                }
            raise AssertionError(f"unexpected step: {step}")

    class CopyLLM:
        def __init__(self):
            self.review_calls = 0

        def json_chat(self, messages, step, temperature=0.2):
            if step == "review_itinerary_soft_quality":
                self.review_calls += 1
                if self.review_calls == 1:
                    return {
                        "issues": [
                            {
                                "type": "night_place_too_early",
                                "severity": "high",
                                "message": "九眼桥应留到晚上。",
                                "suggestion": "把九眼桥放到夜间段。",
                                "evidence": "Day 1 12:15",
                            }
                        ]
                    }
                return {"issues": []}
            assert step == "generate_itinerary_copy"
            return {"route_summary": {"main_message": "已整理出 1 天路线。"}, "days": [], "global_risks": []}

    planning_llm = PlanningLLM()

    itinerary, verification, debug = run_planning_workflow(
            {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "high"}},
        [
            {"poi_id": "p1", "standard_name": "成都太古里", "match_status": "matched", "estimated_duration_min": 120, "final_decision": "include"},
            {
                "poi_id": "p2",
                "standard_name": "九眼桥",
                "match_status": "matched",
                "estimated_duration_min": 60,
                "final_decision": "include",
                "planning_semantics": {"time_suitability": ["evening", "night"], "experience_type": "evening_view"},
            },
        ],
        [{"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 15, "distance_m": 2000}],
        planning_llm,
        CopyLLM(),
        uncertain_pois=[],
        hotel_anchor=None,
    )

    assert planning_llm.steps.count("replan_itinerary_blueprint") == 1
    assert itinerary["days"][0].get("segments", [])[2]["segment_time"] == "night"
    assert verification["passed"] is True
    assert debug["soft_issue_history"] == []


def test_run_planning_workflow_retries_empty_blueprint_with_plannable_pois():
    class PlanningLLM:
        def __init__(self):
            self.blueprint_calls = 0

        def json_chat(self, messages, step, temperature=0.2):
            if step == "plan_poi_semantics":
                return {"semantics": []}
            if step == "plan_itinerary_blueprint":
                self.blueprint_calls += 1
                if self.blueprint_calls == 1:
                    return {"destination": "成都", "days": [], "unscheduled": [], "risk_tags": []}
                return {
                    "destination": "成都",
                        "days": [
                            {"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": [], "risk_tags": []},
                            {"day": 2, "poi_ids": [], "unscheduled_poi_ids": [], "risk_tags": []},
                            {"day": 3, "poi_ids": [], "unscheduled_poi_ids": [], "risk_tags": []},
                        ],
                    "unscheduled": [],
                    "risk_tags": [],
                }
            raise AssertionError(f"unexpected step: {step}")

    class CopyLLM:
        def json_chat(self, messages, step, temperature=0.2):
            if step == "review_itinerary_soft_quality":
                return {"issues": []}
            assert step == "generate_itinerary_copy"
            return {"route_summary": {"main_message": "已整理出 1 天路线。"}, "days": [], "global_risks": []}

    planning_llm = PlanningLLM()
    itinerary, verification, debug = run_planning_workflow(
        {"destination": "成都", "days": 3, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "成都杜甫草堂博物馆",
                "match_status": "matched",
                "estimated_duration_min": 90,
                "final_decision": "include",
                "user_override": "must_include",
            }
        ],
        [],
        planning_llm,
        CopyLLM(),
        uncertain_pois=[],
        hotel_anchor=None,
    )

    assert planning_llm.blueprint_calls == 2
    assert [item["poi_id"] for item in itinerary["days"][0]["items"]] == ["p1"]
    assert verification["passed"] is True
    assert debug["skeleton_versions"][0]["days"][0]["poi_ids"] == ["p1"]


def test_run_planning_workflow_automatically_accepts_dense_pace_without_intervention():
    class DensePlanningLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {
                "destination": "成都",
                "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": [], "risk_tags": []}],
                "unscheduled": [],
                "risk_tags": [],
            }

    class EmptyCopyLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {"issues": []}

    itinerary, verification, debug = run_planning_workflow(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "high"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "成都欢乐谷",
                "match_status": "matched",
                "estimated_duration_min": 900,
                "final_decision": "include",
                "user_override": "must_include",
            }
        ],
        [],
        DensePlanningLLM(),
        EmptyCopyLLM(),
        max_replans=1,
    )

    assert itinerary["days"][0]["items"][0]["poi_id"] == "p1"
    assert verification["passed"] is True
    assert "daily_time_over_intensity_limit" in {issue["type"] for issue in verification["issues"]}
    assert debug["preference_issue_history"] == []


def test_run_planning_workflow_uses_deterministic_blueprint_instead_of_user_choice():
    class OmitsMustPlanningLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {
                "destination": "成都",
                "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": ["p2"], "risk_tags": []}],
                "unscheduled": [{"poi_id": "p2", "reason_codes": ["time_over_budget"]}],
                "risk_tags": [],
            }

    class CopyLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {"issues": []}

    itinerary, verification, debug = run_planning_workflow(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "high"}},
        [
            {"poi_id": "p1", "standard_name": "成都太古里", "match_status": "matched", "estimated_duration_min": 120, "final_decision": "include"},
            {
                "poi_id": "p2",
                "standard_name": "九眼桥",
                "match_status": "matched",
                "estimated_duration_min": 60,
                "final_decision": "include",
                "user_override": "must_include",
            },
        ],
        [
            {"origin_poi_id": "p1", "destination_poi_id": "p2", "duration_min": 15, "distance_m": 2000, "relation": "nearby"},
            {"origin_poi_id": "p2", "destination_poi_id": "p1", "duration_min": 15, "distance_m": 2000, "relation": "nearby"},
        ],
        OmitsMustPlanningLLM(),
        CopyLLM(),
        max_replans=1,
    )

    assert {item["poi_id"] for item in itinerary["days"][0]["items"]} == {"p1", "p2"}
    assert verification["passed"] is True
    assert debug["auto_fallback_used"] is True


def test_run_planning_workflow_does_not_recompile_facts_after_copy_generation():
    class PlanningLLM:
        def json_chat(self, messages, step, temperature=0.2):
            assert step == "plan_itinerary_blueprint"
            return {
                "destination": "成都",
                "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": [], "risk_tags": []}],
                "unscheduled": [],
                "risk_tags": [],
            }

    class CopyLLM:
        def json_chat(self, messages, step, temperature=0.2):
            if step == "review_itinerary_soft_quality":
                return {"issues": []}
            assert step == "generate_itinerary_copy"
            return {
                "days": [
                    {
                        "day": 1,
                        "summary": "上午参观武侯祠。",
                        "items": [{"poi_id": "p1", "reason": "历史文化体验。", "arrival_time": "23:00"}],
                    }
                ]
            }

    prepare_calls = []

    def prepare(itinerary):
        prepare_calls.append(1)
        itinerary["days"][0]["items"][0]["arrival_time"] = "10:00"

    itinerary, verification, _debug = run_planning_workflow(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "成都武侯祠博物馆",
                "match_status": "matched",
                "estimated_duration_min": 90,
                "final_decision": "include",
            }
        ],
        [],
        PlanningLLM(),
        CopyLLM(),
        prepare_itinerary=prepare,
    )

    assert prepare_calls == [1]
    assert itinerary["days"][0]["items"][0]["arrival_time"] == "10:00"
    assert verification["passed"] is True
