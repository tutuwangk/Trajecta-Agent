import pytest
from pydantic import ValidationError

from app.adapters.legacy import fact_snapshot_from_legacy, planning_context_from_legacy
from app.domain.facts import RouteEdge
from app.domain.itinerary import CompiledItinerary
from app.domain.planning import PlanBlueprint, PlannerTurn, TimeConstraint


def _context():
    legacy = {
        "destination": "成都",
        "days": 1,
        "day_budget_min": 540,
        "plannable_pois": [{"poi_id": "p1", "name": "武侯祠", "estimated_duration_min": 90}],
        "must_poi_ids": ["p1"],
        "optional_poi_ids": [],
    }
    runtime = [
        {
            "poi_id": "p1",
            "standard_name": "武侯祠",
            "match_status": "matched",
            "location": {"lng": 104.04, "lat": 30.65},
        }
    ]
    profile = {"destination": "成都", "days": 1, "constraints": {}}
    return planning_context_from_legacy(legacy, profile, runtime, [])


def test_domain_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        RouteEdge(
            origin_poi_id="p1",
            destination_poi_id="p2",
            source="unknown",
            confidence="unavailable",
            unexpected=True,
        )

    with pytest.raises(ValidationError):
        CompiledItinerary.model_validate(
            {
                "destination": "成都",
                "days": [{"day": 1, "items": [], "legacy_unknown": True}],
            }
        )


def test_spatial_route_cannot_be_marked_verified():
    with pytest.raises(ValidationError):
        RouteEdge(
            origin_poi_id="p1",
            destination_poi_id="p2",
            duration_min=10,
            source="spatial_estimate",
            confidence="verified",
        )


def test_blueprint_rejects_unknown_candidate():
    blueprint = PlanBlueprint.model_validate(
        {
            "destination": "成都",
            "days": [{"day": 1, "poi_ids": ["p2"]}],
            "unscheduled": [{"poi_id": "p1", "reason_codes": ["far_detour"]}],
        }
    )

    with pytest.raises(ValueError, match="unknown candidate"):
        blueprint.validate_against(_context())


def test_fact_snapshot_marks_spatial_edges_as_degraded():
    snapshot = fact_snapshot_from_legacy(
        "成都",
        [
            {"poi_id": "p1", "standard_name": "A", "match_status": "matched", "location": {"lng": 1, "lat": 1}},
            {"poi_id": "p2", "standard_name": "B", "match_status": "matched", "location": {"lng": 2, "lat": 2}},
        ],
        [
            {
                "origin_poi_id": "p1",
                "destination_poi_id": "p2",
                "duration_min": 20,
                "distance_m": 3000,
                "source": "spatial_estimate",
            }
        ],
    )

    assert snapshot.degraded is True
    assert snapshot.route_edges[0].confidence == "estimated"


def test_planner_turn_requires_exactly_one_bounded_action_payload():
    turn = PlannerTurn.model_validate(
        {
            "action": "need_facts",
            "fact_requests": [
                {
                    "request_id": "hours-p1",
                    "poi_id": "p1",
                    "kind": "opening_hours",
                    "visit_date": "2026-07-20",
                    "decision_reason": "周一开放状态会决定是否换天。",
                }
            ],
        }
    )

    assert turn.fact_requests[0].visit_date.isoformat() == "2026-07-20"
    with pytest.raises(ValidationError):
        PlannerTurn.model_validate({"action": "need_facts", "fact_requests": []})


def test_typed_time_constraint_accepts_objective_appointment():
    constraint = TimeConstraint(
        poi_id="p1",
        name="成都博物馆",
        appointment_time="14:00",
        preferred_window="afternoon",
        strength="hard",
        source_text="成都博物馆预约了14:00",
    )

    assert constraint.appointment_time == "14:00"
