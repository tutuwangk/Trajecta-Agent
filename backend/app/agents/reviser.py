from __future__ import annotations

from copy import deepcopy
import re

from app.agents.intensity import daily_time_limit_minutes, daily_time_minutes


def revise_itinerary(
    draft_itinerary: dict,
    verification: dict,
    user_profile: dict,
    runtime_pois: list[dict] | None = None,
    instruction: str | None = None,
) -> dict:
    final = deepcopy(draft_itinerary)
    notes = list(final.get("revision_notes", []))
    runtime_by_id = {poi.get("poi_id"): poi for poi in runtime_pois or []}
    removed_any = False
    if runtime_pois is not None:
        removed_any = _remove_unconfirmed_items(final, runtime_by_id)
    removed_any = _remove_avoid_visit_items(final, user_profile) or removed_any
    if instruction:
        removed_any = _apply_instruction_rules(final, instruction, runtime_by_id) or removed_any
    if _needs_time_limit(user_profile, verification, instruction):
        removed_any = _trim_days_by_time(final, runtime_by_id, _must_visit_names(user_profile), daily_time_limit_minutes(user_profile)) or removed_any
        notes.append("已按行程强度控制每日预计耗时。")
    if instruction and _mentions_rain(instruction):
        _mark_rain_risks(final, runtime_by_id)
        notes.append("已标注雨天风险，优先保留室内、餐饮、商场和博物馆类地点。")
    for issue in verification.get("issues", []):
        notes.append(issue.get("suggestion") or issue.get("message", ""))
    if removed_any:
        notes.append("已将不适合直接执行的地点移入未安排地点。")
    final["revision_notes"] = [note for note in notes if note]
    final["global_risks"] = list(dict.fromkeys(final.get("global_risks", []) + [issue["message"] for issue in verification.get("issues", []) if issue.get("message")]))
    return final


def revise_from_user_instruction(current_itinerary: dict, instruction: str, user_profile: dict, runtime_pois: list[dict], route_matrix: list[dict], llm_client) -> dict:
    if _can_handle_without_llm(instruction):
        revised = revise_itinerary(
            current_itinerary,
            {"issues": []},
            user_profile,
            runtime_pois=runtime_pois,
            instruction=instruction,
        )
        revised.setdefault("revision_notes", [])
        revised["revision_notes"].append(f"已按你的要求调整：{instruction}")
        return revised
    payload = llm_client.json_chat(
        [
            {"role": "system", "content": "你是旅行路线修改助手。只能基于已有地点和路线调整，不得虚构地点。"},
            {
                "role": "user",
                "content": f"""请根据用户要求调整路线，保留 JSON 结构。
用户要求：{instruction}
用户需求：{user_profile}
当前路线：{current_itinerary}
可用地点：{runtime_pois}
路径矩阵：{route_matrix}
""",
            },
        ],
        step="revise_itinerary",
    )
    payload.setdefault("revision_notes", [])
    payload["revision_notes"].append(f"已按你的要求调整：{instruction}")
    return revise_itinerary(payload, {"issues": []}, user_profile, runtime_pois=runtime_pois, instruction=instruction)


def _remove_unconfirmed_items(itinerary: dict, runtime_by_id: dict) -> bool:
    removed = False
    for day in itinerary.get("days", []):
        kept = []
        removed_pois = list(day.get("removed_pois", []))
        for item in day.get("items", []):
            poi = runtime_by_id.get(item.get("poi_id"))
            if not poi or poi.get("match_status") != "matched":
                removed_pois.append({"name": item.get("name", ""), "reason": "地点还需要确认，已移入待确认。"})
                removed = True
                continue
            kept.append(item)
        day["items"] = kept
        day["removed_pois"] = removed_pois
    return removed


def _remove_avoid_visit_items(itinerary: dict, user_profile: dict) -> bool:
    avoid_names = user_profile.get("constraints", {}).get("avoid_visit", [])
    if not avoid_names:
        return False
    removed = False
    for day in itinerary.get("days", []):
        kept = []
        removed_pois = list(day.get("removed_pois", []))
        for item in day.get("items", []):
            if any(name and name in item.get("name", "") for name in avoid_names):
                removed_pois.append({"name": item.get("name", ""), "reason": "用户明确不想去，已从路线中删除。"})
                removed = True
                continue
            kept.append(item)
        day["items"] = kept
        day["removed_pois"] = removed_pois
    return removed


def _apply_instruction_rules(itinerary: dict, instruction: str, runtime_by_id: dict) -> bool:
    names = _names_to_delete(instruction, runtime_by_id)
    if not names:
        return False
    removed = False
    for day in itinerary.get("days", []):
        kept = []
        removed_pois = list(day.get("removed_pois", []))
        for item in day.get("items", []):
            if any(name and name in item.get("name", "") for name in names):
                removed_pois.append({"name": item.get("name", ""), "reason": "已按你的要求删除。"})
                removed = True
                continue
            kept.append(item)
        day["items"] = kept
        day["removed_pois"] = removed_pois
    return removed


def _trim_days_by_time(itinerary: dict, runtime_by_id: dict, must_visit: list[str], limit_minutes: int) -> bool:
    removed = False
    for day in itinerary.get("days", []):
        removed_pois = list(day.get("removed_pois", []))
        while day.get("items", []) and daily_time_minutes(day) > limit_minutes:
            index = _least_important_item_index(day["items"], runtime_by_id, must_visit)
            item = day["items"].pop(index)
            removed_pois.append({"name": item.get("name", ""), "reason": "为控制当天总耗时，已从路线中后置。"})
            removed = True
        day["removed_pois"] = removed_pois
    return removed


def _least_important_item_index(items: list[dict], runtime_by_id: dict, must_visit: list[str]) -> int:
    candidates = []
    for index, item in enumerate(items):
        poi = runtime_by_id.get(item.get("poi_id"), {})
        is_must = any(name and name in item.get("name", "") for name in must_visit)
        candidates.append((1 if is_must else 0, float(poi.get("confidence") or 0), -index, index))
    return min(candidates)[-1]


def _names_to_delete(instruction: str, runtime_by_id: dict) -> list[str]:
    if not any(token in instruction for token in ["删掉", "删除", "不要去", "避开"]):
        return []
    names = []
    for poi in runtime_by_id.values():
        possible_names = [poi.get("standard_name", ""), *(poi.get("raw_names") or [])]
        for name in possible_names:
            if name and name in instruction:
                names.append(name)
    if names:
        return list(dict.fromkeys(names))
    match = re.search(r"(?:删掉|删除|不要去|避开)\s*([\u4e00-\u9fa5A-Za-z0-9]+)", instruction)
    return [match.group(1)] if match else []


def _mark_rain_risks(itinerary: dict, runtime_by_id: dict) -> None:
    outdoor = {"park", "citywalk", "attraction"}
    for day in itinerary.get("days", []):
        alternatives = list(day.get("alternatives", []))
        for item in day.get("items", []):
            poi = runtime_by_id.get(item.get("poi_id"), {})
            if poi.get("category") in outdoor:
                notes = item.setdefault("risk_notes", [])
                if "雨天体验可能下降，建议准备室内替代。" not in notes:
                    notes.append("雨天体验可能下降，建议准备室内替代。")
                alternatives.append(f"雨天时将 {item.get('name')} 后置，优先选择室内餐饮、商场或博物馆。")
        day["alternatives"] = list(dict.fromkeys(alternatives))


def _needs_time_limit(user_profile: dict, verification: dict, instruction: str | None) -> bool:
    constraints = user_profile.get("constraints", {})
    if constraints.get("physical_intensity") in {"high", "medium", "low"} or constraints.get("avoid_too_tired"):
        return True
    if user_profile.get("constraints", {}).get("avoid_too_tired"):
        return True
    if instruction and any(token in instruction for token in ["太累", "轻松", "休闲", "慢一点", "松弛"]):
        return True
    return any(issue.get("type") == "daily_time_over_intensity_limit" for issue in verification.get("issues", []))


def _must_visit_names(user_profile: dict) -> list[str]:
    return user_profile.get("constraints", {}).get("must_visit", [])


def _mentions_rain(instruction: str) -> bool:
    return any(token in instruction for token in ["下雨", "雨天", "天气不好"])


def _can_handle_without_llm(instruction: str) -> bool:
    deterministic_tokens = ["删掉", "删除", "不要去", "避开", "太累", "轻松", "休闲", "慢一点", "松弛", "下雨", "雨天"]
    return any(token in instruction for token in deterministic_tokens)
