from app.agents.planning_workflow import _repair_meal_only_blueprint, run_planning_workflow
from app.core import AppError


def test_meal_only_repair_moves_lunch_inside_large_anchor_without_replanning_agent():
    skeleton = {
        "destination": "成都",
        "days": [
            {
                "day": 1,
                "poi_ids": ["park", "dinner"],
                "meal_slots": [
                    {"slot": "lunch", "requirement": "required", "source": "fallback_nearby"},
                    {"slot": "dinner", "requirement": "required", "source": "poi", "poi_id": "dinner"},
                ],
            }
        ],
        "unscheduled": [],
    }
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "park", "arrival_time": "10:00", "duration_min": 330},
                    {"poi_id": "dinner", "arrival_time": "18:00", "duration_min": 90},
                ],
            }
        ]
    }

    repaired = _repair_meal_only_blueprint(
        skeleton,
        itinerary,
        [{"type": "meal_slot_missing", "day": 1, "message": "Day 1 的午餐尚未真正落地。"}],
    )

    assert repaired["days"][0]["meal_slots"] == [
        {"slot": "lunch", "requirement": "required", "source": "inside_poi", "within_poi_id": "park"},
        {"slot": "dinner", "requirement": "required", "source": "poi", "poi_id": "dinner"},
    ]


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


def test_run_planning_workflow_rejects_route_over_absolute_daily_ceiling():
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

    import pytest

    with pytest.raises(AppError) as error:
        run_planning_workflow(
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

    assert error.value.code == "plan_capacity_unresolved"


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


def test_workflow_gives_one_bounded_replan_then_publishes_best_quality_candidate():
    class PlanningLLM:
        def __init__(self):
            self.replans = 0

        def json_chat(self, messages, step, temperature=0.2):
            if step == "plan_itinerary_blueprint":
                return {"destination": "成都", "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": []}], "unscheduled": [], "risk_tags": []}
            if step == "replan_itinerary_blueprint":
                self.replans += 1
                assert "整体收益最高" in messages[1]["content"]
                assert "逐条机械修补" in messages[1]["content"]
                return {"destination": "成都", "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": []}], "unscheduled": [], "risk_tags": []}
            raise AssertionError(step)

    class CopyLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {"issues": []}

    planning_llm = PlanningLLM()
    itinerary, verification, debug = run_planning_workflow(
        {"destination": "成都", "days": 1, "constraints": {}},
        [{"poi_id": "p1", "standard_name": "九眼桥", "match_status": "matched", "estimated_duration_min": 60, "final_decision": "include"}],
        [],
        planning_llm,
        CopyLLM(),
        time_constraints=[{"poi_id": "p1", "preferred_window": "night", "strength": "quasi_hard", "source_text": "晚上去九眼桥"}],
    )

    assert planning_llm.replans == 1
    assert len(debug["candidate_scores"]) == 2
    assert sum(bool(candidate["selected"]) for candidate in debug["candidate_scores"]) == 1
    assert itinerary["days"][0]["items"][0]["poi_id"] == "p1"
    assert verification["publishable"] is True
    assert verification["quality_deviations"]


def test_workflow_compares_deterministic_distribution_when_model_leaves_later_day_empty():
    class FrontLoadsPlanningLLM:
        def __init__(self):
            self.replans = 0

        def json_chat(self, messages, step, temperature=0.2):
            if step == "plan_itinerary_blueprint":
                return {
                    "destination": "成都",
                    "days": [
                        {"day": 1, "poi_ids": ["p1", "p2"], "unscheduled_poi_ids": []},
                        {"day": 2, "poi_ids": [], "unscheduled_poi_ids": []},
                    ],
                    "unscheduled": [],
                    "risk_tags": [],
                }
            if step == "replan_itinerary_blueprint":
                self.replans += 1
                return {
                    "destination": "成都",
                    "days": [
                        {"day": 1, "poi_ids": ["p1", "p2"], "unscheduled_poi_ids": []},
                        {"day": 2, "poi_ids": [], "unscheduled_poi_ids": []},
                    ],
                    "unscheduled": [],
                    "risk_tags": [],
                }
            raise AssertionError(step)

    class CopyLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {"issues": []}

    planner = FrontLoadsPlanningLLM()
    itinerary, verification, debug = run_planning_workflow(
        {"destination": "成都", "days": 2, "constraints": {}},
        [
            {"poi_id": "p1", "raw_name": "第一处", "standard_name": "第一处", "match_status": "matched", "estimated_duration_min": 90, "final_decision": "include"},
            {"poi_id": "p2", "raw_name": "第二处", "standard_name": "第二处", "match_status": "matched", "estimated_duration_min": 90, "final_decision": "include"},
        ],
        [
            {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 15, "distance_m": 2000, "relation": "nearby"},
            {"origin_poi_id": "p2", "destination_poi_id": "p1", "mode": "taxi", "duration_min": 15, "distance_m": 2000, "relation": "nearby"},
        ],
        planner,
        CopyLLM(),
        user_request="第一天去第一处，第二天去第二处。",
    )

    assert planner.replans == 1
    assert [[item["poi_id"] for item in day["items"]] for day in itinerary["days"]] == [["p1"], ["p2"]]
    assert verification["publishable"] is True
    assert debug["auto_fallback_used"] is True
    assert len(debug["candidate_scores"]) == 3


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


def test_run_planning_workflow_collects_safe_llm_call_metrics():
    class DurationLLM:
        call_metrics = [{"step": "estimate_visit_duration", "status": "success", "prompt_tokens": 25}]

    class PlanningLLM:
        call_metrics = [
            {
                "step": "plan_itinerary_blueprint",
                "status": "success",
                "prompt_tokens": 100,
                "completion_tokens": 30,
                "reasoning_tokens": 20,
            }
        ]

        def json_chat(self, messages, step, temperature=0.2):
            return {
                "destination": "成都",
                "days": [{"day": 1, "poi_ids": ["p1"], "unscheduled_poi_ids": [], "risk_tags": []}],
                "unscheduled": [],
                "risk_tags": [],
            }

    class CopyLLM:
        call_metrics = [{"step": "generate_itinerary_copy", "status": "success", "prompt_tokens": 50}]

        def json_chat(self, messages, step, temperature=0.2):
            if step == "review_itinerary_soft_quality":
                return {"issues": []}
            return {"days": [{"day": 1, "items": [{"poi_id": "p1", "reason": "顺路安排。"}]}]}

    _itinerary, verification, debug = run_planning_workflow(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [{"poi_id": "p1", "standard_name": "武侯祠", "match_status": "matched", "estimated_duration_min": 90, "final_decision": "include"}],
        [],
        PlanningLLM(),
        CopyLLM(),
        preflight_llm_clients=[DurationLLM()],
    )

    assert verification["passed"] is True
    assert debug["llm_metrics"]["call_count"] == 3
    assert debug["llm_metrics"]["prompt_tokens"] == 175
    assert debug["llm_metrics"]["reasoning_tokens"] == 20
    assert debug["llm_metrics"]["calls"][0]["step"] == "estimate_visit_duration"


def test_typed_workflow_allows_one_agent_requested_fact_turn_before_blueprint():
    from app.domain.facts import FactResolutionBatch, FactSource, POIAvailabilityFact
    from app.domain.planning import PlanBlueprint, PlannerFactRequest, PlannerTurn

    class ActivePlanner:
        call_metrics = []

        def __init__(self):
            self.turns = 0

        def run_turn(self, context, *, allow_fact_requests=True):
            self.turns += 1
            if allow_fact_requests:
                return PlannerTurn(
                    action="need_facts",
                    fact_requests=[
                        PlannerFactRequest(
                            request_id="hours-p1",
                            poi_id="p1",
                            kind="opening_hours",
                            visit_date="2026-07-20",
                            decision_reason="营业时间影响当天安排。",
                        )
                    ],
                )
            assert context.fact_snapshot.availability_facts[0].status == "open"
            return PlannerTurn(
                action="propose_blueprint",
                blueprint=PlanBlueprint.model_validate(
                    {
                        "destination": "成都",
                        "days": [{"day": 1, "poi_ids": ["p1"]}],
                        "unscheduled": [],
                    }
                ),
            )

        def run(self, context):
            raise AssertionError("no repair should be required")

    class FactResolver:
        def resolve(self, requests, context):
            return FactResolutionBatch(
                availability_facts=[
                    POIAvailabilityFact(
                        poi_id="p1",
                        visit_date="2026-07-20",
                        status="open",
                        open_intervals=["09:00-17:00"],
                        confidence="verified",
                        source=FactSource(provider="official", title="官方参观信息"),
                    )
                ]
            )

    class CopyLLM:
        call_metrics = []

        def json_chat(self, messages, step, temperature=0.2):
            if step == "review_itinerary_soft_quality":
                return {"issues": []}
            return {"days": [{"day": 1, "items": [{"poi_id": "p1", "reason": "按开放日期安排。"}]}]}

    runtime_pois = [
        {
            "poi_id": "p1",
            "standard_name": "成都博物馆",
            "match_status": "matched",
            "estimated_duration_min": 120,
            "final_decision": "include",
        }
    ]
    planner = ActivePlanner()

    itinerary, verification, debug = run_planning_workflow(
        {"destination": "成都", "days": 1, "start_date": "2026-07-20", "constraints": {}},
        runtime_pois,
        [],
        planner,
        CopyLLM(),
        fact_resolver=FactResolver(),
        user_request="周一去成都博物馆",
    )

    assert planner.turns == 2
    assert debug["fact_requests"][0]["poi_id"] == "p1"
    assert runtime_pois[0]["availability_facts"][0]["status"] == "open"
    assert itinerary["days"][0]["items"][0]["poi_id"] == "p1"
    assert verification["publishable"] is True
