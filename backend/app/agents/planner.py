from __future__ import annotations

from app.agents.intensity import daily_time_limit_minutes, sync_day_total_time


def plan_itinerary(user_profile: dict, runtime_pois: list[dict], route_matrix: list[dict], llm_client) -> dict:
    matched_pois = [poi for poi in runtime_pois if _is_plannable_poi(poi)]
    if not matched_pois:
        return {
            "destination": user_profile.get("destination", ""),
            "days": [],
            "global_risks": ["没有可安排的已确认地点。"],
            "uncertain_pois": [poi for poi in runtime_pois if poi.get("match_status") != "matched"],
            "revision_notes": [],
        }
    limit_minutes = daily_time_limit_minutes(user_profile)
    payload = llm_client.json_chat(
        [
            {"role": "system", "content": "你是旅行路线规划助手。必须根据已确认地点和路径数据输出可执行 JSON，不得虚构地点。"},
            {
                "role": "user",
                "content": f"""请生成 {user_profile.get('days', 1)} 天旅行路线。规则：酒店名是每日出发和返回参考点；出行人数只表示人数规模，不表示同行关系；按从酒店出发到晚上回到酒店之间的全部外出时间控制每日总耗时，包含酒店往返交通、点间交通、用餐、排队、游玩和休息，轻松节奏目标约 {limit_minutes} 分钟，超过 540 分钟时当天会显示为特种兵节奏；餐饮安排在饭点；每个地点的 duration_min 必须优先使用地点里的 estimated_duration_min，不得为了满足强度上限压缩单个地点的正常游玩时间；如果必去地点较多或单个大景点正常游玩就超过轻松目标，应保留真实停留时间并提示当天节奏会更满；相邻地点的交通方式、时长和距离只能使用路径矩阵中的 mode、duration_min、distance_m，不得自行估算或混用不同交通方式的数据；优先安排 user_override 为 must_include 的必去地点；待定地点是 final_decision 为 optional 的已确认地点，只能在必去地点优先、路线顺路和当天时间允许时机动安排；不安排 unresolved、exclude、未确认或待修正地点；必须说明删除原因、风险和替代方案，文案保持简短；user_override、final_decision、system_decision、must_include、optional、include、exclude、unresolved 等内部字段只用于判断，不得出现在 reason、summary、risk_notes、revision_notes 等用户可见文案中。

输出 JSON 格式：
{{"destination":"...","route_summary":{{"main_message":"已为你整理出 2 天路线，优先满足美食和拍照。","scheduled_places_count":8,"unscheduled_places_count":4,"attention_required_count":2}},"days":[{{"day":1,"theme":"...","summary":"...","total_outing_min":540,"hotel_departure_transport_min":20,"hotel_return_transport_min":20,"meal_breaks":[{{"duration_min":60}}],"items":[{{"time_block":"上午","poi_id":"...","name":"...","arrival_time":"10:00","duration_min":60,"reason":"...","transport_to_next":{{"mode":"walking","duration_min":8,"distance_m":650}},"risk_notes":[],"amap_link":""}}],"removed_pois":[{{"name":"...","reason":"距离较远"}}],"alternatives":[]}}],"unscheduled_places":[],"attention_places":[],"global_risks":[],"uncertain_pois":[],"revision_notes":[]}}

用户需求：{user_profile}
地点：{matched_pois}
路径矩阵：{route_matrix}
""",
            },
        ],
        step="plan_itinerary",
    )
    payload.setdefault("destination", user_profile.get("destination", ""))
    payload.setdefault("days", [])
    payload.setdefault("global_risks", [])
    payload.setdefault("uncertain_pois", [poi for poi in runtime_pois if not _is_plannable_poi(poi)])
    payload.setdefault("revision_notes", [])
    _apply_estimated_visit_durations(payload, runtime_pois)
    return payload


def _apply_estimated_visit_durations(itinerary: dict, runtime_pois: list[dict]) -> None:
    runtime_by_id = {poi.get("poi_id"): poi for poi in runtime_pois}
    for day in itinerary.get("days", []):
        for item in day.get("items", []):
            poi = runtime_by_id.get(item.get("poi_id"), {})
            estimated = _positive_int(poi.get("estimated_duration_min"))
            current = _positive_int(item.get("duration_min"))
            if estimated and (current is None or current < estimated):
                item["duration_min"] = estimated
        sync_day_total_time(day)


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


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
