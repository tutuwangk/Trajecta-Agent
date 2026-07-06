from __future__ import annotations

LUNCH_THRESHOLD_MIN = 11 * 60 + 30
DINNER_THRESHOLD_MIN = 17 * 60 + 30


def required_meal_slots(day: dict) -> set[str]:
    required: set[str] = set()
    for start, end in _out_of_hotel_windows(day):
        if start <= LUNCH_THRESHOLD_MIN < end:
            required.add("lunch")
        if start <= DINNER_THRESHOLD_MIN < end:
            required.add("dinner")
    return required


def _out_of_hotel_windows(day: dict) -> list[tuple[int, int]]:
    items = day.get("items") or []
    if not items:
        return []
    if day.get("segments"):
        return _segmented_windows(day, items)
    window = _linear_window(day, items)
    return [window] if window else []


def _linear_window(day: dict, items: list[dict]) -> tuple[int, int] | None:
    first_arrival = _first_arrival_minutes(items)
    last_end = _last_item_end_minutes(items)
    if first_arrival is None or last_end is None:
        return None
    departure_transport = _int(day.get("hotel_departure_transport_min") or day.get("hotel_to_first_transport_min"))
    return_transport = _int(day.get("hotel_return_transport_min") or day.get("last_to_hotel_transport_min"))
    return (first_arrival - departure_transport, last_end + return_transport)


def _segmented_windows(day: dict, items: list[dict]) -> list[tuple[int, int]]:
    items_by_id = {str(item.get("poi_id") or ""): item for item in items}
    hotel_breaks_by_after = {
        str(hotel_break.get("after_poi_id") or ""): hotel_break for hotel_break in day.get("hotel_rest_breaks") or []
    }
    hotel_breaks_by_before = {
        str(hotel_break.get("before_poi_id") or ""): hotel_break for hotel_break in day.get("hotel_rest_breaks") or []
    }
    outing_segments = [segment for segment in day.get("segments") or [] if segment.get("kind") == "outing"]
    windows: list[tuple[int, int]] = []

    for index, segment in enumerate(outing_segments):
        poi_ids = [str(poi_id or "") for poi_id in segment.get("poi_ids") or [] if str(poi_id or "").strip()]
        segment_items = [items_by_id.get(poi_id) for poi_id in poi_ids]
        segment_items = [item for item in segment_items if item]
        if not segment_items:
            continue
        first_arrival = _parse_time(segment_items[0].get("arrival_time"))
        last_end = _parse_time(segment_items[-1].get("arrival_time"))
        if first_arrival is None or last_end is None:
            continue
        last_end += _int(segment_items[-1].get("duration_min"))
        if index == 0:
            departure_transport = _int(day.get("hotel_departure_transport_min") or day.get("hotel_to_first_transport_min"))
        else:
            departure_transport = _int(hotel_breaks_by_before.get(poi_ids[0], {}).get("depart_from_hotel_transport_min"))
        if index == len(outing_segments) - 1:
            return_transport = _int(day.get("hotel_return_transport_min") or day.get("last_to_hotel_transport_min"))
        else:
            return_transport = _int(hotel_breaks_by_after.get(poi_ids[-1], {}).get("return_to_hotel_transport_min"))
        windows.append((first_arrival - departure_transport, last_end + return_transport))
    return windows


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


def _last_item_end_minutes(items: list[dict]) -> int | None:
    current = None
    latest = None
    for index, item in enumerate(items):
        arrival = _parse_time(item.get("arrival_time"))
        if arrival is None:
            arrival = current
        if arrival is None:
            continue
        end = arrival + _int(item.get("duration_min"))
        latest = end if latest is None else max(latest, end)
        current = end
        if index < len(items) - 1:
            current += _int((item.get("transport_to_next") or {}).get("duration_min"))
    return latest


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
