from __future__ import annotations


DAILY_TIME_LIMITS = {
    "low": 420,
    "medium": 420,
    "high": 840,
}


def normalize_physical_intensity(value: str | None, avoid_too_tired: bool = False) -> str:
    if value in DAILY_TIME_LIMITS:
        return value
    return "medium" if avoid_too_tired else "medium"


def daily_time_limit_minutes(user_profile: dict) -> int:
    constraints = user_profile.get("constraints", {})
    intensity = normalize_physical_intensity(
        constraints.get("physical_intensity"),
        bool(constraints.get("avoid_too_tired")),
    )
    return DAILY_TIME_LIMITS[intensity]


def daily_time_minutes(day_or_items: dict | list[dict]) -> int:
    if isinstance(day_or_items, dict):
        timeline_total = timeline_time_minutes(day_or_items)
        explicit = _first_int(
            day_or_items,
            ["total_outing_min", "total_outing_minutes", "outing_duration_min", "outing_duration_minutes"],
        )
        component_total = component_time_minutes(day_or_items)
        if timeline_total is not None:
            return timeline_total
        if explicit is not None:
            return max(explicit, component_total)
        return component_total
    return _items_time_minutes(day_or_items)


def intensity_time_minutes(day: dict) -> int:
    explicit = _first_int(
        day,
        ["intensity_outing_min", "intensity_outing_minutes", "intensity_duration_min", "intensity_duration_minutes"],
    )
    if explicit is not None:
        return explicit
    return daily_time_minutes(day)


def sync_day_total_time(day: dict) -> None:
    timeline_total = timeline_time_minutes(day)
    day["total_outing_min"] = timeline_total if timeline_total is not None else component_time_minutes(day)
    day["total_transfer_min"] = transfer_time_minutes(day)


def component_time_minutes(day: dict) -> int:
    total = _first_int(day, ["hotel_departure_transport_min", "hotel_to_first_transport_min"]) or 0
    total += _first_int(day, ["hotel_return_transport_min", "last_to_hotel_transport_min"]) or 0
    for hotel_break in day.get("hotel_rest_breaks") or []:
        total += int(hotel_break.get("return_to_hotel_transport_min") or 0)
        total += int(hotel_break.get("depart_from_hotel_transport_min") or 0)
    for meal in day.get("meal_breaks") or []:
        if meal.get("included_in_item_duration"):
            continue
        total += int(meal.get("duration_min") or meal.get("duration_minutes") or 0)
    total += _items_time_minutes(day.get("items") or [])
    return total


def transfer_time_minutes(day: dict) -> int:
    total = _first_int(day, ["hotel_departure_transport_min", "hotel_to_first_transport_min"]) or 0
    total += _first_int(day, ["hotel_return_transport_min", "last_to_hotel_transport_min"]) or 0
    for hotel_break in day.get("hotel_rest_breaks") or []:
        total += int(hotel_break.get("return_to_hotel_transport_min") or 0)
        total += int(hotel_break.get("depart_from_hotel_transport_min") or 0)
    for item in day.get("items") or []:
        total += int((item.get("transport_to_next") or {}).get("duration_min") or 0)
    return total


def timeline_time_minutes(day: dict) -> int | None:
    items = day.get("items") or []
    if not items:
        return None
    first_arrival = _first_activity_minutes(day)
    last_active_end = _last_active_end_minutes(day)
    if first_arrival is None or last_active_end is None:
        return None
    departure_transport = _first_int(day, ["hotel_departure_transport_min", "hotel_to_first_transport_min"]) or 0
    return_transport = _first_int(day, ["hotel_return_transport_min", "last_to_hotel_transport_min"]) or 0
    hotel_pause_total = _hotel_pause_minutes(day)
    total = (last_active_end + return_transport) - (first_arrival - departure_transport) - hotel_pause_total
    return max(total, 0)


def _items_time_minutes(items: list[dict]) -> int:
    total = 0
    for item in items:
        total += int(item.get("duration_min") or 0)
        total += int((item.get("transport_to_next") or {}).get("duration_min") or 0)
    return total


def _first_int(value: dict, keys: list[str]) -> int | None:
    for key in keys:
        raw = value.get(key)
        if raw is not None:
            return int(raw)
    return None


def _parse_time(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    try:
        return int(hour) * 60 + int(minute[:2])
    except ValueError:
        return None


def _first_arrival_minutes(items: list[dict]) -> int | None:
    for item in items:
        arrival = _parse_time(item.get("arrival_time"))
        if arrival is not None:
            return arrival
    return None


def _first_activity_minutes(day: dict) -> int | None:
    starts = []
    item_start = _first_arrival_minutes(day.get("items") or [])
    if item_start is not None:
        starts.append(item_start)
    for meal in day.get("meal_breaks") or []:
        if meal.get("included_in_item_duration"):
            continue
        meal_start = _parse_time(meal.get("start_time"))
        if meal_start is not None:
            starts.append(meal_start)
    return min(starts) if starts else None


def _last_active_end_minutes(day: dict) -> int | None:
    items = day.get("items") or []
    latest = None
    for item in items:
        arrival = _parse_time(item.get("arrival_time"))
        if arrival is None:
            continue
        end = arrival + int(item.get("duration_min") or 0)
        latest = end if latest is None else max(latest, end)
    for meal in day.get("meal_breaks") or []:
        if meal.get("included_in_item_duration"):
            continue
        start = _parse_time(meal.get("start_time"))
        if start is None:
            continue
        end = start + int(meal.get("duration_min") or meal.get("duration_minutes") or 0)
        latest = end if latest is None else max(latest, end)
    return latest


def _hotel_pause_minutes(day: dict) -> int:
    items_by_id = {str(item.get("poi_id") or ""): item for item in day.get("items") or []}
    total = 0
    for hotel_break in day.get("hotel_rest_breaks") or []:
        hotel_arrival = _parse_time(hotel_break.get("hotel_arrival_time"))
        before_poi = items_by_id.get(str(hotel_break.get("before_poi_id") or ""))
        next_arrival = _parse_time((before_poi or {}).get("arrival_time"))
        depart_transport = int(hotel_break.get("depart_from_hotel_transport_min") or 0)
        if hotel_arrival is None or next_arrival is None:
            total += int(hotel_break.get("duration_min") or 0)
            continue
        hotel_pause = max(0, (next_arrival - depart_transport) - hotel_arrival)
        total += hotel_pause
    return total
