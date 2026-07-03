from __future__ import annotations

from app.core import AppError
from app.services.poi_grounder import _normalize_category


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
