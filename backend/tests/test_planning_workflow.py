from app.agents.planning_workflow import run_planning_workflow


def test_run_planning_workflow_replans_after_hard_issue_and_generates_copy():
    class PlanningLLM:
        def __init__(self):
            self.calls = 0

        def json_chat(self, messages, step, temperature=0.2):
            self.calls += 1
            if step == "plan_itinerary_skeleton":
                return {
                    "destination": "成都",
                    "days": [{"day": 1, "poi_ids": ["p2"], "unscheduled_poi_ids": ["p1"], "risk_tags": []}],
                    "unscheduled": [{"poi_id": "p1", "reason_codes": ["time_over_budget"]}],
                    "risk_tags": [],
                }
            if step == "replan_itinerary_skeleton":
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
