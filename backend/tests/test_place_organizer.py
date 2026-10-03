from app.agents.place_organizer import organize_place


def test_organize_place_defaults_stable_matched_visit_to_include():
    raw_poi = {"raw_name": "IFS", "contexts": ["太古里附近拍照"], "experience_tags": ["拍照"], "confidence": 0.9}
    grounded_poi = {
        "raw_name": "IFS",
        "standard_name": "成都IFS国际金融中心",
        "category_normalized": "shopping_mall",
        "match_confidence": 0.93,
        "match_status": "matched",
    }

    result = organize_place(raw_poi, grounded_poi, {"constraints": {}})

    assert result["system_decision"] == "include"
    assert result["user_override"] == "none"
    assert result["final_decision"] == "include"
    assert result["inferred_role"] == "visit"
    assert result["place_pool_item"]["display_name"] == "成都IFS国际金融中心"
    assert result["place_pool_item"]["decision_label"] == "已纳入"
    assert result["place_pool_item"]["needs_attention"] is False


def test_organize_place_keeps_ambiguous_map_candidate_unresolved():
    raw_poi = {"raw_name": "晓市集", "contexts": ["想去晓市集"], "confidence": 0.8}
    grounded_poi = {
        "raw_name": "晓市集",
        "standard_name": "晓市集",
        "amap_id": "B004",
        "category_normalized": "attraction",
        "location": {"lng": 104.1, "lat": 30.6},
        "match_confidence": 0.62,
        "match_status": "ambiguous",
    }

    result = organize_place(raw_poi, grounded_poi, {"constraints": {}})

    assert result["system_decision"] == "needs_confirmation"
    assert result["final_decision"] == "unresolved"
    assert result["place_pool_item"]["status_label"] == "需确认"
    assert result["place_pool_item"]["needs_attention"] is True


def test_organize_place_keeps_chain_candidate_unresolved():
    raw_poi = {"raw_name": "星巴克", "contexts": ["想喝咖啡"], "confidence": 0.86}
    grounded_poi = {
        "raw_name": "星巴克",
        "standard_name": "星巴克（待选择）",
        "amap_id": "S1",
        "category_normalized": "restaurant",
        "location": {"lng": 104.081, "lat": 30.655},
        "match_confidence": 0.68,
        "match_status": "ambiguous",
        "is_chain": True,
        "selection_mode": "chain_needs_choice",
        "candidate_options": [
            {"id": "S1", "name": "星巴克(成都太古里店)"},
            {"id": "S2", "name": "星巴克(成都IFS店)"},
        ],
    }

    result = organize_place(raw_poi, grounded_poi, {"constraints": {}})

    assert result["system_decision"] == "needs_confirmation"
    assert result["final_decision"] == "unresolved"
    assert result["place_pool_item"]["display_name"] == "星巴克（待选择）"
    assert result["place_pool_item"]["status_label"] == "需确认"
    assert result["place_pool_item"]["decision_label"] == "需确认"
    assert result["place_pool_item"]["needs_attention"] is True
    assert result["place_pool_item"]["primary_actions"] == ["顺路规划", "改名", "移除"]


def test_organize_place_shows_normal_actions_for_resolved_chain_branch():
    raw_poi = {"raw_name": "星巴克", "contexts": ["下午喝咖啡"], "confidence": 0.86}
    grounded_poi = {
        "raw_name": "星巴克",
        "standard_name": "星巴克(成都IFS店)",
        "amap_id": "S2",
        "category_normalized": "restaurant",
        "location": {"lng": 104.0805, "lat": 30.6572},
        "match_confidence": 0.92,
        "match_status": "matched",
        "is_chain": True,
        "chain_status": "resolved",
        "resolved_branch_id": "S2",
        "resolved_from_anchor_poi_id": "amap_I1",
    }

    result = organize_place(raw_poi, grounded_poi, {"constraints": {}}, user_override="optional")

    assert result["system_decision"] == "include"
    assert result["final_decision"] == "optional"
    assert result["place_pool_item"]["status_label"] == "已识别"
    assert result["place_pool_item"]["primary_actions"] == ["顺路规划", "必去", "待定", "移除", "改名"]


def test_organize_place_infers_meal_role_and_honors_remove_override():
    raw_poi = {"raw_name": "饕林餐厅", "contexts": ["晚餐想吃"], "confidence": 0.88}
    grounded_poi = {
        "raw_name": "饕林餐厅",
        "standard_name": "饕林餐厅",
        "category_normalized": "restaurant",
        "match_confidence": 0.91,
        "match_status": "matched",
    }

    result = organize_place(raw_poi, grounded_poi, {"constraints": {}}, user_override="remove")

    assert result["inferred_role"] == "meal"
    assert result["user_override"] == "remove"
    assert result["final_decision"] == "exclude"
    assert result["place_pool_item"]["decision_label"] == "未纳入"


def test_organize_place_honors_optional_override_as_tentative():
    raw_poi = {"raw_name": "人民公园", "contexts": ["有时间可以去"], "confidence": 0.85}
    grounded_poi = {
        "raw_name": "人民公园",
        "standard_name": "人民公园",
        "category_normalized": "park",
        "match_confidence": 0.9,
        "match_status": "matched",
    }

    result = organize_place(raw_poi, grounded_poi, {"constraints": {}}, user_override="optional")

    assert result["user_override"] == "optional"
    assert result["final_decision"] == "optional"
    assert result["place_pool_item"]["decision_label"] == "待定"
