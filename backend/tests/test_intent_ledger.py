from app.agents.intent_ledger import build_intent_ledger, build_planning_envelope


def _pois():
    return [
        {
            "poi_id": "museum",
            "raw_name": "故宫",
            "standard_name": "故宫博物院",
            "category_normalized": "museum",
            "user_override": "must_include",
        },
        {
            "poi_id": "duck",
            "raw_name": "便宜坊烤鸭",
            "standard_name": "便宜坊烤鸭店",
            "category_normalized": "restaurant",
            "planning_semantics": {"meal_capability": "lunch_dinner"},
            "contexts": ["想吃一次便宜坊烤鸭"],
        },
    ]


def test_intent_ledger_preserves_fixed_anchor_day_assignment_and_explicit_meal():
    ledger = build_intent_ledger(
        user_profile={"days": 2, "constraints": {"must_visit": ["故宫"]}},
        runtime_pois=_pois(),
        time_constraints=[
            {
                "poi_id": "museum",
                "preferred_window": "afternoon",
                "appointment_time": "14:00",
                "strength": "hard",
                "source_text": "故宫预约了14:00入场",
            }
        ],
        order_constraints=[],
        user_request="第一天故宫，预约了14:00入场；第二天想吃一次便宜坊烤鸭作为晚餐。",
    )

    commitments = ledger.model_dump(mode="json")["commitments"]
    assert any(item["kind"] == "time" and item["strength"] == "hard_anchor" for item in commitments)
    assert any(item["kind"] == "day" and item["poi_id"] == "museum" and item["preferred_day"] == 1 for item in commitments)
    assert any(item["kind"] == "meal" and item["poi_id"] == "duck" and item["meal_slot"] == "dinner" for item in commitments)
    assert any(item["kind"] == "visit" and item["poi_id"] == "duck" and item["strength"] == "soft_preference" for item in commitments)

    envelope = build_planning_envelope(
        user_profile={"days": 2, "constraints": {"physical_intensity": "medium"}},
        intent_ledger=ledger,
        district_summary=[{"district": "东城区", "poi_ids": ["museum", "duck"]}],
        must_poi_ids=["museum"],
    )
    assert envelope.protected_poi_ids == ["museum"]
    assert envelope.preferred_day_poi_ids[1] == ["museum"]
    assert envelope.explicit_meal_poi_ids == ["duck"]
