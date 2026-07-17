from __future__ import annotations

from datetime import timedelta

from app.domain.facts import FactSnapshot, RouteEdge
from app.domain.itinerary import ReleaseDecision
from app.domain.validation import ValidationReport
from app.planning.segment_boundaries import hotel_rest_boundary_pairs
from app.schemas.models import UserProfile


_EXPERIENCE_CONFLICT_CODES = {
    "fixed_time_constraint_violated",
    "day_assignment_violated",
    "order_constraint_violated",
    "explicit_meal_preference_missing",
}
_EXPERIENCE_ADJUSTMENT_CODES = _EXPERIENCE_CONFLICT_CODES | {
    "time_constraint_violated",
    "segment_time_violated",
    "daily_time_over_intensity_limit",
    "empty_day_with_available_places",
    "preferred_visit_missing",
    "meal_slot_missing",
    "meal_time_invalid",
    "meal_stop_missing",
    "long_transfer",
    "too_many_cross_area_moves",
    "daytime_place_scheduled_too_late",
}


class ReleaseGate:
    def decide(
        self,
        *,
        itinerary: dict,
        report: ValidationReport,
        facts: FactSnapshot,
        user_profile: UserProfile,
    ) -> ReleaseDecision:
        blockers = [issue.code for issue in report.blocking_issues]
        reasons = [issue.message for issue in report.blocking_issues]
        degradation_reasons: list[str] = []
        experience_status, experience_reasons = _experience_decision(report)

        scheduled_ids = {
            str(item.get("poi_id"))
            for day in itinerary.get("days") or []
            for item in day.get("items") or []
            if item.get("poi_id")
        }
        pois_by_id = {poi.poi_id: poi for poi in facts.pois}
        for poi_id in sorted(scheduled_ids):
            poi = pois_by_id.get(poi_id)
            if poi is None or poi.confidence == "unavailable":
                blockers.append("scheduled_poi_fact_unavailable")
                reasons.append(f"已安排地点 {poi_id} 缺少可用事实。")
        if user_profile.hotel_name and (facts.hotel_anchor is None or facts.hotel_anchor.confidence != "verified"):
            degradation_reasons.append("住宿位置无法可靠核验，路线未使用酒店休息段，酒店往返需出发前复核。")
        if user_profile.hotel_name and facts.hotel_anchor is not None and facts.hotel_anchor.confidence == "verified":
            for day in itinerary.get("days") or []:
                if not day.get("items"):
                    continue
                if day.get("hotel_departure_transport_min") is None or day.get("hotel_return_transport_min") is None:
                    blockers.append("hotel_route_unavailable")
                    reasons.append(f"Day {day.get('day')} 缺少酒店往返交通事实。")
                    continue
                for prefix in ("hotel_departure", "hotel_return"):
                    if day.get(f"{prefix}_transport_source") == "spatial_estimate":
                        message = day.get(f"{prefix}_transport_degradation_reason") or f"Day {day.get('day')} 酒店往返使用空间估算。"
                        if message not in degradation_reasons:
                            degradation_reasons.append(message)
                for rest in day.get("hotel_rest_breaks") or []:
                    for prefix, label in (
                        ("return_to_hotel", "返回酒店"),
                        ("depart_from_hotel", "酒店再出发"),
                    ):
                        duration = rest.get(f"{prefix}_transport_min")
                        source = str(rest.get(f"{prefix}_transport_source") or "unknown")
                        if not isinstance(duration, int) or duration <= 0 or source == "unknown":
                            blockers.append("hotel_rest_route_unavailable")
                            reasons.append(f"Day {day.get('day')} 的{label}缺少可用交通事实。")
                            continue
                        if source == "spatial_estimate":
                            message = (
                                rest.get(f"{prefix}_transport_degradation_reason")
                                or f"Day {day.get('day')} 的{label}使用空间估算。"
                            )
                            if message not in degradation_reasons:
                                degradation_reasons.append(message)

        used_edges = _used_route_edges(itinerary, facts.route_edges)
        for origin, destination, edge in used_edges:
            if edge is None or edge.duration_min is None:
                blockers.append("used_route_edge_unavailable")
                reasons.append(f"{origin} 到 {destination} 缺少可用交通耗时。")
                continue
            if edge.confidence != "verified":
                message = edge.degradation_reason or f"{origin} 到 {destination} 使用估算交通数据。"
                if message not in degradation_reasons:
                    degradation_reasons.append(message)

        availability_index = {(fact.poi_id, fact.visit_date): fact for fact in facts.availability_facts}
        unresolved_availability_ids = {
            entity_id
            for gap in facts.gaps
            if gap.code == "poi_availability_unresolved"
            for entity_id in gap.entity_ids
        }
        for day in itinerary.get("days") or []:
            visit_date = (
                user_profile.start_date + timedelta(days=max(0, int(day.get("day") or 1) - 1))
                if user_profile.start_date
                else None
            )
            for item in day.get("items") or []:
                poi_id = str(item.get("poi_id") or "")
                fact = availability_index.get((poi_id, visit_date)) if visit_date else None
                if fact and fact.status == "closed":
                    blockers.append("scheduled_poi_closed_on_visit_date")
                    reasons.append(f"{item.get('name') or poi_id} 在 {visit_date.isoformat()} 有来源证据显示不开放。")
                elif fact and fact.confidence != "verified":
                    message = fact.degradation_reason or f"{item.get('name') or poi_id} 的营业时间需要复核。"
                    if message not in degradation_reasons:
                        degradation_reasons.append(message)
                elif poi_id in unresolved_availability_ids:
                    message = f"{item.get('name') or poi_id} 的日期营业事实未能确认。"
                    if message not in degradation_reasons:
                        degradation_reasons.append(message)

        if blockers:
            return ReleaseDecision(
                status="failed",
                experience_status=experience_status,
                experience_reasons=experience_reasons,
                reasons=list(dict.fromkeys(reasons)),
                blocking_issue_codes=list(dict.fromkeys(blockers)),
                user_actions=["调整地点或稍后重试地图事实获取。"],
            )
        if degradation_reasons:
            return ReleaseDecision(
                status="degraded",
                experience_status=experience_status,
                experience_reasons=experience_reasons,
                reasons=["行程可执行，但部分事实采用估算或仍需复核。"],
                degradation_reasons=degradation_reasons,
                user_actions=["出发前复核标记为估算的交通或营业信息。"],
            )
        return ReleaseDecision(
            status="verified",
            experience_status=experience_status,
            experience_reasons=experience_reasons,
            reasons=["地点、路线和行程硬约束均已通过核验。"],
        )


def apply_release_decision(verification: dict, decision: ReleaseDecision) -> dict:
    result = dict(verification)
    result["result_status"] = decision.status
    result["fact_status"] = decision.fact_status
    result["experience_status"] = decision.experience_status
    result["experience_reasons"] = list(decision.experience_reasons)
    result["release_decision"] = decision.model_dump(mode="json")
    result["degradation_reasons"] = list(decision.degradation_reasons)
    result["publishable"] = decision.status in {"verified", "degraded"}
    result["passed"] = result["publishable"]
    return result


def _experience_decision(report: ValidationReport) -> tuple[str, list[str]]:
    relevant = [issue for issue in report.issues if not issue.release_blocking and issue.code in _EXPERIENCE_ADJUSTMENT_CODES]
    reasons = list(dict.fromkeys(issue.message for issue in relevant if issue.message))[:5]
    if not reasons:
        return "good", []
    if any(issue.code in _EXPERIENCE_CONFLICT_CODES and issue.severity in {"high", "blocking"} for issue in relevant):
        return "conflict", reasons
    return "needs_adjustment", reasons


def _used_route_edges(
    itinerary: dict,
    route_edges: list[RouteEdge],
) -> list[tuple[str, str, RouteEdge | None]]:
    index = {(edge.origin_poi_id, edge.destination_poi_id): edge for edge in route_edges}
    result: list[tuple[str, str, RouteEdge | None]] = []
    for day in itinerary.get("days") or []:
        rest_boundaries = hotel_rest_boundary_pairs(day)
        items = day.get("items") or []
        for origin, destination in zip(items, items[1:]):
            origin_id = str(origin.get("poi_id") or "")
            destination_id = str(destination.get("poi_id") or "")
            if (origin_id, destination_id) in rest_boundaries:
                continue
            result.append((origin_id, destination_id, index.get((origin_id, destination_id))))
    return result
