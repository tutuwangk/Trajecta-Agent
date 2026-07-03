from __future__ import annotations


def enrich_pois(grounded_pois: list[dict], ugc_items: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for poi in grounded_pois:
        poi_id = f"amap_{poi.get('amap_id')}" if poi.get("amap_id") else f"raw_{poi.get('raw_name')}"
        category = poi.get("category_normalized") or "unknown"
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
                "match_status": poi.get("match_status"),
                "ugc_tags": poi.get("experience_tags", []),
                "ugc_evidence": poi.get("contexts", []),
                "estimated_duration_min": _duration_for_category(category),
                "best_time": _best_time_for_category(category, poi.get("experience_tags", [])),
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


def _best_time_for_category(category: str, tags: list[str]) -> list[str]:
    if "夜景" in tags:
        return ["evening", "night"]
    if category == "restaurant":
        return ["lunch", "dinner"]
    if "拍照" in tags:
        return ["afternoon", "evening"]
    return ["morning", "afternoon"]


def _uncertainty_notes(poi: dict) -> list[str]:
    notes = []
    if poi.get("match_status") == "ambiguous":
        notes.append("高德返回多个候选，需用户确认。")
    if poi.get("match_status") == "unmatched":
        notes.append("高德未找到可靠匹配，不应直接安排进路线。")
    return notes
