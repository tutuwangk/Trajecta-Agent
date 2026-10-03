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


def _semantics(
    *,
    poi_role: str,
    meal_capability: str,
    time_advice: list[str],
    planning_function: str,
    relaxed_min: int,
    intense_min: int,
    planning_notes: str,
    experience_type: str,
    outing_role: str,
    quick_stop_eligible: bool = False,
    meal_stop_min: int | None = None,
    poi: dict,
) -> dict:
    base_profiles = {"visit": relaxed_min}
    if quick_stop_eligible:
        base_profiles["quick_stop"] = 15
    if meal_stop_min is not None:
        base_profiles["meal_stop"] = meal_stop_min
    return {
        "poi_role": poi_role,
        "meal_capability": meal_capability,
        "time_advice": time_advice,
        "planning_function": planning_function,
        "duration_profile": {"relaxed_min": relaxed_min, "intense_min": intense_min},
        "planning_notes": planning_notes,
        # Legacy fields kept for existing normalizer/planner code paths.
        "experience_type": experience_type,
        "time_suitability": _legacy_time_suitability(time_advice),
        "outing_role": outing_role,
        "quick_stop_eligible": quick_stop_eligible,
        "base_duration_profiles": base_profiles,
        "chain_resolution_mode": _chain_resolution_mode(poi),
    }


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
    if any(token in text for token in ["酒店", "住宿", "hotel"]):
        return _semantics(
            poi_role="hotel_anchor",
            meal_capability="none",
            time_advice=["flexible"],
            planning_function="rest",
            relaxed_min=0,
            intense_min=0,
            planning_notes="住宿锚点，只用于出发、回程和休息分段。",
            experience_type="hotel_anchor",
            outing_role="filler",
            poi=poi,
        )
    if any(token in text for token in ["机场", "车站", "火车站", "高铁站", "码头", "地铁站"]):
        return _semantics(
            poi_role="transport_anchor",
            meal_capability="none",
            time_advice=["flexible"],
            planning_function="transfer",
            relaxed_min=30,
            intense_min=20,
            planning_notes="交通节点，只用于到达、离开或换乘，不作为游玩目的地。",
            experience_type="transport_anchor",
            outing_role="filler",
            poi=poi,
        )
    if any(token in text for token in ["酒吧", "bar", "cocktail", "livehouse", "兰桂坊", "夜店"]):
        return _semantics(
            poi_role="nightlife",
            meal_capability="none",
            time_advice=["evening", "night"],
            planning_function="ending",
            relaxed_min=120,
            intense_min=90,
            planning_notes="夜生活地点，只适合晚上或夜间，可作为当天收尾。",
            experience_type="nightlife",
            outing_role="anchor",
            meal_stop_min=60,
            poi=poi,
        )
    if any(token in text for token in ["夜景", "灯光", "夜游"]) and category != "restaurant":
        return _semantics(
            poi_role="evening_view",
            meal_capability="none",
            time_advice=["evening", "night"],
            planning_function="ending",
            relaxed_min=90,
            intense_min=60,
            planning_notes="夜景或灯光体验，优先安排在傍晚后。",
            experience_type="evening_view",
            outing_role="anchor",
            poi=poi,
        )
    if any(token in text for token in ["喜茶", "奈雪", "茶百道", "星巴克", "咖啡", "奶茶", "茶饮", "甜品", "果汁"]):
        return _semantics(
            poi_role="drink_stop",
            meal_capability="drink_only",
            time_advice=["morning", "afternoon", "evening"],
            planning_function="filler",
            relaxed_min=45,
            intense_min=15,
            planning_notes="饮品补给点，只适合短暂停靠，不承接正式午餐或晚餐。",
            experience_type="light_drink",
            outing_role="filler",
            quick_stop_eligible=True,
            poi=poi,
        )
    if category == "restaurant" and any(token in text for token in ["小吃", "冰粉", "蛋烘糕", "早餐", "早饭", "包子", "面包"]):
        is_breakfast = any(token in text for token in ["早餐", "早饭", "包子", "面包"])
        return _semantics(
            poi_role="breakfast_meal" if is_breakfast else "snack_light_meal",
            meal_capability="breakfast" if is_breakfast else "snack_only",
            time_advice=["morning", "midday", "afternoon"] if is_breakfast else ["midday", "afternoon", "evening"],
            planning_function="meal" if is_breakfast else "filler",
            relaxed_min=45,
            intense_min=30,
            planning_notes="早餐点可承接早餐；小吃甜品默认只作轻食或补充，非正式午晚餐。",
            experience_type="snack",
            outing_role="filler",
            quick_stop_eligible=not is_breakfast,
            meal_stop_min=45,
            poi=poi,
        )
    if category == "restaurant":
        is_breakfast = any(token in text for token in ["早餐", "早饭", "brunch", "早午餐"])
        time_advice = ["morning", "midday"] if is_breakfast else ["midday", "evening"]
        if any(token in text for token in ["早餐", "早饭"]):
            time_advice = ["morning", "midday"]
        return _semantics(
            poi_role="breakfast_meal" if is_breakfast else "full_meal",
            meal_capability="breakfast" if is_breakfast else "lunch_dinner",
            time_advice=time_advice,
            planning_function="meal",
            relaxed_min=75,
            intense_min=60,
            planning_notes="正餐地点，优先由 LLM 安排到合适的早餐、午餐或晚餐时段。",
            experience_type="full_meal",
            outing_role="anchor" if any(token in text for token in ["必吃", "专门", "晚餐", "午餐"]) else "filler",
            meal_stop_min=60,
            poi=poi,
        )
    if "拍照" in tags or any(token in text for token in ["拍照", "打卡", "出片"]):
        return _semantics(
            poi_role="photo_spot",
            meal_capability="none",
            time_advice=["daylight", "morning", "afternoon", "evening"],
            planning_function="anchor",
            relaxed_min=90,
            intense_min=60,
            planning_notes="拍照打卡点，优先安排在自然光较好的上午、下午或黄昏。",
            experience_type="daytime_visit",
            outing_role="anchor",
            poi=poi,
        )
    if category in {"shopping_mall"} or any(token in text for token in ["商场", "商圈", "购物中心", "太古里", "IFS"]):
        return _semantics(
            poi_role="shopping_rest",
            meal_capability="none",
            time_advice=["flexible", "afternoon", "evening"],
            planning_function="rest",
            relaxed_min=90,
            intense_min=60,
            planning_notes="商场或商圈，可承担休息、避雨、购物和串联周边地点。",
            experience_type="daytime_visit",
            outing_role="anchor",
            poi=poi,
        )
    if category in {"citywalk"} or any(token in text for token in ["街", "巷", "citywalk", "步行"]):
        return _semantics(
            poi_role="citywalk_area",
            meal_capability="none",
            time_advice=["daylight", "morning", "afternoon", "evening"],
            planning_function="anchor",
            relaxed_min=120,
            intense_min=75,
            planning_notes="步行街区，适合串联附近地点，避开过晚时段。",
            experience_type="daytime_visit",
            outing_role="anchor",
            poi=poi,
        )
    return _semantics(
        poi_role="scenic_anchor",
        meal_capability="none",
        time_advice=["open_hours", "morning", "afternoon"],
        planning_function="anchor",
        relaxed_min=150 if category in {"museum", "park"} else 120,
        intense_min=90 if category in {"museum", "park"} else 75,
        planning_notes="核心游玩点，需安排在开放时间内，通常承担半天或主要游玩目的。",
        experience_type="daytime_visit",
        outing_role="anchor",
        poi=poi,
    )


def _legacy_time_suitability(time_advice: list[str]) -> list[str]:
    mapping = {
        "open_hours": ["morning", "afternoon"],
        "daylight": ["morning", "afternoon", "evening"],
        "flexible": ["morning", "midday", "afternoon", "evening"],
    }
    result: list[str] = []
    for item in time_advice:
        for value in mapping.get(item, [item]):
            if value not in result:
                result.append(value)
    return result


def _chain_resolution_mode(poi: dict) -> str:
    if poi.get("is_chain") and poi.get("chain_status") != "resolved":
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
