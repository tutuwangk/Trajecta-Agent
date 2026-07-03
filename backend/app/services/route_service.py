from __future__ import annotations

from itertools import permutations


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


def build_route_matrix(runtime_pois: list[dict], amap_client, cache_service=None) -> list[dict]:
    matched = [poi for poi in runtime_pois if poi.get("match_status") == "matched" and poi.get("location", {}).get("lng")]
    matrix: list[dict] = []
    for origin, destination in permutations(matched, 2):
        origin_coord = _coord(origin)
        dest_coord = _coord(destination)
        cache_key = f"{origin_coord}->{dest_coord}:walking"
        cached = cache_service.get_route(cache_key) if cache_service else None
        if cached:
            matrix.append(cached)
            continue
        walking = amap_client.walking_direction(origin_coord, dest_coord)
        mode = "walking"
        distance_m = _extract_distance(walking)
        duration_min = _extract_duration_min(walking)
        if duration_min is None or duration_min > 25:
            driving = amap_client.driving_direction(origin_coord, dest_coord)
            driving_duration = _extract_duration_min(driving)
            if driving_duration is not None:
                mode = "driving"
                distance_m = _extract_distance(driving)
                duration_min = driving_duration
        item = {
            "origin_poi_id": origin["poi_id"],
            "destination_poi_id": destination["poi_id"],
            "mode": mode,
            "distance_m": distance_m,
            "duration_min": duration_min,
            "relation": classify_relation(mode, duration_min),
            "source": "amap_direction_api",
        }
        if cache_service:
            cache_service.set_route(cache_key, item)
        matrix.append(item)
    return matrix


def _coord(poi: dict) -> str:
    location = poi.get("location", {})
    return f"{location.get('lng')},{location.get('lat')}"


def _extract_distance(response: dict | None) -> int | None:
    if not response:
        return None
    route = response.get("route", {})
    path = (route.get("paths") or [{}])[0]
    try:
        return int(float(path.get("distance")))
    except (TypeError, ValueError):
        return None


def _extract_duration_min(response: dict | None) -> int | None:
    if not response:
        return None
    route = response.get("route", {})
    path = (route.get("paths") or [{}])[0]
    try:
        return max(1, round(float(path.get("duration")) / 60))
    except (TypeError, ValueError):
        return None
