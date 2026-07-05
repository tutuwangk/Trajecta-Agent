from __future__ import annotations

from app.agents.intensity import daily_time_limit_minutes, daily_time_minutes, sync_day_total_time

DEFAULT_RELAXED_FIRST_ARRIVAL_MIN = 10 * 60
DEFAULT_HIGH_FIRST_ARRIVAL_MIN = 9 * 60
LUNCH_WINDOW = (11 * 60 + 30, 13 * 60 + 30)
DINNER_WINDOW = (17 * 60 + 30, 19 * 60 + 30)
LARGE_PLACE_MIN = 240
MEAL_BREAK_MIN = 60
SHORT_OPTIONAL_STOP_MIN = 45
SHORT_OPTIONAL_TRANSFER_MIN = 15


def normalize_itinerary(
    itinerary: dict,
    user_profile: dict,
    runtime_pois: list[dict],
    route_matrix: list[dict],
) -> None:
    runtime_by_id = {poi.get("poi_id"): poi for poi in runtime_pois}
    route_by_pair = {(edge.get("origin_poi_id"), edge.get("destination_poi_id")): edge for edge in route_matrix}
    limit_minutes = daily_time_limit_minutes(user_profile)
    must_visit = user_profile.get("constraints", {}).get("must_visit", [])

    itinerary.setdefault("global_risks", [])
    for day in itinerary.get("days", []):
        _remove_unplannable(day, runtime_by_id)
        _sync_day_transports(day, route_by_pair)
        _rebuild_timing(day, runtime_by_id, _is_high_intensity(user_profile))
        _trim_optional_items(day, itinerary, runtime_by_id, route_by_pair, must_visit, limit_minutes)
        _sync_day_transports(day, route_by_pair)
        _rebuild_timing(day, runtime_by_id, _is_high_intensity(user_profile))
        sync_day_total_time(day)


def _remove_unplannable(day: dict, runtime_by_id: dict) -> None:
    kept = []
    removed = list(day.get("removed_pois", []))
    for item in day.get("items") or []:
        poi = runtime_by_id.get(item.get("poi_id"))
        if not poi or not _is_plannable_poi(poi) or poi.get("final_decision") in {"exclude", "unresolved"}:
            removed.append({"name": item.get("name", ""), "reason": "地点还需要确认，暂不放进路线。"})
            continue
        kept.append(item)
    day["items"] = kept
    day["removed_pois"] = removed


def _trim_optional_items(
    day: dict,
    itinerary: dict,
    runtime_by_id: dict,
    route_by_pair: dict,
    must_visit: list[str],
    limit_minutes: int,
) -> None:
    removed = list(day.get("removed_pois", []))
    removed_any = False
    while day.get("items") and daily_time_minutes(day) > limit_minutes:
        if len(day["items"]) <= 1:
            item = day["items"][0]
            _append_unique(
                itinerary.setdefault("global_risks", []),
                f"{item.get('name', '这个地点')}本身耗时较长，当天节奏会更接近特种兵。",
            )
            break
        index = _removable_item_index(day["items"], runtime_by_id, must_visit)
        if index is None:
            if _all_items_are_must(day["items"], runtime_by_id, must_visit):
                _append_unique(
                    itinerary.setdefault("global_risks", []),
                    "必去地点较多或单个地点耗时较长，当天节奏会更接近特种兵。",
                )
            break
        item = day["items"].pop(index)
        removed.append({"name": item.get("name", ""), "reason": "为控制当天外出时间，已先放入备选。"})
        removed_any = True
        _sync_day_transports(day, route_by_pair)
        _rebuild_timing(day, runtime_by_id, False)
    if removed_any:
        day["removed_pois"] = removed


def _removable_item_index(items: list[dict], runtime_by_id: dict, must_visit: list[str]) -> int | None:
    candidates = []
    for index, item in enumerate(items):
        poi = runtime_by_id.get(item.get("poi_id"), {})
        if _is_must_item(item, poi, must_visit) or _is_preservable_optional(items, index, poi):
            continue
        duration = _int(item.get("duration_min"))
        transfer = _int((item.get("transport_to_next") or {}).get("duration_min"))
        confidence = float(poi.get("confidence") or poi.get("match_confidence") or 0)
        candidates.append((duration + transfer, -confidence, index))
    if not candidates:
        return None
    return max(candidates)[-1]


def _is_preservable_optional(items: list[dict], index: int, poi: dict) -> bool:
    item = items[index]
    duration = _int(item.get("duration_min"))
    prev_transfer = _int((items[index - 1].get("transport_to_next") or {}).get("duration_min")) if index > 0 else 0
    next_transfer = _int((item.get("transport_to_next") or {}).get("duration_min"))
    if _is_meal_poi(item, poi):
        return max(prev_transfer, next_transfer) <= SHORT_OPTIONAL_TRANSFER_MIN
    if duration > SHORT_OPTIONAL_STOP_MIN:
        return False
    return max(prev_transfer, next_transfer) <= SHORT_OPTIONAL_TRANSFER_MIN


def _all_items_are_must(items: list[dict], runtime_by_id: dict, must_visit: list[str]) -> bool:
    return all(_is_must_item(item, runtime_by_id.get(item.get("poi_id"), {}), must_visit) for item in items)


def _is_must_item(item: dict, poi: dict, must_visit: list[str]) -> bool:
    if poi.get("user_override") == "must_include":
        return True
    return any(name and name in item.get("name", "") for name in must_visit)


def _sync_day_transports(day: dict, route_by_pair: dict) -> None:
    items = day.get("items") or []
    for index, item in enumerate(items):
        if index >= len(items) - 1:
            item.pop("transport_to_next", None)
            continue
        edge = route_by_pair.get((item.get("poi_id"), items[index + 1].get("poi_id")))
        if not edge:
            item.pop("transport_to_next", None)
            continue
        item["transport_to_next"] = {
            "mode": edge.get("mode", "unknown"),
            "duration_min": edge.get("duration_min"),
            "distance_m": edge.get("distance_m"),
        }


def _rebuild_timing(day: dict, runtime_by_id: dict, high_intensity: bool) -> None:
    items = day.get("items") or []
    if not items:
        day["meal_breaks"] = []
        return
    current = _parse_time(items[0].get("arrival_time"))
    if current is None:
        current = DEFAULT_HIGH_FIRST_ARRIVAL_MIN if high_intensity else DEFAULT_RELAXED_FIRST_ARRIVAL_MIN
    meal_breaks: list[dict] = []
    lunch_done = False
    dinner_done = False
    for index, item in enumerate(items):
        item["arrival_time"] = _format_time(current)
        start = current
        duration = _int(item.get("duration_min"))
        end = start + duration
        poi = runtime_by_id.get(item.get("poi_id"), {})
        if duration >= LARGE_PLACE_MIN:
            if _overlaps(start, end, *LUNCH_WINDOW):
                meal_breaks.append(_inside_meal("午餐", "12:00", item))
                lunch_done = True
            if _overlaps(start, end, *DINNER_WINDOW):
                meal_breaks.append(_inside_meal("晚餐", "18:00", item))
                dinner_done = True
        current = end
        if not lunch_done and _should_insert_external_meal(current, *LUNCH_WINDOW) and not _is_meal_poi(item, poi):
            meal_breaks.append({"label": "午餐", "start_time": _format_time(max(current, LUNCH_WINDOW[0])), "duration_min": MEAL_BREAK_MIN})
            current = max(current, LUNCH_WINDOW[0]) + MEAL_BREAK_MIN
            lunch_done = True
        if not dinner_done and _should_insert_external_meal(current, *DINNER_WINDOW) and not _is_meal_poi(item, poi):
            meal_breaks.append({"label": "晚餐", "start_time": _format_time(max(current, DINNER_WINDOW[0])), "duration_min": MEAL_BREAK_MIN})
            current = max(current, DINNER_WINDOW[0]) + MEAL_BREAK_MIN
            dinner_done = True
        if index < len(items) - 1:
            current += _int((item.get("transport_to_next") or {}).get("duration_min"))
    day["meal_breaks"] = meal_breaks
    sync_day_total_time(day)


def _inside_meal(label: str, start_time: str, item: dict) -> dict:
    return {
        "label": label,
        "start_time": start_time,
        "duration_min": MEAL_BREAK_MIN,
        "within_poi_id": item.get("poi_id"),
        "included_in_item_duration": True,
    }


def _should_insert_external_meal(current: int, window_start: int, window_end: int) -> bool:
    return window_start <= current <= window_end


def _is_meal_poi(item: dict, poi: dict) -> bool:
    text = f"{item.get('time_block', '')}{item.get('name', '')}{poi.get('category', '')}{poi.get('category_normalized', '')}{poi.get('inferred_role', '')}"
    return poi.get("inferred_role") == "meal" or poi.get("category") == "restaurant" or any(
        token in text for token in ["午餐", "晚餐", "餐", "小吃", "咖啡", "restaurant"]
    )


def _is_high_intensity(user_profile: dict) -> bool:
    return user_profile.get("constraints", {}).get("physical_intensity") == "high"


def _is_plannable_poi(poi: dict) -> bool:
    if poi.get("match_status") == "matched":
        return True
    location = poi.get("location") or {}
    return (
        poi.get("user_override") == "must_include"
        and poi.get("match_status") == "ambiguous"
        and bool(poi.get("amap_id"))
        and location.get("lng") is not None
        and location.get("lat") is not None
    )


def _parse_time(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    try:
        return int(hour) * 60 + int(minute[:2])
    except ValueError:
        return None


def _format_time(minutes: int) -> str:
    normalized = minutes % (24 * 60)
    return f"{normalized // 60:02d}:{normalized % 60:02d}"


def _overlaps(start: int, end: int, window_start: int, window_end: int) -> bool:
    return start < window_end and end > window_start


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _append_unique(values: list, text: str) -> None:
    if text not in values:
        values.append(text)
