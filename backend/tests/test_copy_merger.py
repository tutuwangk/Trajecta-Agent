from app.agents.reviser import generate_copy
from app.planning.copy_merger import route_fact_fingerprint


class WrongDayCopyLLM:
    def json_chat(self, messages, step, temperature=0.2):
        return {
            "route_summary": {"main_message": "已为你整理出 5 天路线。"},
            "days": [
                {
                    "day": 1,
                    "summary": "第一天慢慢逛。",
                    "items": [{"poi_id": "p1", "reason": "顺路安排。"}],
                    "removed_pois": [],
                    "risk_notes": [],
                }
            ],
            "global_risks": [],
        }


def test_copy_generation_cannot_change_route_facts_and_corrects_day_count_drift():
    itinerary = {
        "destination": "成都",
        "days": [
            {
                "day": 1,
                "items": [
                    {
                        "poi_id": "p1",
                        "name": "武侯祠",
                        "arrival_time": "09:30",
                        "duration_min": 120,
                    }
                ],
                "removed_pois": [],
                "meal_breaks": [],
                "segments": [],
            }
        ],
        "global_risks": [],
        "revision_notes": [],
    }
    before = route_fact_fingerprint(itinerary)

    result = generate_copy(
        itinerary,
        {"days": [], "hard_issues": [], "soft_issues": [], "global_risk_tags": []},
        {"destination": "成都", "days": 1, "route_goal": "balanced", "preferences": {}},
        WrongDayCopyLLM(),
    )

    assert route_fact_fingerprint(result) == before
    assert "1 天路线" in result["route_summary"]["main_message"]
