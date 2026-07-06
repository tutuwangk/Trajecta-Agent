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
    assert semantics["meal_capability"] == "none"

