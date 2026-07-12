from app.api import routes
from app.core import AppError
from app.schemas.models import PlanningDecisionRequest


def test_sync_precise_transport_edges_rebuilds_selected_branch_route(monkeypatch):
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [
                    {"poi_id": "p1", "name": "喜茶(武侯祠店)", "selected_branch_id": "H2"},
                    {"poi_id": "p2", "name": "东郊记忆"},
                ],
            }
        ]
    }
    runtime_pois = [
        {
            "poi_id": "p1",
            "standard_name": "喜茶（待选择）",
            "location": {"lng": 104.0805, "lat": 30.6572},
            "route_branch_options": [
                {"branch_id": "H2", "name": "喜茶(武侯祠店)", "location": {"lng": 104.047, "lat": 30.645}},
            ],
        },
        {
            "poi_id": "p2",
            "standard_name": "东郊记忆",
            "location": {"lng": 104.121, "lat": 30.641},
        },
    ]
    route_matrix = [
        {"origin_poi_id": "p1", "destination_poi_id": "p2", "mode": "taxi", "duration_min": 55, "distance_m": 19000}
    ]

    def fake_build_route_edge(origin, destination, amap_client, user_profile):
        if origin.get("amap_id") == "H2" and destination.get("poi_id") == "p2":
            return {"mode": "walking", "duration_min": 8, "distance_m": 600}
        return {"mode": "taxi", "duration_min": 55, "distance_m": 19000}

    monkeypatch.setattr(routes, "build_route_edge", fake_build_route_edge)

    routes._sync_precise_transport_edges(itinerary, runtime_pois, route_matrix, {"constraints": {}}, object())

    assert itinerary["days"][0]["items"][0]["transport_to_next"] == {
        "mode": "walking",
        "duration_min": 8,
        "distance_m": 600,
    }


def test_sync_precise_transport_edges_replaces_spatial_estimate_for_selected_pair(monkeypatch):
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [{"poi_id": "p1", "name": "武侯祠"}, {"poi_id": "p2", "name": "锦里"}],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "武侯祠", "location": {"lng": 104.047, "lat": 30.645}},
        {"poi_id": "p2", "standard_name": "锦里", "location": {"lng": 104.05, "lat": 30.646}},
    ]
    route_matrix = [
        {
            "origin_poi_id": "p1",
            "destination_poi_id": "p2",
            "mode": "walking",
            "duration_min": 15,
            "distance_m": 900,
            "source": "spatial_estimate",
        }
    ]
    calls = []

    def fake_build_route_edge(origin, destination, amap_client, user_profile):
        calls.append((origin["poi_id"], destination["poi_id"]))
        return {
            "origin_poi_id": origin["poi_id"],
            "destination_poi_id": destination["poi_id"],
            "mode": "walking",
            "duration_min": 6,
            "distance_m": 420,
            "relation": "same_cluster",
            "source": "amap_direction_api",
        }

    monkeypatch.setattr(routes, "build_route_edge", fake_build_route_edge)

    routes._sync_precise_transport_edges(itinerary, runtime_pois, route_matrix, {"constraints": {}}, object())
    routes._sync_precise_transport_edges(itinerary, runtime_pois, route_matrix, {"constraints": {}}, object())

    assert calls == [("p1", "p2")]
    assert route_matrix[0]["source"] == "amap_direction_api"
    assert itinerary["days"][0]["items"][0]["transport_to_next"]["duration_min"] == 6


def test_extract_time_constraints_marks_explicit_evening_request():
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "九眼桥", "raw_names": ["九眼桥"]},
        {"poi_id": "p2", "standard_name": "成都太古里", "raw_names": ["太古里"]},
    ]

    constraints = routes._extract_time_constraints("晚上去九眼桥，白天逛太古里", "", runtime_pois)

    assert constraints[0] == {
        "poi_id": "p1",
        "name": "九眼桥",
        "preferred_window": "night",
        "strength": "quasi_hard",
        "source_text": "晚上去九眼桥",
    }
    assert constraints[1] == {
        "poi_id": "p2",
        "name": "太古里",
        "preferred_window": "midday",
        "strength": "quasi_hard",
        "source_text": "白天逛太古里",
    }


def test_extract_time_constraints_supports_more_time_window_phrases():
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "IFS", "raw_names": ["IFS"]},
        {"poi_id": "p2", "standard_name": "安顺廊桥", "raw_names": ["安顺廊桥"]},
    ]

    constraints = routes._extract_time_constraints("先去IFS，傍晚去安顺廊桥", "", runtime_pois)

    assert constraints == [
        {
            "poi_id": "p1",
            "name": "IFS",
            "preferred_window": "morning",
            "strength": "quasi_hard",
            "source_text": "先去IFS",
        },
        {
            "poi_id": "p2",
            "name": "安顺廊桥",
            "preferred_window": "evening",
            "strength": "quasi_hard",
            "source_text": "傍晚去安顺廊桥",
        }
    ]


def test_assert_publishable_allows_quality_deviation():
    routes._assert_publishable(
        {"passed": False, "issues": [{"type": "meal_time_invalid", "message": "午餐过晚"}]},
        run_id="run-1",
    )


def test_submit_planning_decision_does_not_consume_choice_when_replan_fails(monkeypatch):
    events = []

    class FakeStore:
        def get_session(self, session_id):
            return {"session_id": session_id, "raw_input": "", "notes": "", "user_profile": {}}

        def preview_planning_decision(self, session_id, intervention_id, choice_id):
            events.append("preview")
            return {"intervention_id": intervention_id, "choice_id": choice_id, "choice_label": "保留夜间"}

        def resolve_planning_intervention(self, session_id, intervention_id, choice_id):
            events.append("resolve")

    def fail_plan(*args, **kwargs):
        assert kwargs["provisional_planning_decision"]["choice_id"] == "keep_time"
        raise AppError("蓝图无效", code="llm_invalid_plan_skeleton", step="plan_day_blueprint")

    monkeypatch.setattr(routes, "store", FakeStore())
    monkeypatch.setattr(routes, "_plan_session", fail_plan)

    response = routes.submit_planning_decision(
        "session-1",
        PlanningDecisionRequest(intervention_id="intervention-1", choice_id="keep_time"),
    )

    assert response["ok"] is False
    assert events == ["preview"]


def test_submit_planning_decision_resolves_choice_after_successful_replan(monkeypatch):
    events = []

    class FakeStore:
        def get_session(self, session_id):
            return {"session_id": session_id, "raw_input": "", "notes": "", "user_profile": {}}

        def preview_planning_decision(self, session_id, intervention_id, choice_id):
            events.append("preview")
            return {"intervention_id": intervention_id, "choice_id": choice_id, "choice_label": "保留夜间"}

        def resolve_planning_intervention(self, session_id, intervention_id, choice_id):
            events.append("resolve")

    def successful_plan(*args, **kwargs):
        events.append("plan")
        return {"status": "completed"}, {"verify_itinerary": "done"}

    monkeypatch.setattr(routes, "store", FakeStore())
    monkeypatch.setattr(routes, "_plan_session", successful_plan)

    response = routes.submit_planning_decision(
        "session-1",
        PlanningDecisionRequest(intervention_id="intervention-1", choice_id="keep_time"),
    )

    assert response["ok"] is True
    assert events == ["preview", "plan", "resolve"]
