from __future__ import annotations

from copy import deepcopy
import json
import re

from app.planning.copy_merger import assert_copy_preserved_route_facts, enforce_copy_day_count, route_fact_fingerprint


def generate_copy(itinerary: dict, copy_context: dict, user_profile: dict, llm_client=None) -> dict:
    final = deepcopy(itinerary)
    fact_fingerprint = route_fact_fingerprint(final)
    payload = None
    if llm_client is not None:
        try:
            payload = llm_client.json_chat(_copy_messages(copy_context), step="generate_itinerary_copy", temperature=0.3)
        except Exception:
            payload = None
    if not isinstance(payload, dict):
        payload = _fallback_copy_payload(final, user_profile, copy_context)
    _merge_copy_payload(final, payload)
    assert_copy_preserved_route_facts(fact_fingerprint, final)
    enforce_copy_day_count(final, user_profile)
    _sanitize_itinerary_text(final)
    _ensure_display_sections(final, user_profile)
    return final


def _copy_messages(copy_context: dict) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "## Role\n"
                "你是旅行路线文案助手。\n\n"
                "## Mission\n"
                "只根据给定事实写 route_summary、day summary、item reason、removed reason、risk notes，输出严格 JSON。\n\n"
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
        if "theme" in day:
            day["theme"] = _sanitize_user_text(day.get("theme", ""))
        if "summary" in day:
            day["summary"] = _sanitize_user_text(day.get("summary", ""))
        if "risk_notes" in day:
            day["risk_notes"] = [_sanitize_user_text(text) for text in _text_list(day.get("risk_notes", []))]
        for segment in day.get("segments", []):
            if isinstance(segment, dict) and "reason" in segment:
                segment["reason"] = _sanitize_user_text(segment.get("reason", ""))
        for hotel_break in day.get("hotel_rest_breaks", []):
            if isinstance(hotel_break, dict) and "reason" in hotel_break:
                hotel_break["reason"] = _sanitize_user_text(hotel_break.get("reason", ""))
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
        r"<(?:think|analysis|reasoning)\b[^>]*>.*?</(?:think|analysis|reasoning)>",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"```(?:think|analysis|reasoning)\s*.*?```",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    has_reasoning_prefix = bool(re.search(r"(?:思考|分析|推理)(?:过程|内容)?\s*[:：]", value))
    if has_reasoning_prefix:
        final_markers = list(re.finditer(r"(?:最终(?:安排|建议|结论)|给用户的(?:安排|建议)|答复)\s*[:：]\s*", value))
        if final_markers:
            value = value[final_markers[-1].end() :]
        else:
            value = re.sub(r"^(?:思考|分析|推理)(?:过程|内容)?\s*[:：].*(?:\n|$)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"</?(?:think|analysis|reasoning)\b[^>]*>", "", value, flags=re.IGNORECASE)
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


def _short_reason(reason: str) -> str:
    for token in ["距离较远", "时间不足", "匹配不确定", "不顺路", "类型重复", "单独安排", "已移除"]:
        if token in reason:
            return token
    return reason or "本次未安排"
