from app.agents.visit_duration_estimator import estimate_visit_durations


def test_estimate_visit_durations_extends_large_landmark_stays():
    pois = [
        {
            "poi_id": "p1",
            "standard_name": "颐和园",
            "category": "attraction",
            "estimated_duration_min": 75,
        },
        {
            "poi_id": "p2",
            "standard_name": "北京环球影城",
            "category": "attraction",
            "estimated_duration_min": 75,
        },
    ]

    estimated = estimate_visit_durations(pois)

    by_name = {poi["standard_name"]: poi for poi in estimated}
    assert by_name["颐和园"]["estimated_duration_min"] >= 240
    assert by_name["北京环球影城"]["estimated_duration_min"] >= 600
    assert "duration_reason" in by_name["颐和园"]


def test_estimate_visit_durations_raises_generic_attraction_default_above_short_stop():
    estimated = estimate_visit_durations(
        [
            {
                "poi_id": "p1",
                "standard_name": "城市地标",
                "category": "attraction",
                "estimated_duration_min": 75,
            }
        ]
    )

    assert estimated[0]["estimated_duration_min"] >= 120


def test_estimate_visit_durations_uses_generic_place_semantics_without_llm():
    estimated = estimate_visit_durations(
        [
            {
                "poi_id": "p1",
                "standard_name": "城市动物园",
                "category": "attraction",
                "estimated_duration_min": 75,
            },
            {
                "poi_id": "p2",
                "standard_name": "西山风景区",
                "category": "attraction",
                "estimated_duration_min": 75,
            },
        ]
    )

    by_name = {poi["standard_name"]: poi for poi in estimated}
    assert by_name["城市动物园"]["estimated_duration_min"] >= 210
    assert by_name["西山风景区"]["estimated_duration_min"] >= 240


def test_estimate_visit_durations_keeps_lightweight_places_short_enough_for_relaxed_days():
    estimated = estimate_visit_durations(
        [
            {"poi_id": "p1", "standard_name": "南锣鼓巷", "category": "attraction", "estimated_duration_min": 75},
            {"poi_id": "p2", "standard_name": "鼓楼", "category": "attraction", "estimated_duration_min": 75},
            {"poi_id": "p3", "standard_name": "景山公园", "category": "park", "estimated_duration_min": 75},
            {"poi_id": "p4", "standard_name": "雍和宫", "category": "attraction", "estimated_duration_min": 75},
        ]
    )

    by_name = {poi["standard_name"]: poi for poi in estimated}
    assert by_name["南锣鼓巷"]["estimated_duration_min"] <= 90
    assert by_name["鼓楼"]["estimated_duration_min"] <= 90
    assert by_name["景山公园"]["estimated_duration_min"] <= 90
    assert by_name["雍和宫"]["estimated_duration_min"] <= 90


def test_estimate_visit_durations_uses_llm_for_unlisted_large_place():
    class DurationLLM:
        def __init__(self):
            self.messages = []

        def json_chat(self, messages, step, temperature=0.2):
            self.messages = messages
            return {
                "durations": [
                    {
                        "poi_id": "p1",
                        "estimated_duration_min": 210,
                        "duration_confidence": "high",
                        "duration_reason": "大型动物园通常需要完整半天游玩。",
                    }
                ]
            }

    llm = DurationLLM()
    estimated = estimate_visit_durations(
        [
            {
                "poi_id": "p1",
                "standard_name": "国家动物园",
                "category": "attraction",
                "estimated_duration_min": 75,
                "ugc_evidence": ["亲子游玩、看熊猫和动物展区"],
            }
        ],
        llm,
    )

    assert estimated[0]["estimated_duration_min"] == 210
    assert estimated[0]["duration_confidence"] == "high"
    assert "正常人在这个地方玩一次" in llm.messages[1]["content"]
