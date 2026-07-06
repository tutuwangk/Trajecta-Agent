from __future__ import annotations

from copy import deepcopy
from typing import Callable

from app.agents.itinerary_normalizer import normalize_itinerary
from app.agents.planner import (
    build_copy_context,
    compile_planning_context,
    materialize_itinerary_from_skeleton,
    plan_skeleton_with_llm,
)
from app.agents.reviser import generate_copy, llm_replan, rule_repair
from app.agents.verifier import review_soft_quality, validate_hard_constraints, verify_itinerary
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
    prepare_itinerary: PrepareItinerary | None = None,
    max_replans: int = 2,
) -> tuple[dict, dict, dict]:
    planning_context = compile_planning_context(
        user_profile,
        runtime_pois,
        route_matrix,
        uncertain_pois=uncertain_pois,
        hotel_anchor=hotel_anchor,
        order_constraints=order_constraints,
    )
    skeleton_versions: list[dict] = []
    hard_issue_history: list[dict] = []

    skeleton = plan_skeleton_with_llm(planning_context, planning_llm)
    final_itinerary: dict | None = None
    final_hard_validation = {"passed": True, "issues": []}

    for attempt in range(max_replans + 1):
        skeleton_versions.append(deepcopy(skeleton))
        itinerary = materialize_itinerary_from_skeleton(planning_context, skeleton)
        _prepare_itinerary(itinerary, user_profile, runtime_pois, route_matrix, prepare_itinerary)
        hard_validation = validate_hard_constraints(itinerary, user_profile, route_matrix, runtime_pois)
        if hard_validation["passed"]:
            final_itinerary = itinerary
            final_hard_validation = hard_validation
            break

        hard_issue_history.append(deepcopy(hard_validation))
        repaired = rule_repair(itinerary, hard_validation, user_profile, runtime_pois=runtime_pois)
        _prepare_itinerary(repaired, user_profile, runtime_pois, route_matrix, prepare_itinerary)
        repaired_validation = validate_hard_constraints(repaired, user_profile, route_matrix, runtime_pois)
        if repaired_validation["passed"]:
            final_itinerary = repaired
            final_hard_validation = repaired_validation
            break

        if attempt >= max_replans:
            raise AppError("路线规划仍未满足硬约束，请稍后重试。", code="plan_constraints_unresolved", step="plan")
        skeleton = llm_replan(planning_context, skeleton, repaired_validation, planning_llm)

    if final_itinerary is None:
        raise AppError("路线规划失败，未生成有效结果。", code="plan_constraints_unresolved", step="plan")

    soft_issues = review_soft_quality(final_itinerary, user_profile, route_matrix, runtime_pois, llm_client=copy_llm)
    copy_context = build_copy_context(planning_context, final_itinerary, final_hard_validation, soft_issues)
    final = generate_copy(final_itinerary, copy_context, user_profile, copy_llm)
    _prepare_itinerary(final, user_profile, runtime_pois, route_matrix, prepare_itinerary)
    verification = verify_itinerary(final, user_profile, route_matrix, runtime_pois)
    return final, verification, {
        "planning_context_snapshot": _context_snapshot(planning_context),
        "skeleton_versions": skeleton_versions,
        "hard_issue_history": hard_issue_history,
        "repair_attempts": len(hard_issue_history),
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
    }
