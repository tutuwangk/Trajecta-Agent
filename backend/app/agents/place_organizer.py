from __future__ import annotations

from typing import Literal


SystemDecision = Literal["include", "optional", "needs_confirmation", "exclude"]
UserOverride = Literal["must_include", "optional", "remove", "rename_confirm", "arrange_nearby", "none"]
FinalDecision = Literal["include", "optional", "exclude", "unresolved"]


def organize_place(
    raw_poi: dict,
    grounded_poi: dict,
    user_profile: dict,
    user_override: str = "none",
) -> dict:
    requested_override = user_override
    normalized_override = normalize_user_override(user_override)
    system_decision, reason = _system_decision(raw_poi, grounded_poi, user_profile)
    final_decision = _final_decision(system_decision, normalized_override)
    if requested_override == "optional" and final_decision == "include":
        final_decision = "optional"
    inferred_role = _infer_role(raw_poi, grounded_poi)
    item = build_place_pool_item(raw_poi, grounded_poi, system_decision, normalized_override, final_decision, inferred_role)
    return {
        "system_decision": system_decision,
        "user_override": normalized_override,
        "final_decision": final_decision,
        "inferred_role": inferred_role,
        "decision_reason": reason,
        "place_pool_item": item,
    }


def normalize_user_override(value: str | None) -> UserOverride:
    mapping = {
        "must_visit": "must_include",
        "must_include": "must_include",
        "delete": "remove",
        "remove": "remove",
        "rename_confirm": "rename_confirm",
        "arrange_nearby": "arrange_nearby",
        "keep": "none",
        "optional": "optional",
        "none": "none",
    }
    return mapping.get(value or "none", "none")  # type: ignore[return-value]


def legacy_decision(user_override: str, final_decision: str) -> str:
    if user_override == "must_include":
        return "must_visit"
    if user_override == "optional":
        return "optional"
    if user_override == "remove" or final_decision == "exclude":
        return "delete"
    if final_decision == "optional":
        return "optional"
    return "keep"


def build_place_pool_item(
    raw_poi: dict,
    grounded_poi: dict,
    system_decision: str,
    user_override: str,
    final_decision: str,
    inferred_role: str,
) -> dict:
    status = grounded_poi.get("match_status") or "unmatched"
    display_name = grounded_poi.get("standard_name") or grounded_poi.get("raw_name") or raw_poi.get("raw_name") or "未命名地点"
    return {
        "id": _place_id(grounded_poi),
        "display_name": display_name,
        "type_label": _type_label(inferred_role, grounded_poi.get("category_normalized") or raw_poi.get("possible_category", "")),
        "status_label": _status_label(status),
        "decision_label": _decision_label(final_decision),
        "primary_actions": _primary_actions(user_override, final_decision, grounded_poi),
        "needs_attention": status != "matched" or system_decision == "needs_confirmation" or final_decision == "unresolved",
    }


def _system_decision(raw_poi: dict, grounded_poi: dict, user_profile: dict) -> tuple[SystemDecision, str]:
    raw_name = str(raw_poi.get("raw_name") or grounded_poi.get("raw_name") or "")
    standard_name = str(grounded_poi.get("standard_name") or "")
    avoid_names = user_profile.get("constraints", {}).get("avoid_visit", [])
    if any(name and (name in raw_name or name in standard_name) for name in avoid_names):
        return "exclude", "你已表达不想去，默认不放进路线。"
    status = grounded_poi.get("match_status")
    if status == "unmatched":
        return "exclude", "地图位置还不可靠，暂不放进路线。"
    if status == "ambiguous":
        if grounded_poi.get("is_chain"):
            return "needs_confirmation", "这是连锁品牌，需要先选择具体门店。"
        return "needs_confirmation", ""
    confidence = float(grounded_poi.get("match_confidence") or raw_poi.get("confidence") or 1)
    raw_confidence = float(raw_poi.get("confidence") or confidence)
    if confidence >= 0.8 and raw_confidence >= 0.7:
        return "include", ""
    return "optional", ""


def _final_decision(system_decision: str, user_override: str) -> FinalDecision:
    if user_override == "must_include":
        return "include"
    if user_override == "optional":
        return "optional"
    if user_override == "remove":
        return "exclude"
    if system_decision == "include":
        return "optional"
    if system_decision == "optional":
        return "optional"
    if system_decision == "exclude":
        return "exclude"
    return "unresolved"


def _infer_role(raw_poi: dict, grounded_poi: dict) -> str:
    category = f"{grounded_poi.get('category_normalized', '')}{grounded_poi.get('category_raw', '')}{raw_poi.get('possible_category', '')}"
    contexts = " ".join(str(item) for item in raw_poi.get("contexts", []) + grounded_poi.get("contexts", []))
    text = f"{category}{contexts}{raw_poi.get('raw_name', '')}{grounded_poi.get('standard_name', '')}"
    if any(token in contexts for token in ["从这里出发", "起点", "出发"]):
        return "start"
    if any(token in contexts for token in ["最后到", "终点", "结束"]):
        return "end"
    if any(token in text for token in ["餐", "restaurant", "美食", "咖啡", "小吃"]):
        return "meal"
    if any(token in text for token in ["酒店", "住宿", "hotel"]):
        return "hotel"
    if any(token in text for token in ["机场", "车站", "地铁站", "transport", "交通"]):
        return "transport"
    return "visit"


def _place_id(grounded_poi: dict) -> str:
    if grounded_poi.get("amap_id"):
        return f"amap_{grounded_poi['amap_id']}"
    return f"raw_{grounded_poi.get('raw_name', '')}"


def _status_label(status: str) -> str:
    return {"matched": "已识别", "ambiguous": "需确认", "unmatched": "未匹配"}.get(status, "需确认")


def _decision_label(final_decision: str) -> str:
    return {
        "include": "已纳入",
        "optional": "待定",
        "exclude": "未纳入",
        "unresolved": "需确认",
    }.get(final_decision, "需确认")


def _type_label(role: str, category: str) -> str:
    if role == "meal":
        return "餐饮"
    if role == "hotel":
        return "住宿"
    if role == "transport":
        return "交通"
    if "shopping" in category:
        return "商圈"
    if "museum" in category:
        return "文化"
    if "park" in category:
        return "公园"
    return "游玩"


def _primary_actions(user_override: str, final_decision: str, grounded_poi: dict) -> list[str]:
    actions = ["必去", "待定", "移除", "改名"]
    if grounded_poi.get("is_chain") and grounded_poi.get("chain_status") != "resolved":
        return ["顺路规划", "改名", "移除"]
    if grounded_poi.get("is_chain"):
        return ["顺路规划", *actions]
    if user_override == "remove" or final_decision == "exclude":
        return ["必去", "待定", "改名"]
    return actions


def _has_map_candidate(grounded_poi: dict) -> bool:
    location = grounded_poi.get("location") or {}
    return bool(grounded_poi.get("amap_id") and location.get("lng") is not None and location.get("lat") is not None)
