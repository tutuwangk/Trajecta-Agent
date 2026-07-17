from __future__ import annotations

import json
from typing import Any

from app.domain.facts import RouteEdge
from app.domain.planning import PlannerTurn, PlanningContext


PLANNER_INSTRUCTIONS = """
你是本产品唯一具有路线取舍职责的 PlannerAgent。你要主动判断信息是否足以做决定，并在有限回合内完成可执行路线蓝图。

你的决策权：选择地点、分天、排序、餐饮承接、时段策略、舍弃可选地点，以及在冲突偏好之间做整体取舍。
系统的权责：地点身份、坐标、营业事实、交通时间、分钟级时间线、事实置信度和发布状态。你不得发明或修改这些事实。

决策优先级：
1. 已知固定预约、明确关闭等不可违背事实；
2. 用户必去体验和明确要求；
3. 日期可行性、路线连贯性和完整游玩时间；
4. 强度、餐饮、区域聚合等体验目标。

当营业时间或停止入场时间会实质改变选点、分天或排序，且当前事实缺失时，可主动输出 need_facts。一次只请求真正影响决策的少量事实；事实请求额度耗尽后必须基于已有证据提出蓝图，不得继续请求。
收到 validation_report 时，综合比较可行策略并修正整体方案，不要逐条机械打补丁。允许放弃可选地点，也允许在无法同时满足偏好时选择更合理的方案并记录风险。
""".strip()


def build_planner_prompt(context: PlanningContext, *, allow_fact_requests: bool) -> str:
    hotel = context.fact_snapshot.hotel_anchor
    payload = {
        "trip": {
            "destination": context.user_profile.destination,
            "days": context.user_profile.days,
            "start_date": context.user_profile.start_date.isoformat() if context.user_profile.start_date else None,
            "trip_dates": [item.isoformat() for item in context.trip_dates],
            "route_goal": context.user_profile.route_goal,
            "day_budget_min": context.day_budget_min,
            "physical_intensity": context.user_profile.constraints.physical_intensity,
        },
        "user_request": context.user_request[:6000],
        "constraints": context.user_profile.constraints.model_dump(mode="json"),
        "transport_preference": context.user_profile.transport_preference,
        "candidates": [_compact_candidate(candidate) for candidate in context.candidates],
        "order_constraints": [item.model_dump(mode="json") for item in context.order_constraints],
        "time_constraints": [item.model_dump(mode="json") for item in context.time_constraints],
        "planning_preferences": context.planning_preferences.model_dump(mode="json"),
        "fact_version": context.fact_snapshot.version,
        "fact_gaps": [gap.model_dump(mode="json") for gap in context.fact_snapshot.gaps],
        "availability_facts": [fact.model_dump(mode="json") for fact in context.fact_snapshot.availability_facts],
        "hotel_anchor": hotel.model_dump(mode="json") if hotel else None,
        "route_facts_summary": compact_route_facts(context),
        "previous_blueprint": context.previous_blueprint.model_dump(mode="json") if context.previous_blueprint else None,
        "validation_report": context.validation_report.model_dump(mode="json") if context.validation_report else None,
        "recent_history": context.history[-4:],
        "fact_request_budget_available": allow_fact_requests and bool(context.trip_dates),
    }
    if context.previous_blueprint:
        task = "综合校验反馈修正旧蓝图，输出 revise_blueprint。"
    elif allow_fact_requests and context.trip_dates:
        task = "判断是否需要补充关键营业事实；需要时输出 need_facts，否则直接输出 propose_blueprint。"
    else:
        task = "事实请求额度不可用或已耗尽，必须输出 propose_blueprint。"
    schema = json.dumps(PlannerTurn.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    return (
        f"{task}\n\n"
        "只返回一个符合 output_schema 的 JSON object；不要输出解释、Markdown 或代码围栏。\n\n"
        f"<planning_context>\n{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n</planning_context>\n\n"
        f"<output_schema>\n{schema}\n</output_schema>"
    )


def _compact_candidate(candidate) -> dict[str, Any]:
    return {
        "poi_id": candidate.poi_id,
        "name": candidate.name,
        "district": candidate.district,
        "category": candidate.category,
        "priority": candidate.priority,
        "experience_type": candidate.experience_type,
        "duration_min": candidate.duration_min,
        "meal_capability": candidate.meal_capability,
        "time_windows": candidate.time_windows[:4],
        "planning_function": candidate.planning_function,
        "planning_notes": candidate.planning_notes[:240],
        "quick_stop_eligible": candidate.quick_stop_eligible,
    }


def compact_route_facts(context: PlanningContext, max_neighbors: int = 4) -> dict[str, Any]:
    allowed = context.allowed_poi_ids
    by_origin: dict[str, list[RouteEdge]] = {}
    for edge in context.fact_snapshot.route_edges:
        if edge.origin_poi_id in allowed and edge.destination_poi_id in allowed:
            by_origin.setdefault(edge.origin_poi_id, []).append(edge)
    neighbors: dict[str, list[dict[str, Any]]] = {}
    far_pairs: list[list[str]] = []
    for origin in sorted(by_origin):
        ranked = sorted(
            by_origin[origin],
            key=lambda edge: (
                edge.duration_min is None,
                edge.duration_min if edge.duration_min is not None else 10**9,
                edge.destination_poi_id,
            ),
        )
        neighbors[origin] = [
            {
                "poi_id": edge.destination_poi_id,
                "duration_min": edge.duration_min,
                "distance_m": edge.distance_m,
                "relation": edge.relation,
                "confidence": edge.confidence,
                "source": edge.source,
            }
            for edge in ranked[:max_neighbors]
        ]
        far_pairs.extend([origin, edge.destination_poi_id] for edge in ranked if edge.relation == "separate_day")
    districts: dict[str, list[str]] = {}
    for candidate in context.candidates:
        districts.setdefault(candidate.district or "未分区", []).append(candidate.poi_id)
    return {
        "district_clusters": [
            {"district": district, "poi_ids": poi_ids}
            for district, poi_ids in sorted(districts.items())
        ],
        "neighbors": neighbors,
        "far_pairs": far_pairs[: max(8, len(context.candidates))],
    }
