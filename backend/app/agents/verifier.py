from __future__ import annotations

from app.agents.intensity import daily_time_limit_minutes, daily_time_minutes


def verify_itinerary(
    itinerary: dict,
    user_profile: dict,
    route_matrix: list[dict],
    runtime_pois: list[dict] | None = None,
) -> dict:
    issues: list[dict] = []
    runtime_by_id = {poi.get("poi_id"): poi for poi in runtime_pois or []}
    route_by_pair = {(edge.get("origin_poi_id"), edge.get("destination_poi_id")): edge for edge in route_matrix}
    scheduled_names: set[str] = set()

    for day in itinerary.get("days", []):
        items = day.get("items", [])
        districts: set[str] = set()
        total_minutes = daily_time_minutes(day)
        limit_minutes = daily_time_limit_minutes(user_profile)
        if total_minutes > limit_minutes:
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
            if poi.get("match_status") != "matched":
                issues.append(
                    {
                        "type": "unmatched_poi_scheduled",
                        "severity": "high",
                        "message": f"{item.get('name')} 尚未确认，不应进入路线。",
                        "suggestion": "移入不确定地点或让用户手动确认。",
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
        if len(items) >= 3 and not _has_meal_stop(items, runtime_by_id):
            issues.append(
                {
                    "type": "meal_stop_missing",
                    "severity": "low",
                    "message": f"Day {day.get('day')} 没有明确餐饮点，饭点安排可能不完整。",
                    "suggestion": "在午餐或晚餐时间补充餐饮地点，或提示用户自行选择。",
                }
            )
        for origin, destination in zip(items, items[1:]):
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
    return {"passed": not issues, "issues": issues}


def _has_meal_stop(items: list[dict], runtime_by_id: dict) -> bool:
    for item in items:
        poi = runtime_by_id.get(item.get("poi_id"), {})
        text = f"{item.get('time_block', '')}{item.get('name', '')}{poi.get('category', '')}"
        if poi.get("category") == "restaurant" or any(token in text for token in ["午餐", "晚餐", "餐", "小吃", "咖啡"]):
            return True
    return False
