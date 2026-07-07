from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy

from app.agents.intensity import daily_time_limit_minutes
from app.core import AppError


def compile_planning_context(
    user_profile: dict,
    runtime_pois: list[dict],
    route_matrix: list[dict],
    uncertain_pois: list[dict] | None = None,
    hotel_anchor: dict | None = None,
    order_constraints: list[dict] | None = None,
    time_constraints: list[dict] | None = None,
    planning_decisions: list[dict] | None = None,
) -> dict:
    plannable_pois: list[dict] = []
    optional_poi_ids: list[str] = []
    must_poi_ids: list[str] = []
    preferred_poi_ids: list[str] = []
    meal_candidate_poi_ids: list[str] = []
    meal_candidates: list[dict] = []
    district_groups: OrderedDict[str, list[str]] = OrderedDict()
    poi_lookup: dict[str, dict] = {}

    for poi in runtime_pois:
        if not _is_plannable_for_context(poi):
            continue
        poi_id = str(poi.get("poi_id") or "").strip()
        if not poi_id:
            continue
        name = _poi_name(poi)
        normalized = {
            "poi_id": poi_id,
            "name": name,
            "brand_name": poi.get("brand_name") or "",
            "district": poi.get("district") or "",
            "category": poi.get("category") or poi.get("category_normalized") or "unknown",
            "experience_type": _experience_type(poi),
            "estimated_duration_min": _selected_visit_duration_min(poi, user_profile),
            "visit_duration_profile": _visit_duration_profile(poi),
            "user_override": poi.get("user_override") or "none",
            "final_decision": poi.get("final_decision") or "include",
            "inferred_role": poi.get("inferred_role") or "",
            "experience_tags": list(poi.get("experience_tags") or poi.get("ugc_tags") or []),
            "time_suitability": list((poi.get("planning_semantics") or {}).get("time_suitability") or poi.get("best_time") or []),
            "route_semantics": dict(poi.get("route_semantics") or {}),
            "outing_role": (poi.get("planning_semantics") or {}).get("outing_role") or "anchor",
            "meal_capability": (poi.get("planning_semantics") or {}).get("meal_capability") or "none",
            "quick_stop_eligible": bool((poi.get("planning_semantics") or {}).get("quick_stop_eligible")),
            "base_duration_profiles": dict((poi.get("planning_semantics") or {}).get("base_duration_profiles") or {}),
            "must_keep": _is_must_keep_candidate(poi, user_profile),
            "is_meal_candidate": _is_meal_candidate(poi),
        }
        poi_lookup[poi_id] = {**poi, **normalized}
        plannable_pois.append(normalized)
        if normalized["must_keep"]:
            must_poi_ids.append(poi_id)
        elif normalized["final_decision"] == "optional":
            optional_poi_ids.append(poi_id)
        else:
            preferred_poi_ids.append(poi_id)
        if normalized["is_meal_candidate"]:
            meal_candidate_poi_ids.append(poi_id)
            meal_candidates.append(
                {
                    "poi_id": poi_id,
                    "name": name,
                    "district": normalized["district"],
                    "estimated_duration_min": normalized["estimated_duration_min"],
                    "final_decision": normalized["final_decision"],
                    "must_keep": normalized["must_keep"],
                    "meal_suitability_hint": _meal_suitability_hint(poi),
                    "route_fit_context": _meal_route_fit_context(poi),
                }
            )
        district = normalized["district"]
        if district:
            district_groups.setdefault(district, []).append(poi_id)

    return {
        "destination": user_profile.get("destination", ""),
        "days": int(user_profile.get("days") or 1),
        "day_budget_min": daily_time_limit_minutes(user_profile),
        "route_goal": user_profile.get("route_goal", "balanced"),
        "must_visit_names": list(user_profile.get("constraints", {}).get("must_visit", [])),
        "avoid_visit_names": list(user_profile.get("constraints", {}).get("avoid_visit", [])),
        "hotel_anchor": hotel_anchor,
        "plannable_pois": plannable_pois,
        "must_poi_ids": must_poi_ids,
        "preferred_poi_ids": preferred_poi_ids,
        "optional_poi_ids": optional_poi_ids,
        "meal_candidate_poi_ids": meal_candidate_poi_ids,
        "meal_candidates": meal_candidates,
        "district_summary": [
            {"district": district, "poi_ids": poi_ids, "count": len(poi_ids)}
            for district, poi_ids in district_groups.items()
        ],
        "route_matrix": route_matrix,
        "order_constraints": list(order_constraints or []),
        "time_constraints": list(time_constraints or []),
        "planning_decisions": list(planning_decisions or []),
        "route_semantics": {
            poi_id: dict(poi.get("route_semantics") or {})
            for poi_id, poi in poi_lookup.items()
            if poi.get("route_semantics")
        },
        "poi_lookup": poi_lookup,
        "allowed_poi_ids": [poi["poi_id"] for poi in plannable_pois],
        "uncertain_pois": list(uncertain_pois or []),
        "runtime_pois": runtime_pois,
    }


def enrich_planning_semantics_with_llm(planning_context: dict, llm_client, max_attempts: int = 2) -> dict:
    if not planning_context.get("plannable_pois"):
        return planning_context
    last_error: AppError | None = None
    for _ in range(max_attempts):
        try:
            payload = llm_client.json_chat(_semantic_messages(planning_context), step="plan_poi_semantics", temperature=0.2)
            semantics = _normalize_semantics_payload(payload, planning_context)
            enriched = deepcopy(planning_context)
            enriched["route_semantics"] = semantics
            for poi in enriched.get("plannable_pois", []):
                poi_id = poi.get("poi_id")
                if poi_id in semantics:
                    poi["route_semantics"] = semantics[poi_id]
            for poi_id, poi in enriched.get("poi_lookup", {}).items():
                if poi_id in semantics:
                    poi["route_semantics"] = semantics[poi_id]
            return enriched
        except AppError as exc:
            last_error = exc
            if exc.code not in {"llm_invalid_json", "llm_invalid_planning_semantics"}:
                raise
    raise last_error or AppError("LLM 未返回有效规划语义。", code="llm_invalid_planning_semantics", step="plan_poi_semantics")


def plan_skeleton_with_llm(planning_context: dict, llm_client, max_attempts: int = 2) -> dict:
    if not planning_context.get("plannable_pois"):
        return _empty_skeleton(planning_context)
    return _call_skeleton_llm(
        llm_client,
        _planning_messages(planning_context),
        planning_context,
        step="plan_itinerary_skeleton",
        max_attempts=max_attempts,
    )


def plan_day_blueprint_with_llm(planning_context: dict, llm_client, max_attempts: int = 2) -> dict:
    if not planning_context.get("plannable_pois"):
        return _empty_skeleton(planning_context)
    return _call_skeleton_llm(
        llm_client,
        _blueprint_messages(planning_context),
        planning_context,
        step="plan_itinerary_blueprint",
        max_attempts=max_attempts,
    )


def replan_skeleton_with_llm(
    planning_context: dict,
    previous_skeleton: dict,
    issues: dict,
    llm_client,
    max_attempts: int = 2,
) -> dict:
    return _call_skeleton_llm(
        llm_client,
        _replan_messages(planning_context, previous_skeleton, issues),
        planning_context,
        step="replan_itinerary_skeleton",
        max_attempts=max_attempts,
    )


def replan_day_blueprint_with_llm(
    planning_context: dict,
    previous_skeleton: dict,
    issues: dict | list[dict],
    llm_client,
    max_attempts: int = 2,
) -> dict:
    return _call_skeleton_llm(
        llm_client,
        _replan_blueprint_messages(planning_context, previous_skeleton, issues),
        planning_context,
        step="replan_itinerary_blueprint",
        max_attempts=max_attempts,
    )


def materialize_itinerary_from_skeleton(planning_context: dict, skeleton: dict) -> dict:
    poi_lookup = planning_context.get("poi_lookup", {})
    itinerary = {
        "destination": skeleton.get("destination") or planning_context.get("destination", ""),
        "days": [],
        "global_risks": list(skeleton.get("risk_tags") or []),
        "uncertain_pois": list(planning_context.get("uncertain_pois") or []),
        "revision_notes": [],
    }
    for raw_day in skeleton.get("days", []):
        items = []
        segments = _normalize_day_segments(raw_day)
        meal_slots = _normalize_meal_slots(raw_day.get("meal_slots"))
        poi_ids = []
        if segments:
            for segment in segments:
                if segment.get("kind") == "outing":
                    poi_ids.extend(segment.get("poi_ids") or [])
        else:
            poi_ids = list(raw_day.get("poi_ids", []))
        for poi_id in poi_ids:
            poi = _resolve_materialized_poi(poi_lookup[poi_id], raw_day.get("selected_branch_ids", {}).get(poi_id))
            scheduled_role = _resolved_scheduled_role(poi_id, poi, raw_day.get("scheduled_roles", {}), meal_slots)
            items.append(
                {
                    "poi_id": poi_id,
                    "name": poi["standard_name"] if poi.get("standard_name") else poi["name"],
                    "duration_min": _materialized_duration_min(poi, scheduled_role),
                    "time_block": "",
                    "risk_notes": [],
                    "selected_branch_id": poi.get("selected_branch_id"),
                    "scheduled_role": scheduled_role,
                    "burden_role": _burden_role(poi, scheduled_role),
                    "trim_priority": _trim_priority(poi, scheduled_role),
                    "quick_stop_total_cost_min": poi.get("quick_stop_total_cost_min"),
                    "preferred_time_windows": _preferred_time_windows(poi_id, poi, planning_context),
                }
            )
        removed_pois = []
        for poi_id in raw_day.get("unscheduled_poi_ids", []):
            poi = poi_lookup.get(poi_id)
            if not poi:
                continue
            removed_pois.append(
                {
                    "poi_id": poi_id,
                    "name": poi["name"],
                    "reason_codes": list(raw_day.get("drop_reason_codes", {}).get(poi_id, [])),
                }
            )
        itinerary["days"].append(
            {
                "day": int(raw_day.get("day") or len(itinerary["days"]) + 1),
                "theme": str(raw_day.get("theme_hint") or "").strip(),
                "summary": "",
                "meal_slots": meal_slots,
                "selected_branch_ids": dict(raw_day.get("selected_branch_ids") or {}),
                "scheduled_roles": dict(raw_day.get("scheduled_roles") or {}),
                "segments": segments,
                "items": items,
                "removed_pois": removed_pois,
                "alternatives": [],
                "meal_breaks": [],
                "risk_tags": list(raw_day.get("risk_tags") or []),
            }
        )
    return itinerary


def build_copy_context(planning_context: dict, itinerary: dict, hard_issues: dict | None = None, soft_issues: list[dict] | None = None) -> dict:
    poi_lookup = planning_context.get("poi_lookup", {})
    return {
        "destination": itinerary.get("destination") or planning_context.get("destination", ""),
        "route_goal": planning_context.get("route_goal", "balanced"),
        "days": [
            {
                "day": day.get("day"),
                "theme": day.get("theme", ""),
                "outing_min": day.get("total_outing_min"),
                "risk_tags": list(day.get("risk_tags") or []),
                "items": [
                    {
                        "poi_id": item.get("poi_id"),
                        "name": item.get("name"),
                        "duration_min": item.get("duration_min"),
                        "district": poi_lookup.get(item.get("poi_id"), {}).get("district", ""),
                        "category": poi_lookup.get(item.get("poi_id"), {}).get("category", ""),
                        "must_keep": poi_lookup.get(item.get("poi_id"), {}).get("must_keep", False),
                    }
                    for item in day.get("items", [])
                ],
                "removed_pois": [
                    {
                        "poi_id": item.get("poi_id"),
                        "name": item.get("name"),
                        "reason_codes": list(item.get("reason_codes") or []),
                    }
                    for item in day.get("removed_pois", [])
                ],
            }
            for day in itinerary.get("days", [])
        ],
        "hard_issues": list((hard_issues or {}).get("issues", [])),
        "soft_issues": list(soft_issues or []),
        "global_risk_tags": list(itinerary.get("global_risks") or []),
        "uncertain_pois": list(itinerary.get("uncertain_pois") or []),
    }


def plan_itinerary(user_profile: dict, runtime_pois: list[dict], route_matrix: list[dict], llm_client) -> dict:
    planning_context = compile_planning_context(user_profile, runtime_pois, route_matrix)
    skeleton = plan_skeleton_with_llm(planning_context, llm_client)
    return materialize_itinerary_from_skeleton(planning_context, skeleton)


def _call_skeleton_llm(llm_client, messages: list[dict[str, str]], planning_context: dict, step: str, max_attempts: int) -> dict:
    last_error: AppError | None = None
    for _ in range(max_attempts):
        try:
            payload = llm_client.json_chat(messages, step=step, temperature=0.2)
            return _normalize_skeleton_payload(payload, planning_context, step)
        except AppError as exc:
            last_error = exc
            if exc.code not in {"llm_invalid_json", "llm_invalid_plan_skeleton"}:
                raise
    raise last_error or AppError("LLM 未返回有效路线骨架。", code="llm_invalid_plan_skeleton", step=step)


def _planning_messages(planning_context: dict) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "## Role\n"
                "你是旅行路线主规划者。\n\n"
                "## Mission\n"
                "只基于候选集合规划路线骨架，输出严格 JSON。\n\n"
                "## Hard Rules\n"
                "- 不得新增候选集合之外的 poi_id。\n"
                "- 禁止生成时间线、交通字段、total_outing_min、summary、reason、risk_notes 等最终文案字段。\n"
                "- 只输出分天、每天 poi 顺序或 segments、meal_slots、未安排 poi、reason codes、risk tags。\n"
                "- 如果某个 poi 是快速顺路停靠、正式用餐或夜生活收尾，请在 `scheduled_roles` 中为对应 poi_id 标明角色。\n"
                "- 必去地点优先，待定地点只能在时间允许且顺路时安排。\n"
                "- 午餐和晚餐是正式规划约束；若 11:30 前还没回酒店，当天必须显式落地午餐；若 17:30 前还没回酒店，当天必须显式落地晚餐。早餐只在用户资料里有明确早餐推荐且顺路时才可作为 optional meal slot 输出。\n"
                "- 每个 meal slot 必须说明由真实餐饮地点、场内用餐还是就近补位满足。\n"
                "- 严格使用 planning_context 里的 experience_type、time_suitability、outing_role 与 order_constraints 做决策。\n"
                "- `light_drink` 只能视为饮品/轻补给，不可承担正式午餐或晚餐；若顺路且适合短暂停靠，可标成 `quick_stop`。\n"
                "- `full_meal` / `snack` 若承担午餐或晚餐，应标成 `meal_stop`；午餐和晚餐都必须落地，但不应因为强度控制而被当作可随意删掉的负担。\n"
                "- `nightlife` 只能安排在 evening/night，不可承担午餐；若作为当天收尾，请标成 `nightlife_stop`。\n"
                "- 如果上午适合的地点和晚上适合的地点之间没有合理顺路安排，可以输出 segments，并在中间插入 `hotel_rest`。\n"
                "- 如果无法满足，保留骨架并用 reason codes / risk tags 表达，不得编造事实。"
            ),
        },
        {
            "role": "user",
            "content": (
                "<hard_rules>\n"
                "- 不得新增候选集合之外的 poi_id。\n"
                "- 禁止生成时间线、交通字段、total_outing_min、summary、reason、risk_notes 等最终文案字段。\n"
                "- 请输出 meal_slots，并判断现有餐饮地点更适合 breakfast、lunch、dinner 还是兼容多个时段。\n"
                "- 如果已有餐饮地点可承接某顿饭，不要再为同一顿饭输出泛化占位。\n"
                "- 只有在餐饮地点不足、明显不顺路或为保住必去地点不值得绕行时，才允许 fallback_nearby。\n"
                "- 用户明确写了先后顺序时，优先满足 `strong_preference`；若因保住必去点、减少明显绕路或控制强度而调整，可在 reason codes / risk tags 里表达。\n"
                "- 若使用 segments，outing 段里的 poi_ids 仍然必须全部来自候选集合，hotel_rest 段只允许写 duration_min 和 reason。\n"
                "</hard_rules>\n\n"
                "<task>\n"
                "请输出可执行的路线骨架。\n"
                "</task>\n\n"
                "<planning_context>\n"
                f"{_prompt_context(planning_context)}\n"
                "</planning_context>\n\n"
                "<output_schema>\n"
                '{"destination":"...","days":[{"day":1,"theme_hint":"...","segments":[{"kind":"outing","segment_time":"morning","poi_ids":["..."]},{"kind":"hotel_rest","duration_min":180,"reason":"中午回酒店休息"},{"kind":"outing","segment_time":"evening","poi_ids":["..."]}],"poi_ids":["..."],"scheduled_roles":{"poi_id":"quick_stop|meal_stop|anchor_visit|filler_visit|nightlife_stop"},"meal_slots":[{"slot":"lunch","requirement":"required","source":"poi","poi_id":"..."}],"unscheduled_poi_ids":["..."],"drop_reason_codes":{"poi_id":["time_over_budget"]},"risk_tags":["must_places_dense"]}],"unscheduled":[{"poi_id":"...","reason_codes":["far_detour"]}],"risk_tags":["..."]}\n'
                "</output_schema>"
            ),
        },
    ]


def _replan_messages(planning_context: dict, previous_skeleton: dict, issues: dict) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "## Role\n"
                "你是旅行路线重规划助手。\n\n"
                "## Mission\n"
                "只修复给定硬问题，尽量保持原骨架不变，输出严格 JSON。\n\n"
                "## Hard Rules\n"
                "- 只修改受影响的天数和 poi 排列。\n"
                "- 不得新增候选集合之外的 poi_id。\n"
                "- 禁止生成时间线与最终文案。\n"
                "- 优先满足 must_visit_missing、超强度、非法地点等硬约束。"
            ),
        },
        {
            "role": "user",
            "content": (
                "<planning_context>\n"
                f"{_prompt_context(planning_context)}\n"
                "</planning_context>\n\n"
                "<previous_skeleton>\n"
                f"{previous_skeleton}\n"
                "</previous_skeleton>\n\n"
                "<issues>\n"
                f"{issues}\n"
                "</issues>"
            ),
        },
    ]


def _semantic_messages(planning_context: dict) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "## Role\n"
                "你是旅行路线的规划语义层。\n\n"
                "## Mission\n"
                "为每个候选地点判断本次路线里的用途、正餐能力、适合时段和风险提示，只输出严格 JSON。\n\n"
                "## Hard Rules\n"
                "- 不得新增候选集合之外的 poi_id。\n"
                "- 不改地点池决策，只输出路线专用语义。\n"
                "- 午餐/晚餐只有正餐地点，或用户明确指定的轻食地点，才能承接。\n"
                "- 面包店、咖啡、甜品、饮品默认只能作为早餐、轻补给或顺路停靠。"
            ),
        },
        {
            "role": "user",
            "content": (
                "<task>请输出规划语义层结果。</task>\n\n"
                "<planning_context>\n"
                f"{_prompt_context(planning_context)}\n"
                "</planning_context>\n\n"
                "<output_schema>\n"
                '{"semantics":[{"poi_id":"...","visit_role":"主目的地|顺路补充|餐饮|夜间体验|购物/休息|备选","meal_level":"正餐|轻食|小吃/甜品|饮品|非餐饮","meal_fit":["早餐|午餐|晚餐|仅补给|不可承接正餐"],"time_fit":["上午|中午|下午|傍晚|夜间|全天"],"priority_reason":"...","risk_hint":"..."}]}\n'
                "</output_schema>"
            ),
        },
    ]


def _blueprint_messages(planning_context: dict) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "## Role\n"
                "你是旅行日程蓝图规划者。\n\n"
                "## Mission\n"
                "像真人旅行规划一样先决定每天怎么过，输出可落地蓝图 JSON。\n\n"
                "## Hard Rules\n"
                "- 不得新增候选集合之外的 poi_id。\n"
                "- 禁止输出最终 arrival_time、交通时间、total_outing_min 和最终文案。\n"
                "- 必须使用规划语义层的 meal_level、meal_fit、time_fit 和用户明确时间要求。\n"
                "- 明确写了晚上/夜间的地点应放 evening/night 分段；无法满足时放入未安排并给 reason code。\n"
                "- 午餐/晚餐不能由饮品、甜品、普通面包店默认承接；无合适餐厅时使用 fallback_nearby。\n"
                "- 可以使用 segments 表达上午、午餐、下午、晚餐、夜间和回酒店休息。"
            ),
        },
        {
            "role": "user",
            "content": (
                "<task>请输出 DayBlueprint。</task>\n\n"
                "<planning_context>\n"
                f"{_prompt_context(planning_context)}\n"
                "</planning_context>\n\n"
                "<output_schema>\n"
                '{"destination":"...","days":[{"day":1,"theme_hint":"...","segments":[{"kind":"outing","segment_time":"morning","poi_ids":["..."]},{"kind":"hotel_rest","duration_min":180,"reason":"回酒店休息"},{"kind":"outing","segment_time":"night","poi_ids":["..."]}],"poi_ids":["..."],"scheduled_roles":{"poi_id":"quick_stop|meal_stop|anchor_visit|filler_visit|nightlife_stop"},"meal_slots":[{"slot":"lunch","requirement":"required","source":"fallback_nearby"}],"unscheduled_poi_ids":["..."],"drop_reason_codes":{"poi_id":["time_window_conflict"]},"risk_tags":["..."]}],"unscheduled":[{"poi_id":"...","reason_codes":["far_detour"]}],"risk_tags":["..."]}\n'
                "</output_schema>"
            ),
        },
    ]


def _replan_blueprint_messages(planning_context: dict, previous_skeleton: dict, issues: dict | list[dict]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "## Role\n"
                "你是旅行日程蓝图重排助手。\n\n"
                "## Mission\n"
                "根据硬问题和高严重体验问题重排 DayBlueprint，尽量保持合理旅行体验。\n\n"
                "## Hard Rules\n"
                "- 不得新增候选集合之外的 poi_id。\n"
                "- 禁止输出最终 arrival_time、交通时间、total_outing_min 和最终文案。\n"
                "- 硬问题必须修；高严重软问题优先修；中低严重软问题可以保留为风险。"
            ),
        },
        {
            "role": "user",
            "content": (
                "<planning_context>\n"
                f"{_prompt_context(planning_context)}\n"
                "</planning_context>\n\n"
                "<previous_blueprint>\n"
                f"{previous_skeleton}\n"
                "</previous_blueprint>\n\n"
                "<issues>\n"
                f"{issues}\n"
                "</issues>"
            ),
        },
    ]


def _normalize_semantics_payload(payload: dict, planning_context: dict) -> dict[str, dict]:
    if not isinstance(payload, dict):
        raise AppError("LLM 返回的规划语义不是对象。", code="llm_invalid_planning_semantics", step="plan_poi_semantics")
    allowed_poi_ids = set(planning_context.get("allowed_poi_ids", []))
    result: dict[str, dict] = {}
    for raw_item in payload.get("semantics") or []:
        if not isinstance(raw_item, dict):
            continue
        poi_id = str(raw_item.get("poi_id") or "").strip()
        if not poi_id:
            continue
        if poi_id not in allowed_poi_ids:
            raise AppError("LLM 返回了候选集合之外的规划语义 poi_id。", code="llm_invalid_planning_semantics", step="plan_poi_semantics")
        result[poi_id] = {
            "visit_role": str(raw_item.get("visit_role") or "").strip(),
            "meal_level": str(raw_item.get("meal_level") or "").strip(),
            "meal_fit": _string_list(raw_item.get("meal_fit")),
            "time_fit": _string_list(raw_item.get("time_fit")),
            "priority_reason": str(raw_item.get("priority_reason") or "").strip(),
            "risk_hint": str(raw_item.get("risk_hint") or "").strip(),
        }
    return result


def _normalize_skeleton_payload(payload: dict, planning_context: dict, step: str) -> dict:
    if not isinstance(payload, dict):
        raise AppError("LLM 返回的路线骨架不是对象。", code="llm_invalid_plan_skeleton", step=step)
    allowed_poi_ids = set(planning_context.get("allowed_poi_ids", []))
    branch_options_by_poi = {
        poi.get("poi_id"): {option.get("branch_id") for option in poi.get("branch_options") or [] if option.get("branch_id")}
        for poi in planning_context.get("plannable_pois", [])
        if poi.get("branch_options")
    }
    scheduled: set[str] = set()
    days: list[dict] = []
    for index, raw_day in enumerate(payload.get("days") or []):
        if not isinstance(raw_day, dict):
            raise AppError("LLM 返回的 day 节点格式不正确。", code="llm_invalid_plan_skeleton", step=step)
        segments = _normalize_day_segments(raw_day, allowed_poi_ids=allowed_poi_ids, step=step)
        poi_ids = []
        if segments:
            for segment in segments:
                if segment.get("kind") == "outing":
                    poi_ids.extend(segment.get("poi_ids") or [])
        else:
            poi_ids = _string_list(raw_day.get("poi_ids"))
        unscheduled_poi_ids = _string_list(raw_day.get("unscheduled_poi_ids"))
        if any(poi_id not in allowed_poi_ids for poi_id in [*poi_ids, *unscheduled_poi_ids]):
            raise AppError("LLM 返回了候选集合之外的 poi_id。", code="llm_invalid_plan_skeleton", step=step)
        if len(set(poi_ids)) != len(poi_ids):
            raise AppError("LLM 在同一天重复安排了相同 poi_id。", code="llm_invalid_plan_skeleton", step=step)
        duplicate = scheduled.intersection(poi_ids)
        if duplicate:
            raise AppError("LLM 在多个天数重复安排了相同 poi_id。", code="llm_invalid_plan_skeleton", step=step)
        scheduled.update(poi_ids)
        meal_slots = _normalize_meal_slots(raw_day.get("meal_slots"), allowed_poi_ids=allowed_poi_ids, step=step)
        selected_branch_ids = _normalize_selected_branch_ids(
            raw_day.get("selected_branch_ids"),
            poi_ids,
            branch_options_by_poi,
            step=step,
        )
        scheduled_roles = _normalize_scheduled_roles(raw_day.get("scheduled_roles"), poi_ids, step=step)
        drop_reason_codes = {
            poi_id: _string_list(reason_codes)
            for poi_id, reason_codes in (raw_day.get("drop_reason_codes") or {}).items()
            if poi_id in allowed_poi_ids
        }
        days.append(
            {
                "day": _positive_int(raw_day.get("day")) or index + 1,
                "theme_hint": str(raw_day.get("theme_hint") or raw_day.get("theme") or "").strip(),
                "poi_ids": poi_ids,
                "segments": segments,
                "selected_branch_ids": selected_branch_ids,
                "scheduled_roles": scheduled_roles,
                "meal_slots": meal_slots,
                "unscheduled_poi_ids": unscheduled_poi_ids,
                "drop_reason_codes": drop_reason_codes,
                "risk_tags": _string_list(raw_day.get("risk_tags")),
            }
        )
    unscheduled = []
    raw_unscheduled = payload.get("unscheduled") or []
    if raw_unscheduled:
        for item in raw_unscheduled:
            if not isinstance(item, dict):
                continue
            poi_id = str(item.get("poi_id") or "").strip()
            if not poi_id:
                continue
            if poi_id not in allowed_poi_ids:
                raise AppError("LLM 返回了候选集合之外的未安排 poi_id。", code="llm_invalid_plan_skeleton", step=step)
            unscheduled.append({"poi_id": poi_id, "reason_codes": _string_list(item.get("reason_codes"))})
    else:
        seen_unscheduled: set[str] = set()
        for day in days:
            for poi_id in day["unscheduled_poi_ids"]:
                if poi_id in seen_unscheduled:
                    continue
                unscheduled.append(
                    {
                        "poi_id": poi_id,
                        "reason_codes": list(day["drop_reason_codes"].get(poi_id, [])),
                    }
                )
                seen_unscheduled.add(poi_id)
    return {
        "destination": str(payload.get("destination") or planning_context.get("destination", "")).strip(),
        "days": days,
        "unscheduled": unscheduled,
        "risk_tags": _string_list(payload.get("risk_tags")),
    }


def _prompt_context(planning_context: dict) -> dict:
    return {
        "destination": planning_context.get("destination", ""),
        "days": planning_context.get("days", 1),
        "day_budget_min": planning_context.get("day_budget_min"),
        "route_goal": planning_context.get("route_goal"),
        "must_visit_names": planning_context.get("must_visit_names", []),
        "avoid_visit_names": planning_context.get("avoid_visit_names", []),
        "hotel_anchor": planning_context.get("hotel_anchor"),
        "plannable_pois": planning_context.get("plannable_pois", []),
        "order_constraints": planning_context.get("order_constraints", []),
        "time_constraints": planning_context.get("time_constraints", []),
        "planning_decisions": planning_context.get("planning_decisions", []),
        "route_semantics": planning_context.get("route_semantics", {}),
        "must_poi_ids": planning_context.get("must_poi_ids", []),
        "preferred_poi_ids": planning_context.get("preferred_poi_ids", []),
        "optional_poi_ids": planning_context.get("optional_poi_ids", []),
        "meal_candidate_poi_ids": planning_context.get("meal_candidate_poi_ids", []),
        "meal_candidates": planning_context.get("meal_candidates", []),
        "district_summary": planning_context.get("district_summary", []),
        "route_matrix": planning_context.get("route_matrix", []),
    }


def _empty_skeleton(planning_context: dict) -> dict:
    return {
        "destination": planning_context.get("destination", ""),
        "days": [],
        "unscheduled": [],
        "risk_tags": ["没有可安排的已确认地点。"],
    }


def _is_plannable_for_context(poi: dict) -> bool:
    if poi.get("final_decision") in {"exclude", "unresolved"}:
        return False
    return _is_plannable_poi(poi)


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


def _is_must_keep_candidate(poi: dict, user_profile: dict) -> bool:
    if poi.get("user_override") == "must_include":
        return True
    name = _poi_name(poi)
    return any(raw_name and raw_name in name for raw_name in user_profile.get("constraints", {}).get("must_visit", []))


def _is_meal_candidate(poi: dict) -> bool:
    semantics = poi.get("planning_semantics") or {}
    experience_type = str(semantics.get("experience_type") or "").strip()
    if experience_type in {"full_meal", "snack"}:
        return True
    if experience_type in {"light_drink", "nightlife", "daytime_visit", "evening_view"}:
        return False
    text = " ".join(
        str(value)
        for value in [
            _poi_name(poi),
            poi.get("category"),
            poi.get("category_normalized"),
            *(poi.get("contexts") or []),
        ]
        if value
    )
    if any(token in text for token in ["咖啡", "奶茶", "茶饮", "果汁", "酒吧", "bar", "cocktail"]):
        return False
    return "餐" in text or "火锅" in text or poi.get("category") == "restaurant"


def _meal_suitability_hint(poi: dict) -> list[str]:
    experience_type = _experience_type(poi)
    if experience_type == "light_drink":
        return ["breakfast"]
    if experience_type == "nightlife":
        return ["dinner"]
    if experience_type == "snack":
        return ["breakfast", "lunch"]
    if experience_type == "full_meal":
        return ["lunch", "dinner"]
    texts = [
        _poi_name(poi),
        *(poi.get("contexts") or []),
        *(poi.get("experience_tags") or []),
        *(poi.get("ugc_tags") or []),
    ]
    text = " ".join(str(value) for value in texts if value)
    slots: list[str] = []
    if any(token in text for token in ["早餐", "早饭", "豆浆", "包子", "咖啡", "brunch"]):
        slots.append("breakfast")
    if any(token in text for token in ["午餐", "中午", "火锅", "小吃", "brunch"]):
        slots.append("lunch")
    if any(token in text for token in ["晚餐", "晚上", "夜宵", "火锅", "酒吧"]):
        slots.append("dinner")
    if not slots:
        slots.extend(["lunch", "dinner"])
    return list(dict.fromkeys(slots))


def _meal_route_fit_context(poi: dict) -> list[str]:
    texts = [
        *(poi.get("contexts") or []),
        *(poi.get("experience_tags") or []),
        *(poi.get("ugc_tags") or []),
    ]
    result: list[str] = []
    for text in texts:
        value = str(text or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _experience_type(poi: dict) -> str:
    semantics = poi.get("planning_semantics") or {}
    return str(semantics.get("experience_type") or poi.get("experience_type") or "daytime_visit")


def _preferred_time_windows(poi_id: str, poi: dict, planning_context: dict) -> list[str]:
    windows: list[str] = []
    for constraint in planning_context.get("time_constraints") or []:
        if str(constraint.get("poi_id") or "") == poi_id and constraint.get("preferred_window"):
            windows.append(str(constraint.get("preferred_window")))
    route_semantics = poi.get("route_semantics") or planning_context.get("route_semantics", {}).get(poi_id, {})
    for label in route_semantics.get("time_fit") or []:
        window = _normalized_time_window(str(label))
        if window:
            windows.append(window)
    for label in (poi.get("planning_semantics") or {}).get("time_suitability") or poi.get("best_time") or []:
        window = _normalized_time_window(str(label))
        if window:
            windows.append(window)
    return list(dict.fromkeys(windows))


def _normalized_time_window(value: str) -> str:
    text = value.strip().lower()
    mapping = {
        "上午": "morning",
        "morning": "morning",
        "中午": "midday",
        "midday": "midday",
        "下午": "afternoon",
        "afternoon": "afternoon",
        "傍晚": "evening",
        "晚上": "evening",
        "夜间": "night",
        "夜晚": "night",
        "evening": "evening",
        "night": "night",
        "全天": "all_day",
        "all_day": "all_day",
    }
    return mapping.get(text, "")


def _normalize_day_segments(
    raw_day: dict,
    allowed_poi_ids: set[str] | None = None,
    step: str = "plan_itinerary_skeleton",
) -> list[dict]:
    segments = []
    for raw_segment in raw_day.get("segments") or []:
        if not isinstance(raw_segment, dict):
            raise AppError("LLM 返回的 segments 格式不正确。", code="llm_invalid_plan_skeleton", step=step)
        kind = str(raw_segment.get("kind") or "").strip().lower()
        if kind == "outing":
            poi_ids = _string_list(raw_segment.get("poi_ids"))
            if not poi_ids:
                raise AppError("outing segment 缺少 poi_ids。", code="llm_invalid_plan_skeleton", step=step)
            if allowed_poi_ids is not None and any(poi_id not in allowed_poi_ids for poi_id in poi_ids):
                raise AppError("LLM 返回了候选集合之外的 segment poi_id。", code="llm_invalid_plan_skeleton", step=step)
            segments.append(
                {
                    "kind": "outing",
                    "segment_time": str(raw_segment.get("segment_time") or "").strip().lower(),
                    "poi_ids": poi_ids,
                }
            )
            continue
        if kind == "hotel_rest":
            segments.append(
                {
                    "kind": "hotel_rest",
                    "duration_min": _positive_int(raw_segment.get("duration_min")) or 0,
                    "reason": str(raw_segment.get("reason") or "").strip(),
                }
            )
            continue
        raise AppError("LLM 返回了不支持的 segment kind。", code="llm_invalid_plan_skeleton", step=step)
    return segments


def _normalize_meal_slots(raw_slots, allowed_poi_ids: set[str] | None = None, step: str = "plan_itinerary_skeleton") -> list[dict]:
    if not raw_slots:
        return []
    result: list[dict] = []
    for raw_slot in raw_slots:
        if not isinstance(raw_slot, dict):
            raise AppError("LLM 返回的 meal_slots 格式不正确。", code="llm_invalid_plan_skeleton", step=step)
        slot = str(raw_slot.get("slot") or "").strip().lower()
        requirement = str(raw_slot.get("requirement") or "required").strip().lower()
        source = str(raw_slot.get("source") or "").strip().lower()
        if slot not in {"breakfast", "lunch", "dinner"}:
            raise AppError("LLM 返回了不支持的 meal slot。", code="llm_invalid_plan_skeleton", step=step)
        if requirement not in {"required", "optional"}:
            raise AppError("LLM 返回了不支持的 meal slot requirement。", code="llm_invalid_plan_skeleton", step=step)
        if source not in {"poi", "inside_poi", "fallback_nearby"}:
            raise AppError("LLM 返回了不支持的 meal slot source。", code="llm_invalid_plan_skeleton", step=step)
        normalized = {"slot": slot, "requirement": requirement, "source": source}
        if source == "poi":
            poi_id = str(raw_slot.get("poi_id") or "").strip()
            if not poi_id:
                raise AppError("LLM 返回的餐饮 slot 缺少 poi_id。", code="llm_invalid_plan_skeleton", step=step)
            if allowed_poi_ids is not None and poi_id not in allowed_poi_ids:
                raise AppError("LLM 返回了候选集合之外的餐饮 poi_id。", code="llm_invalid_plan_skeleton", step=step)
            normalized["poi_id"] = poi_id
        if source == "inside_poi":
            within_poi_id = str(raw_slot.get("within_poi_id") or raw_slot.get("poi_id") or "").strip()
            if not within_poi_id:
                raise AppError("LLM 返回的场内用餐 slot 缺少 within_poi_id。", code="llm_invalid_plan_skeleton", step=step)
            if allowed_poi_ids is not None and within_poi_id not in allowed_poi_ids:
                raise AppError("LLM 返回了候选集合之外的场内用餐 poi_id。", code="llm_invalid_plan_skeleton", step=step)
            normalized["within_poi_id"] = within_poi_id
        result.append(normalized)
    return result


def _normalize_selected_branch_ids(raw_value, scheduled_poi_ids: list[str], branch_options_by_poi: dict[str, set[str]], step: str) -> dict[str, str]:
    if not raw_value:
        return {}
    if not isinstance(raw_value, dict):
        raise AppError("LLM 返回的 selected_branch_ids 格式不正确。", code="llm_invalid_plan_skeleton", step=step)
    scheduled = set(scheduled_poi_ids)
    normalized: dict[str, str] = {}
    for poi_id, branch_id in raw_value.items():
        normalized_poi_id = str(poi_id or "").strip()
        normalized_branch_id = str(branch_id or "").strip()
        if not normalized_poi_id or not normalized_branch_id:
            continue
        if normalized_poi_id not in scheduled:
            raise AppError("LLM 为未安排的 poi_id 返回了 selected_branch_id。", code="llm_invalid_plan_skeleton", step=step)
        valid_branch_ids = branch_options_by_poi.get(normalized_poi_id)
        if not valid_branch_ids or normalized_branch_id not in valid_branch_ids:
            raise AppError("LLM 返回了不合法的连锁门店 branch_id。", code="llm_invalid_plan_skeleton", step=step)
        normalized[normalized_poi_id] = normalized_branch_id
    return normalized


def _normalize_scheduled_roles(raw_value, scheduled_poi_ids: list[str], step: str) -> dict[str, str]:
    if not raw_value:
        return {}
    if not isinstance(raw_value, dict):
        raise AppError("LLM 返回的 scheduled_roles 格式不正确。", code="llm_invalid_plan_skeleton", step=step)
    allowed_roles = {"quick_stop", "meal_stop", "anchor_visit", "filler_visit", "nightlife_stop"}
    scheduled = set(scheduled_poi_ids)
    normalized: dict[str, str] = {}
    for poi_id, role in raw_value.items():
        normalized_poi_id = str(poi_id or "").strip()
        normalized_role = str(role or "").strip()
        if not normalized_poi_id or not normalized_role:
            continue
        if normalized_poi_id not in scheduled:
            raise AppError("LLM 为未安排的 poi_id 返回了 scheduled_role。", code="llm_invalid_plan_skeleton", step=step)
        if normalized_role not in allowed_roles:
            raise AppError("LLM 返回了不支持的 scheduled_role。", code="llm_invalid_plan_skeleton", step=step)
        normalized[normalized_poi_id] = normalized_role
    return normalized


def _resolve_materialized_poi(poi: dict, selected_branch_id: str | None) -> dict:
    resolved = dict(poi)
    branch_options = list(poi.get("route_branch_options") or poi.get("branch_options") or [])
    if not branch_options:
        if selected_branch_id:
            resolved["selected_branch_id"] = selected_branch_id
        return resolved
    selected = None
    if selected_branch_id:
        for option in branch_options:
            if str(option.get("branch_id") or "") == selected_branch_id:
                selected = option
                break
    if selected is None:
        selected = branch_options[0]
        selected_branch_id = str(selected.get("branch_id") or "") or None
    if not selected:
        return resolved
    resolved.update(
        {
            "selected_branch_id": selected_branch_id,
            "standard_name": selected.get("name") or resolved.get("standard_name") or resolved.get("name"),
            "address": selected.get("address", resolved.get("address", "")),
            "location": selected.get("location", resolved.get("location", {})),
            "city": selected.get("city", resolved.get("city", "")),
            "district": selected.get("district", resolved.get("district", "")),
            "category_raw": selected.get("category_raw", resolved.get("category_raw", "")),
            "category_normalized": selected.get("category_normalized", resolved.get("category_normalized", "")),
            "amap_id": selected_branch_id or resolved.get("amap_id", ""),
            "quick_stop_duration_min": selected.get("quick_stop_duration_min"),
            "meal_stop_duration_min": selected.get("meal_stop_duration_min"),
            "quick_stop_total_cost_min": selected.get("quick_stop_total_cost_min"),
            "meal_stop_total_cost_min": selected.get("meal_stop_total_cost_min"),
        }
    )
    return resolved


def _resolved_scheduled_role(poi_id: str, poi: dict, scheduled_roles: dict, meal_slots: list[dict]) -> str:
    explicit = str((scheduled_roles or {}).get(poi_id) or "").strip()
    if explicit:
        return explicit
    if any(slot.get("source") == "poi" and slot.get("poi_id") == poi_id for slot in meal_slots):
        return "meal_stop"
    if any(slot.get("source") == "inside_poi" and slot.get("within_poi_id") == poi_id for slot in meal_slots):
        return "meal_stop"
    experience_type = _experience_type(poi)
    if experience_type == "nightlife":
        return "nightlife_stop"
    if _quick_stop_eligible(poi):
        return "quick_stop"
    if (poi.get("planning_semantics") or {}).get("outing_role") == "filler":
        return "filler_visit"
    return "anchor_visit"


def _quick_stop_eligible(poi: dict) -> bool:
    if _experience_type(poi) not in {"light_drink", "snack"}:
        return False
    total_cost = _positive_int(poi.get("quick_stop_total_cost_min"))
    if total_cost is None and poi.get("route_branch_options"):
        total_cost = _positive_int((poi.get("route_branch_options") or [{}])[0].get("quick_stop_total_cost_min"))
    if total_cost is not None:
        return total_cost <= 45
    semantics = poi.get("planning_semantics") or {}
    return bool(semantics.get("quick_stop_eligible"))


def _materialized_duration_min(poi: dict, scheduled_role: str) -> int:
    estimated = _positive_int(poi.get("estimated_duration_min")) or 60
    profiles = dict((poi.get("planning_semantics") or {}).get("base_duration_profiles") or {})
    if scheduled_role == "quick_stop":
        return _positive_int(poi.get("quick_stop_duration_min")) or _positive_int(profiles.get("quick_stop")) or 15
    if scheduled_role == "meal_stop":
        return _positive_int(poi.get("meal_stop_duration_min")) or _positive_int(profiles.get("meal_stop")) or 60
    return estimated


def _burden_role(poi: dict, scheduled_role: str) -> str:
    if scheduled_role == "meal_stop":
        return "protected_basic"
    if scheduled_role == "quick_stop":
        return "light_detour"
    duration = _positive_int(poi.get("estimated_duration_min")) or 60
    if duration >= 240:
        return "heavy_load"
    return "normal_load"


def _trim_priority(poi: dict, scheduled_role: str) -> str:
    if scheduled_role == "meal_stop":
        return "never_trim_before_meal"
    if scheduled_role == "quick_stop":
        return "keep_if_low_detour"
    if poi.get("user_override") == "must_include":
        return "must_keep"
    return "trim_first"


def _selected_visit_duration_min(poi: dict, user_profile: dict) -> int:
    profile = _visit_duration_profile(poi)
    if not profile:
        return _positive_int(poi.get("estimated_duration_min")) or 60
    intensity = str((user_profile.get("constraints") or {}).get("physical_intensity") or "").strip()
    if intensity == "high":
        return _positive_int(profile.get("intense_min")) or _positive_int(poi.get("estimated_duration_min")) or 60
    return _positive_int(profile.get("relaxed_min")) or _positive_int(profile.get("intense_min")) or _positive_int(poi.get("estimated_duration_min")) or 60


def _visit_duration_profile(poi: dict) -> dict:
    profile = poi.get("visit_duration_profile")
    if not isinstance(profile, dict):
        estimated = _positive_int(poi.get("estimated_duration_min")) or 60
        return {"relaxed_min": estimated, "intense_min": estimated}
    relaxed_min = _positive_int(profile.get("relaxed_min"))
    intense_min = _positive_int(profile.get("intense_min"))
    if intense_min is None and relaxed_min is None:
        estimated = _positive_int(poi.get("estimated_duration_min")) or 60
        return {"relaxed_min": estimated, "intense_min": estimated}
    if intense_min is None:
        intense_min = relaxed_min
    if relaxed_min is None:
        relaxed_min = intense_min
    relaxed_min = min(relaxed_min, intense_min)
    return {
        "relaxed_min": relaxed_min,
        "intense_min": intense_min,
        "confidence": profile.get("confidence"),
        "reason": profile.get("reason"),
    }


def _poi_name(poi: dict) -> str:
    return str(poi.get("standard_name") or poi.get("raw_name") or poi.get("name") or poi.get("poi_id") or "未命名地点")


def _string_list(value) -> list[str]:
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
