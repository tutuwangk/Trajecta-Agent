from __future__ import annotations

from copy import deepcopy
import json
import re

from app.agents.intensity import daily_time_limit_minutes, daily_time_minutes, intensity_time_minutes


def revise_itinerary(
    draft_itinerary: dict,
    verification: dict,
    user_profile: dict,
    runtime_pois: list[dict] | None = None,
    instruction: str | None = None,
) -> dict:
    final = deepcopy(draft_itinerary)
    notes = _text_list(final.get("revision_notes", []))
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
    final["global_risks"] = _unique_texts(
        _text_list(final.get("global_risks", []))
        + _text_list([issue.get("message") for issue in verification.get("issues", []) if issue.get("message")])
    )
    _sanitize_itinerary_text(final)
    _ensure_display_sections(final, user_profile)
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
                "content": f"""请根据用户要求调整路线，保留 JSON 结构。user_override、final_decision、system_decision、must_include、optional、include、exclude、unresolved 等内部字段只用于判断，不得出现在 reason、summary、risk_notes、revision_notes 等用户可见文案中。
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


def rule_repair(
    draft_itinerary: dict,
    verification: dict,
    user_profile: dict,
    runtime_pois: list[dict] | None = None,
) -> dict:
    return revise_itinerary(draft_itinerary, verification, user_profile, runtime_pois=runtime_pois)


def llm_replan(planning_context: dict, previous_skeleton: dict, issues: dict, llm_client) -> dict:
    from app.agents.planner import replan_skeleton_with_llm

    return replan_skeleton_with_llm(planning_context, previous_skeleton, issues, llm_client)


def generate_copy(itinerary: dict, copy_context: dict, user_profile: dict, llm_client=None) -> dict:
    final = deepcopy(itinerary)
    payload = None
    if llm_client is not None:
        try:
            payload = llm_client.json_chat(_copy_messages(copy_context), step="generate_itinerary_copy", temperature=0.3)
        except Exception:
            payload = None
    if not isinstance(payload, dict):
        payload = _fallback_copy_payload(final, user_profile, copy_context)
    _merge_copy_payload(final, payload)
    _sanitize_itinerary_text(final)
    _ensure_display_sections(final, user_profile)
    return final


def _remove_unconfirmed_items(itinerary: dict, runtime_by_id: dict) -> bool:
    removed = False
    for day in itinerary.get("days", []):
        kept = []
        removed_pois = list(day.get("removed_pois", []))
        for item in day.get("items", []):
            poi = runtime_by_id.get(item.get("poi_id"))
            if not poi or not _is_plannable_poi(poi) or poi.get("final_decision") in {"exclude", "unresolved"}:
                removed_pois.append({"name": item.get("name", ""), "reason": _removed_reason(poi)})
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
        while day.get("items", []) and intensity_time_minutes(day) > limit_minutes:
            if len(day["items"]) <= 1:
                item = day["items"][0]
                risks = itinerary.setdefault("global_risks", [])
                risks.append(f"{item.get('name', '这个地点')}本身需要较长时间，当前路线会超过所选行程强度，建议当天少安排其他项目。")
                break
            index = _least_important_item_index(day["items"], runtime_by_id, must_visit)
            if index is None:
                risks = itinerary.setdefault("global_risks", [])
                risks.append("必去地点较多，当前路线会超过所选行程强度，建议当天放慢节奏或拆分到其他天。")
                break
            item = day["items"].pop(index)
            removed_pois.append({"name": item.get("name", ""), "reason": "为控制当天总耗时，已从路线中后置。"})
            removed = True
        day["removed_pois"] = removed_pois
    return removed


def _least_important_item_index(items: list[dict], runtime_by_id: dict, must_visit: list[str]) -> int | None:
    candidates = []
    for index, item in enumerate(items):
        poi = runtime_by_id.get(item.get("poi_id"), {})
        if _is_must_keep_item(item, poi, must_visit) or _preserve_role_item(items, index):
            continue
        candidates.append((float(poi.get("confidence") or 0), -index, index))
    if not candidates:
        return None
    return min(candidates)[-1]


def _is_must_keep_item(item: dict, poi: dict, must_visit: list[str]) -> bool:
    if poi.get("user_override") == "must_include":
        return True
    return any(name and name in item.get("name", "") for name in must_visit)


def _preserve_role_item(items: list[dict], index: int) -> bool:
    item = items[index]
    if item.get("trim_priority") == "never_trim_before_meal" or item.get("scheduled_role") == "meal_stop" or item.get("burden_role") == "protected_basic":
        return True
    if item.get("trim_priority") != "keep_if_low_detour" and item.get("scheduled_role") != "quick_stop" and item.get("burden_role") != "light_detour":
        return False
    prev_transfer = _int((items[index - 1].get("transport_to_next") or {}).get("duration_min")) if index > 0 else 0
    next_transfer = _int((item.get("transport_to_next") or {}).get("duration_min"))
    nearby_transfer = min([value for value in [prev_transfer, next_transfer] if value > 0], default=max(prev_transfer, next_transfer))
    return nearby_transfer + min(_int(item.get("duration_min")), 15) <= 45


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


def _copy_messages(copy_context: dict) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "## Role\n"
                "你是旅行路线文案助手。\n\n"
                "## Mission\n"
                "只根据给定事实写 route_summary、day summary、item reason、removed reason、risk notes。\n\n"
                "## Hard Rules\n"
                "- 不得新增地点、时间、交通、风险结论。\n"
                "- 不得改动 poi_id、day、事实字段。\n"
                "- 文案面向普通旅行用户，简短、直接。\n"
                "- 内部字段与标签只用于判断，不得直接暴露。"
            ),
        },
        {
            "role": "user",
            "content": (
                "<copy_context>\n"
                f"{copy_context}\n"
                "</copy_context>\n\n"
                "<output_schema>\n"
                '{"route_summary":{"main_message":"..."},"days":[{"day":1,"summary":"...","items":[{"poi_id":"...","reason":"...","risk_notes":["..."]}],"removed_pois":[{"poi_id":"...","reason":"..."}],"risk_notes":["..."]}],"global_risks":["..."]}\n'
                "</output_schema>"
            ),
        },
    ]


def _fallback_copy_payload(itinerary: dict, user_profile: dict, copy_context: dict) -> dict:
    days = []
    for day in itinerary.get("days", []):
        day_items = [
            {
                "poi_id": item.get("poi_id"),
                "reason": "先安排必去地点。" if _is_reasonably_must_keep(item.get("name", ""), copy_context, day.get("day")) else "按顺路和当天节奏安排。",
                "risk_notes": [],
            }
            for item in day.get("items", [])
        ]
        removed = [
            {
                "poi_id": item.get("poi_id"),
                "reason": _reason_text_from_codes(item.get("reason_codes") or []),
            }
            for item in day.get("removed_pois", [])
        ]
        days.append(
            {
                "day": day.get("day"),
                "summary": "围绕当天主要地点顺路安排。",
                "items": day_items,
                "removed_pois": removed,
                "risk_notes": [],
            }
        )
    risks = [_issue_to_text(issue) for issue in copy_context.get("hard_issues", []) + copy_context.get("soft_issues", [])]
    if not risks:
        risks = [_risk_tag_text(tag) for tag in copy_context.get("global_risk_tags", [])]
    return {
        "route_summary": {"main_message": _main_message(itinerary, user_profile)},
        "days": days,
        "global_risks": [risk for risk in risks if risk],
    }


def _merge_copy_payload(itinerary: dict, payload: dict) -> None:
    summary = itinerary.get("route_summary") if isinstance(itinerary.get("route_summary"), dict) else {}
    raw_summary = payload.get("route_summary") if isinstance(payload.get("route_summary"), dict) else {}
    if raw_summary.get("main_message"):
        summary["main_message"] = raw_summary["main_message"]
    itinerary["route_summary"] = summary

    day_by_id = {day.get("day"): day for day in itinerary.get("days", [])}
    for raw_day in payload.get("days", []) or []:
        if not isinstance(raw_day, dict):
            continue
        day = day_by_id.get(raw_day.get("day"))
        if not day:
            continue
        if raw_day.get("summary"):
            day["summary"] = raw_day["summary"]
        if raw_day.get("risk_notes"):
            day["risk_notes"] = _text_list(raw_day.get("risk_notes"))
        item_by_id = {item.get("poi_id"): item for item in day.get("items", [])}
        for raw_item in raw_day.get("items", []) or []:
            if not isinstance(raw_item, dict):
                continue
            item = item_by_id.get(raw_item.get("poi_id"))
            if not item:
                continue
            if raw_item.get("reason"):
                item["reason"] = raw_item["reason"]
            if raw_item.get("risk_notes"):
                item["risk_notes"] = _text_list(raw_item.get("risk_notes"))
        removed_by_key = {
            (item.get("poi_id"), item.get("name")): item
            for item in day.get("removed_pois", [])
            if isinstance(item, dict)
        }
        for raw_removed in raw_day.get("removed_pois", []) or []:
            if not isinstance(raw_removed, dict):
                continue
            removed = removed_by_key.get((raw_removed.get("poi_id"), raw_removed.get("name")))
            if removed is None and raw_removed.get("poi_id"):
                removed = next(
                    (item for item in day.get("removed_pois", []) if isinstance(item, dict) and item.get("poi_id") == raw_removed.get("poi_id")),
                    None,
                )
            if removed and raw_removed.get("reason"):
                removed["reason"] = raw_removed["reason"]

    itinerary["global_risks"] = _unique_texts(_text_list(itinerary.get("global_risks", [])) + _text_list(payload.get("global_risks", [])))


def _is_reasonably_must_keep(name: str, copy_context: dict, day_number) -> bool:
    for day in copy_context.get("days", []):
        if day.get("day") != day_number:
            continue
        for item in day.get("items", []):
            if item.get("name") == name and item.get("must_keep"):
                return True
    return False


def _reason_text_from_codes(reason_codes: list[str]) -> str:
    for code in reason_codes:
        mapped = {
            "time_over_budget": "为控制当天外出时间，先放入备选。",
            "far_detour": "路线较绕，本次先不安排。",
            "must_keep_priority": "优先保留更重要的地点。",
        }.get(code)
        if mapped:
            return mapped
    return "本次先不安排。"


def _issue_to_text(issue: dict) -> str:
    return str(issue.get("message") or issue.get("suggestion") or "").strip()


def _risk_tag_text(tag: str) -> str:
    return {
        "must_places_dense": "必去地点较多，当天节奏会更满。",
        "cross_district_heavy": "当天跨区较多，移动时间可能偏长。",
    }.get(tag, tag)


def _text_list(value) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [text for item in values if (text := _text_value(item))]


def _text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("message", "suggestion", "reason", "summary", "name"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _unique_texts(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


def _sanitize_itinerary_text(itinerary: dict) -> None:
    summary = itinerary.get("route_summary")
    if isinstance(summary, dict) and "main_message" in summary:
        summary["main_message"] = _sanitize_user_text(summary.get("main_message", ""))
    itinerary["global_risks"] = [_sanitize_user_text(text) for text in _text_list(itinerary.get("global_risks", []))]
    itinerary["revision_notes"] = [_sanitize_user_text(text) for text in _text_list(itinerary.get("revision_notes", []))]
    for day in itinerary.get("days", []):
        if "summary" in day:
            day["summary"] = _sanitize_user_text(day.get("summary", ""))
        for item in day.get("items", []):
            if "reason" in item:
                item["reason"] = _sanitize_user_text(item.get("reason", ""))
            if "risk_notes" in item:
                item["risk_notes"] = [_sanitize_user_text(text) for text in _text_list(item.get("risk_notes", []))]
        for item in day.get("removed_pois", []):
            if isinstance(item, dict):
                item["reason"] = _sanitize_user_text(item.get("reason", ""))


def _sanitize_user_text(text: str) -> str:
    value = str(text or "")
    value = re.sub(
        r"\b(?:must_include|must_visit|user_override|final_decision|system_decision|arrange_nearby|needs_confirmation|unresolved|exclude|optional|include)\b\s*[，,、。；;:：]?\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"^[\s，,、。；;:：]+", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def _ensure_display_sections(itinerary: dict, user_profile: dict) -> None:
    unscheduled = _collect_unscheduled(itinerary)
    attention = _collect_attention(itinerary)
    scheduled_count = sum(len(day.get("items", [])) for day in itinerary.get("days", []))
    itinerary["unscheduled_places"] = unscheduled
    itinerary["attention_places"] = attention
    summary = itinerary.get("route_summary") if isinstance(itinerary.get("route_summary"), dict) else {}
    summary.setdefault("main_message", _main_message(itinerary, user_profile))
    summary["scheduled_places_count"] = scheduled_count
    summary["unscheduled_places_count"] = len(unscheduled)
    summary["attention_required_count"] = len(attention)
    itinerary["route_summary"] = summary


def _collect_unscheduled(itinerary: dict) -> list[dict]:
    items: list[dict] = []
    for day in itinerary.get("days", []):
        for item in day.get("removed_pois", []):
            if isinstance(item, dict):
                items.append({"name": item.get("name", "未安排地点"), "reason": _short_reason(item.get("reason", ""))})
            else:
                items.append({"name": str(item), "reason": "本次未安排"})
    return items


def _collect_attention(itinerary: dict) -> list[dict]:
    attention: list[dict] = []
    for poi in itinerary.get("uncertain_pois", []) or []:
        if not isinstance(poi, dict):
            continue
        attention.append(
            {
                "name": poi.get("standard_name") or poi.get("raw_name") or poi.get("name") or "待确认地点",
                "reason": _short_reason(poi.get("decision_reason") or "地点还需要确认"),
            }
        )
    return attention


def _main_message(itinerary: dict, user_profile: dict) -> str:
    days = user_profile.get("days") or len(itinerary.get("days", [])) or 1
    preferences = user_profile.get("preferences", {})
    preference_labels = []
    if preferences.get("food", 0) >= 5:
        preference_labels.append("美食")
    if preferences.get("photo", 0) >= 5:
        preference_labels.append("拍照")
    if preferences.get("citywalk", 0) >= 5:
        preference_labels.append("城市漫步")
    goal = "、".join(preference_labels[:3]) or _route_goal_label(user_profile.get("route_goal", "balanced"))
    return f"已为你整理出 {days} 天路线，优先满足{goal}。"


def _route_goal_label(route_goal: str) -> str:
    return {
        "food_first": "美食",
        "photo_first": "拍照",
    }.get(route_goal, "整体体验")


def _removed_reason(poi: dict | None) -> str:
    if not poi:
        return "地点不在可用清单中"
    if poi.get("final_decision") == "exclude":
        return "已从本次路线移除"
    if poi.get("final_decision") == "unresolved":
        return "匹配不确定"
    return "地点还需要确认"


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


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _short_reason(reason: str) -> str:
    for token in ["距离较远", "时间不足", "匹配不确定", "不顺路", "类型重复", "单独安排", "已移除"]:
        if token in reason:
            return token
    return reason or "本次未安排"
