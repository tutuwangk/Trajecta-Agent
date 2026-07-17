import json

from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from app.agents.planner_agent import PlannerAgent
from app.api import routes
from app.services.cache_service import CacheService
from app.services.database import SQLiteStore
from main import create_app


class CopyLLM:
    call_metrics = []

    def json_chat(self, messages, step, temperature=0.2):
        if step == "review_itinerary_soft_quality":
            return {"issues": []}
        if step == "generate_itinerary_copy":
            return {
                "route_summary": {"main_message": "已为你整理出 1 天成都路线。"},
                "days": [
                    {
                        "day": 1,
                        "summary": "上午看历史，中午吃川菜。",
                        "items": [
                            {"poi_id": "amap_A", "reason": "先安排必去地点。", "risk_notes": []},
                            {"poi_id": "amap_B", "reason": "顺路安排午餐。", "risk_notes": []},
                        ],
                        "removed_pois": [],
                        "risk_notes": [],
                    }
                ],
                "global_risks": [],
            }
        raise AssertionError(step)


class FakeAmap:
    def geocode(self, *args, **kwargs):
        return None


def test_full_api_flow_runs_pydantic_ai_agent_and_publishes_verified_itinerary(monkeypatch, tmp_path):
    store = SQLiteStore(str(tmp_path / "agent-flow.sqlite3"))
    recorded_stages = []
    original_update_planning_run = store.update_planning_run

    def record_update(run_id, *, status, stage, **kwargs):
        recorded_stages.append(stage)
        return original_update_planning_run(run_id, status=status, stage=stage, **kwargs)

    monkeypatch.setattr(store, "update_planning_run", record_update)
    monkeypatch.setattr(routes, "store", store)
    monkeypatch.setattr(routes, "duration_cache", CacheService(store))
    monkeypatch.setattr(
        routes,
        "extract_ugc_items",
        lambda text, llm: [{"mentioned_pois": [{"raw_name": "武侯祠"}, {"raw_name": "陈麻婆豆腐"}]}],
    )
    monkeypatch.setattr(
        routes,
        "extract_poi_names",
        lambda items, text: [
            {"raw_name": "武侯祠", "possible_category": "museum", "experience_tags": ["历史"]},
            {"raw_name": "陈麻婆豆腐", "possible_category": "restaurant", "experience_tags": ["美食"]},
        ],
    )
    monkeypatch.setattr(
        routes,
        "ground_pois",
        lambda raw, profile, amap: [
            {
                "raw_name": "武侯祠",
                "standard_name": "武侯祠",
                "amap_id": "A",
                "location": {"lng": 104.047, "lat": 30.647},
                "city": "成都",
                "district": "武侯区",
                "category_normalized": "museum",
                "match_status": "matched",
            },
            {
                "raw_name": "陈麻婆豆腐",
                "standard_name": "陈麻婆豆腐",
                "amap_id": "B",
                "location": {"lng": 104.065, "lat": 30.67},
                "city": "成都",
                "district": "青羊区",
                "category_normalized": "restaurant",
                "match_status": "matched",
            },
        ],
    )
    monkeypatch.setattr(routes, "default_llm_client", lambda: object())
    monkeypatch.setattr(routes, "default_duration_llm_client", lambda: object())
    monkeypatch.setattr(routes, "default_copy_llm_client", lambda: CopyLLM())
    monkeypatch.setattr(routes, "default_amap_client", lambda: FakeAmap())
    monkeypatch.setattr(routes, "estimate_visit_durations", lambda pois, llm, cache=None: pois)
    monkeypatch.setattr(
        routes,
        "build_route_edge",
        lambda origin, destination, amap, profile: {
            "origin_poi_id": origin["poi_id"],
            "destination_poi_id": destination["poi_id"],
            "mode": "taxi",
            "duration_min": 18,
            "distance_m": 3200,
            "relation": "same_day_possible",
            "source": "amap_direction_api",
        },
    )
    blueprint = {
        "destination": "成都",
        "days": [
            {
                "day": 1,
                "poi_ids": ["amap_A", "amap_B"],
                "segments": [],
                "scheduled_roles": {"amap_A": "anchor_visit", "amap_B": "meal_stop"},
                "meal_slots": [
                    {"slot": "lunch", "source": "poi", "poi_id": "amap_B"},
                    {"slot": "dinner", "source": "fallback_nearby"},
                ],
                "unscheduled_poi_ids": [],
                "drop_reason_codes": {},
                "risk_tags": [],
            }
        ],
        "unscheduled": [],
        "risk_tags": [],
    }
    planner = PlannerAgent(TestModel(call_tools=[], custom_output_text=json.dumps(blueprint)))
    monkeypatch.setattr(routes, "default_planner_agent", lambda: planner)

    with TestClient(create_app()) as client:
        created = client.post(
            "/sessions",
            json={
                "raw_input": "成都一日游，武侯祠必去",
                "notes": "武侯祠和陈麻婆豆腐",
                "user_profile": {
                    "destination": "成都",
                    "days": 1,
                    "route_goal": "balanced",
                    "constraints": {"physical_intensity": "medium", "must_visit": ["武侯祠"]},
                },
            },
        ).json()
        session_id = created["data"]["session_id"]
        assert client.post(f"/sessions/{session_id}/recognize-places").json()["ok"] is True

        started = client.post(
            f"/sessions/{session_id}/plan",
            headers={"Idempotency-Key": "full-flow-1"},
        ).json()
        replayed = client.post(
            f"/sessions/{session_id}/plan",
            headers={"Idempotency-Key": "full-flow-1"},
        ).json()
        session = client.get(f"/sessions/{session_id}").json()["data"]

    assert started["data"]["status"] == "running"
    assert replayed["data"]["status"] == "completed"
    assert session["latest_planning_run"]["status"] == "completed"
    assert session["latest_planning_run"]["result_status"] == "verified"
    assert session["latest_planning_run"]["metrics"]["hard_constraint_pass_rate"] == 1.0
    assert session["latest_planning_run"]["metrics"]["final_publish_success_rate"] == 1.0
    assert session["latest_planning_run"]["metrics"]["copy_fact_drift_count"] == 0
    assert session["itinerary_state"]["itinerary"]["result_status"] == "verified"
    assert planner.call_metrics[0]["framework"] == "pydantic_ai"
    assert len(planner.call_metrics) == 1
    expected_order = ["understanding", "grounding", "fact_snapshot", "blueprint", "compiling", "release_gate", "copywriting", "completed"]
    positions = [recorded_stages.index(stage) for stage in expected_order]
    assert positions == sorted(positions)
