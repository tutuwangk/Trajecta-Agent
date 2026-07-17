import json

import pytest
from pydantic_ai.models.test import TestModel

from app.adapters.legacy import fact_snapshot_from_legacy, validation_report_from_legacy
from app.agents.planner_agent import PlannerAgent
from app.agents.planning_workflow import run_planning_workflow
from app.core import AppError
from app.planning.release_gate import ReleaseGate, apply_release_decision
from app.schemas.models import UserProfile


class CopyLLM:
    call_metrics = []

    def json_chat(self, messages, step, temperature=0.2):
        if step == "review_itinerary_soft_quality":
            return {"issues": []}
        if step == "generate_itinerary_copy":
            return {"route_summary": {"main_message": "路线已整理。"}, "days": [], "global_risks": []}
        raise AssertionError(step)


def _poi(poi_id, name, *, duration=90, amap_id=None):
    offset = int(poi_id[-1]) if poi_id[-1].isdigit() else 1
    return {
        "poi_id": poi_id,
        "amap_id": amap_id or poi_id,
        "standard_name": name,
        "match_status": "matched",
        "final_decision": "include",
        "estimated_duration_min": duration,
        "location": {"lng": 104.04 + offset / 1000, "lat": 30.64 + offset / 1000},
    }


def _edge(origin, destination, *, source="amap_direction_api", duration=15):
    return {
        "origin_poi_id": origin,
        "destination_poi_id": destination,
        "mode": "taxi",
        "duration_min": duration,
        "distance_m": 2500,
        "source": source,
    }


def _blueprint(poi_ids, *, meals=None):
    return {
        "destination": "成都",
        "days": [
            {
                "day": 1,
                "poi_ids": poi_ids,
                "segments": [],
                "scheduled_roles": {poi_id: "anchor_visit" for poi_id in poi_ids},
                "meal_slots": meals or [],
                "unscheduled_poi_ids": [],
                "drop_reason_codes": {},
                "risk_tags": [],
            }
        ],
        "unscheduled": [],
        "risk_tags": [],
    }


SCENARIOS = [
    pytest.param([_poi("p1", "武侯祠")], [], _blueprint(["p1"]), {}, None, "verified", id="01-single-verified"),
    pytest.param(
        [_poi("p1", "武侯祠"), _poi("p2", "人民公园")],
        [_edge("p1", "p2")],
        _blueprint(["p1", "p2"], meals=[{"slot": "lunch", "source": "fallback_nearby"}]),
        {},
        None,
        "verified",
        id="02-precise-route-verified",
    ),
    pytest.param(
        [_poi("p1", "武侯祠"), _poi("p2", "人民公园")],
        [_edge("p1", "p2", source="spatial_estimate")],
        _blueprint(["p1", "p2"], meals=[{"slot": "lunch", "source": "fallback_nearby"}]),
        {},
        None,
        "degraded",
        id="03-amap-degraded",
    ),
    pytest.param(
        [_poi("p1", "武侯祠"), _poi("p2", "人民公园")],
        [],
        _blueprint(["p1", "p2"], meals=[{"slot": "lunch", "source": "fallback_nearby"}]),
        {},
        None,
        "failed",
        id="04-missing-route-failed",
    ),
    pytest.param(
        [_poi("p1", "欢乐谷", duration=900)],
        [],
        _blueprint(
            ["p1"],
            meals=[
                {"slot": "lunch", "source": "inside_poi", "within_poi_id": "p1"},
                {"slot": "dinner", "source": "inside_poi", "within_poi_id": "p1"},
            ],
        ),
        {},
        None,
        "failed",
        id="05-extreme-duration-failed",
    ),
    pytest.param([_poi("p1", "秦岭景区（暂停营业）")], [], _blueprint(["p1"]), {}, None, "failed", id="06-paused-failed"),
    pytest.param(
        [_poi("p1", "秦岭景区", amap_id="same"), _poi("p2", "秦岭景区", amap_id="same")],
        [_edge("p1", "p2")],
        _blueprint(["p1", "p2"], meals=[{"slot": "lunch", "source": "fallback_nearby"}]),
        {},
        None,
        "verified",
        id="07-duplicate-deduplicated",
    ),
    pytest.param(
        [_poi("p1", "武侯祠", duration=60), _poi("p2", "锦里", duration=60), _poi("p3", "人民公园", duration=60)],
        [_edge("p1", "p2", duration=8), _edge("p2", "p3", duration=12)],
        _blueprint(["p1", "p2", "p3"], meals=[{"slot": "lunch", "source": "fallback_nearby"}]),
        {},
        None,
        "verified",
        id="08-three-stop-verified",
    ),
    pytest.param(
        [_poi("p1", "成都博物馆", duration=240)],
        [],
        _blueprint(["p1"], meals=[{"slot": "lunch", "source": "inside_poi", "within_poi_id": "p1"}]),
        {},
        None,
        "verified",
        id="09-inside-lunch-verified",
    ),
    pytest.param(
        [_poi("p1", "武侯祠")],
        [],
        _blueprint(["p1"]),
        {"constraints": {"avoid_visit": ["武侯祠"]}},
        None,
        "failed",
        id="10-avoid-constraint-failed",
    ),
]


@pytest.mark.parametrize("pois,routes,blueprint,profile_patch,hotel_anchor,expected", SCENARIOS)
def test_simulated_real_full_agent_flow_has_explicit_terminal_outcome(
    pois, routes, blueprint, profile_patch, hotel_anchor, expected
):
    profile = {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}}
    profile.update(profile_patch)
    planner = PlannerAgent(TestModel(call_tools=[], custom_output_text=json.dumps(blueprint)))

    def release_candidate(itinerary, verification):
        facts = fact_snapshot_from_legacy("成都", pois, routes, hotel_anchor=hotel_anchor)
        report = validation_report_from_legacy(verification, fact_version=facts.version)
        decision = ReleaseGate().decide(
            itinerary=itinerary,
            report=report,
            facts=facts,
            user_profile=UserProfile.model_validate(profile),
        )
        released = apply_release_decision(verification, decision)
        if decision.status == "failed":
            raise AppError("模拟场景未通过发布门禁。", code="itinerary_publish_blocked", step="release_gate")
        return released

    try:
        _, verification, _ = run_planning_workflow(
            profile,
            pois,
            routes,
            planner,
            CopyLLM(),
            hotel_anchor=hotel_anchor,
            release_candidate=release_candidate,
        )
        actual = verification["result_status"]
    except AppError:
        actual = "failed"

    assert actual == expected
