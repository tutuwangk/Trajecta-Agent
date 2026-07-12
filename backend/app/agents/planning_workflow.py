from __future__ import annotations

from copy import deepcopy
from typing import Callable

from app.agents.itinerary_normalizer import normalize_itinerary
from app.agents.planner import (
    build_planning_packet,
    build_copy_context,
    compile_planning_context,
    _deterministic_blueprint,
    materialize_itinerary_from_skeleton,
    plan_day_blueprint_with_llm,
    replan_day_blueprint_with_llm,
)
from app.agents.planning_preferences import build_planning_preferences
from app.agents.reviser import generate_copy
from app.agents.schedule_evaluator import (
    build_replan_feedback,
    evaluate_schedule_candidate,
    quality_deviation_messages,
)
from app.agents.verifier import review_preference_conflicts, review_soft_quality, validate_hard_constraints, verify_itinerary
from app.core import AppError


PrepareItinerary = Callable[[dict], None]


def run_planning_workflow(
    user_profile: dict,
    runtime_pois: list[dict],
    route_matrix: list[dict],
    planning_llm,
    copy_llm,
    uncertain_pois: list[dict] | None = None,
    hotel_anchor: dict | None = None,
    order_constraints: list[dict] | None = None,
    time_constraints: list[dict] | None = None,
    planning_decisions: list[dict] | None = None,
    prepare_itinerary: PrepareItinerary | None = None,
    max_replans: int = 2,
    preflight_llm_clients: list | None = None,
) -> tuple[dict, dict, dict]:
    planning_context = compile_planning_context(
        user_profile,
        runtime_pois,
        route_matrix,
        uncertain_pois=uncertain_pois,
        hotel_anchor=hotel_anchor,
        order_constraints=order_constraints,
        time_constraints=time_constraints,
        planning_decisions=planning_decisions,
    )
    planning_preferences = dict(planning_context.get("planning_preferences") or build_planning_preferences(planning_decisions))
    planning_context["runtime_pois"] = runtime_pois
    planning_context["planning_preferences"] = planning_preferences
    skeleton_versions: list[dict] = []
    factual_issue_history: list[dict] = []
    preference_issue_history: list[list[dict]] = []
    soft_issue_history: list[list[dict]] = []

    skeleton = plan_day_blueprint_with_llm(planning_context, planning_llm)
    final_itinerary: dict | None = None
    final_hard_validation = {"passed": True, "issues": []}
    final_soft_issues: list[dict] = []
    auto_fallback_used = _is_fallback_blueprint(skeleton)

    candidates: list[dict] = []
    for attempt in range(max_replans + 1):
        skeleton_versions.append(deepcopy(skeleton))
        itinerary = materialize_itinerary_from_skeleton(planning_context, skeleton)
        _prepare_itinerary(itinerary, user_profile, runtime_pois, route_matrix, prepare_itinerary)
        hard_validation = validate_hard_constraints(
            itinerary,
            user_profile,
            route_matrix,
            runtime_pois,
            time_constraints=planning_context.get("time_constraints", []),
            order_constraints=planning_context.get("order_constraints", []),
        )
        if not hard_validation["passed"]:
            factual_issue_history.append(deepcopy(hard_validation))
            if attempt >= max_replans:
                break
            feedback = build_replan_feedback(
                evaluate_schedule_candidate(itinerary, hard_validation.get("issues", []), [], attempt=attempt),
                candidates[-1] if candidates else None,
            )
            skeleton = replan_day_blueprint_with_llm(planning_context, skeleton, feedback, planning_llm)
            auto_fallback_used = auto_fallback_used or _is_fallback_blueprint(skeleton)
            continue

        preference_issues = review_preference_conflicts(
            itinerary,
            user_profile,
            route_matrix,
            runtime_pois,
            time_constraints=planning_context.get("time_constraints", []),
            order_constraints=planning_context.get("order_constraints", []),
            planning_preferences=planning_preferences,
        )
        if preference_issues:
            preference_issue_history.append(deepcopy(preference_issues))
        candidate = evaluate_schedule_candidate(
            itinerary,
            hard_validation.get("issues", []),
            preference_issues,
            attempt=attempt,
        )
        candidate["itinerary"] = itinerary
        candidate["skeleton"] = deepcopy(skeleton)
        candidates.append(candidate)
        if not preference_issues:
            final_hard_validation = hard_validation
            break
        if attempt >= max_replans:
            break
        feedback = build_replan_feedback(candidate, candidates[-2] if len(candidates) > 1 else None)
        skeleton = replan_day_blueprint_with_llm(planning_context, skeleton, feedback, planning_llm)
        auto_fallback_used = auto_fallback_used or _is_fallback_blueprint(skeleton)

    if not candidates:
        fallback_skeleton = _deterministic_blueprint(planning_context)
        auto_fallback_used = True
        skeleton_versions.append(deepcopy(fallback_skeleton))
        fallback_itinerary = materialize_itinerary_from_skeleton(planning_context, fallback_skeleton)
        _prepare_itinerary(fallback_itinerary, user_profile, runtime_pois, route_matrix, prepare_itinerary)
        fallback_hard = validate_hard_constraints(
            fallback_itinerary,
            user_profile,
            route_matrix,
            runtime_pois,
            time_constraints=planning_context.get("time_constraints", []),
            order_constraints=planning_context.get("order_constraints", []),
        )
        if not fallback_hard["passed"]:
            raise AppError(
                "路线规划失败，地点或路线事实无法生成安全结果。",
                code="plan_factual_constraints_unresolved",
                step="plan",
                details={"blockers": _issue_blockers(fallback_hard.get("issues", []))},
            )
        fallback_preferences = review_preference_conflicts(
            fallback_itinerary,
            user_profile,
            route_matrix,
            runtime_pois,
            time_constraints=planning_context.get("time_constraints", []),
            order_constraints=planning_context.get("order_constraints", []),
            planning_preferences=planning_preferences,
        )
        fallback_candidate = evaluate_schedule_candidate(
            fallback_itinerary, [], fallback_preferences, attempt=len(skeleton_versions) - 1
        )
        fallback_candidate.update({"itinerary": fallback_itinerary, "skeleton": fallback_skeleton})
        candidates.append(fallback_candidate)

    if candidates and all(
        any(issue.get("type") == "must_visit_missing" for issue in candidate.get("quality_issues") or [])
        for candidate in candidates
    ):
        fallback_skeleton = _deterministic_blueprint(planning_context)
        fallback_itinerary = materialize_itinerary_from_skeleton(planning_context, fallback_skeleton)
        _prepare_itinerary(fallback_itinerary, user_profile, runtime_pois, route_matrix, prepare_itinerary)
        fallback_hard = validate_hard_constraints(
            fallback_itinerary, user_profile, route_matrix, runtime_pois,
            time_constraints=planning_context.get("time_constraints", []),
            order_constraints=planning_context.get("order_constraints", []),
        )
        if fallback_hard["passed"]:
            fallback_preferences = review_preference_conflicts(
                fallback_itinerary, user_profile, route_matrix, runtime_pois,
                time_constraints=planning_context.get("time_constraints", []),
                order_constraints=planning_context.get("order_constraints", []),
                planning_preferences=planning_preferences,
            )
            fallback_candidate = evaluate_schedule_candidate(
                fallback_itinerary, [], fallback_preferences, attempt="fallback"
            )
            fallback_candidate.update({"itinerary": fallback_itinerary, "skeleton": fallback_skeleton})
            candidates.append(fallback_candidate)
            auto_fallback_used = True

    best_candidate = max((candidate for candidate in candidates if candidate["publishable"]), key=lambda item: item["score"])
    final_itinerary = best_candidate["itinerary"]
    final_hard_validation = {"passed": True, "issues": []}

    final_soft_issues = list(best_candidate.get("quality_issues") or [])
    reviewed_soft_issues = review_soft_quality(
            final_itinerary,
            user_profile,
            route_matrix,
            runtime_pois,
            time_constraints=planning_context.get("time_constraints", []),
            order_constraints=planning_context.get("order_constraints", []),
            llm_client=copy_llm,
        )
    final_soft_issues.extend(issue for issue in reviewed_soft_issues if issue not in final_soft_issues)
    copy_context = build_copy_context(planning_context, final_itinerary, final_hard_validation, final_soft_issues)
    final = generate_copy(final_itinerary, copy_context, user_profile, copy_llm)
    deviation_messages = quality_deviation_messages(best_candidate.get("quality_issues") or [])
    if deviation_messages:
        risks = list(final.get("global_risks") or [])
        for message in deviation_messages:
            if message not in risks:
                risks.append(message)
        final["global_risks"] = risks
    verification = verify_itinerary(
        final,
        user_profile,
        route_matrix,
        runtime_pois,
        time_constraints=planning_context.get("time_constraints", []),
        order_constraints=planning_context.get("order_constraints", []),
    )
    verification["quality_deviations"] = deviation_messages
    verification["candidate_score"] = best_candidate.get("score")
    return final, verification, {
        "planning_context_snapshot": _context_snapshot(planning_context),
        "planning_packet_meta": build_planning_packet(planning_context).get("meta", {}),
        "skeleton_versions": skeleton_versions,
        "hard_issue_history": factual_issue_history,
        "preference_issue_history": preference_issue_history,
        "soft_issue_history": soft_issue_history,
        "candidate_scores": [
            {
                "attempt": candidate.get("attempt"),
                "score": candidate.get("score"),
                "publishable": candidate.get("publishable"),
                "issue_types": [issue.get("type") for issue in candidate.get("quality_issues") or []],
                "analysis": candidate.get("analysis"),
                "selected": candidate is best_candidate,
            }
            for candidate in candidates
        ],
        "repair_attempts": len(factual_issue_history),
        "auto_fallback_used": auto_fallback_used,
        "llm_metrics": _collect_llm_metrics(*(list(preflight_llm_clients or []) + [planning_llm, copy_llm])),
    }


def _prepare_itinerary(
    itinerary: dict,
    user_profile: dict,
    runtime_pois: list[dict],
    route_matrix: list[dict],
    custom_prepare: PrepareItinerary | None,
) -> None:
    if custom_prepare is not None:
        custom_prepare(itinerary)
        return
    _sync_transport_edges(itinerary, route_matrix)
    normalize_itinerary(itinerary, user_profile, runtime_pois, route_matrix)


def _sync_transport_edges(itinerary: dict, route_matrix: list[dict]) -> None:
    route_by_pair = {(edge.get("origin_poi_id"), edge.get("destination_poi_id")): edge for edge in route_matrix}
    for day in itinerary.get("days", []):
        items = day.get("items", [])
        for index, item in enumerate(items):
            if index >= len(items) - 1:
                item.pop("transport_to_next", None)
                continue
            edge = route_by_pair.get((item.get("poi_id"), items[index + 1].get("poi_id")))
            if not edge:
                item.pop("transport_to_next", None)
                continue
            item["transport_to_next"] = {
                "mode": edge.get("mode", "unknown"),
                "duration_min": edge.get("duration_min"),
                "distance_m": edge.get("distance_m"),
            }


def _context_snapshot(planning_context: dict) -> dict:
    return {
        "destination": planning_context.get("destination", ""),
        "days": planning_context.get("days", 1),
        "day_budget_min": planning_context.get("day_budget_min"),
        "must_poi_ids": list(planning_context.get("must_poi_ids", [])),
        "preferred_poi_ids": list(planning_context.get("preferred_poi_ids", [])),
        "optional_poi_ids": list(planning_context.get("optional_poi_ids", [])),
        "time_constraints": list(planning_context.get("time_constraints", [])),
        "planning_decisions": list(planning_context.get("planning_decisions", [])),
    }


def _runtime_with_route_semantics(runtime_pois: list[dict], planning_context: dict) -> list[dict]:
    semantics_by_id = planning_context.get("route_semantics", {})
    if not semantics_by_id:
        return runtime_pois
    enriched: list[dict] = []
    for poi in runtime_pois:
        item = dict(poi)
        poi_id = item.get("poi_id")
        if poi_id in semantics_by_id:
            item["route_semantics"] = semantics_by_id[poi_id]
        enriched.append(item)
    return enriched


def _issue_blockers(issues: list[dict]) -> list[dict]:
    blockers = []
    for issue in issues:
        blockers.append(
            {
                "type": issue.get("type"),
                "message": issue.get("message"),
                "action_hint": issue.get("suggestion"),
                "affected_day": issue.get("day"),
                "affected_poi_name": issue.get("poi_name") or issue.get("name"),
            }
        )
    return blockers


def _collect_llm_metrics(*clients) -> dict:
    calls = []
    for client in clients:
        for metric in getattr(client, "call_metrics", []) or []:
            if isinstance(metric, dict):
                calls.append(dict(metric))
    return {
        "call_count": len(calls),
        "error_count": sum(1 for call in calls if call.get("status") == "error"),
        "prompt_tokens": sum(int(call.get("prompt_tokens") or 0) for call in calls),
        "completion_tokens": sum(int(call.get("completion_tokens") or 0) for call in calls),
        "reasoning_tokens": sum(int(call.get("reasoning_tokens") or 0) for call in calls),
        "duration_ms": sum(int(call.get("duration_ms") or 0) for call in calls),
        "calls": calls,
    }


def _is_fallback_blueprint(skeleton: dict) -> bool:
    return any("稳定规划方式" in str(item) for item in skeleton.get("risk_tags") or [])


def _build_planning_intervention(issues: list[dict], planning_context: dict) -> dict:
    issue_types = {issue.get("type") for issue in issues}
    domains = [str(issue.get("domain") or "") for issue in issues]
    primary_domain = _intervention_domain(domains)
    if primary_domain in {"must_places", "time_preferences"}:
        question = "有些必去或指定时段的安排互相挤压，你想优先保留哪种安排？"
        options = [
            {"id": "keep_must_places", "label": "优先保留必去地点", "description": "路线可能更满，待定地点会减少。"},
            {"id": "keep_time_preferences", "label": "优先保留指定时段", "description": "不适合该时段的地点会进入备选。"},
            {"id": "relax_pace", "label": "放宽节奏", "description": "当天可能更接近特种兵。"},
        ]
    elif primary_domain == "order_preferences":
        question = "当前顺序偏好和其他安排有冲突，你想优先保留哪种安排？"
        options = [
            {"id": "keep_order_preferences", "label": "优先保留先后顺序", "description": "其他地点可能后移、改天或进入备选。"},
            {"id": "keep_must_places", "label": "优先保留核心地点", "description": "必要时放宽这条先后顺序。"},
            {"id": "relax_pace", "label": "接受更满一点", "description": "通过更紧凑的节奏尽量兼顾顺序。"},
        ]
    elif primary_domain == "meal_arrangement" or "meal_slot_missing" in issue_types:
        question = "当天缺少合适的正餐安排，你想怎么处理？"
        options = [
            {"id": "use_nearby_meal", "label": "就近用餐", "description": "不强行把轻食当正餐。"},
            {"id": "drop_optional_for_meal", "label": "减少待定地点", "description": "优先腾出正常午餐或晚餐时间。"},
        ]
    elif primary_domain == "pace":
        question = "当前路线节奏偏满，你想优先保留哪种安排？"
        options = [
            {"id": "relax_pace", "label": "接受更满一点", "description": "尽量保住当前地点，只放宽节奏要求。"},
            {"id": "keep_must_places", "label": "只保留核心地点", "description": "允许删减待定地点来压缩路线。"},
        ]
    else:
        question = "当前路线存在需要取舍的问题，你想优先保留哪种安排？"
        options = [
            {"id": "keep_must_places", "label": "优先保留必去地点", "description": "压缩或放弃待定地点。"},
            {"id": "relax_pace", "label": "放宽节奏", "description": "当天可能更满。"},
        ]
    return {
        "status": "needs_user_choice",
        "domain": primary_domain,
        "question": question,
        "options": options,
        "issues": issues,
        "display_issues": _display_issues(issues),
        "context_summary": {
            "destination": planning_context.get("destination", ""),
            "days": planning_context.get("days", 1),
        },
    }


def _intervention_domain(domains: list[str]) -> str:
    for domain in domains:
        if domain:
            return domain
    return "planning_preference"


def _display_issues(issues: list[dict]) -> list[dict]:
    display_items: list[dict] = []
    for issue in issues[:3]:
        display_items.append(
            {
                "type": issue.get("type"),
                "domain": issue.get("domain"),
                "message": issue.get("message"),
                "suggestion": issue.get("suggestion"),
                "evidence": issue.get("evidence"),
                "day": issue.get("day"),
                "poi_name": issue.get("poi_name"),
            }
        )
    return display_items
