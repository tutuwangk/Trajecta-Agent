import pytest

from app.services.poi_enricher import enrich_pois


def test_enrich_pois_marks_light_drink_as_non_meal_capability():
    enriched = enrich_pois(
        [
            {
                "raw_name": "喜茶(IFS店)",
                "standard_name": "喜茶(IFS店)",
                "category_normalized": "restaurant",
                "category_raw": "餐饮服务;冷饮店",
                "match_status": "matched",
                "contexts": ["顺手买杯奶茶"],
                "experience_tags": [],
            }
        ],
        [],
    )

    semantics = enriched[0]["planning_semantics"]
    assert semantics["experience_type"] == "light_drink"
    assert semantics["poi_role"] == "drink_stop"
    assert semantics["meal_capability"] == "drink_only"
    assert semantics["planning_function"] == "filler"
    assert "正式午餐" in semantics["planning_notes"]


@pytest.mark.parametrize(
    ("poi", "expected_role", "expected_meal", "expected_time"),
    [
        (
            {"raw_name": "武侯祠", "standard_name": "武侯祠", "category_normalized": "attraction", "category_raw": "风景名胜", "contexts": [], "experience_tags": []},
            "scenic_anchor",
            "none",
            ["open_hours", "morning", "afternoon"],
        ),
        (
            {"raw_name": "东郊记忆", "standard_name": "东郊记忆", "category_normalized": "attraction", "category_raw": "风景名胜", "contexts": ["很出片"], "experience_tags": ["拍照"]},
            "photo_spot",
            "none",
            ["daylight", "morning", "afternoon", "evening"],
        ),
        (
            {"raw_name": "园里火锅", "standard_name": "园里火锅", "category_normalized": "restaurant", "category_raw": "餐饮服务;中餐厅", "contexts": ["午餐想吃火锅"], "experience_tags": []},
            "full_meal",
            "lunch_dinner",
            ["midday", "evening"],
        ),
        (
            {"raw_name": "赵记豆浆", "standard_name": "赵记豆浆", "category_normalized": "restaurant", "category_raw": "餐饮服务;早餐", "contexts": ["早餐"], "experience_tags": []},
            "breakfast_meal",
            "breakfast",
            ["morning", "midday", "afternoon"],
        ),
        (
            {"raw_name": "九眼桥夜景", "standard_name": "九眼桥夜景", "category_normalized": "attraction", "category_raw": "风景名胜", "contexts": ["夜景"], "experience_tags": []},
            "evening_view",
            "none",
            ["evening", "night"],
        ),
        (
            {"raw_name": "兰桂坊", "standard_name": "兰桂坊", "category_normalized": "restaurant", "category_raw": "酒吧", "contexts": ["晚上喝一杯"], "experience_tags": []},
            "nightlife",
            "none",
            ["evening", "night"],
        ),
    ],
)
def test_enrich_pois_assigns_travel_poi_role_pool(poi, expected_role, expected_meal, expected_time):
    enriched = enrich_pois([{**poi, "match_status": "matched"}], [])

    semantics = enriched[0]["planning_semantics"]
    assert semantics["poi_role"] == expected_role
    assert semantics["meal_capability"] == expected_meal
    assert semantics["time_advice"] == expected_time
    assert "duration_profile" in semantics
