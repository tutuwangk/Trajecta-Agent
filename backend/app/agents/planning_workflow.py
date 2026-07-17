from __future__ import annotations

from copy import deepcopy
from typing import Callable

from app.adapters.legacy import plan_blueprint_from_legacy, planning_context_from_legacy, validation_report_from_legacy
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
from app.planning.feasibility_compiler import FeasibilityCompiler
from app.planning.poi_fact_resolver import apply_fact_resolution
from app.planning.segment_boundaries import hotel_rest_boundary_pairs


PrepareItinerary = Callable[[dict], None]
WorkflowPhaseHook = Callable[[str, dict], None]
ReleaseCandidate = Callable[[dict, dict], dict]


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
    max_replans: int = 1,
    preflight_llm_clients: list | None = None,
    on_phase: WorkflowPhaseHook | None = None,
    release_candidate: ReleaseCandidate | None = None,
    fact_resolver=None,
    user_request: str = "",
    initial_blueprint: dict | None = None,
) -> tuple[dict, dict, dict]:
    planning_context, typed_context = _build_context(
        user_profile=user_profile,
        runtime_pois=runtime_pois,
        route_matrix=route_matrix,
        uncertain_pois=uncertain_pois,
        hotel_anchor=hotel_anchor,
        order_constraints=order_constraints,
        time_constraints=time_constraints,
        planning_decisions=planning_decisions,
        user_request=user_request,
        typed=hasattr(planning_llm, "run") or hasattr(planning_llm, "run_blueprint"),
    )
    planning_preferences = dict(planning_context["planning_preferences"])
    compiler = FeasibilityCompiler() if typed_context is not None else None
    skeleton_versions: list[dict] = []
    factual_issue_history: list[dict] = []
    preference_issue_history: list[list[dict]] = []
    soft_issue_history: list[list[dict]] = []

    fact_request_history: list[dict] = []
    if initial_blueprint:
        skeleton = plan_blueprint_from_legacy(initial_blueprint).model_dump(mode="json")
    elif typed_context is not None and hasattr(planning_llm, "run_turn"):
        try:
            turn = planning_llm.run_turn(typed_context, allow_fact_requests=True)
            if turn.action == "need_facts":
                fact_request_history = [item.model_dump(mode="json") for item in turn.fact_requests]
                if fact_resolver is not None:
                    batch = fact_resolver.resolve(turn.fact_requests, typed_context)
                    apply_fact_resolution(runtime_pois, batch)
                    planning_context, typed_context = _build_context(
                        user_profile=user_profile,
                        runtime_pois=runtime_pois,
                        route_matrix=route_matrix,
                        uncertain_pois=uncertain_pois,
                        hotel_anchor=hotel_anchor,
                        order_constraints=order_constraints,
                        time_constraints=time_constraints,
                        planning_decisions=planning_decisions,
                        user_request=user_request,
                        typed=True,
                    )
                    planning_preferences = dict(planning_context["planning_preferences"])
                    _emit_phase(
                        on_phase,
                        "fact_snapshot",
                        {"fact_snapshot": typed_context.fact_snapshot.model_dump(mode="json")},
                    )
                turn = planning_llm.run_turn(typed_context, allow_fact_requests=False)
            if turn.blueprint is None:
                raise AppError(
                    "PlannerAgent 未在事实回合后提交路线蓝图。",
                    code="planner_agent_fact_loop_exhausted",
                    step="plan_itinerary_blueprint",
                )
            skeleton = turn.blueprint.model_dump(mode="json")
        except AppError:
            skeleton = _deterministic_blueprint(planning_context)
    else:
        skeleton = _plan_blueprint(planning_context, typed_context, planning_llm)
    _emit_phase(on_phase, "blueprint", {"blueprint": skeleton})
    final_itinerary: dict | None = None
    final_hard_validation = {"passed": True, "issues": []}
    final_soft_issues: list[dict] = []
    auto_fallback_used = _is_fallback_blueprint(skeleton)

    candidates: list[dict] = []
    for attempt in range(max_replans + 1):
        skeleton_versions.append(deepcopy(skeleton))
        _emit_phase(on_phase, "compiling", {"blueprint": skeleton})
        itinerary, hard_validation = _compile_candidate(
            planning_context=planning_context,
            typed_context=typed_context,
            compiler=compiler,
            skeleton=skeleton,
            user_profile=user_profile,
            runtime_pois=runtime_pois,
            route_matrix=route_matrix,
            prepare_itinerary=prepare_itinerary,
            repair_attempt=attempt,
        )
        if not hard_validation["passed"]:
            factual_issue_history.append(deepcopy(hard_validation))
            if attempt >= max_replans:
                break
            meal_repair = _repair_meal_only_blueprint(skeleton, itinerary, hard_validation.get("issues") or [])
            if meal_repair is not None:
                skeleton = meal_repair
                continue
            _emit_phase(on_phase, "repairing", {"blueprint": skeleton, "validation_report": hard_validation})
            feedback = build_replan_feedback(
                evaluate_schedule_candidate(itinerary, hard_validation.get("issues", []), [], attempt=attempt),
                candidates[-1] if candidates else None,
            )
            skeleton = _replan_blueprint(planning_context, typed_context, skeleton, feedback, planning_llm)
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
            intent_ledger=planning_context.get("intent_ledger"),
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
        _emit_phase(
            on_phase,
            "repairing",
            {"blueprint": skeleton, "validation_report": {"passed": False, "issues": preference_issues}},
        )
        feedback = build_replan_feedback(candidate, candidates[-2] if len(candidates) > 1 else None)
        skeleton = _replan_blueprint(planning_context, typed_context, skeleton, feedback, planning_llm)
        auto_fallback_used = auto_fallback_used or _is_fallback_blueprint(skeleton)

    if not candidates:
        fallback_skeleton = _deterministic_blueprint(planning_context)
        auto_fallback_used = True
        skeleton_versions.append(deepcopy(fallback_skeleton))
        _emit_phase(on_phase, "fallback", {"blueprint": fallback_skeleton})
        fallback_itinerary, fallback_hard = _compile_candidate(
            planning_context=planning_context,
            typed_context=typed_context,
            compiler=compiler,
            skeleton=fallback_skeleton,
            user_profile=user_profile,
            runtime_pois=runtime_pois,
            route_matrix=route_matrix,
            prepare_itinerary=prepare_itinerary,
            repair_attempt=max_replans + 1,
        )
        if not fallback_hard["passed"]:
            fallback_issues = list(fallback_hard.get("issues") or [])
            error_code, error_message = _unresolved_plan_error(fallback_issues)
            raise AppError(
                error_message,
                code=error_code,
                step="plan",
                details={
                    "blockers": _issue_blockers(fallback_issues),
                    "attempt_count": len(skeleton_versions),
                    "repair_attempts": len(factual_issue_history),
                    "hard_issue_history": factual_issue_history,
                    "llm_metrics": _collect_llm_metrics(*(list(preflight_llm_clients or []) + [planning_llm, copy_llm])),
                },
            )
        fallback_preferences = review_preference_conflicts(
            fallback_itinerary,
            user_profile,
            route_matrix,
            runtime_pois,
            time_constraints=planning_context.get("time_constraints", []),
            order_constraints=planning_context.get("order_constraints", []),
            planning_preferences=planning_preferences,
            intent_ledger=planning_context.get("intent_ledger"),
        )
        fallback_candidate = evaluate_schedule_candidate(
            fallback_itinerary, [], fallback_preferences, attempt=len(skeleton_versions) - 1
        )
        fallback_candidate.update({"itinerary": fallback_itinerary, "skeleton": fallback_skeleton})
        candidates.append(fallback_candidate)

    fallback_trigger_types = {
        "must_visit_missing",
        "empty_day_with_available_places",
        "day_assignment_violated",
    }
    if candidates and all(
        any(issue.get("type") in fallback_trigger_types for issue in candidate.get("quality_issues") or [])
        for candidate in candidates
    ):
        fallback_skeleton = _deterministic_blueprint(planning_context)
        _emit_phase(on_phase, "fallback", {"blueprint": fallback_skeleton})
        fallback_itinerary, fallback_hard = _compile_candidate(
            planning_context=planning_context,
            typed_context=typed_context,
            compiler=compiler,
            skeleton=fallback_skeleton,
            user_profile=user_profile,
            runtime_pois=runtime_pois,
            route_matrix=route_matrix,
            prepare_itinerary=prepare_itinerary,
            repair_attempt=max_replans + 1,
        )
        if fallback_hard["passed"]:
            fallback_preferences = review_preference_conflicts(
                fallback_itinerary, user_profile, route_matrix, runtime_pois,
                time_constraints=planning_context.get("time_constraints", []),
                order_constraints=planning_context.get("order_constraints", []),
                planning_preferences=planning_preferences,
                intent_ledger=planning_context.get("intent_ledger"),
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
        intent_ledger=planning_context.get("intent_ledger"),
    )
    final_soft_issues.extend(issue for issue in reviewed_soft_issues if issue not in final_soft_issues)
    pre_copy_verification = verify_itinerary(
        final_itinerary,
        user_profile,
        route_matrix,
        runtime_pois,
        time_constraints=planning_context.get("time_constraints", []),
        order_constraints=planning_context.get("order_constraints", []),
        intent_ledger=planning_context.get("intent_ledger"),
    )
    if release_candidate is not None:
        pre_copy_verification = release_candidate(final_itinerary, pre_copy_verification)
    _emit_phase(
        on_phase,
        "release_gate",
        {"blueprint": best_candidate["skeleton"], "validation_report": pre_copy_verification},
    )
    copy_context = build_copy_context(planning_context, final_itinerary, final_hard_validation, final_soft_issues)
    _emit_phase(on_phase, "copywriting", {"blueprint": best_candidate["skeleton"]})
    final = generate_copy(final_itinerary, copy_context, user_profile, copy_llm)
    deviation_messages = quality_deviation_messages(final_soft_issues)
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
        intent_ledger=planning_context.get("intent_ledger"),
    )
    for key in (
        "result_status",
        "fact_status",
        "experience_status",
        "experience_reasons",
        "release_decision",
        "degradation_reasons",
        "publishable",
        "passed",
    ):
        if key in pre_copy_verification:
            verification[key] = deepcopy(pre_copy_verification[key])
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
        "fact_requests": fact_request_history,
        "repair_attempts": len(factual_issue_history),
        "auto_fallback_used": auto_fallback_used,
        "llm_metrics": _collect_llm_metrics(*(list(preflight_llm_clients or []) + [planning_llm, copy_llm])),
    }


def _compile_candidate(
    *,
    planning_context: dict,
    typed_context,
    compiler: FeasibilityCompiler | None,
    skeleton: dict,
    user_profile: dict,
    runtime_pois: list[dict],
    route_matrix: list[dict],
    prepare_itinerary: PrepareItinerary | None,
    repair_attempt: int,
) -> tuple[dict, dict]:
    if typed_context is not None and compiler is not None:
        try:
            result = compiler.compile(
                context=typed_context,
                blueprint=skeleton,
                legacy_context=planning_context,
                runtime_pois=runtime_pois,
                route_matrix=route_matrix,
                prepare_itinerary=prepare_itinerary,
                repair_attempt=repair_attempt,
            )
        except ValueError as exc:
            raise AppError(
                "路线蓝图无法编译为合法行程。",
                code="plan_blueprint_invalid",
                step="compiling",
                details={"validation_error": str(exc)},
            ) from exc
        return result.itinerary.model_dump(mode="json", exclude_none=True), {
            "passed": result.validation_report.passed,
            "issues": list(result.legacy_verification.get("blocking_issues") or []),
        }
    itinerary = materialize_itinerary_from_skeleton(planning_context, skeleton)
    _prepare_itinerary(itinerary, user_profile, runtime_pois, route_matrix, prepare_itinerary)
    validation = validate_hard_constraints(
        itinerary,
        user_profile,
        route_matrix,
        runtime_pois,
        time_constraints=planning_context.get("time_constraints", []),
        order_constraints=planning_context.get("order_constraints", []),
        intent_ledger=planning_context.get("intent_ledger"),
    )
    return itinerary, validation


def _build_context(
    *,
    user_profile: dict,
    runtime_pois: list[dict],
    route_matrix: list[dict],
    uncertain_pois: list[dict] | None,
    hotel_anchor: dict | None,
    order_constraints: list[dict] | None,
    time_constraints: list[dict] | None,
    planning_decisions: list[dict] | None,
    user_request: str,
    typed: bool,
) -> tuple[dict, object | None]:
    context = compile_planning_context(
        user_profile,
        runtime_pois,
        route_matrix,
        uncertain_pois=uncertain_pois,
        hotel_anchor=hotel_anchor,
        order_constraints=order_constraints,
        time_constraints=time_constraints,
        planning_decisions=planning_decisions,
        user_request=user_request,
    )
    context["runtime_pois"] = runtime_pois
    context["planning_preferences"] = dict(
        context.get("planning_preferences") or build_planning_preferences(planning_decisions)
    )
    typed_context = planning_context_from_legacy(context, user_profile, runtime_pois, route_matrix) if typed else None
    return context, typed_context


def _plan_blueprint(planning_context: dict, typed_context, planner) -> dict:
    if typed_context is not None and hasattr(planner, "run"):
        try:
            return planner.run(typed_context).model_dump(mode="json")
        except AppError:
            return _deterministic_blueprint(planning_context)
    return plan_day_blueprint_with_llm(planning_context, planner)


def _replan_blueprint(
    planning_context: dict,
    typed_context,
    previous_blueprint: dict,
    feedback: dict,
    planner,
) -> dict:
    if typed_context is not None and hasattr(planner, "run"):
        repair_context = typed_context.model_copy(
            update={
                "previous_blueprint": plan_blueprint_from_legacy(previous_blueprint),
                "validation_report": validation_report_from_legacy(
                    feedback,
                    fact_version=typed_context.fact_snapshot.version,
                ),
            }
        )
        try:
            return planner.run(repair_context).model_dump(mode="json")
        except AppError:
            return _deterministic_blueprint(planning_context, "replan_itinerary_blueprint")
    return replan_day_blueprint_with_llm(planning_context, previous_blueprint, feedback, planner)


def _emit_phase(hook: WorkflowPhaseHook | None, phase: str, artifacts: dict) -> None:
    if hook is not None:
        hook(phase, artifacts)


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
        rest_boundaries = hotel_rest_boundary_pairs(day)
        for index, item in enumerate(items):
            if index >= len(items) - 1:
                item.pop("transport_to_next", None)
                continue
            pair = (str(item.get("poi_id") or ""), str(items[index + 1].get("poi_id") or ""))
            if pair in rest_boundaries:
                item.pop("transport_to_next", None)
                continue
            edge = route_by_pair.get(pair)
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
        "intent_ledger": dict(planning_context.get("intent_ledger") or {}),
        "planning_envelope": dict(planning_context.get("planning_envelope") or {}),
    }


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


def _unresolved_plan_error(issues: list[dict]) -> tuple[str, str]:
    issue_types = {str(issue.get("type") or "") for issue in issues}
    if "daily_absolute_limit_exceeded" in issue_types:
        return (
            "plan_capacity_unresolved",
            "必去地点在当前天数内仍然过于拥挤，请增加天数或将部分必去地点改为备选。",
        )
    if issue_types.intersection({"meal_slot_missing", "meal_time_invalid"}):
        return "plan_meal_unresolved", "路线的用餐时段仍有冲突，请调整地点数量或用餐要求。"
    if issue_types.intersection({"missing_transfer", "route_unknown"}):
        return "plan_route_fact_unresolved", "部分实际采用路段缺少可靠交通时间，暂时无法安全发布。"
    if any("time" in issue_type or "segment" in issue_type for issue_type in issue_types):
        return "plan_time_constraints_unresolved", "部分地点时段仍有冲突，请调整地点或时间要求后重试。"
    if issue_types.intersection(
        {"unknown_poi_scheduled", "unmatched_poi_scheduled", "unresolved_place_scheduled", "unavailable_place_scheduled"}
    ):
        return "plan_poi_fact_unresolved", "部分地点尚未可靠核验，请先确认地点后重新生成路线。"
    return "plan_factual_constraints_unresolved", "路线仍有无法自动解决的硬约束，请根据具体提示调整后重试。"


def _repair_meal_only_blueprint(skeleton: dict, itinerary: dict, issues: list[dict]) -> dict | None:
    if not issues or any(str(issue.get("type") or "") not in {"meal_slot_missing", "meal_time_invalid"} for issue in issues):
        return None
    repaired = deepcopy(skeleton)
    changed = False
    itinerary_days = {int(day.get("day") or 0): day for day in itinerary.get("days") or []}
    blueprint_days = {int(day.get("day") or 0): day for day in repaired.get("days") or []}
    for issue in issues:
        day_number = int(issue.get("day") or 0)
        blueprint_day = blueprint_days.get(day_number)
        compiled_day = itinerary_days.get(day_number)
        if not blueprint_day or not compiled_day:
            continue
        text = f"{issue.get('message', '')}{issue.get('suggestion', '')}"
        slot_name = "dinner" if "晚餐" in text else "lunch" if "午餐" in text else ""
        if not slot_name:
            continue
        target_minute = 18 * 60 if slot_name == "dinner" else 12 * 60
        large_anchor_id = next(
            (
                str(item.get("poi_id") or "")
                for item in compiled_day.get("items") or []
                if _arrival_minute(item.get("arrival_time")) is not None
                and int(item.get("duration_min") or 0) >= 240
                and _arrival_minute(item.get("arrival_time")) <= target_minute
                < _arrival_minute(item.get("arrival_time")) + int(item.get("duration_min") or 0)
            ),
            "",
        )
        replacement = (
            {"slot": slot_name, "requirement": "required", "source": "inside_poi", "within_poi_id": large_anchor_id}
            if large_anchor_id
            else {"slot": slot_name, "requirement": "required", "source": "fallback_nearby"}
        )
        slots = list(blueprint_day.get("meal_slots") or [])
        existing_index = next((index for index, slot in enumerate(slots) if slot.get("slot") == slot_name), None)
        if existing_index is None:
            slots.append(replacement)
        elif slots[existing_index].get("source") == "poi":
            # A user-selected restaurant is a planning commitment.  Mechanical
            # repair may fill an empty slot, but must not silently replace an
            # explicit restaurant with a generic nearby meal.
            continue
        elif slots[existing_index] == replacement:
            continue
        else:
            slots[existing_index] = replacement
        blueprint_day["meal_slots"] = slots
        changed = True
    return repaired if changed else None


def _arrival_minute(value: str | None) -> int | None:
    if not value or ":" not in value:
        return None
    try:
        hour, minute = value.split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


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
