from __future__ import annotations

from itertools import permutations

from app.core import AppError

MAX_WALKING_DISTANCE_M = 1500
MAX_WALKING_DURATION_MIN = 25
DEFAULT_TRANSPORT_PREFERENCES = ["walking", "taxi", "public_transport"]


def classify_relation(mode: str, duration_min: int | None) -> str:
    if duration_min is None:
        return "unknown"
    if mode == "walking":
        if duration_min <= 10:
            return "same_cluster"
        if duration_min <= 25:
            return "nearby"
        return "same_day_possible"
    if duration_min <= 45:
        return "same_day_possible"
    return "separate_day"


def build_route_matrix(runtime_pois: list[dict], amap_client, cache_service=None, user_profile: dict | None = None) -> list[dict]:
    matched = [poi for poi in runtime_pois if _is_routable_poi(poi)]
    preferences = _transport_preferences(user_profile)
    matrix: list[dict] = []
    for origin, destination in permutations(matched, 2):
        origin_coord = _coord(origin)
        dest_coord = _coord(destination)
        cache_key = f"{origin_coord}->{dest_coord}:{','.join(preferences)}"
        cached = cache_service.get_route(cache_key) if cache_service else None
        if cached:
            matrix.append(cached)
            continue
        item = build_route_edge(origin, destination, amap_client, user_profile)
        if cache_service:
            cache_service.set_route(cache_key, item)
        matrix.append(item)
    return matrix


def build_route_edge(origin: dict, destination: dict, amap_client, user_profile: dict | None = None) -> dict:
    preferences = _transport_preferences(user_profile)
    candidates = _route_candidates(origin, destination, amap_client, preferences)
    selected = _select_candidate(candidates, preferences)
    mode = selected.get("mode")
    duration_min = selected.get("duration_min")
    return {
        "origin_poi_id": origin["poi_id"],
        "destination_poi_id": destination["poi_id"],
        "mode": mode,
        "distance_m": selected.get("distance_m"),
        "duration_min": duration_min,
        "relation": classify_relation(mode, duration_min),
        "source": "amap_direction_api",
    }


def _route_candidates(origin: dict, destination: dict, amap_client, preferences: list[str]) -> list[dict]:
    origin_coord = _coord(origin)
    dest_coord = _coord(destination)
    candidates: list[dict] = []
    if "walking" in preferences:
        walking = amap_client.walking_direction(origin_coord, dest_coord)
        candidate = _candidate("walking", walking)
        if candidate and _is_reasonable_walk(candidate):
            candidates.append(candidate)
    if "taxi" in preferences:
        driving = amap_client.driving_direction(origin_coord, dest_coord)
        candidate = _candidate("taxi", driving)
        if candidate:
            candidates.append(candidate)
    if "public_transport" in preferences:
        city = origin.get("city") or destination.get("city")
        if city:
            transit = _optional_transit_direction(amap_client, origin_coord, dest_coord, city)
            candidate = _candidate("public_transport", transit)
            if candidate:
                candidates.append(candidate)
    return candidates


def _select_candidate(candidates: list[dict], preferences: list[str]) -> dict:
    if not candidates:
        return {"mode": "unknown", "distance_m": None, "duration_min": None}
    by_mode = {candidate["mode"]: candidate for candidate in candidates}
    for mode in preferences:
        candidate = by_mode.get(mode)
        if candidate:
            return candidate
    return min(candidates, key=lambda item: item.get("duration_min") or 9999)


def _candidate(mode: str, response: dict | None) -> dict | None:
    distance_m = _extract_distance(response)
    duration_min = _extract_duration_min(response)
    if duration_min is None:
        return None
    return {"mode": mode, "distance_m": distance_m, "duration_min": duration_min}


def _is_reasonable_walk(candidate: dict) -> bool:
    distance_m = candidate.get("distance_m")
    duration_min = candidate.get("duration_min")
    return (
        isinstance(duration_min, int)
        and duration_min <= MAX_WALKING_DURATION_MIN
        and (distance_m is None or distance_m <= MAX_WALKING_DISTANCE_M)
    )


def _optional_transit_direction(amap_client, origin: str, destination: str, city: str) -> dict | None:
    try:
        return amap_client.transit_direction(origin, destination, city)
    except AppError:
        return None


def _transport_preferences(user_profile: dict | None) -> list[str]:
    raw_preferences = (user_profile or {}).get("transport_preference") or DEFAULT_TRANSPORT_PREFERENCES
    normalized: list[str] = []
    for item in raw_preferences:
        mode = _normalize_mode(str(item))
        if mode and mode not in normalized:
            normalized.append(mode)
    for fallback in DEFAULT_TRANSPORT_PREFERENCES:
        if fallback not in normalized:
            normalized.append(fallback)
    return normalized


def _normalize_mode(mode: str) -> str:
    return {
        "driving": "taxi",
        "taxi": "taxi",
        "walking": "walking",
        "walk": "walking",
        "transit": "public_transport",
        "public_transport": "public_transport",
    }.get(mode, mode)


def _coord(poi: dict) -> str:
    location = poi.get("location", {})
    return f"{location.get('lng')},{location.get('lat')}"


def _is_routable_poi(poi: dict) -> bool:
    location = poi.get("location") or {}
    if location.get("lng") is None or location.get("lat") is None:
        return False
    if poi.get("match_status") == "matched":
        return True
    if (
        poi.get("user_override") == "arrange_nearby"
        and ((poi.get("planning_semantics") or {}).get("chain_resolution_mode") == "route_dependent_chain" or poi.get("route_branch_options"))
    ):
        return True
    return (
        poi.get("user_override") == "must_include"
        and poi.get("match_status") == "ambiguous"
        and bool(poi.get("amap_id"))
    )


def _extract_distance(response: dict | None) -> int | None:
    if not response:
        return None
    route = response.get("route", {})
    path = (route.get("paths") or route.get("transits") or [{}])[0]
    try:
        return int(float(path.get("distance")))
    except (TypeError, ValueError):
        try:
            return int(float(path.get("walking_distance")))
        except (TypeError, ValueError):
            return None


def _extract_duration_min(response: dict | None) -> int | None:
    if not response:
        return None
    route = response.get("route", {})
    path = (route.get("paths") or route.get("transits") or [{}])[0]
    try:
        return max(1, round(float(path.get("duration")) / 60))
    except (TypeError, ValueError):
        return None
