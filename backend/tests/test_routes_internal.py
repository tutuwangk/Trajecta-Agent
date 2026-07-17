import pytest
from fastapi import BackgroundTasks

from app.api import routes
from app.core import AppError
from app.schemas.models import PlanningDecisionRequest
from app.services.database import SQLiteStore


def test_hotel_anchor_prefers_verified_hotel_poi_over_name_geocode():
    class FakeAmap:
        def search_poi(self, keyword, city):
            assert keyword == "全季酒店（四川大学店）"
            assert city == "成都"
            return [
                {
                    "id": "hotel-correct",
                    "name": "全季酒店(成都四川大学店)",
                    "type": "住宿服务;宾馆酒店;经济型连锁酒店",
                    "cityname": "成都市",
                    "adname": "武侯区",
                    "address": "新南路118号",
                    "location": "104.075,30.635",
                }
            ]

        def geocode(self, address, city):
            raise AssertionError("酒店名称不应进入地址地理编码")

    anchor = routes._hotel_anchor(
        {"hotel_name": "全季酒店（四川大学店）", "destination": "成都"},
        FakeAmap(),
    )

    assert anchor["amap_id"] == "hotel-correct"
    assert anchor["address"] == "新南路118号"
    assert anchor["district"] == "武侯区"
    assert anchor["location"] == {"lng": 104.075, "lat": 30.635}
    assert anchor["confidence"] == "verified"


def test_hotel_anchor_rejects_unrelated_named_candidate_instead_of_accepting_any_coordinate():
    class FakeAmap:
        def search_poi(self, keyword, city):
            return [
                {
                    "id": "wrong",
                    "name": "全季酒店(都江堰景区店)",
                    "type": "住宿服务;宾馆酒店",
                    "cityname": "成都市",
                    "adname": "都江堰市",
                    "address": "远处地址",
                    "location": "103.62,30.99",
                }
            ]

        def geocode(self, address, city):
            raise AssertionError("模糊酒店名称不应回退为地理编码")

    assert routes._hotel_anchor({"hotel_name": "全季酒店（四川大学店）", "destination": "成都"}, FakeAmap()) is None


def test_failed_planning_run_response_exposes_sanitized_blockers():
    response = routes._planning_run_response(
        {
            "id": "run-1",
            "session_id": "session-1",
            "status": "failed",
            "stage": "plan",
            "error_code": "plan_capacity_unresolved",
            "error_message": "路线过满",
            "debug": {"blockers": [{"message": "第 2 天超过上限", "action_hint": "减少一个地点"}]},
        }
    )

    assert response["blockers"] == [{"message": "第 2 天超过上限", "action_hint": "减少一个地点"}]


def test_resume_planning_run_reuses_existing_run_id(monkeypatch):
    enqueued = []

    class FakeStore:
        def get_session(self, session_id):
            return {"session_id": session_id, "raw_input": "", "notes": "", "user_profile": {}}

        def get_planning_run(self, run_id):
            return {
                "id": run_id,
                "session_id": "session-1",
                "status": "running",
                "stage": "fact_snapshot",
                "checkpoint": "fact_snapshot",
                "result_status": "",
            }

    monkeypatch.setattr(routes, "store", FakeStore())
    monkeypatch.setattr(
        routes,
        "_enqueue_planning",
        lambda background_tasks, run_id, session_id, session: enqueued.append((run_id, session_id)),
    )

    response = routes.resume_planning_run("session-1", "run-1", BackgroundTasks())

    assert response["ok"] is True
    assert response["data"] == {"status": "running", "run_id": "run-1", "stage": "fact_snapshot"}
    assert enqueued == [("run-1", "session-1")]


def test_existing_run_replays_frozen_input_and_keeps_its_own_result(monkeypatch, tmp_path):
    sqlite_store = SQLiteStore(str(tmp_path / "frozen-run.sqlite3"))
    session_id = sqlite_store.create_session("当前输入", "当前资料", {"destination": "重庆", "days": 2})
    frozen = {
        "schema_version": 1,
        "session": {
            "raw_input": "冻结输入",
            "notes": "冻结资料",
            "user_profile": {"destination": "成都", "days": 1},
        },
        "pois": [{"poi_id": "p1"}],
        "planning_decisions": [],
        "revision_intent": {},
        "provisional_planning_decision": {},
    }
    run_id = sqlite_store.start_planning_run(session_id, input_snapshot=frozen)
    captured = {}

    def fake_execute(received_session_id, input_snapshot, **kwargs):
        captured.update(input_snapshot)
        return (
            {
                "status": "completed",
                "runtime_pois": [{"poi_id": "p1"}],
                "route_matrix": [],
                "itinerary": {"destination": "成都"},
                "verification": {"result_status": "verified"},
                "_run_debug": {"attempt_count": 1, "metrics": {}},
            },
            {},
        )

    monkeypatch.setattr(routes, "store", sqlite_store)
    monkeypatch.setattr(routes, "_execute_plan_session", fake_execute)

    routes._plan_session(
        session_id,
        {"raw_input": "当前输入", "notes": "当前资料", "user_profile": {"destination": "重庆", "days": 2}},
        existing_run_id=run_id,
    )

    stored = sqlite_store.get_planning_run(run_id)
    replayed = routes._planning_run_response(stored)
    assert captured["session"]["user_profile"]["destination"] == "成都"
    assert replayed["itinerary"]["destination"] == "成都"
    assert replayed["runtime_pois"] == [{"poi_id": "p1"}]


def test_release_gate_resume_reuses_compiled_artifacts_without_planner_or_map_calls(monkeypatch):
    from app.adapters.legacy import fact_snapshot_from_legacy
    from app.domain.itinerary import ReleaseDecision

    runtime_pois = [
        {
            "poi_id": "p1",
            "standard_name": "武侯祠",
            "match_status": "matched",
            "location": {"lng": 104.04, "lat": 30.65},
        }
    ]
    facts = fact_snapshot_from_legacy("成都", runtime_pois, [])
    decision = ReleaseDecision(status="verified", reasons=["事实已核验。"])
    itinerary = {
        "destination": "成都",
        "days": [{"day": 1, "items": [{"poi_id": "p1", "name": "武侯祠", "arrival_time": "10:00", "duration_min": 90}]}],
        "global_risks": [],
    }
    verification = {
        "passed": True,
        "publishable": True,
        "issues": [],
        "blocking_issues": [],
        "quality_issues": [],
        "result_status": "verified",
        "release_decision": decision.model_dump(mode="json"),
    }
    run = {
        "checkpoint": "release_gate",
        "attempt_count": 1,
        "fact_snapshot": facts.model_dump(mode="json"),
        "blueprint": {"destination": "成都", "days": [{"day": 1, "poi_ids": ["p1"]}]},
        "release_decision": decision.model_dump(mode="json"),
        "validation_report": verification,
        "result_snapshot": {
            "runtime_pois": runtime_pois,
            "route_matrix": [],
            "itinerary": itinerary,
            "verification": verification,
        },
    }

    class CopyLLM:
        call_metrics = []

        def json_chat(self, messages, step, temperature=0.3):
            return {"route_summary": {"main_message": "已复用核验路线。"}, "days": [], "global_risks": []}

    class Store:
        def save_itinerary(self, *args):
            self.saved = args

    fake_store = Store()
    monkeypatch.setattr(routes, "store", fake_store)
    monkeypatch.setattr(routes, "default_copy_llm_client", lambda: CopyLLM())
    monkeypatch.setattr(routes, "default_planner_agent", lambda: (_ for _ in ()).throw(AssertionError("planner called")))
    monkeypatch.setattr(routes, "default_duration_llm_client", lambda: (_ for _ in ()).throw(AssertionError("duration called")))
    monkeypatch.setattr(routes, "default_amap_client", lambda: (_ for _ in ()).throw(AssertionError("amap called")))

    data, step_status = routes._execute_plan_session(
        "session-1",
        {
            "session": {
                "raw_input": "目的地：成都",
                "notes": "武侯祠",
                "user_profile": {"destination": "成都", "days": 1, "start_date": "2026-07-20", "constraints": {}},
            },
            "pois": [],
        },
        resume_run=run,
    )

    assert data["status"] == "completed"
    assert data["itinerary"]["route_summary"]["main_message"] == "已复用核验路线。"
    assert data["_run_debug"]["resumed_from_checkpoint"] == "release_gate"
    assert step_status["plan_itinerary"] == "reused"


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


def test_adjacent_outing_segments_still_fetch_precise_transport(monkeypatch):
    itinerary = {
        "days": [
            {
                "day": 1,
                "segments": [
                    {"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]},
                    {"kind": "outing", "segment_time": "afternoon", "poi_ids": ["p2"]},
                ],
                "items": [{"poi_id": "p1", "name": "武侯祠"}, {"poi_id": "p2", "name": "人民公园"}],
            }
        ]
    }
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "武侯祠", "location": {"lng": 104.047, "lat": 30.645}},
        {"poi_id": "p2", "standard_name": "人民公园", "location": {"lng": 104.06, "lat": 30.67}},
    ]
    route_matrix = [
        {
            "origin_poi_id": "p1",
            "destination_poi_id": "p2",
            "mode": "taxi",
            "duration_min": 22,
            "source": "spatial_estimate",
        }
    ]
    calls = []

    def fake_build_route_edge(origin, destination, amap_client, user_profile):
        calls.append((origin["poi_id"], destination["poi_id"]))
        return {
            "origin_poi_id": origin["poi_id"],
            "destination_poi_id": destination["poi_id"],
            "mode": "taxi",
            "duration_min": 12,
            "distance_m": 2500,
            "relation": "same_day_possible",
            "source": "amap_direction_api",
        }

    monkeypatch.setattr(routes, "build_route_edge", fake_build_route_edge)

    routes._sync_precise_transport_edges(itinerary, runtime_pois, route_matrix, {"constraints": {}}, object())

    assert calls == [("p1", "p2")]
    assert route_matrix[0]["source"] == "amap_direction_api"
    assert itinerary["days"][0]["items"][0]["transport_to_next"]["duration_min"] == 12


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


def test_extract_time_constraints_supports_place_before_night_request():
    runtime_pois = [
        {"poi_id": "p1", "standard_name": "九眼桥", "raw_names": ["九眼桥"]},
    ]

    constraints = routes._extract_time_constraints("九眼桥安排在晚上", "", runtime_pois)

    assert constraints == [
        {
            "poi_id": "p1",
            "name": "九眼桥",
            "preferred_window": "night",
            "strength": "quasi_hard",
            "source_text": "九眼桥安排在晚上",
        }
    ]


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


def test_extract_time_constraints_promotes_objective_appointment_to_hard_constraint():
    constraints = routes._extract_time_constraints(
        "成都博物馆预约了14:00，不能迟到",
        "",
        [{"poi_id": "p1", "standard_name": "成都博物馆"}],
    )

    assert constraints[0]["appointment_time"] == "14:00"
    assert constraints[0]["strength"] == "hard"


def test_assert_publishable_blocks_invalid_meal_time():
    with pytest.raises(AppError) as error:
        routes._assert_publishable(
            {"passed": False, "issues": [{"type": "meal_time_invalid", "message": "午餐过晚"}]},
            run_id="run-1",
        )

    assert error.value.code == "itinerary_publish_blocked"


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
