from __future__ import annotations

from app.agents.intensity import component_time_minutes, daily_time_ceiling_minutes, daily_time_limit_minutes, daily_time_minutes
from app.agents.meal_rules import required_meal_slots
from app.planning.segment_boundaries import hotel_rest_boundary_pairs


FACTUAL_ISSUE_TYPES = {
    "no_places_scheduled",
    "unknown_poi_scheduled",
    "unmatched_poi_scheduled",
    "excluded_place_scheduled",
    "unresolved_place_scheduled",
    "missing_transfer",
    "route_unknown",
    "avoid_visit_scheduled",
    "daily_absolute_limit_exceeded",
    "duplicate_place_scheduled",
    "unavailable_place_scheduled",
    "hotel_scheduled_as_attraction",
}

PREFERENCE_ISSUE_TYPES = {
    "daily_time_over_intensity_limit",
    "must_visit_missing",
    "avoid_visit_scheduled",
    "meal_slot_missing",
    "meal_time_invalid",
    "empty_day_with_available_places",
    "time_constraint_violated",
    "order_constraint_violated",
    "meal_slot_missing",
    "meal_time_invalid",
    "daytime_place_scheduled_too_late",
    "fixed_time_constraint_violated",
    "segment_time_violated",
    "long_transfer",
    "too_many_cross_area_moves",
    "day_assignment_violated",
    "explicit_meal_preference_missing",
    "preferred_visit_missing",
    "meal_stop_missing",
}

RELEASE_BLOCKING_TYPES = FACTUAL_ISSUE_TYPES | {
    "must_visit_missing",
    "avoid_visit_scheduled",
}


def verify_itinerary(
    itinerary: dict,
    user_profile: dict,
    route_matrix: list[dict],
    runtime_pois: list[dict] | None = None,
    time_constraints: list[dict] | None = None,
    order_constraints: list[dict] | None = None,
    intent_ledger: dict | list[dict] | None = None,
) -> dict:
    issues = _collect_issues(
        itinerary,
        user_profile,
        route_matrix,
        runtime_pois,
        time_constraints=time_constraints,
        order_constraints=order_constraints,
        intent_ledger=intent_ledger,
    )
    blocking_issues = [issue for issue in issues if issue.get("type") in RELEASE_BLOCKING_TYPES]
    publishable = not blocking_issues
    return {
        "passed": publishable,
        "publishable": publishable,
        "issues": issues,
        "blocking_issues": blocking_issues,
        "quality_issues": [issue for issue in issues if issue not in blocking_issues],
    }


def validate_hard_constraints(
    itinerary: dict,
    user_profile: dict,
    route_matrix: list[dict],
    runtime_pois: list[dict] | None = None,
    time_constraints: list[dict] | None = None,
    order_constraints: list[dict] | None = None,
    intent_ledger: dict | list[dict] | None = None,
) -> dict:
    issues = [
        issue
        for issue in _collect_issues(
            itinerary,
            user_profile,
            route_matrix,
            runtime_pois,
            time_constraints=time_constraints,
            order_constraints=order_constraints,
            intent_ledger=intent_ledger,
        )
        if issue["type"] in RELEASE_BLOCKING_TYPES
    ]
    return {"passed": not issues, "issues": issues}


def review_preference_conflicts(
    itinerary: dict,
    user_profile: dict,
    route_matrix: list[dict],
    runtime_pois: list[dict] | None = None,
    time_constraints: list[dict] | None = None,
    order_constraints: list[dict] | None = None,
    planning_preferences: dict | None = None,
    intent_ledger: dict | list[dict] | None = None,
) -> list[dict]:
    issues = [
        dict(issue, domain=_preference_issue_domain(issue["type"]))
        for issue in _collect_issues(
            itinerary,
            user_profile,
            route_matrix,
            runtime_pois,
            time_constraints=time_constraints,
            order_constraints=order_constraints,
            intent_ledger=intent_ledger,
        )
        if issue["type"] in PREFERENCE_ISSUE_TYPES and issue["type"] not in RELEASE_BLOCKING_TYPES
    ]
    return [issue for issue in issues if not _is_resolved_by_preference(issue, planning_preferences or {})]


def review_soft_quality(
    itinerary: dict,
    user_profile: dict,
    route_matrix: list[dict],
    runtime_pois: list[dict] | None = None,
    time_constraints: list[dict] | None = None,
    order_constraints: list[dict] | None = None,
    llm_client=None,
    intent_ledger: dict | list[dict] | None = None,
) -> list[dict]:
    issues = [
        issue
        for issue in _collect_issues(
            itinerary,
            user_profile,
            route_matrix,
            runtime_pois,
            time_constraints=time_constraints,
            order_constraints=order_constraints,
            intent_ledger=intent_ledger,
        )
        if issue["type"] not in FACTUAL_ISSUE_TYPES and issue["type"] not in PREFERENCE_ISSUE_TYPES
    ]
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
                        "只识别体验层面的软问题，不修改路线事实，输出严格 JSON。\n\n"
                        "## Hard Rules\n"
                        "- 不得新增地点与时间。\n"
                        "- 不得输出硬约束问题。\n"
                        "- 早餐只有在用户明确要求时才需要评审；用户未要求早餐时，不得把缺少早餐写成问题。\n"
                        "- 当天在午餐或晚餐窗口前已经回酒店时，不得把缺少对应餐次写成问题。\n"
                        "- 已有 fallback_nearby 或 inside_poi 餐次时，不得写成缺少正餐。\n"
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
    time_constraints: list[dict] | None = None,
    order_constraints: list[dict] | None = None,
    intent_ledger: dict | list[dict] | None = None,
) -> list[dict]:
    issues: list[dict] = []
    runtime_by_id = {poi.get("poi_id"): poi for poi in runtime_pois or []}
    route_by_pair = {(edge.get("origin_poi_id"), edge.get("destination_poi_id")): edge for edge in route_matrix}
    scheduled_poi_ids: set[str] = set()
    scheduled_names: set[str] = set()
    scheduled_place_keys: dict[str, str] = {}
    days = list(itinerary.get("days", []))
    scheduled_item_count = sum(len(day.get("items") or []) for day in days)
    expect_each_day_used = bool(days) and scheduled_item_count >= len(days)

    for day in days:
        items = day.get("items", [])
        if not items and expect_each_day_used:
            issues.append(
                {
                    "type": "empty_day_with_available_places",
                    "severity": "high",
                    "day": day.get("day"),
                    "message": f"Day {day.get('day')} 为空，但当前地点数量足以覆盖全部天数。",
                    "suggestion": "由蓝图重新分天，避免把多个地点挤在前几天而留下空白日。",
                }
            )
        segment_boundary_pairs = _segment_boundary_pairs(day)
        districts: set[str] = set()
        total_minutes = daily_time_minutes(day)
        limit_minutes = daily_time_limit_minutes(user_profile)
        ceiling_minutes = daily_time_ceiling_minutes(user_profile)
        if total_minutes > ceiling_minutes:
            issues.append(
                {
                    "type": "daily_absolute_limit_exceeded",
                    "severity": "high",
                    "day": day.get("day"),
                    "message": f"Day {day.get('day')} 预计总耗时约 {total_minutes} 分钟，超过可发布的绝对上限 {ceiling_minutes} 分钟。",
                    "suggestion": "减少地点或拆分到其他天；该问题解决前不能发布路线。",
                }
            )
        if total_minutes > limit_minutes:
            issues.append(
                {
                    "type": "daily_time_over_intensity_limit",
                    "severity": "medium",
                    "day": day.get("day"),
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
            if item.get("poi_id"):
                scheduled_poi_ids.add(str(item.get("poi_id")))
            place_key = _verification_place_key(poi)
            if place_key and place_key in scheduled_place_keys and scheduled_place_keys[place_key] != str(item.get("poi_id") or ""):
                issues.append(
                    {
                        "type": "duplicate_place_scheduled",
                        "severity": "high",
                        "day": day.get("day"),
                        "poi_id": item.get("poi_id"),
                        "message": f"{item.get('name')} 与已安排地点指向同一地点，不能重复进入路线。",
                        "suggestion": "合并重复地点，只保留一个规范 POI。",
                    }
                )
            elif place_key:
                scheduled_place_keys[place_key] = str(item.get("poi_id") or "")
            if _runtime_poi_unavailable(poi):
                issues.append(
                    {
                        "type": "unavailable_place_scheduled",
                        "severity": "high",
                        "day": day.get("day"),
                        "poi_id": item.get("poi_id"),
                        "message": f"{item.get('name')} 当前标记为暂停营业或关闭，不能进入正式路线。",
                        "suggestion": "移出主路线并重新规划。",
                    }
                )
            availability_issue = _date_availability_issue(day, item, poi, user_profile)
            if availability_issue:
                issues.append(availability_issue)
            if _runtime_poi_is_hotel(poi):
                issues.append(
                    {
                        "type": "hotel_scheduled_as_attraction",
                        "severity": "high",
                        "day": day.get("day"),
                        "poi_id": item.get("poi_id"),
                        "message": "住宿锚点不能作为普通景点进入路线。",
                        "suggestion": "将酒店保留为每日起终点，不计入游玩地点。",
                    }
                )
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
            item_constraints = [
                constraint
                for constraint in time_constraints or []
                if str(constraint.get("poi_id") or "") == str(item.get("poi_id") or "")
            ]
            time_issue = _time_constraint_issue(day, item, item_constraints)
            if time_issue:
                issues.append(time_issue)
            elif not item_constraints:
                # A user-provided time constraint takes precedence over the
                # generic category semantics (for example 九眼桥 at night).
                semantic_time_issue = _semantic_time_issue(day, item, poi)
                if semantic_time_issue:
                    issues.append(semantic_time_issue)
        issues.extend(_segment_time_issues(day))
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
        meal_scope_minutes = max(total_minutes, component_time_minutes(day))
        meal_slot_issues = _collect_meal_slot_issues(day) if meal_slots or meal_scope_minutes >= 240 else []
        issues.extend(meal_slot_issues)
        if not meal_slots and not meal_slot_issues and len(items) >= 3 and not _has_meal_stop(items, runtime_by_id):
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
    issues.extend(_order_constraint_issues(days, order_constraints or []))
    issues.extend(_intent_commitment_issues(days, intent_ledger))
    has_plannable_runtime_poi = any(
        _is_plannable_poi(poi) and poi.get("final_decision") in {None, "", "include", "optional"}
        for poi in runtime_pois or []
    )
    if runtime_pois and not scheduled_poi_ids and has_plannable_runtime_poi:
        issues.append(
            {
                "type": "no_places_scheduled",
                "severity": "high",
                "message": "已确认地点没有进入路线。",
                "suggestion": "重新生成路线，并至少安排一个已确认地点或明确说明取舍原因。",
            }
        )
    reported_missing_names: set[str] = set()
    for poi in runtime_pois or []:
        poi_id = str(poi.get("poi_id") or "").strip()
        if not poi_id or poi_id in scheduled_poi_ids or not _is_required_runtime_poi(poi):
            continue
        name = _poi_display_name(poi)
        reported_missing_names.add(name)
        issues.append(
            {
                "type": "must_visit_missing",
                "severity": "high",
                "poi_id": poi_id,
                "poi_name": name,
                "message": f"必去地点 {name} 未进入路线。",
                "suggestion": "重新排序并优先安排该地点。",
            }
        )
    constraints = user_profile.get("constraints", {})
    for name in constraints.get("must_visit", []):
        if any(_same_place_name(name, reported_name) for reported_name in reported_missing_names):
            continue
        if name and not any(name in scheduled for scheduled in scheduled_names):
            issues.append(
                {
                    "type": "must_visit_missing",
                    "severity": "high",
                    "poi_name": name,
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
                    "poi_name": name,
                    "message": f"用户不想去的地点 {name} 被安排进路线。",
                    "suggestion": "删除该地点并补充替代方案。",
                }
            )
    return issues


def _intent_commitment_issues(days: list[dict], intent_ledger: dict | list[dict] | None) -> list[dict]:
    if isinstance(intent_ledger, dict):
        commitments = list(intent_ledger.get("commitments") or [])
    elif isinstance(intent_ledger, list):
        commitments = list(intent_ledger)
    else:
        commitments = []
    if not commitments:
        return []

    positions: dict[str, tuple[int, dict, dict]] = {}
    for day in days:
        for item in day.get("items") or []:
            poi_id = str(item.get("poi_id") or "")
            if poi_id:
                positions[poi_id] = (int(day.get("day") or 0), day, item)

    issues: list[dict] = []
    for commitment in commitments:
        if not isinstance(commitment, dict):
            continue
        kind = str(commitment.get("kind") or "")
        strength = str(commitment.get("strength") or "soft_preference")
        poi_id = str(commitment.get("poi_id") or "")
        position = positions.get(poi_id)
        source_text = str(commitment.get("source_text") or "")
        if kind == "day" and commitment.get("preferred_day") and position:
            preferred_day = int(commitment["preferred_day"])
            if position[0] != preferred_day:
                issues.append(
                    {
                        "type": "day_assignment_violated",
                        "severity": "high" if strength == "strong_preference" else "medium",
                        "day": position[0],
                        "poi_id": poi_id,
                        "poi_name": position[2].get("name"),
                        "message": f"用户希望 {position[2].get('name')} 安排在 Day {preferred_day}，当前放在 Day {position[0]}。",
                        "suggestion": "优先换天或调整同日组合；若移动成本明显更差，应明确说明取舍。",
                        "evidence": source_text,
                    }
                )
        if kind == "meal":
            if position and _commitment_meal_is_fulfilled(position[1], poi_id, str(commitment.get("meal_slot") or "")):
                continue
            poi_name = position[2].get("name") if position else source_text or poi_id
            issues.append(
                {
                    "type": "explicit_meal_preference_missing",
                    "severity": "high" if strength == "strong_preference" else "medium",
                    "day": position[0] if position else None,
                    "poi_id": poi_id,
                    "poi_name": poi_name,
                    "message": f"用户明确指定的餐饮 {poi_name} 没有真正承接对应餐次。",
                    "suggestion": "先尝试换天或调整顺序；仍不可行时保留未采用原因，再使用附近就餐补位。",
                    "evidence": source_text,
                }
            )
        if kind == "visit" and poi_id and not position:
            issues.append(
                {
                    "type": "preferred_visit_missing",
                    "severity": "high" if strength == "strong_preference" else "medium",
                    "poi_id": poi_id,
                    "message": f"用户提及的地点 {source_text or poi_id} 未进入路线。",
                    "suggestion": "优先比较重新分天与顺路补入；若会明显超载，可保留为备选并说明取舍。",
                    "evidence": source_text,
                }
            )
    return issues


def _commitment_meal_is_fulfilled(day: dict, poi_id: str, meal_slot: str) -> bool:
    for slot in day.get("meal_slots") or []:
        if slot.get("source") != "poi" or str(slot.get("poi_id") or "") != poi_id:
            continue
        if not meal_slot or str(slot.get("slot") or "") == meal_slot:
            return _is_meal_slot_satisfied(day, slot)
    return False


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
                    "day": day.get("day"),
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
                    "day": day.get("day"),
                    "message": f"Day {day.get('day')} 的{_slot_label(slot.get('slot'))}尚未真正落地。",
                    "suggestion": f"让{_slot_label(slot.get('slot'))}由真实餐厅、场内用餐或就近补位之一明确承接。",
                }
            )
            continue
        meal_start = _meal_slot_start(day, slot)
        if meal_start is not None and not _meal_start_in_window(str(slot.get("slot") or ""), meal_start):
            issues.append(
                {
                    "type": "meal_time_invalid",
                    "severity": "high",
                    "day": day.get("day"),
                    "message": f"Day {day.get('day')} 的{_slot_label(slot.get('slot'))}从 {_format_minutes(meal_start)} 开始，不在合理用餐时段。",
                    "suggestion": f"由蓝图重排前后地点，让{_slot_label(slot.get('slot'))}在对应餐窗开始。",
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


def _meal_slot_start(day: dict, slot: dict) -> int | None:
    slot_name = str(slot.get("slot") or "")
    if slot.get("source") == "poi":
        item = next((item for item in day.get("items") or [] if item.get("poi_id") == slot.get("poi_id")), None)
        return _parse_time((item or {}).get("arrival_time"))
    meal = next(
        (
            meal
            for meal in day.get("meal_breaks") or []
            if str(meal.get("slot") or "") == slot_name
            and (slot.get("source") != "inside_poi" or meal.get("within_poi_id") == slot.get("within_poi_id"))
        ),
        None,
    )
    return _parse_time((meal or {}).get("start_time"))


def _meal_start_in_window(slot_name: str, start: int) -> bool:
    windows = {
        "breakfast": (7 * 60, 10 * 60),
        "lunch": (11 * 60 + 30, 15 * 60),
        "dinner": (17 * 60 + 30, 21 * 60 + 30),
    }
    window = windows.get(slot_name)
    return True if window is None else window[0] <= start <= window[1]


def _has_removable_item(items: list[dict], runtime_by_id: dict, user_profile: dict) -> bool:
    must_visit = user_profile.get("constraints", {}).get("must_visit", [])
    return any(not _is_must_item(item, runtime_by_id.get(item.get("poi_id"), {}), must_visit) for item in items)


def _preference_issue_domain(issue_type: str) -> str:
    mapping = {
        "must_visit_missing": "must_places",
        "time_constraint_violated": "time_preferences",
        "order_constraint_violated": "order_preferences",
        "daily_time_over_intensity_limit": "pace",
        "meal_slot_missing": "meal_arrangement",
        "meal_time_invalid": "meal_arrangement",
        "segment_time_violated": "time_preferences",
        "empty_day_with_available_places": "day_distribution",
        "day_assignment_violated": "day_distribution",
        "explicit_meal_preference_missing": "meal_arrangement",
        "preferred_visit_missing": "must_places",
        "long_transfer": "route_quality",
        "too_many_cross_area_moves": "route_quality",
        "meal_stop_missing": "meal_arrangement",
        "daytime_place_scheduled_too_late": "time_preferences",
        "avoid_visit_scheduled": "avoid_places",
    }
    return mapping.get(issue_type, "planning_preference")


def _is_resolved_by_preference(issue: dict, planning_preferences: dict) -> bool:
    issue_type = str(issue.get("type") or "")
    if issue_type == "daily_time_over_intensity_limit":
        return planning_preferences.get("pace") in {"relax_pace", "keep_must_places"}
    if issue_type == "must_visit_missing":
        return planning_preferences.get("must_places") == "keep_time_preferences"
    if issue_type == "time_constraint_violated":
        return planning_preferences.get("time_preferences") == "keep_must_places"
    if issue_type == "order_constraint_violated":
        return planning_preferences.get("order_preferences") == "keep_must_places"
    return False


def _is_must_item(item: dict, poi: dict, must_visit: list[str]) -> bool:
    if poi.get("user_override") == "must_include":
        return True
    return any(name and name in item.get("name", "") for name in must_visit)


def _is_required_runtime_poi(poi: dict) -> bool:
    if poi.get("user_override") != "must_include":
        return False
    if poi.get("final_decision") in {"exclude", "unresolved"}:
        return False
    return _is_plannable_poi(poi)


def _poi_display_name(poi: dict) -> str:
    return str(poi.get("standard_name") or poi.get("name") or poi.get("raw_name") or poi.get("poi_id") or "这个地点").strip()


def _same_place_name(left: str, right: str) -> bool:
    return bool(left and right and (left in right or right in left))


def _verification_place_key(poi: dict) -> str:
    amap_id = str(poi.get("amap_id") or "").strip()
    if amap_id:
        return f"amap:{amap_id}"
    name = str(poi.get("standard_name") or poi.get("name") or poi.get("raw_name") or "").strip().lower()
    name = name.replace("（暂停营业）", "").replace("(暂停营业)", "")
    normalized = "".join(character for character in name if character.isalnum() or "\u4e00" <= character <= "\u9fff")
    return f"name:{normalized}" if normalized else ""


def _runtime_poi_unavailable(poi: dict) -> bool:
    status = str(
        poi.get("business_status")
        or poi.get("availability_status")
        or poi.get("operating_status")
        or ""
    ).strip().lower()
    name = str(poi.get("standard_name") or poi.get("name") or poi.get("raw_name") or "")
    return status in {"closed", "suspended", "permanently_closed", "暂停营业", "永久关闭", "歇业"} or any(
        token in name for token in ["暂停营业", "永久关闭", "已关闭", "已歇业"]
    )


def _runtime_poi_is_hotel(poi: dict) -> bool:
    poi_id = str(poi.get("poi_id") or "").lower()
    category = str(poi.get("category") or poi.get("category_normalized") or "").lower()
    return poi_id in {"hotel", "hotel_anchor"} or category in {"hotel", "酒店", "住宿服务"}


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


def _time_constraint_issue(day: dict, item: dict, time_constraints: list[dict]) -> dict | None:
    poi_id = str(item.get("poi_id") or "")
    if not poi_id:
        return None
    matching = [constraint for constraint in time_constraints if str(constraint.get("poi_id") or "") == poi_id]
    if not matching:
        return None
    arrival = _parse_time(item.get("arrival_time"))
    if arrival is None:
        return None
    for constraint in matching:
        if constraint.get("strength") not in {"quasi_hard", "hard"}:
            continue
        window = str(constraint.get("preferred_window") or "")
        fixed_value = constraint.get("fixed_time") or constraint.get("appointment_time") or constraint.get("objective_deadline")
        is_fixed = constraint.get("strength") == "hard" and bool(
            fixed_value
        )
        fixed_target = _parse_time(fixed_value) if is_fixed else None
        satisfied = abs(arrival - fixed_target) <= 15 if fixed_target is not None else _time_starts_in_window(arrival, window)
        if satisfied:
            continue
        label = str(fixed_value) if fixed_target is not None else _time_window_label(window)
        return {
            "type": "fixed_time_constraint_violated" if is_fixed else "time_constraint_violated",
            "severity": "high",
            "day": day.get("day"),
            "poi_name": item.get("name"),
            "message": f"用户明确希望 {item.get('name')} 安排在{label}，但当前时间不匹配。",
            "suggestion": f"把 {item.get('name')} 调整到{label}，或让用户选择是否放宽这个要求。",
            "evidence": constraint.get("source_text") or f"Day {day.get('day')}",
        }
    return None


def _semantic_time_issue(day: dict, item: dict, poi: dict) -> dict | None:
    semantics = poi.get("planning_semantics") or {}
    advice = {
        str(value or "").strip().lower()
        for value in (semantics.get("time_advice") or semantics.get("time_suitability") or [])
        if str(value or "").strip()
    }
    if not advice:
        return None
    normalized_advice: set[str] = set()
    advice_aliases = {
        "open_hours": {"morning", "afternoon"},
        "daylight": {"morning", "afternoon", "evening"},
        "flexible": {"morning", "midday", "afternoon", "evening"},
    }
    for value in advice:
        normalized_advice.update(advice_aliases.get(value, {value}))
    advice = normalized_advice
    arrival = _parse_time(item.get("arrival_time"))
    if arrival is None:
        return None
    if advice.issubset({"morning", "midday", "afternoon"}) and arrival >= 18 * 60 + 30:
        return {
            "type": "daytime_place_scheduled_too_late",
            "severity": "high",
            "day": day.get("day"),
            "poi_name": item.get("name"),
            "message": f"{item.get('name')} 是白天游览地点，但当前到达时间已晚于 18:30。",
            "suggestion": f"把 {item.get('name')} 调整到白天，或重新分配当天地点。",
            "evidence": f"Day {day.get('day')}",
        }
    if not advice.issubset({"evening", "night"}):
        return None
    earliest = 19 * 60 if advice == {"night"} else 17 * 60 + 30
    if arrival >= earliest:
        return None
    return {
        "type": "time_constraint_violated",
        "severity": "high",
        "day": day.get("day"),
        "poi_name": item.get("name"),
        "message": f"{item.get('name')} 是夜间体验，但当前到达时间早于傍晚。",
        "suggestion": f"由蓝图把 {item.get('name')} 调整到傍晚后开始。",
        "evidence": f"Day {day.get('day')}",
    }


def _segment_time_issues(day: dict) -> list[dict]:
    items_by_id = {str(item.get("poi_id") or ""): item for item in day.get("items") or []}
    windows = {
        "morning": (8 * 60, 12 * 60 + 30),
        "midday": (11 * 60, 14 * 60 + 30),
        "afternoon": (11 * 60 + 30, 18 * 60),
        "evening": (17 * 60 + 30, 22 * 60),
        "night": (19 * 60, 24 * 60),
    }
    issues: list[dict] = []
    for segment in day.get("segments") or []:
        if segment.get("kind") != "outing":
            continue
        segment_time = str(segment.get("segment_time") or "").strip().lower()
        window = windows.get(segment_time)
        if not window:
            continue
        for poi_id in segment.get("poi_ids") or []:
            item = items_by_id.get(str(poi_id or ""))
            arrival = _parse_time((item or {}).get("arrival_time"))
            if arrival is None or window[0] <= arrival < window[1]:
                continue
            issues.append(
                {
                    "type": "segment_time_violated",
                    "severity": "high",
                    "day": day.get("day"),
                    "poi_name": (item or {}).get("name"),
                    "message": f"{(item or {}).get('name')} 在 {_format_minutes(arrival)} 到达，已超出蓝图的{_time_window_label(segment_time)}分段。",
                    "suggestion": "由蓝图重新安排分段、饭点或地点顺序，使编译后的到达时间与分段一致。",
                }
            )
    return issues


def _format_minutes(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _order_constraint_issues(days: list[dict], order_constraints: list[dict]) -> list[dict]:
    if not order_constraints:
        return []
    by_id: dict[str, tuple[int, int, str]] = {}
    by_name: dict[str, tuple[int, int, str]] = {}
    for day_index, day in enumerate(days):
        for item_index, item in enumerate(day.get("items") or []):
            name = str(item.get("name") or "")
            position = (day_index, item_index, name)
            poi_id = str(item.get("poi_id") or "")
            if poi_id:
                by_id[poi_id] = position
            if name:
                by_name[name] = position
    issues: list[dict] = []
    for constraint in order_constraints:
        if str(constraint.get("strength") or "") != "strong_preference":
            continue
        before = str(constraint.get("before") or "").strip()
        after = str(constraint.get("after") or "").strip()
        if not before or not after:
            continue
        before_position = by_id.get(str(constraint.get("before_poi_id") or "")) or _match_global_name_position(before, by_name)
        after_position = by_id.get(str(constraint.get("after_poi_id") or "")) or _match_global_name_position(after, by_name)
        if before_position is None or after_position is None:
            continue
        if before_position[:2] < after_position[:2]:
            continue
        issues.append(
            {
                "type": "order_constraint_violated",
                "severity": "high",
                "day": before_position[0] + 1,
                "poi_name": before,
                "message": f"用户明确希望先去 {before} 再去 {after}，但当前全程顺序不匹配。",
                "suggestion": f"比较换天或同日重排，把 {before} 放在 {after} 之前；若整体体验更差，可明确说明取舍。",
                "evidence": f"Day {before_position[0] + 1} / Day {after_position[0] + 1}",
            }
        )
    return issues


def _match_global_name_position(name: str, positions: dict[str, tuple[int, int, str]]) -> tuple[int, int, str] | None:
    for scheduled_name, position in positions.items():
        if name and name in scheduled_name:
            return position
    return None


def _date_availability_issue(day: dict, item: dict, poi: dict, user_profile: dict) -> dict | None:
    start_date = str(user_profile.get("start_date") or "")
    if not start_date:
        return None
    try:
        from datetime import date, timedelta

        visit_date = date.fromisoformat(start_date) + timedelta(days=max(0, int(day.get("day") or 1) - 1))
    except (TypeError, ValueError):
        return None
    fact = next(
        (
            value for value in poi.get("availability_facts") or []
            if str(value.get("visit_date") or "") == visit_date.isoformat()
        ),
        None,
    )
    if not fact or fact.get("status") != "closed":
        return None
    return {
        "type": "unavailable_place_scheduled",
        "severity": "high",
        "day": day.get("day"),
        "poi_id": item.get("poi_id"),
        "poi_name": item.get("name"),
        "message": f"{item.get('name')} 在 {visit_date.isoformat()} 有来源证据显示不开放。",
        "suggestion": "由 PlannerAgent 换天、替换可选地点或移出当天路线。",
        "evidence": str((fact.get("source") or {}).get("url") or fact.get("evidence_summary") or "营业事实"),
    }


def _time_starts_in_window(start: int, window: str) -> bool:
    window_start, window_end = _time_window_minutes(window)
    if window_start is None or window_end is None:
        return True
    return window_start <= start < window_end


def _time_window_minutes(window: str) -> tuple[int | None, int | None]:
    mapping = {
        "morning": (8 * 60, 12 * 60),
        "midday": (11 * 60, 14 * 60),
        "afternoon": (12 * 60, 17 * 60 + 30),
        "evening": (17 * 60 + 30, 19 * 60),
        "night": (19 * 60, 24 * 60),
    }
    return mapping.get(window, (None, None))


def _time_window_label(window: str) -> str:
    return {
        "morning": "上午",
        "midday": "中午",
        "afternoon": "下午",
        "evening": "傍晚",
        "night": "晚上",
    }.get(window, "指定时段")


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
    return hotel_rest_boundary_pairs(day)
