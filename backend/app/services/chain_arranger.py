from __future__ import annotations

from app.core import AppError
from app.services.poi_grounder import _normalize_category

QUICK_STOP_DURATION_MIN = 15
MEAL_STOP_DURATION_MIN = 60


def arrange_chain_to_anchor(chain_poi: dict, anchor_poi: dict, amap_client) -> dict:
    if not chain_poi.get("is_chain"):
        raise AppError("这个地点不是连锁店，不能使用顺路规划。", code="not_chain_place", step="arrange_nearby")
    if not _has_location(anchor_poi):
        raise AppError("缺少顺路参照点坐标，暂时无法匹配门店。", code="route_context_missing", step="arrange_nearby")
    candidates = [candidate for candidate in chain_poi.get("candidate_options", []) if _has_location(candidate)]
    if not candidates:
        raise AppError("这个连锁店没有可用门店候选。", code="chain_candidate_missing", step="arrange_nearby")

    scored = [(_travel_minutes(anchor_poi, candidate, amap_client), candidate) for candidate in candidates]
    detour_minutes, selected = sorted(scored, key=lambda item: item[0])[0]
    return {
        **chain_poi,
        "standard_name": selected.get("name", ""),
        "amap_id": selected.get("id", ""),
        "address": selected.get("address", ""),
        "location": selected.get("location", {}),
        "city": selected.get("city", ""),
        "district": selected.get("district", ""),
        "category_raw": selected.get("category_raw", ""),
        "category_normalized": selected.get("category_normalized") or _normalize_category(selected.get("category_raw", "")),
        "match_confidence": 0.9,
        "match_status": "matched",
        "is_chain": True,
        "chain_status": "resolved",
        "selection_mode": "chain_needs_choice",
        "resolved_branch_id": selected.get("id", ""),
        "resolved_branch_name": selected.get("name", ""),
        "resolved_from_anchor_poi_id": str(anchor_poi.get("poi_id") or "").strip(),
        "resolved_from_anchor_name": anchor_poi.get("standard_name", ""),
        "resolved_by": "nearby_anchor",
        "detour_minutes": detour_minutes,
    }


def arrange_chain_near_route(chain_poi: dict, context: dict, amap_client) -> dict:
    if not chain_poi.get("is_chain"):
        raise AppError("这个地点不是连锁店，不能使用顺路安排。", code="not_chain_place", step="arrange_nearby")
    candidates = [candidate for candidate in chain_poi.get("candidate_options", []) if _has_location(candidate)]
    if not candidates:
        raise AppError("这个连锁店没有可用门店候选。", code="chain_candidate_missing", step="arrange_nearby")

    previous_grounded = context.get("previous_grounded")
    next_grounded = context.get("next_grounded")
    if not _has_location(previous_grounded or {}) and not _has_location(next_grounded or {}):
        raise AppError("还缺少可参考的前后地点，暂时无法顺路选店。", code="route_context_missing", step="arrange_nearby")

    scored = []
    for candidate in candidates:
        score = 0
        if _has_location(previous_grounded or {}):
            score += _travel_minutes(previous_grounded, candidate, amap_client)
        if _has_location(next_grounded or {}):
            score += _travel_minutes(candidate, next_grounded, amap_client)
        scored.append((score, candidate))
    detour_minutes, selected = sorted(scored, key=lambda item: item[0])[0]
    return {
        **chain_poi,
        "standard_name": selected.get("name", ""),
        "amap_id": selected.get("id", ""),
        "address": selected.get("address", ""),
        "location": selected.get("location", {}),
        "city": selected.get("city", ""),
        "district": selected.get("district", ""),
        "category_raw": selected.get("category_raw", ""),
        "category_normalized": selected.get("category_normalized") or _normalize_category(selected.get("category_raw", "")),
        "match_confidence": 0.86,
        "match_status": "matched",
        "is_chain": True,
        "selection_mode": "arranged_nearby",
        "arranged_by": "route_context",
        "detour_minutes": detour_minutes,
    }


def prepare_chain_for_planning(chain_poi: dict, anchor_pois: list[dict], amap_client) -> dict:
    if not chain_poi.get("is_chain"):
        raise AppError("这个地点不是连锁店，不能做顺路门店预选。", code="not_chain_place", step="prepare_chain_for_planning")
    candidates = [candidate for candidate in chain_poi.get("candidate_options", []) if _has_location(candidate)]
    if not candidates:
        raise AppError("这个连锁店没有可用门店候选。", code="chain_candidate_missing", step="prepare_chain_for_planning")
    anchors = [anchor for anchor in anchor_pois if _has_location(anchor)]
    if not anchors:
        raise AppError("缺少可参考的锚点地点，暂时无法预选连锁店。", code="route_context_missing", step="prepare_chain_for_planning")

    scored: list[tuple[int, dict, list[str]]] = []
    branch_options: list[dict] = []
    for candidate in candidates:
        score = 0
        anchor_ids: list[str] = []
        for anchor in anchors:
            score += _travel_minutes(anchor, candidate, amap_client)
            anchor_id = str(anchor.get("poi_id") or anchor.get("standard_name") or "").strip()
            if anchor_id:
                anchor_ids.append(anchor_id)
        branch_options.append(
            {
                "branch_id": candidate.get("id", ""),
                "name": candidate.get("name", ""),
                "address": candidate.get("address", ""),
                "location": candidate.get("location", {}),
                "city": candidate.get("city", ""),
                "district": candidate.get("district", ""),
                "category_raw": candidate.get("category_raw", ""),
                "category_normalized": candidate.get("category_normalized") or _normalize_category(candidate.get("category_raw", "")),
                "detour_minutes": score,
                "quick_stop_duration_min": QUICK_STOP_DURATION_MIN,
                "meal_stop_duration_min": MEAL_STOP_DURATION_MIN,
                "quick_stop_total_cost_min": score + QUICK_STOP_DURATION_MIN,
                "meal_stop_total_cost_min": score + MEAL_STOP_DURATION_MIN,
                "anchor_poi_ids": anchor_ids,
            }
        )
        scored.append((score, candidate, anchor_ids))
    detour_minutes, selected, anchor_ids = sorted(scored, key=lambda item: item[0])[0]
    branch_options.sort(key=lambda item: item.get("detour_minutes", 9999))
    return {
        **chain_poi,
        "brand_name": chain_poi.get("raw_name") or chain_poi.get("standard_name") or "",
        "standard_name": chain_poi.get("standard_name") or chain_poi.get("raw_name") or "",
        "amap_id": selected.get("id", ""),
        "address": selected.get("address", ""),
        "location": selected.get("location", {}),
        "city": selected.get("city", ""),
        "district": selected.get("district", ""),
        "category_raw": selected.get("category_raw", ""),
        "category_normalized": selected.get("category_normalized") or _normalize_category(selected.get("category_raw", "")),
        "match_confidence": 0.82,
        "match_status": "ambiguous",
        "selection_mode": "route_dependent_chain",
        "chain_resolution_mode": "route_dependent_chain",
        "provisional_branch_id": selected.get("id", ""),
        "provisional_branch_name": selected.get("name", ""),
        "route_branch_options": branch_options,
        "route_selected_anchor_ids": anchor_ids,
        "detour_minutes": detour_minutes,
    }


def _travel_minutes(origin: dict, destination: dict, amap_client) -> int:
    walking = amap_client.walking_direction(_coord(origin), _coord(destination))
    minutes = _extract_duration_min(walking)
    if minutes is None:
        return 9999
    if minutes <= 25:
        return minutes
    driving = amap_client.driving_direction(_coord(origin), _coord(destination))
    return _extract_duration_min(driving) or minutes


def _extract_duration_min(response: dict | None) -> int | None:
    if not response:
        return None
    route = response.get("route", {})
    path = (route.get("paths") or [{}])[0]
    try:
        return max(1, round(float(path.get("duration")) / 60))
    except (TypeError, ValueError):
        return None


def _coord(poi: dict) -> str:
    location = poi.get("location", {})
    return f"{location.get('lng')},{location.get('lat')}"


def _has_location(poi: dict) -> bool:
    location = poi.get("location") or {}
    return location.get("lng") is not None and location.get("lat") is not None
