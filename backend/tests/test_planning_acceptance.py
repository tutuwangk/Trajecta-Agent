from app.agents.planning_workflow import run_planning_workflow


class ChengduPlanningLLM:
    def json_chat(self, messages, step, temperature=0.2):
        assert step in {"plan_itinerary_blueprint", "replan_itinerary_blueprint"}
        return {
            "destination": "成都",
            "days": [
                {
                    "day": 1,
                    "poi_ids": ["p1", "p2"],
                    "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p2"}],
                    "scheduled_roles": {"p2": "meal_stop"},
                    "unscheduled_poi_ids": [],
                    "risk_tags": [],
                },
                {
                    "day": 2,
                    "poi_ids": ["p3"],
                    "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "fallback_nearby"}],
                    "unscheduled_poi_ids": [],
                    "risk_tags": [],
                },
                {"day": 3, "poi_ids": ["p4"], "unscheduled_poi_ids": [], "risk_tags": []},
                {
                    "day": 4,
                    "segments": [
                        {"kind": "outing", "segment_time": "morning", "poi_ids": ["p5", "p7"]},
                        {"kind": "hotel_rest", "duration_min": 180, "reason": "下午休息"},
                        {"kind": "outing", "segment_time": "night", "poi_ids": ["p6"]},
                    ],
                    "meal_slots": [{"slot": "lunch", "requirement": "required", "source": "poi", "poi_id": "p7"}],
                    "scheduled_roles": {"p7": "meal_stop"},
                    "unscheduled_poi_ids": [],
                    "risk_tags": [],
                },
            ],
            "unscheduled": [],
            "risk_tags": [],
        }


class ChengduCopyLLM:
    def json_chat(self, messages, step, temperature=0.2):
        if step == "review_itinerary_soft_quality":
            return {"issues": []}
        assert step == "generate_itinerary_copy"
        return {"route_summary": {"main_message": "成都四日路线已整理。"}, "days": [], "global_risks": []}


def test_fixed_chengdu_route_is_complete_and_publishable_across_repeated_runs():
    user_profile = {"destination": "成都", "days": 4, "constraints": {"physical_intensity": "high"}}
    runtime_pois = [
        _poi("p1", "武侯祠", 90),
        _poi("p2", "钵钵鸡", 75, category="restaurant", meal_capability="lunch_dinner"),
        _poi("p3", "成都博物馆", 240, category="museum"),
        _poi("p4", "东郊记忆", 120),
        _poi("p5", "成都太古里", 60),
        _poi("p7", "川菜馆", 60, category="restaurant", meal_capability="lunch_dinner"),
        _poi("p6", "九眼桥夜景", 90, time_suitability=["evening", "night"]),
    ]
    route_matrix = [
        _edge("p1", "p2", 15),
        _edge("p5", "p7", 15),
        _edge("p7", "p6", 20),
    ]
    time_constraints = [
        {"poi_id": "p6", "preferred_window": "evening", "strength": "quasi_hard", "source_text": "晚上去九眼桥"}
    ]

    results = [
        run_planning_workflow(
            user_profile,
            runtime_pois,
            route_matrix,
            ChengduPlanningLLM(),
            ChengduCopyLLM(),
            time_constraints=time_constraints,
        )
        for _ in range(3)
    ]

    for itinerary, verification, _debug in results:
        assert verification["passed"] is True
        assert [day["day"] for day in itinerary["days"]] == [1, 2, 3, 4]
        for day in itinerary["days"]:
            for meal in day.get("meal_breaks") or []:
                if meal.get("slot") == "lunch":
                    assert "11:30" <= meal["start_time"] <= "14:00"
        day_one_lunch = next(item for item in itinerary["days"][0]["items"] if item["poi_id"] == "p2")
        assert day_one_lunch["meal_roles"] == ["lunch"]
        assert "11:30" <= day_one_lunch["arrival_time"] <= "14:00"
        night_item = next(item for item in itinerary["days"][3]["items"] if item["poi_id"] == "p6")
        assert night_item["arrival_time"] >= "17:30"


def _poi(
    poi_id: str,
    name: str,
    duration_min: int,
    *,
    category: str = "attraction",
    meal_capability: str = "none",
    time_suitability: list[str] | None = None,
) -> dict:
    return {
        "poi_id": poi_id,
        "standard_name": name,
        "match_status": "matched",
        "final_decision": "include",
        "category": category,
        "estimated_duration_min": duration_min,
        "planning_semantics": {
            "experience_type": "full_meal" if category == "restaurant" else "daytime_visit",
            "meal_capability": meal_capability,
            "time_suitability": time_suitability or ["morning", "afternoon"],
        },
    }


def _edge(origin: str, destination: str, duration_min: int) -> dict:
    return {
        "origin_poi_id": origin,
        "destination_poi_id": destination,
        "mode": "taxi",
        "duration_min": duration_min,
        "distance_m": duration_min * 300,
        "source": "amap_direction_api",
    }
