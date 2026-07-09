from __future__ import annotations

from app.agents.intensity import daily_time_limit_minutes


def plan_itinerary(user_profile: dict, runtime_pois: list[dict], route_matrix: list[dict], llm_client) -> dict:
    matched_pois = [poi for poi in runtime_pois if poi.get("match_status") == "matched"]
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
                "content": f"""请生成 {user_profile.get('days', 1)} 天旅行路线。规则：酒店名是每日出发和返回参考点；出行人数只表示人数规模，不表示同行关系；按从酒店出发到晚上回到酒店之间的全部外出时间控制每日总耗时，包含酒店往返交通、点间交通、用餐、排队、游玩和休息，当前强度每日上限约 {limit_minutes} 分钟；餐饮安排在饭点，尽量把餐饮点放在午餐或晚餐的时间点；优先安排 user_override 为 must_include 或 final_decision 为 include 的地点；待定地点是 final_decision 为 optional 的已确认地点，只能在必去地点和强度上限满足后，根据用户偏好、路线顺路程度、交通成本和当天空余时间机动安排；不安排 unresolved、exclude、未确认或待修正地点；必须说明删除原因、风险和替代方案，文案保持简短。

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
    payload.setdefault("uncertain_pois", [poi for poi in runtime_pois if poi.get("match_status") != "matched"])
    payload.setdefault("revision_notes", [])
    return payload
