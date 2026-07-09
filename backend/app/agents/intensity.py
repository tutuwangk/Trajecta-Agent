from __future__ import annotations


DAILY_TIME_LIMITS = {
    "low": 300,
    "medium": 540,
    "high": 840,
}


def normalize_physical_intensity(value: str | None, avoid_too_tired: bool = False) -> str:
    if value in DAILY_TIME_LIMITS:
        return value
    return "low" if avoid_too_tired else "medium"


def daily_time_limit_minutes(user_profile: dict) -> int:
    constraints = user_profile.get("constraints", {})
    intensity = normalize_physical_intensity(
        constraints.get("physical_intensity"),
        bool(constraints.get("avoid_too_tired")),
    )
    return DAILY_TIME_LIMITS[intensity]


def daily_time_minutes(day_or_items: dict | list[dict]) -> int:
    if isinstance(day_or_items, dict):
        timeline_minutes = _timeline_time_minutes(day_or_items.get("items") or [])
        if timeline_minutes is not None:
            return timeline_minutes

        explicit = _first_int(
            day_or_items,
            ["total_outing_min", "total_outing_minutes", "outing_duration_min", "outing_duration_minutes"],
        )
        if explicit is not None:
            return explicit

        total = _first_int(day_or_items, ["hotel_departure_transport_min", "hotel_to_first_transport_min"]) or 0
        total += _first_int(day_or_items, ["hotel_return_transport_min", "last_to_hotel_transport_min"]) or 0
        for meal in day_or_items.get("meal_breaks") or []:
            total += int(meal.get("duration_min") or meal.get("duration_minutes") or 0)
        total += _items_time_minutes(day_or_items.get("items") or [])
        return total
    return _items_time_minutes(day_or_items)


def _timeline_time_minutes(items: list[dict]) -> int | None:
    if not items:
        return None

    first_arrival = _clock_minutes(items[0].get("arrival_time"))
    last_arrival = _clock_minutes(items[-1].get("arrival_time"))
    if first_arrival is None or last_arrival is None:
        return None

    last_departure = last_arrival + int(items[-1].get("duration_min") or 0)
    return max(0, last_departure - first_arrival)


def _clock_minutes(value: str | None) -> int | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        return None
    if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
        return None
    return hours * 60 + minutes


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
