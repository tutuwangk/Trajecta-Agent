from __future__ import annotations

from app.agents.intensity import daily_time_limit_minutes, daily_time_minutes, intensity_time_minutes
from app.agents.meal_rules import required_meal_slots


HARD_ISSUE_TYPES = {
    "daily_time_over_intensity_limit",
    "unknown_poi_scheduled",
    "unmatched_poi_scheduled",
    "excluded_place_scheduled",
    "unresolved_place_scheduled",
    "missing_transfer",
    "route_unknown",
    "long_transfer",
    "must_visit_missing",
    "avoid_visit_scheduled",
    "meal_slot_missing",
}


def verify_itinerary(
    itinerary: dict,
    user_profile: dict,
    route_matrix: list[dict],
    runtime_pois: list[dict] | None = None,
) -> dict:
    issues = _collect_issues(itinerary, user_profile, route_matrix, runtime_pois)
    return {"passed": not issues, "issues": issues}


def validate_hard_constraints(
    itinerary: dict,
    user_profile: dict,
    route_matrix: list[dict],
    runtime_pois: list[dict] | None = None,
) -> dict:
    issues = [issue for issue in _collect_issues(itinerary, user_profile, route_matrix, runtime_pois) if issue["type"] in HARD_ISSUE_TYPES]
    return {"passed": not issues, "issues": issues}


def review_soft_quality(
    itinerary: dict,
    user_profile: dict,
    route_matrix: list[dict],
    runtime_pois: list[dict] | None = None,
    llm_client=None,
) -> list[dict]:
    issues = [issue for issue in _collect_issues(itinerary, user_profile, route_matrix, runtime_pois) if issue["type"] not in HARD_ISSUE_TYPES]
    if llm_client is None:
        return issues
    try:
        payload = llm_client.json_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "## Role\n"
                        "你是旅行路线软评审助手。\n\n"
                        "## Mission\n"
                        "只识别体验层面的软问题，不修改路线事实。\n\n"
                        "## Hard Rules\n"
                        "- 不得新增地点与时间。\n"
                        "- 不得输出硬约束问题。\n"
                        "- 只输出 issues 数组，每条含 type、severity、message、suggestion、evidence。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "<itinerary>\n"
                        f"{itinerary}\n"
                        "</itinerary>\n\n"
                        "<user_profile>\n"
                        f"{user_profile}\n"
                        "</user_profile>\n\n"
                        "<output_schema>\n"
                        '{"issues":[{"type":"pace_dense","severity":"medium","message":"...","suggestion":"...","evidence":"Day 1"}]}\n'
                        "</output_schema>"
                    ),
                },
            ],
            step="review_itinerary_soft_quality",
            temperature=0.2,
        )
    except Exception:
        return issues
    for issue in (payload or {}).get("issues", []):
        if not isinstance(issue, dict):
            continue
        message = str(issue.get("message") or "").strip()
        suggestion = str(issue.get("suggestion") or "").strip()
        if not message and not suggestion:
            continue
        issues.append(
            {
                "type": str(issue.get("type") or "soft_review_issue"),
                "severity": str(issue.get("severity") or "medium"),
                "message": message or suggestion,
                "suggestion": suggestion or message,
                "evidence": str(issue.get("evidence") or "").strip(),
            }
        )
    return issues


def _collect_issues(
    itinerary: dict,
    user_profile: dict,
    route_matrix: list[dict],
    runtime_pois: list[dict] | None = None,
) -> list[dict]:
    issues: list[dict] = []
    runtime_by_id = {poi.get("poi_id"): poi for poi in runtime_pois or []}
    route_by_pair = {(edge.get("origin_poi_id"), edge.get("destination_poi_id")): edge for edge in route_matrix}
    scheduled_names: set[str] = set()

    for day in itinerary.get("days", []):
        items = day.get("items", [])
        segment_boundary_pairs = _segment_boundary_pairs(day)
        districts: set[str] = set()
        total_minutes = intensity_time_minutes(day)
        limit_minutes = daily_time_limit_minutes(user_profile)
        if total_minutes > limit_minutes and _has_removable_item(items, runtime_by_id, user_profile):
            issues.append(
                {
                    "type": "daily_time_over_intensity_limit",
                    "severity": "high",
                    "message": f"Day {day.get('day')} 预计总耗时约 {total_minutes} 分钟，超过当前强度上限。",
                    "suggestion": "缩短停留时间，减少移动距离，或把部分地点拆到其他天。",
                }
            )
        for item in items:
            scheduled_names.add(item.get("name", ""))
            poi = runtime_by_id.get(item.get("poi_id"))
            if not poi:
                issues.append(
                    {
                        "type": "unknown_poi_scheduled",
                        "severity": "high",
                        "message": f"{item.get('name')} 不在已确认地点列表中，不应进入路线。",
                        "suggestion": "删除该地点，或先完成地点确认。",
                    }
                )
                continue
            if poi.get("district"):
                districts.add(poi["district"])
            if not _is_plannable_poi(poi):
                issues.append(
                    {
                        "type": "unmatched_poi_scheduled",
                        "severity": "high",
                        "message": f"{item.get('name')} 尚未确认，不应进入路线。",
                        "suggestion": "移入不确定地点或让用户手动确认。",
                    }
                )
            if poi.get("final_decision") == "exclude":
                issues.append(
                    {
                        "type": "excluded_place_scheduled",
                        "severity": "high",
                        "message": f"{item.get('name')} 已被移除或默认排除，不应进入路线。",
                        "suggestion": "删除该地点，并把原因放入未安排地点。",
                    }
                )
            if poi.get("final_decision") == "unresolved":
                issues.append(
                    {
                        "type": "unresolved_place_scheduled",
                        "severity": "high",
                        "message": f"{item.get('name')} 还需要确认，不应作为正式路线节点。",
                        "suggestion": "先作为待确认地点展示，确认后再进入路线。",
                    }
                )
        if len(districts) > 2:
            issues.append(
                {
                    "type": "too_many_cross_area_moves",
                    "severity": "medium",
                    "message": f"Day {day.get('day')} 跨越 {len(districts)} 个区域，移动成本可能偏高。",
                    "suggestion": "优先保留同区域地点，远距离地点拆到其他天或后置。",
                }
            )
        meal_slots = day.get("meal_slots") or []
        if meal_slots:
            issues.extend(_collect_meal_slot_issues(day))
        elif len(items) >= 3 and not _has_meal_stop(items, runtime_by_id):
            issues.append(
                {
                    "type": "meal_stop_missing",
                    "severity": "low",
                    "message": f"Day {day.get('day')} 没有明确餐饮点，饭点安排可能不完整。",
                    "suggestion": "在午餐或晚餐时间补充餐饮地点，或提示用户自行选择。",
                }
            )
        for origin, destination in zip(items, items[1:]):
            if (origin.get("poi_id"), destination.get("poi_id")) in segment_boundary_pairs:
                continue
            explicit_transport = origin.get("transport_to_next") or {}
            explicit_duration = explicit_transport.get("duration_min")
            if explicit_duration is not None:
                if explicit_duration >= 60:
                    issues.append(
                        {
                            "type": "long_transfer",
                            "severity": "medium",
                            "message": f"{origin.get('name')} 到 {destination.get('name')} 需要较长移动，不适合连续安排。",
                            "suggestion": "将远距离地点拆到单独一天或后置。",
                        }
                    )
                continue
            edge = route_by_pair.get((origin.get("poi_id"), destination.get("poi_id")))
            if not edge:
                issues.append(
                    {
                        "type": "missing_transfer",
                        "severity": "medium",
                        "message": f"{origin.get('name')} 到 {destination.get('name')} 缺少路径数据。",
                        "suggestion": "重新计算路线距离，或调整路线顺序。",
                    }
                )
                continue
            if edge.get("duration_min") is None or edge.get("relation") == "unknown":
                issues.append(
                    {
                        "type": "route_unknown",
                        "severity": "medium",
                        "message": f"{origin.get('name')} 到 {destination.get('name')} 的交通时间不可用。",
                        "suggestion": "重新计算路径，失败时改用更可靠的相邻地点。",
                    }
                )
            if edge.get("relation") == "separate_day":
                issues.append(
                    {
                        "type": "long_transfer",
                        "severity": "medium",
                        "message": f"{origin.get('name')} 到 {destination.get('name')} 需要较长移动，不适合连续安排。",
                        "suggestion": "将远距离地点拆到单独一天或后置。",
                    }
                )
    constraints = user_profile.get("constraints", {})
    for name in constraints.get("must_visit", []):
        if name and not any(name in scheduled for scheduled in scheduled_names):
            issues.append(
                {
                    "type": "must_visit_missing",
                    "severity": "high",
                    "message": f"必去地点 {name} 未进入路线。",
                    "suggestion": "重新排序并优先安排该地点。",
                }
            )
    for name in constraints.get("avoid_visit", []):
        if name and any(name in scheduled for scheduled in scheduled_names):
            issues.append(
                {
                    "type": "avoid_visit_scheduled",
                    "severity": "high",
                    "message": f"用户不想去的地点 {name} 被安排进路线。",
                    "suggestion": "删除该地点并补充替代方案。",
                }
            )
    return issues


def _has_meal_stop(items: list[dict], runtime_by_id: dict) -> bool:
    for item in items:
        poi = runtime_by_id.get(item.get("poi_id"), {})
        semantics = poi.get("planning_semantics") or {}
        if semantics.get("experience_type") in {"full_meal", "snack"}:
            return True
        if semantics.get("experience_type"):
            continue
        if item.get("meal_roles"):
            return True
        text = f"{item.get('time_block', '')}{item.get('name', '')}{poi.get('category', '')}{poi.get('category_normalized', '')}"
        if poi.get("category") == "restaurant" and not any(token in text for token in ["咖啡", "奶茶", "茶饮", "果汁", "甜品"]):
            return True
        if any(token in text for token in ["午餐", "晚餐", "早餐", "火锅", "面馆", "小吃", "餐厅"]):
            return True
    return False


def _collect_meal_slot_issues(day: dict) -> list[dict]:
    issues: list[dict] = []
    required_slots = {slot for slot in _expected_required_slots(day) if slot in {"lunch", "dinner"}}
    planned_slots = {
        str(slot.get("slot"))
        for slot in day.get("meal_slots") or []
        if str(slot.get("requirement") or "required") == "required"
    }
    for slot in sorted(required_slots - planned_slots):
        issues.append(
            {
                "type": "meal_slot_missing",
                "severity": "high",
                "message": f"Day {day.get('day')} 缺少必需的{_slot_label(slot)}安排。",
                "suggestion": f"补充可承接{_slot_label(slot)}的真实餐饮地点，或改为就近/场内用餐。",
            }
        )
    for slot in day.get("meal_slots") or []:
        if str(slot.get("requirement") or "required") != "required":
            continue
        if not _is_meal_slot_satisfied(day, slot):
            issues.append(
                {
                    "type": "meal_slot_missing",
                    "severity": "high",
                    "message": f"Day {day.get('day')} 的{_slot_label(slot.get('slot'))}尚未真正落地。",
                    "suggestion": f"让{_slot_label(slot.get('slot'))}由真实餐厅、场内用餐或就近补位之一明确承接。",
                }
            )
    return issues


def _expected_required_slots(day: dict) -> set[str]:
    return required_meal_slots(day)


def _is_meal_slot_satisfied(day: dict, slot: dict) -> bool:
    slot_name = str(slot.get("slot"))
    source = str(slot.get("source"))
    if source == "poi":
        poi_id = slot.get("poi_id")
        for item in day.get("items") or []:
            if item.get("poi_id") == poi_id and slot_name in (item.get("meal_roles") or []):
                return True
        return False
    if source == "inside_poi":
        within_poi_id = slot.get("within_poi_id")
        return any(
            meal.get("slot") == slot_name
            and meal.get("within_poi_id") == within_poi_id
            and meal.get("included_in_item_duration")
            for meal in day.get("meal_breaks") or []
        )
    if source == "fallback_nearby":
        return any(meal.get("slot") == slot_name and meal.get("source") == "fallback_nearby" for meal in day.get("meal_breaks") or [])
    return False


def _has_removable_item(items: list[dict], runtime_by_id: dict, user_profile: dict) -> bool:
    must_visit = user_profile.get("constraints", {}).get("must_visit", [])
    return any(not _is_must_item(item, runtime_by_id.get(item.get("poi_id"), {}), must_visit) for item in items)


def _is_must_item(item: dict, poi: dict, must_visit: list[str]) -> bool:
    if poi.get("user_override") == "must_include":
        return True
    return any(name and name in item.get("name", "") for name in must_visit)


def _parse_time(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    hour, minute = value.split(":", 1)
    try:
        return int(hour) * 60 + int(minute[:2])
    except ValueError:
        return None


def _overlaps(start: int, end: int, window_start: int, window_end: int) -> bool:
    return start < window_end and end > window_start


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _slot_label(slot: str | None) -> str:
    mapping = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
    return mapping.get(slot or "", "用餐")


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


def _outing_windows(day: dict) -> list[tuple[int, int]]:
    items = day.get("items") or []
    if not items:
        return []
    if day.get("segments"):
        items_by_id = {item.get("poi_id"): item for item in items}
        windows: list[tuple[int, int]] = []
        for segment in day.get("segments") or []:
            if segment.get("kind") != "outing":
                continue
            segment_items = [items_by_id.get(poi_id) for poi_id in segment.get("poi_ids") or []]
            segment_items = [item for item in segment_items if item]
            window = _items_window(segment_items)
            if window:
                windows.append(window)
        return windows
    window = _items_window(items)
    return [window] if window else []


def _items_window(items: list[dict]) -> tuple[int, int] | None:
    first_arrival = None
    current = None
    for index, item in enumerate(items):
        arrival = _parse_time(item.get("arrival_time"))
        if arrival is None:
            arrival = current
        if arrival is None:
            continue
        if first_arrival is None:
            first_arrival = arrival
        current = arrival + _int(item.get("duration_min"))
        if index < len(items) - 1:
            current += _int((item.get("transport_to_next") or {}).get("duration_min"))
    if first_arrival is None or current is None:
        return None
    return (first_arrival, current)


def _segment_boundary_pairs(day: dict) -> set[tuple[str, str]]:
    boundaries: set[tuple[str, str]] = set()
    segments = day.get("segments") or []
    for index, segment in enumerate(segments):
        if segment.get("kind") != "hotel_rest":
            continue
        previous_outing = _nearest_outing_segment(segments, index, -1)
        next_outing = _nearest_outing_segment(segments, index, 1)
        if not previous_outing or not next_outing:
            continue
        after_poi_id = str((previous_outing.get("poi_ids") or [None])[-1] or "").strip()
        before_poi_id = str((next_outing.get("poi_ids") or [None])[0] or "").strip()
        if after_poi_id and before_poi_id:
            boundaries.add((after_poi_id, before_poi_id))
    return boundaries


def _nearest_outing_segment(segments: list[dict], start_index: int, step: int) -> dict | None:
    index = start_index + step
    while 0 <= index < len(segments):
        segment = segments[index]
        if segment.get("kind") == "outing":
            return segment
        index += step
    return None
