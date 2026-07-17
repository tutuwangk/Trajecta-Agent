import json

import pytest
from pydantic_ai.models.test import TestModel

from app.adapters.legacy import planning_context_from_legacy
from app.agents.planner_agent import PlannerAgent, _planner_prompt, _validated_blueprint_output
from app.domain.planning import PlanBlueprint, PlanningPreferences
from app.core import AppError


def test_planner_agent_returns_typed_blueprint_with_bounded_pydantic_ai_run():
    context = planning_context_from_legacy(
        {
            "destination": "成都",
            "days": 1,
            "day_budget_min": 540,
            "plannable_pois": [{"poi_id": "p1", "name": "武侯祠", "estimated_duration_min": 90}],
            "must_poi_ids": ["p1"],
            "optional_poi_ids": [],
        },
        {"destination": "成都", "days": 1, "constraints": {}},
        [
            {
                "poi_id": "p1",
                "standard_name": "武侯祠",
                "match_status": "matched",
                "location": {"lng": 104.04, "lat": 30.65},
            }
        ],
        [],
    )
    output = {
        "destination": "成都",
        "days": [
            {
                "day": 1,
                "poi_ids": ["p1"],
                "segments": [],
                "meal_slots": [],
                "unscheduled_poi_ids": [],
                "scheduled_roles": {},
                "selected_branch_ids": {},
                "drop_reason_codes": {},
                "risk_tags": [],
            }
        ],
        "unscheduled": [],
        "risk_tags": [],
    }
    planner = PlannerAgent(TestModel(call_tools=[], custom_output_text=json.dumps(output)))

    result = planner.run(context)

    assert result.days[0].poi_ids == ["p1"]
    assert planner.call_metrics[0]["framework"] == "pydantic_ai"
    assert planner.call_metrics[0]["request_attempts"] == 1


def test_planner_agent_can_proactively_request_one_date_fact_turn():
    context = planning_context_from_legacy(
        {
            "destination": "成都",
            "days": 1,
            "day_budget_min": 540,
            "user_request": "成都博物馆安排在出发当天。",
            "plannable_pois": [{"poi_id": "p1", "name": "成都博物馆", "category": "museum"}],
            "must_poi_ids": ["p1"],
        },
        {"destination": "成都", "days": 1, "start_date": "2026-07-20", "constraints": {}},
        [{"poi_id": "p1", "standard_name": "成都博物馆", "match_status": "matched"}],
        [],
    )
    output = {
        "action": "need_facts",
        "fact_requests": [
            {
                "request_id": "hours-p1",
                "poi_id": "p1",
                "kind": "opening_hours",
                "visit_date": "2026-07-20",
                "decision_reason": "营业状态会改变当天安排。",
            }
        ],
    }
    planner = PlannerAgent(TestModel(call_tools=[], custom_output_text=json.dumps(output, ensure_ascii=False)))

    turn = planner.run_turn(context, allow_fact_requests=True)

    assert turn.action == "need_facts"
    assert turn.fact_requests[0].poi_id == "p1"
    assert planner.call_metrics[0]["step"] == "request_planning_facts"


def test_planner_prompt_contains_date_and_user_request_but_not_removed_fields():
    context = planning_context_from_legacy(
        {
            "destination": "成都",
            "days": 1,
            "day_budget_min": 540,
            "user_request": "周一去成都博物馆",
            "plannable_pois": [{"poi_id": "p1", "name": "成都博物馆"}],
            "must_poi_ids": ["p1"],
        },
        {"destination": "成都", "days": 1, "start_date": "2026-07-20", "constraints": {}},
        [{"poi_id": "p1", "standard_name": "成都博物馆"}],
        [],
    )

    prompt = _planner_prompt(context)

    assert '"start_date":"2026-07-20"' in prompt
    assert "周一去成都博物馆" in prompt
    assert '"travelers"' not in prompt
    assert '"budget_level"' not in prompt


def test_planning_context_truncates_raw_user_material_before_model_prompt():
    context = planning_context_from_legacy(
        {
            "destination": "成都",
            "days": 1,
            "day_budget_min": 540,
            "user_request": "重要要求" + "资料" * 10000,
            "plannable_pois": [{"poi_id": "p1", "name": "武侯祠", "planning_notes": "说明" * 1000}],
            "must_poi_ids": ["p1"],
        },
        {"destination": "成都", "days": 1, "start_date": "2026-07-20", "constraints": {}},
        [{"poi_id": "p1", "standard_name": "武侯祠"}],
        [],
    )

    prompt = _planner_prompt(context)

    assert len(context.user_request) == 6000
    assert "说明" * 121 not in prompt
    assert len(prompt) < 30000


def test_planner_agent_adapter_accepts_json_wrapped_by_provider_text():
    context = planning_context_from_legacy(
        {
            "destination": "成都",
            "days": 1,
            "day_budget_min": 540,
            "plannable_pois": [{"poi_id": "p1", "name": "武侯祠", "estimated_duration_min": 90}],
            "must_poi_ids": ["p1"],
        },
        {"destination": "成都", "days": 1, "constraints": {}},
        [{"poi_id": "p1", "standard_name": "武侯祠", "match_status": "matched"}],
        [],
    )
    raw = '结果如下：\n```json\n{"destination":"成都","days":[{"day":1,"poi_ids":["p1"]}],"unscheduled":[]}\n```'

    result = _validated_blueprint_output(raw, context)

    assert result.days[0].poi_ids == ["p1"]


def test_planner_agent_adapter_marks_omitted_optional_candidate_unscheduled():
    context = planning_context_from_legacy(
        {
            "destination": "成都",
            "days": 1,
            "day_budget_min": 540,
            "plannable_pois": [
                {"poi_id": "p1", "name": "武侯祠", "estimated_duration_min": 90},
                {"poi_id": "p2", "name": "山姆", "estimated_duration_min": 90, "final_decision": "optional"},
            ],
            "must_poi_ids": ["p1"],
            "optional_poi_ids": ["p2"],
        },
        {"destination": "成都", "days": 1, "constraints": {}},
        [
            {"poi_id": "p1", "standard_name": "武侯祠", "match_status": "matched", "final_decision": "include"},
            {"poi_id": "p2", "standard_name": "山姆", "match_status": "matched", "final_decision": "optional"},
        ],
        [],
    )
    raw = '{"destination":"成都","days":[{"day":1,"poi_ids":["p1"]}],"unscheduled":[]}'

    result = _validated_blueprint_output(raw, context)

    assert [(item.poi_id, item.reason_codes) for item in result.unscheduled] == [("p2", ["agent_omitted"])]


def test_planner_prompt_contains_confirmed_planning_preferences():
    context = planning_context_from_legacy(
        {
            "destination": "成都",
            "days": 1,
            "day_budget_min": 540,
            "plannable_pois": [{"poi_id": "p1", "name": "武侯祠"}],
            "must_poi_ids": ["p1"],
        },
        {"destination": "成都", "days": 1, "constraints": {}},
        [{"poi_id": "p1", "standard_name": "武侯祠", "location": {"lng": 104.04, "lat": 30.65}}],
        [],
    ).model_copy(
        update={"planning_preferences": PlanningPreferences(pace="keep_must_places")}
    )

    prompt = _planner_prompt(context)

    assert '"planning_preferences"' in prompt
    assert '"pace":"keep_must_places"' in prompt


def test_planner_prompt_contains_compact_route_facts_and_hotel_anchor():
    context = planning_context_from_legacy(
        {
            "destination": "成都",
            "days": 1,
            "day_budget_min": 540,
            "hotel_anchor": {
                "poi_id": "hotel_anchor",
                "standard_name": "春熙路酒店",
                "location": {"lng": 104.08, "lat": 30.65},
            },
            "plannable_pois": [
                {"poi_id": "p1", "name": "武侯祠", "district": "武侯区"},
                {"poi_id": "p2", "name": "人民公园", "district": "青羊区"},
            ],
        },
        {"destination": "成都", "days": 1, "hotel_name": "春熙路酒店", "constraints": {}},
        [
            {"poi_id": "p1", "standard_name": "武侯祠", "district": "武侯区", "location": {"lng": 104.04, "lat": 30.65}},
            {"poi_id": "p2", "standard_name": "人民公园", "district": "青羊区", "location": {"lng": 104.06, "lat": 30.67}},
        ],
        [
            {
                "origin_poi_id": "p1",
                "destination_poi_id": "p2",
                "duration_min": 18,
                "distance_m": 3200,
                "relation": "same_day_possible",
                "source": "spatial_estimate",
            }
        ],
    )

    prompt = _planner_prompt(context)

    assert '"hotel_anchor"' in prompt
    assert '"standard_name":"春熙路酒店"' in prompt
    assert '"route_facts_summary"' in prompt
    assert '"duration_min":18' in prompt
    assert '"source":"spatial_estimate"' in prompt


def test_planner_agent_records_bounded_structural_failure():
    context = planning_context_from_legacy(
        {
            "destination": "成都",
            "days": 1,
            "day_budget_min": 540,
            "plannable_pois": [{"poi_id": "p1", "name": "武侯祠"}],
            "must_poi_ids": ["p1"],
        },
        {"destination": "成都", "days": 1, "constraints": {}},
        [{"poi_id": "p1", "standard_name": "武侯祠", "location": {"lng": 104.04, "lat": 30.65}}],
        [],
    )
    invalid = _blueprint_for("p2")
    planner = PlannerAgent(TestModel(call_tools=[], custom_output_text=json.dumps(invalid)))

    with pytest.raises(AppError) as caught:
        planner.run(context)

    assert caught.value.code == "planner_agent_invalid_output"
    assert planner.call_metrics[-1]["status"] == "error"
    assert planner.call_metrics[-1]["framework"] == "pydantic_ai"


def test_blueprint_rejects_hotel_rest_used_as_an_afternoon_or_meal_break():
    with pytest.raises(ValueError, match="rest_until"):
        PlanBlueprint.model_validate(
            {
                "days": [
                    {
                        "day": 1,
                        "poi_ids": ["p1", "p2"],
                        "segments": [
                            {"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]},
                            {"kind": "hotel_rest", "rest_until": "afternoon", "reason": "午餐休整"},
                            {"kind": "outing", "segment_time": "afternoon", "poi_ids": ["p2"]},
                        ],
                    }
                ]
            }
        )


def _blueprint_for(poi_id: str) -> dict:
    return {
        "destination": "成都",
        "days": [
            {
                "day": 1,
                "poi_ids": [poi_id],
                "segments": [],
                "meal_slots": [],
                "unscheduled_poi_ids": [],
                "scheduled_roles": {},
                "selected_branch_ids": {},
                "drop_reason_codes": {},
                "risk_tags": [],
            }
        ],
        "unscheduled": [],
        "risk_tags": [],
    }
