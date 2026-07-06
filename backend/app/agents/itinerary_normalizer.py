from __future__ import annotations

from app.agents.intensity import daily_time_limit_minutes, daily_time_minutes, sync_day_total_time
from app.agents.meal_rules import required_meal_slots

DEFAULT_RELAXED_FIRST_ARRIVAL_MIN = 10 * 60
DEFAULT_HIGH_FIRST_ARRIVAL_MIN = 9 * 60
BREAKFAST_WINDOW = (7 * 60, 9 * 60 + 30)
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
        _sync_day_segments(day)
        _sync_day_transports(day, route_by_pair)
        _rebuild_timing(day, runtime_by_id, _is_high_intensity(user_profile))
        _trim_optional_items(day, itinerary, runtime_by_id, route_by_pair, must_visit, limit_minutes)
        _sync_day_segments(day)
        _sync_day_transports(day, route_by_pair)
        _rebuild_timing(day, runtime_by_id, _is_high_intensity(user_profile))
        sync_day_total_time(day)
        day["intensity_outing_min"] = _trim_pressure_minutes(day, runtime_by_id)


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
    while day.get("items") and _trim_pressure_minutes(day, runtime_by_id) > limit_minutes:
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
        _sync_day_segments(day)
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
    if item.get("trim_priority") == "never_trim_before_meal":
        return True
    prev_transfer = _int((items[index - 1].get("transport_to_next") or {}).get("duration_min")) if index > 0 else 0
    next_transfer = _int((item.get("transport_to_next") or {}).get("duration_min"))
    if item.get("trim_priority") == "keep_if_low_detour":
        local_quick_cost = _local_quick_stop_cost(item, prev_transfer, next_transfer)
        quick_cost = _int(item.get("quick_stop_total_cost_min"))
        if quick_cost and local_quick_cost:
            return min(quick_cost, local_quick_cost) <= 45
        if quick_cost:
            return quick_cost <= 45
        return local_quick_cost <= 45
    duration = _int(item.get("duration_min"))
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
    for item in items:
        item.pop("meal_roles", None)
    current = _default_start_time(day, items, runtime_by_id, high_intensity)
    if day.get("segments"):
        _rebuild_segmented_timing(day, runtime_by_id, current)
        return
    if day.get("meal_slots"):
        _rebuild_timing_from_meal_slots(day, runtime_by_id, current)
        sync_day_total_time(day)
        return
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
        "slot": _slot_from_label(label),
        "start_time": start_time,
        "duration_min": MEAL_BREAK_MIN,
        "within_poi_id": item.get("poi_id"),
        "included_in_item_duration": True,
        "source": "inside_poi",
    }


def _should_insert_external_meal(current: int, window_start: int, window_end: int) -> bool:
    return window_start <= current <= window_end


def _is_meal_poi(item: dict, poi: dict) -> bool:
    if item.get("scheduled_role") == "meal_stop" or item.get("burden_role") == "protected_basic":
        return True
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
    if (
        poi.get("user_override") == "arrange_nearby"
        and ((poi.get("planning_semantics") or {}).get("chain_resolution_mode") == "route_dependent_chain" or poi.get("route_branch_options"))
        and location.get("lng") is not None
        and location.get("lat") is not None
    ):
        return True
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


def _local_quick_stop_cost(item: dict, prev_transfer: int, next_transfer: int) -> int:
    nearby_transfer = min([value for value in [prev_transfer, next_transfer] if value > 0], default=max(prev_transfer, next_transfer))
    return nearby_transfer + min(_int(item.get("duration_min")), 15)


def _sync_day_segments(day: dict) -> None:
    raw_segments = day.get("segments") or []
    if not raw_segments:
        return
    ordered_poi_ids = [str(item.get("poi_id") or "") for item in day.get("items") or [] if str(item.get("poi_id") or "").strip()]
    if not ordered_poi_ids:
        day["segments"] = []
        return
    allowed_poi_ids = set(ordered_poi_ids)
    synced: list[dict] = []
    seen: set[str] = set()

    for raw_segment in raw_segments:
        if raw_segment.get("kind") != "outing":
            synced.append(
                {
                    "kind": "hotel_rest",
                    "duration_min": _int(raw_segment.get("duration_min")),
                    "reason": str(raw_segment.get("reason") or "").strip(),
                }
            )
            continue
        filtered = []
        for poi_id in raw_segment.get("poi_ids") or []:
            normalized_poi_id = str(poi_id or "").strip()
            if not normalized_poi_id or normalized_poi_id not in allowed_poi_ids or normalized_poi_id in seen:
                continue
            filtered.append(normalized_poi_id)
            seen.add(normalized_poi_id)
        if not filtered:
            continue
        segment_time = str(raw_segment.get("segment_time") or "").strip().lower()
        if synced and synced[-1].get("kind") == "outing":
            synced[-1]["poi_ids"].extend(filtered)
            continue
        synced.append({"kind": "outing", "segment_time": segment_time, "poi_ids": filtered})

    remaining = [poi_id for poi_id in ordered_poi_ids if poi_id not in seen]
    if remaining:
        if synced and synced[-1].get("kind") == "outing":
            synced[-1]["poi_ids"].extend(remaining)
        else:
            synced.append({"kind": "outing", "segment_time": "", "poi_ids": remaining})

    cleaned: list[dict] = []
    for index, segment in enumerate(synced):
        if segment.get("kind") != "hotel_rest":
            cleaned.append(segment)
            continue
        has_outing_before = any(previous.get("kind") == "outing" for previous in synced[:index])
        has_outing_after = any(next_segment.get("kind") == "outing" for next_segment in synced[index + 1 :])
        if has_outing_before and has_outing_after:
            cleaned.append(segment)
    day["segments"] = cleaned


def _trim_pressure_minutes(day: dict, runtime_by_id: dict) -> int:
    total = _int(day.get("hotel_departure_transport_min") or day.get("hotel_to_first_transport_min"))
    total += _int(day.get("hotel_return_transport_min") or day.get("last_to_hotel_transport_min"))
    for hotel_break in day.get("hotel_rest_breaks") or []:
        total += _int(hotel_break.get("return_to_hotel_transport_min"))
        total += _int(hotel_break.get("depart_from_hotel_transport_min"))
    for meal in day.get("meal_breaks") or []:
        if meal.get("included_in_item_duration"):
            continue
        if meal.get("slot") in {"lunch", "dinner"}:
            continue
        total += _int(meal.get("duration_min") or meal.get("duration_minutes"))
    for item in day.get("items") or []:
        poi = runtime_by_id.get(item.get("poi_id"), {})
        total += _item_trim_pressure(item, poi)
        total += _int((item.get("transport_to_next") or {}).get("duration_min"))
    return total


def _item_trim_pressure(item: dict, poi: dict) -> int:
    role = str(item.get("scheduled_role") or "")
    if role == "meal_stop" or item.get("burden_role") == "protected_basic":
        return 0
    if role == "quick_stop" or item.get("burden_role") == "light_detour":
        return min(_int(item.get("duration_min")), 15)
    if _is_meal_poi(item, poi):
        return 0
    return _int(item.get("duration_min"))


def _rebuild_timing_from_meal_slots(day: dict, runtime_by_id: dict, current: int) -> None:
    items = day.get("items") or []
    meal_slots = _resolve_day_meal_slots(day, items, runtime_by_id)
    day["meal_slots"] = meal_slots
    meal_breaks: list[dict] = []
    pending_fallbacks = sorted(
        [slot for slot in meal_slots if slot.get("source") == "fallback_nearby"],
        key=lambda slot: _slot_preferred_time(slot.get("slot")),
    )
    fallback_index = 0

    for index, item in enumerate(items):
        item["arrival_time"] = _format_time(current)
        current += _int(item.get("duration_min"))
        _apply_poi_meal_roles(item, meal_slots)
        meal_breaks.extend(_build_inside_meals(item, meal_slots))

        while fallback_index < len(pending_fallbacks) and _slot_preferred_time(pending_fallbacks[fallback_index].get("slot")) <= current:
            start = max(current, _slot_preferred_time(pending_fallbacks[fallback_index].get("slot")))
            meal_breaks.append(_fallback_meal_break(pending_fallbacks[fallback_index], start))
            current = start + MEAL_BREAK_MIN
            fallback_index += 1

        if index < len(items) - 1:
            current += _int((item.get("transport_to_next") or {}).get("duration_min"))

    while fallback_index < len(pending_fallbacks):
        start = max(current, _slot_preferred_time(pending_fallbacks[fallback_index].get("slot")))
        meal_breaks.append(_fallback_meal_break(pending_fallbacks[fallback_index], start))
        current = start + MEAL_BREAK_MIN
        fallback_index += 1

    day["meal_breaks"] = meal_breaks
    _drop_stale_required_meals(day)
    sync_day_total_time(day)


def _rebuild_segmented_timing(day: dict, runtime_by_id: dict, current: int) -> None:
    items_by_id = {item.get("poi_id"): item for item in day.get("items") or []}
    meal_slots = _resolve_day_meal_slots(day, day.get("items") or [], runtime_by_id)
    day["meal_slots"] = meal_slots
    meal_breaks: list[dict] = []
    hotel_rest_breaks = _hotel_rest_breaks_by_after(day)
    total_minutes = (day.get("hotel_departure_transport_min") or day.get("hotel_to_first_transport_min") or 0) + (
        day.get("hotel_return_transport_min") or day.get("last_to_hotel_transport_min") or 0
    )
    pending_fallbacks = sorted(
        [slot for slot in meal_slots if slot.get("source") == "fallback_nearby"],
        key=lambda slot: _slot_preferred_time(slot.get("slot")),
    )
    fallback_index = 0

    for segment in day.get("segments") or []:
        if segment.get("kind") != "outing":
            continue
        current = max(current, _segment_start_time(segment, runtime_by_id, items_by_id, current))
        for index, poi_id in enumerate(segment.get("poi_ids") or []):
            item = items_by_id.get(poi_id)
            if not item:
                continue
            item["arrival_time"] = _format_time(current)
            duration = _int(item.get("duration_min"))
            current += duration
            total_minutes += duration
            _apply_poi_meal_roles(item, meal_slots)
            meal_breaks.extend(_build_inside_meals(item, meal_slots))

            while fallback_index < len(pending_fallbacks) and _slot_preferred_time(pending_fallbacks[fallback_index].get("slot")) <= current:
                start = max(current, _slot_preferred_time(pending_fallbacks[fallback_index].get("slot")))
                meal_breaks.append(_fallback_meal_break(pending_fallbacks[fallback_index], start))
                total_minutes += MEAL_BREAK_MIN
                current = start + MEAL_BREAK_MIN
                fallback_index += 1

            if index < len(segment.get("poi_ids") or []) - 1:
                total_minutes += _int((item.get("transport_to_next") or {}).get("duration_min"))
                current += _int((item.get("transport_to_next") or {}).get("duration_min"))

        last_poi_id = (segment.get("poi_ids") or [None])[-1]
        hotel_break = hotel_rest_breaks.get(last_poi_id)
        if hotel_break:
            current += _int(hotel_break.get("return_to_hotel_transport_min"))
            hotel_break["hotel_arrival_time"] = _format_time(current)
            current += _int(hotel_break.get("duration_min"))
            hotel_break["rest_end_time"] = _format_time(current)
            total_minutes += _int(hotel_break.get("return_to_hotel_transport_min")) + _int(hotel_break.get("depart_from_hotel_transport_min"))
            current += _int(hotel_break.get("depart_from_hotel_transport_min"))
            hotel_break["next_departure_time"] = _format_time(current)

    while fallback_index < len(pending_fallbacks):
        start = max(current, _slot_preferred_time(pending_fallbacks[fallback_index].get("slot")))
        meal_breaks.append(_fallback_meal_break(pending_fallbacks[fallback_index], start))
        total_minutes += MEAL_BREAK_MIN
        current = start + MEAL_BREAK_MIN
        fallback_index += 1

    day["meal_breaks"] = meal_breaks
    _drop_stale_required_meals(day)
    day["total_outing_min"] = total_minutes


def _resolve_day_meal_slots(day: dict, items: list[dict], runtime_by_id: dict) -> list[dict]:
    item_ids = {item.get("poi_id") for item in items}
    resolved: list[dict] = []
    reserved_poi_ids: set[str] = set()
    for raw_slot in day.get("meal_slots") or []:
        slot = dict(raw_slot)
        source = slot.get("source")
        slot_name = str(slot.get("slot") or "")
        if source == "poi" and slot.get("poi_id") not in item_ids:
            replacement = _replacement_meal_slot(slot_name, items, runtime_by_id, reserved_poi_ids)
            if replacement is not None:
                slot = replacement
                reserved_poi_ids.add(str(replacement.get("poi_id") or replacement.get("within_poi_id") or ""))
            elif slot.get("requirement") == "required":
                slot = {"slot": slot.get("slot"), "requirement": "required", "source": "fallback_nearby"}
            else:
                continue
        if source == "inside_poi":
            replacement = _replacement_meal_slot(slot_name, items, runtime_by_id, reserved_poi_ids)
            within_poi_id = slot.get("within_poi_id")
            if replacement is not None:
                slot = replacement
                reserved_poi_ids.add(str(replacement.get("poi_id") or replacement.get("within_poi_id") or ""))
            elif within_poi_id not in item_ids:
                if slot.get("requirement") == "required":
                    slot = {"slot": slot.get("slot"), "requirement": "required", "source": "fallback_nearby"}
                else:
                    continue
        if source == "fallback_nearby" and slot.get("requirement") == "required":
            replacement = _replacement_meal_slot(slot_name, items, runtime_by_id, reserved_poi_ids)
            if replacement is not None:
                slot = replacement
                reserved_poi_ids.add(str(replacement.get("poi_id") or replacement.get("within_poi_id") or ""))
        if slot.get("source") == "poi":
            reserved_poi_ids.add(str(slot.get("poi_id") or ""))
        if slot.get("source") == "inside_poi":
            reserved_poi_ids.add(str(slot.get("within_poi_id") or ""))
        resolved.append(slot)
    return resolved


def _replacement_meal_slot(slot_name: str, items: list[dict], runtime_by_id: dict, reserved_poi_ids: set[str]) -> dict | None:
    candidates: list[tuple[tuple[int, int, int], dict]] = []
    preferred_time = _slot_preferred_time(slot_name)
    for index, item in enumerate(items):
        poi_id = str(item.get("poi_id") or "")
        if not poi_id or poi_id in reserved_poi_ids:
            continue
        poi = runtime_by_id.get(poi_id, {})
        if not _can_cover_meal_slot(item, poi, slot_name):
            continue
        if not _meal_slot_timing_plausible(item, slot_name, index, len(items)):
            continue
        arrival = _parse_time(item.get("arrival_time"))
        distance = abs(arrival - preferred_time) if arrival is not None else 0
        role_penalty = 0 if item.get("scheduled_role") == "meal_stop" or item.get("burden_role") == "protected_basic" else 1
        order_penalty = index if slot_name != "dinner" else max(len(items) - 1 - index, 0)
        candidates.append(((role_penalty, distance, order_penalty), item))
    if not candidates:
        return None
    item = min(candidates, key=lambda candidate: candidate[0])[1]
    return {"slot": slot_name, "requirement": "required", "source": "poi", "poi_id": str(item.get("poi_id") or "")}


def _can_cover_meal_slot(item: dict, poi: dict, slot_name: str) -> bool:
    if item.get("scheduled_role") == "quick_stop" or item.get("burden_role") == "light_detour":
        return False
    semantics = poi.get("planning_semantics") or {}
    experience_type = str(semantics.get("experience_type") or "")
    if experience_type == "light_drink":
        return False
    meal_capability = str(semantics.get("meal_capability") or "")
    if _meal_capability_supports_slot(meal_capability, slot_name):
        return True
    if experience_type == "full_meal":
        return slot_name in {"lunch", "dinner"}
    if experience_type == "nightlife":
        return slot_name == "dinner"
    if item.get("scheduled_role") == "meal_stop" and _is_meal_poi(item, poi):
        return slot_name in {"lunch", "dinner"}
    return False


def _meal_capability_supports_slot(meal_capability: str, slot_name: str) -> bool:
    mapping = {
        "none": set(),
        "breakfast_only": {"breakfast"},
        "lunch_only": {"lunch"},
        "dinner_only": {"dinner"},
        "breakfast_lunch": {"breakfast", "lunch"},
        "lunch_dinner": {"lunch", "dinner"},
        "all_day_light_meal": set(),
    }
    return slot_name in mapping.get(meal_capability, set())


def _meal_slot_timing_plausible(item: dict, slot_name: str, index: int, item_count: int) -> bool:
    arrival = _parse_time(item.get("arrival_time"))
    if arrival is None:
        if slot_name == "breakfast":
            return index == 0
        if slot_name == "dinner":
            return index == item_count - 1
        return True
    duration = _int(item.get("duration_min"))
    end = arrival + duration
    window_start, window_end = _meal_slot_window(slot_name)
    if _overlaps(arrival, end, window_start, window_end):
        return True
    if slot_name == "dinner":
        return arrival >= 16 * 60 + 30
    if slot_name == "breakfast":
        return end <= 10 * 60 + 30
    return arrival <= 13 * 60 + 30 and end >= 11 * 60


def _meal_slot_window(slot_name: str) -> tuple[int, int]:
    if slot_name == "breakfast":
        return BREAKFAST_WINDOW
    if slot_name == "dinner":
        return DINNER_WINDOW
    return LUNCH_WINDOW


def _drop_stale_required_meals(day: dict) -> None:
    required = required_meal_slots(day)
    removed_slots = {
        str(slot.get("slot"))
        for slot in day.get("meal_slots") or []
        if str(slot.get("requirement") or "required") == "required"
        and str(slot.get("slot") or "") in {"lunch", "dinner"}
        and str(slot.get("slot") or "") not in required
    }
    if not removed_slots:
        return
    day["meal_slots"] = [
        slot
        for slot in day.get("meal_slots") or []
        if not (
            str(slot.get("requirement") or "required") == "required"
            and str(slot.get("slot") or "") in removed_slots
        )
    ]
    day["meal_breaks"] = [meal for meal in day.get("meal_breaks") or [] if str(meal.get("slot") or "") not in removed_slots]
    for item in day.get("items") or []:
        roles = [role for role in item.get("meal_roles") or [] if str(role or "") not in removed_slots]
        if roles:
            item["meal_roles"] = roles
        else:
            item.pop("meal_roles", None)


def _apply_poi_meal_roles(item: dict, meal_slots: list[dict]) -> None:
    roles = [
        str(slot.get("slot"))
        for slot in meal_slots
        if slot.get("source") == "poi" and slot.get("poi_id") == item.get("poi_id")
    ]
    if roles:
        item["meal_roles"] = list(dict.fromkeys(roles))


def _build_inside_meals(item: dict, meal_slots: list[dict]) -> list[dict]:
    result: list[dict] = []
    for slot in meal_slots:
        if slot.get("source") != "inside_poi" or slot.get("within_poi_id") != item.get("poi_id"):
            continue
        result.append(
            {
                "label": _slot_label(slot.get("slot")),
                "slot": slot.get("slot"),
                "start_time": _format_time(_slot_inside_time(slot.get("slot"))),
                "duration_min": MEAL_BREAK_MIN,
                "within_poi_id": item.get("poi_id"),
                "included_in_item_duration": True,
                "source": "inside_poi",
            }
        )
    return result


def _fallback_meal_break(slot: dict, start_minutes: int) -> dict:
    return {
        "label": _slot_label(slot.get("slot")),
        "slot": slot.get("slot"),
        "start_time": _format_time(start_minutes),
        "duration_min": MEAL_BREAK_MIN,
        "source": "fallback_nearby",
    }


def _slot_from_label(label: str) -> str:
    mapping = {"早餐": "breakfast", "午餐": "lunch", "晚餐": "dinner"}
    return mapping.get(label, "lunch")


def _slot_label(slot: str | None) -> str:
    mapping = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
    return mapping.get(slot or "", "用餐")


def _slot_preferred_time(slot: str | None) -> int:
    if slot == "breakfast":
        return BREAKFAST_WINDOW[0]
    if slot == "dinner":
        return DINNER_WINDOW[0]
    return LUNCH_WINDOW[0]


def _slot_inside_time(slot: str | None) -> int:
    if slot == "breakfast":
        return 8 * 60
    if slot == "dinner":
        return 18 * 60
    return 12 * 60


def _default_start_time(day: dict, items: list[dict], runtime_by_id: dict, high_intensity: bool) -> int:
    explicit = _parse_time(items[0].get("arrival_time"))
    if explicit is not None:
        return explicit
    if day.get("segments"):
        first_outing = next((segment for segment in day.get("segments") or [] if segment.get("kind") == "outing"), None)
        if first_outing:
            inferred = _segment_start_time(first_outing, runtime_by_id, {item.get("poi_id"): item for item in items}, None)
            if inferred is not None:
                return inferred
    first_poi = runtime_by_id.get(items[0].get("poi_id"), {})
    suitability = _time_suitability(first_poi)
    if "night" in suitability or "evening" in suitability:
        return 18 * 60
    if "midday" in suitability:
        return 11 * 60 + 30
    return DEFAULT_HIGH_FIRST_ARRIVAL_MIN if high_intensity else DEFAULT_RELAXED_FIRST_ARRIVAL_MIN


def _segment_start_time(segment: dict, runtime_by_id: dict, items_by_id: dict, fallback_current: int | None) -> int:
    segment_time = str(segment.get("segment_time") or "").strip().lower()
    if segment_time == "night":
        return 18 * 60
    if segment_time == "evening":
        return 18 * 60
    if segment_time == "afternoon":
        return 14 * 60
    if segment_time == "midday":
        return 11 * 60 + 30
    if segment_time == "morning":
        return 10 * 60
    first_poi_id = (segment.get("poi_ids") or [None])[0]
    poi = runtime_by_id.get(first_poi_id, {})
    suitability = _time_suitability(poi)
    if "night" in suitability:
        return 18 * 60
    if "evening" in suitability:
        return 18 * 60
    if "midday" in suitability:
        return 11 * 60 + 30
    if "afternoon" in suitability and "morning" not in suitability:
        return 14 * 60
    return fallback_current or DEFAULT_RELAXED_FIRST_ARRIVAL_MIN


def _time_suitability(poi: dict) -> list[str]:
    semantics = poi.get("planning_semantics") or {}
    return list(semantics.get("time_suitability") or poi.get("best_time") or [])


def _hotel_rest_breaks_by_after(day: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for hotel_break in day.get("hotel_rest_breaks") or []:
        after_poi_id = hotel_break.get("after_poi_id")
        if after_poi_id:
            result[str(after_poi_id)] = hotel_break
    return result
