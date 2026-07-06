from __future__ import annotations


def enrich_pois(grounded_pois: list[dict], ugc_items: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for poi in grounded_pois:
        poi_id = f"amap_{poi.get('amap_id')}" if poi.get("amap_id") else f"raw_{poi.get('raw_name')}"
        category = poi.get("category_normalized") or "unknown"
        planning_semantics = _planning_semantics(poi, category)
        enriched.append(
            {
                "poi_id": poi_id,
                "raw_names": [poi.get("raw_name", "")],
                "standard_name": poi.get("standard_name") or poi.get("raw_name"),
                "amap_id": poi.get("amap_id", ""),
                "city": poi.get("city", ""),
                "district": poi.get("district", ""),
                "address": poi.get("address", ""),
                "location": poi.get("location", {}),
                "category": category,
                "category_raw": poi.get("category_raw", ""),
                "match_status": poi.get("match_status"),
                "ugc_tags": poi.get("experience_tags", []),
                "ugc_evidence": poi.get("contexts", []),
                "estimated_duration_min": _duration_for_category(category),
                "best_time": planning_semantics["time_suitability"],
                "planning_semantics": planning_semantics,
                "brand_name": poi.get("brand_name", ""),
                "route_branch_options": list(poi.get("route_branch_options") or []),
                "provisional_branch_id": poi.get("provisional_branch_id", ""),
                "queue_risk": "medium" if any("排队" in context for context in poi.get("contexts", [])) else "unknown",
                "physical_intensity": "low" if category in {"restaurant", "shopping_mall"} else "medium",
                "confidence": poi.get("match_confidence", 0),
                "uncertainty_notes": _uncertainty_notes(poi),
                "system_decision": poi.get("system_decision", "include"),
                "user_override": poi.get("user_override", "none"),
                "final_decision": poi.get("final_decision", "include"),
                "inferred_role": poi.get("inferred_role", "visit"),
                "decision_reason": poi.get("decision_reason", ""),
            }
        )
    return enriched


def _duration_for_category(category: str) -> int:
    return {
        "restaurant": 75,
        "shopping_mall": 90,
        "museum": 150,
        "park": 90,
        "citywalk": 120,
        "attraction": 75,
    }.get(category, 60)


def _planning_semantics(poi: dict, category: str) -> dict:
    texts = [
        str(poi.get("raw_name") or ""),
        str(poi.get("standard_name") or ""),
        str(poi.get("category_raw") or ""),
        " ".join(str(item) for item in poi.get("contexts") or []),
        " ".join(str(item) for item in poi.get("experience_tags") or []),
    ]
    text = " ".join(texts)
    tags = poi.get("experience_tags") or []
    if any(token in text for token in ["酒吧", "bar", "cocktail", "livehouse", "兰桂坊", "夜店"]):
        return {
            "experience_type": "nightlife",
            "time_suitability": ["evening", "night"],
            "outing_role": "anchor",
            "meal_capability": "dinner_only",
            "quick_stop_eligible": False,
            "base_duration_profiles": {"visit": 120, "meal_stop": 60},
            "chain_resolution_mode": _chain_resolution_mode(poi),
        }
    if any(token in text for token in ["夜景"]) and category != "restaurant":
        return {
            "experience_type": "evening_view",
            "time_suitability": ["evening", "night"],
            "outing_role": "anchor",
            "meal_capability": "none",
            "quick_stop_eligible": False,
            "base_duration_profiles": {"visit": 90},
            "chain_resolution_mode": _chain_resolution_mode(poi),
        }
    if any(token in text for token in ["喜茶", "奈雪", "茶百道", "星巴克", "咖啡", "奶茶", "茶饮", "甜品", "果汁"]):
        return {
            "experience_type": "light_drink",
            "time_suitability": ["morning", "afternoon", "evening"],
            "outing_role": "filler",
            "meal_capability": "none",
            "quick_stop_eligible": True,
            "base_duration_profiles": {"visit": 45, "quick_stop": 15},
            "chain_resolution_mode": _chain_resolution_mode(poi),
        }
    if category == "restaurant" and any(token in text for token in ["小吃", "冰粉", "蛋烘糕", "早餐", "早饭", "包子", "面包"]):
        return {
            "experience_type": "snack",
            "time_suitability": ["morning", "midday", "afternoon", "evening"],
            "outing_role": "filler",
            "meal_capability": "breakfast_lunch" if any(token in text for token in ["早餐", "早饭", "包子", "面包"]) else "lunch_dinner",
            "quick_stop_eligible": True,
            "base_duration_profiles": {"visit": 45, "quick_stop": 15, "meal_stop": 45},
            "chain_resolution_mode": _chain_resolution_mode(poi),
        }
    if category == "restaurant":
        time_suitability = ["midday", "evening"]
        if any(token in text for token in ["早餐", "早饭"]):
            time_suitability = ["morning", "midday"]
        return {
            "experience_type": "full_meal",
            "time_suitability": time_suitability,
            "outing_role": "anchor" if any(token in text for token in ["必吃", "专门", "晚餐", "午餐"]) else "filler",
            "meal_capability": "breakfast_lunch" if time_suitability == ["morning", "midday"] else "lunch_dinner",
            "quick_stop_eligible": False,
            "base_duration_profiles": {"visit": 75, "meal_stop": 60},
            "chain_resolution_mode": _chain_resolution_mode(poi),
        }
    if "拍照" in tags:
        return {
            "experience_type": "daytime_visit",
            "time_suitability": ["afternoon", "evening"],
            "outing_role": "anchor",
            "meal_capability": "none",
            "quick_stop_eligible": False,
            "base_duration_profiles": {"visit": 90},
            "chain_resolution_mode": _chain_resolution_mode(poi),
        }
    return {
        "experience_type": "daytime_visit",
        "time_suitability": ["morning", "afternoon"],
        "outing_role": "anchor",
        "meal_capability": "none",
        "quick_stop_eligible": False,
        "base_duration_profiles": {"visit": 120},
        "chain_resolution_mode": _chain_resolution_mode(poi),
    }


def _chain_resolution_mode(poi: dict) -> str:
    if poi.get("is_chain") and poi.get("user_override") == "arrange_nearby":
        return "route_dependent_chain"
    if poi.get("is_chain") and poi.get("match_status") == "ambiguous":
        return "unresolved_chain"
    if poi.get("is_chain"):
        return "user_fixed_branch"
    return "none"


def _uncertainty_notes(poi: dict) -> list[str]:
    notes = []
    if poi.get("match_status") == "ambiguous":
        notes.append("高德返回多个候选，需用户确认。")
    if poi.get("match_status") == "unmatched":
        notes.append("高德未找到可靠匹配，不应直接安排进路线。")
    return notes
