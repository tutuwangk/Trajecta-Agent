import pytest

from app.agents.planning_workflow import PlanningInterventionRequired, run_planning_workflow


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
    assert debug["hard_issue_history"][0]["issues"][0]["type"] == "must_visit_missing"


def test_run_planning_workflow_replans_after_high_severity_soft_issue():
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
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
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

    assert "replan_itinerary_blueprint" in planning_llm.steps
    assert itinerary["days"][0]["segments"][2]["segment_time"] == "night"
    assert verification["passed"] is True
    assert debug["soft_issue_history"][0][0]["type"] == "night_place_too_early"


def test_run_planning_workflow_returns_intervention_after_unresolved_major_conflict():
    class InvalidPlanningLLM:
        def json_chat(self, messages, step, temperature=0.2):
            if step == "plan_poi_semantics":
                return {"semantics": []}
            return {
                "destination": "成都",
                "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": ["p2"], "risk_tags": []}],
                "unscheduled": [{"poi_id": "p2", "reason_codes": ["time_over_budget"]}],
                "risk_tags": [],
            }

    class EmptyCopyLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {"issues": []}

    with pytest.raises(PlanningInterventionRequired) as raised:
        run_planning_workflow(
            {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium", "must_visit": ["九眼桥"]}},
            [
                {"poi_id": "p1", "standard_name": "成都太古里", "match_status": "matched", "estimated_duration_min": 120, "final_decision": "include"},
                {"poi_id": "p2", "standard_name": "九眼桥", "match_status": "matched", "estimated_duration_min": 60, "final_decision": "include", "user_override": "must_include"},
            ],
            [],
            InvalidPlanningLLM(),
            EmptyCopyLLM(),
            max_replans=1,
        )

    intervention = raised.value.intervention
    assert intervention["status"] == "needs_user_choice"
    assert intervention["options"][0]["id"] == "keep_must_places"
