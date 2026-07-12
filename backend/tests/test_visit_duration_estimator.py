from app.core import AppError
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
    assert by_name["颐和园"]["visit_duration_profile"] == {
        "relaxed_min": 180,
        "intense_min": 240,
        "confidence": "high",
        "reason": "大型皇家园林正常游玩需要半天左右。",
    }
    assert by_name["北京环球影城"]["visit_duration_profile"] == {
        "relaxed_min": 480,
        "intense_min": 600,
        "confidence": "high",
        "reason": "主题乐园通常需要接近全天游玩。",
    }
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
                        "relaxed_duration_min": 150,
                        "intense_duration_min": 210,
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

    assert estimated[0]["visit_duration_profile"] == {
        "relaxed_min": 150,
        "intense_min": 210,
        "confidence": "high",
        "reason": "大型动物园通常需要完整半天游玩。",
    }
    assert estimated[0]["duration_confidence"] == "high"
    assert "正常人在这个地方玩一次" in llm.messages[1]["content"]


def test_estimate_visit_durations_uses_category_and_tags_for_theme_parks():
    estimated = estimate_visit_durations(
        [
            {
                "poi_id": "p1",
                "standard_name": "城市欢乐世界",
                "category": "体育休闲服务;休闲场所;游乐场",
                "category_raw": "体育休闲服务;休闲场所;游乐场",
                "ugc_tags": ["游乐场", "主题公园"],
                "estimated_duration_min": 60,
            }
        ]
    )

    assert estimated[0]["visit_duration_profile"]["relaxed_min"] >= 330
    assert estimated[0]["visit_duration_profile"]["intense_min"] >= 420
    assert "游乐" in estimated[0]["duration_reason"] or "主题" in estimated[0]["duration_reason"]


def test_estimate_visit_durations_falls_back_when_llm_returns_invalid_json():
    class InvalidJsonLLM:
        def json_chat(self, messages, step, temperature=0.2):
            raise AppError("LLM 返回内容不是有效 JSON。", code="llm_invalid_json", step=step)

    estimated = estimate_visit_durations(
        [
            {
                "poi_id": "p1",
                "standard_name": "国家动物园",
                "category": "attraction",
                "estimated_duration_min": 75,
            }
        ],
        InvalidJsonLLM(),
    )

    assert estimated[0]["estimated_duration_min"] >= 210
    assert estimated[0]["duration_reason"] == "动物园通常需要完整半天游玩。"


def test_estimate_visit_durations_falls_back_on_any_llm_request_failure():
    class UnavailableLLM:
        def json_chat(self, messages, step, temperature=0.2):
            raise AppError("服务不可用", code="llm_request_failed", step=step)

    estimated = estimate_visit_durations(
        [{"poi_id": "p1", "standard_name": "城市博物馆", "category": "museum", "estimated_duration_min": 60}],
        UnavailableLLM(),
    )

    assert estimated[0]["estimated_duration_min"] == 150
    assert estimated[0]["duration_source"] == "deterministic_fallback"


def test_estimate_visit_durations_falls_back_on_unexpected_duration_client_error():
    class BrokenLLM:
        def json_chat(self, messages, step, temperature=0.2):
            raise RuntimeError("unexpected provider adapter failure")

    estimated = estimate_visit_durations(
        [{"poi_id": "p1", "standard_name": "城市博物馆", "category": "museum", "estimated_duration_min": 60}],
        BrokenLLM(),
    )

    assert estimated[0]["estimated_duration_min"] == 150
    assert estimated[0]["duration_source"] == "deterministic_fallback"


def test_estimate_visit_durations_uses_valid_rows_and_falls_back_invalid_rows():
    class PartialLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {
                "durations": [
                    {
                        "poi_id": "p1",
                        "relaxed_duration_min": 135,
                        "intense_duration_min": 180,
                        "duration_confidence": "high",
                        "duration_reason": "大型展馆需要较完整参观时间。",
                    },
                    {"poi_id": "p2", "relaxed_duration_min": "bad", "intense_duration_min": -10},
                    {"poi_id": "unknown", "relaxed_duration_min": 600, "intense_duration_min": 720},
                ]
            }

    estimated = estimate_visit_durations(
        [
            {"poi_id": "p1", "standard_name": "未来科技馆", "category": "attraction", "estimated_duration_min": 75},
            {"poi_id": "p2", "standard_name": "城市地标", "category": "attraction", "estimated_duration_min": 75},
        ],
        PartialLLM(),
    )

    assert estimated[0]["visit_duration_profile"]["relaxed_min"] == 135
    assert estimated[0]["visit_duration_profile"]["intense_min"] == 180
    assert estimated[0]["duration_source"] == "llm"
    assert estimated[1]["estimated_duration_min"] == 120
    assert estimated[1]["duration_source"] == "deterministic_fallback"


def test_llm_duration_cannot_shorten_high_confidence_theme_park_floor():
    class TooShortLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {
                "durations": [
                    {
                        "poi_id": "p1",
                        "relaxed_duration_min": 45,
                        "intense_duration_min": 60,
                        "duration_confidence": "high",
                        "duration_reason": "快速游玩。",
                    }
                ]
            }

    estimated = estimate_visit_durations(
        [
            {
                "poi_id": "p1",
                "standard_name": "城市欢乐世界",
                "category_raw": "体育休闲服务;休闲场所;游乐场",
                "ugc_tags": ["主题公园"],
                "estimated_duration_min": 60,
            }
        ],
        TooShortLLM(),
    )

    assert estimated[0]["visit_duration_profile"]["relaxed_min"] >= 390
    assert estimated[0]["visit_duration_profile"]["intense_min"] >= 480
    assert estimated[0]["duration_source"] == "llm_guardrailed"


def test_estimate_visit_durations_reuses_cached_llm_profile():
    class FailingIfCalledLLM:
        model = "duration-model"

        def json_chat(self, messages, step, temperature=0.2):
            raise AssertionError("cached duration should avoid an LLM request")

    class Cache:
        def get_duration(self, cache_key):
            return {
                "relaxed_min": 90,
                "intense_min": 120,
                "confidence": "high",
                "reason": "cached",
            }

        def set_duration(self, cache_key, value):
            raise AssertionError("cache hit should not be overwritten")

    estimated = estimate_visit_durations(
        [{"poi_id": "p1", "standard_name": "城市展览馆", "category": "attraction", "estimated_duration_min": 75}],
        FailingIfCalledLLM(),
        cache=Cache(),
    )

    assert estimated[0]["visit_duration_profile"]["intense_min"] == 120
    assert estimated[0]["duration_source"] == "llm_cache"


def test_duration_cache_failure_does_not_block_estimation():
    class DurationLLM:
        model = "duration-model"

        def json_chat(self, messages, step, temperature=0.2):
            return {"durations": []}

    class BrokenCache:
        def get_duration(self, cache_key):
            raise RuntimeError("cache unavailable")

        def set_duration(self, cache_key, value):
            raise RuntimeError("cache unavailable")

    estimated = estimate_visit_durations(
        [{"poi_id": "p1", "standard_name": "城市博物馆", "category": "museum", "estimated_duration_min": 60}],
        DurationLLM(),
        cache=BrokenCache(),
    )

    assert estimated[0]["estimated_duration_min"] == 150
    assert estimated[0]["duration_source"] == "deterministic_fallback"


def test_duration_cache_key_changes_when_place_facts_change():
    class CountingLLM:
        model = "duration-model"

        def __init__(self):
            self.calls = 0

        def json_chat(self, messages, step, temperature=0.2):
            self.calls += 1
            return {
                "durations": [
                    {
                        "poi_id": "p1",
                        "relaxed_duration_min": 90,
                        "intense_duration_min": 120,
                        "duration_confidence": "medium",
                        "duration_reason": "常规参观。",
                    }
                ]
            }

    class Cache:
        def __init__(self):
            self.values = {}

        def get_duration(self, cache_key):
            return self.values.get(cache_key)

        def set_duration(self, cache_key, value):
            self.values[cache_key] = value

    llm = CountingLLM()
    cache = Cache()
    original = {"poi_id": "p1", "standard_name": "城市展览馆", "category": "attraction", "estimated_duration_min": 75}
    renamed = {**original, "standard_name": "城市科技博物馆", "category": "museum"}

    estimate_visit_durations([original], llm, cache=cache)
    estimate_visit_durations([original], llm, cache=cache)
    estimate_visit_durations([renamed], llm, cache=cache)

    assert llm.calls == 2


def test_duration_llm_cannot_expand_light_drink_into_half_day_visit():
    class TooLongLLM:
        def json_chat(self, messages, step, temperature=0.2):
            return {
                "durations": [
                    {
                        "poi_id": "p1",
                        "relaxed_duration_min": 300,
                        "intense_duration_min": 420,
                        "duration_confidence": "high",
                        "duration_reason": "长时间体验。",
                    }
                ]
            }

    estimated = estimate_visit_durations(
        [
            {
                "poi_id": "p1",
                "standard_name": "街角咖啡店",
                "category": "restaurant",
                "ugc_tags": ["咖啡"],
                "estimated_duration_min": 45,
            }
        ],
        TooLongLLM(),
    )

    assert estimated[0]["visit_duration_profile"]["intense_min"] <= 120
    assert estimated[0]["duration_source"] == "llm_guardrailed"
