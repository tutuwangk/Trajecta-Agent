from app.agents.planner import plan_itinerary


def test_plan_itinerary_tells_llm_not_to_expose_internal_decision_fields():
    class RecordingLLM:
        def __init__(self):
            self.messages = []

        def json_chat(self, messages, step):
            self.messages = messages
            return {"destination": "北京", "days": [], "global_risks": [], "uncertain_pois": [], "revision_notes": []}

    llm = RecordingLLM()
    plan_itinerary(
        {"destination": "北京", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [{"poi_id": "p1", "standard_name": "景山公园", "match_status": "matched", "user_override": "must_include"}],
        [],
        llm,
    )

    prompt = llm.messages[1]["content"]
    assert "内部字段只用于判断" in prompt
    assert "不得出现在 reason" in prompt
    assert "交通方式、时长和距离只能使用路径矩阵" in prompt
    assert "不得为了满足强度上限压缩单个地点的正常游玩时间" in prompt


def test_plan_itinerary_keeps_estimated_visit_duration_over_llm_short_stop():
    class ShortStopLLM:
        def json_chat(self, messages, step):
            return {
                "destination": "北京",
                "days": [
                    {
                        "day": 1,
                        "theme": "皇家园林",
                        "summary": "上午游览颐和园。",
                        "total_outing_min": 135,
                        "hotel_departure_transport_min": 30,
                        "hotel_return_transport_min": 30,
                        "items": [
                            {
                                "time_block": "上午",
                                "poi_id": "p1",
                                "name": "颐和园",
                                "arrival_time": "09:00",
                                "duration_min": 75,
                                "reason": "游览湖光山色。",
                                "risk_notes": [],
                            }
                        ],
                        "removed_pois": [],
                        "alternatives": [],
                    }
                ],
                "global_risks": [],
                "uncertain_pois": [],
                "revision_notes": [],
            }

    itinerary = plan_itinerary(
        {"destination": "北京", "days": 1, "constraints": {"physical_intensity": "low"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "颐和园",
                "estimated_duration_min": 240,
                "match_status": "matched",
            }
        ],
        [],
        ShortStopLLM(),
    )

    day = itinerary["days"][0]
    assert day["items"][0]["duration_min"] == 240
    assert day["total_outing_min"] >= 300


def test_plan_itinerary_uses_user_confirmed_ambiguous_map_candidate():
    class RecordingLLM:
        def __init__(self):
            self.messages = []

        def json_chat(self, messages, step):
            self.messages = messages
            return {"destination": "成都", "days": [], "global_risks": [], "uncertain_pois": [], "revision_notes": []}

    llm = RecordingLLM()
    plan_itinerary(
        {"destination": "成都", "days": 1, "constraints": {"physical_intensity": "medium"}},
        [
            {
                "poi_id": "p1",
                "standard_name": "晓市集",
                "match_status": "ambiguous",
                "amap_id": "B004",
                "location": {"lng": 104.1, "lat": 30.6},
                "user_override": "must_include",
                "final_decision": "include",
            }
        ],
        [],
        llm,
    )

    assert llm.messages
    assert "晓市集" in llm.messages[1]["content"]
