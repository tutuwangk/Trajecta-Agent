from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.domain.facts import Coordinates, FactGap, FactSnapshot, GroundedPOI, HotelAnchor, POIAvailabilityFact, RouteEdge
from app.domain.planning import PlanBlueprint, PlanningCandidate, PlanningContext, PlanningPreferences
from app.domain.validation import ValidationIssue, ValidationReport
from app.schemas.models import UserProfile


_POI_KEYS = {
    "poi_id",
    "standard_name",
    "raw_name",
    "amap_id",
    "address",
    "city",
    "district",
    "category",
    "category_normalized",
    "location",
    "match_status",
    "final_decision",
    "user_override",
    "estimated_duration_min",
    "experience_tags",
    "planning_semantics",
}


def fact_snapshot_from_legacy(
    destination: str,
    runtime_pois: list[dict],
    route_matrix: list[dict],
    hotel_anchor: dict | None = None,
) -> FactSnapshot:
    pois = [grounded_poi_from_legacy(poi) for poi in runtime_pois if poi.get("poi_id")]
    edges = [route_edge_from_legacy(edge) for edge in route_matrix if edge.get("origin_poi_id") and edge.get("destination_poi_id")]
    availability_facts = [
        POIAvailabilityFact.model_validate(fact)
        for poi in runtime_pois
        for fact in poi.get("availability_facts") or []
        if isinstance(fact, dict)
    ]
    gaps: list[FactGap] = []
    for poi in pois:
        if poi.location is None:
            gaps.append(
                FactGap(
                    code="poi_location_missing",
                    message=f"{poi.standard_name} 缺少可核验坐标。",
                    entity_ids=[poi.poi_id],
                    blocking=poi.final_decision == "include",
                )
            )
    for edge in edges:
        if edge.duration_min is None:
            gaps.append(
                FactGap(
                    code="route_edge_unavailable",
                    message=f"{edge.origin_poi_id} 到 {edge.destination_poi_id} 缺少交通耗时。",
                    entity_ids=[edge.origin_poi_id, edge.destination_poi_id],
                    blocking=False,
                )
            )
    for poi in runtime_pois:
        for raw_gap in poi.get("fact_gaps") or []:
            if isinstance(raw_gap, dict):
                gaps.append(FactGap.model_validate(raw_gap))
    hotel = hotel_anchor_from_legacy(hotel_anchor) if hotel_anchor else None
    canonical = json.dumps(
        {
            "destination": destination,
            "pois": [poi.model_dump(mode="json") for poi in pois],
            "edges": [edge.model_dump(mode="json") for edge in edges],
            "hotel": hotel.model_dump(mode="json") if hotel else None,
            "availability": [fact.model_dump(mode="json") for fact in availability_facts],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    version = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return FactSnapshot(
        version=version,
        destination=destination,
        pois=pois,
        hotel_anchor=hotel,
        route_edges=edges,
        availability_facts=availability_facts,
        gaps=gaps,
    )


def grounded_poi_from_legacy(value: dict) -> GroundedPOI:
    location = _coordinates(value.get("location"))
    match_status = str(value.get("match_status") or "matched")
    confidence = "verified" if match_status == "matched" and location else "estimated" if location else "unavailable"
    name = str(value.get("standard_name") or value.get("name") or value.get("raw_name") or value.get("poi_id") or "").strip()
    metadata = {key: raw for key, raw in value.items() if key not in _POI_KEYS}
    return GroundedPOI(
        poi_id=str(value.get("poi_id") or "").strip(),
        standard_name=name,
        raw_name=str(value.get("raw_name") or ""),
        amap_id=str(value.get("amap_id") or ""),
        address=str(value.get("address") or ""),
        city=str(value.get("city") or ""),
        district=str(value.get("district") or ""),
        category=str(value.get("category") or value.get("category_normalized") or "unknown"),
        location=location,
        match_status=match_status,
        final_decision=str(value.get("final_decision") or "include"),
        user_override=str(value.get("user_override") or "none"),
        estimated_duration_min=max(0, int(value.get("estimated_duration_min") or 90)),
        confidence=confidence,
        unavailable_reason="" if confidence != "unavailable" else "missing_verified_location",
        experience_tags=[str(item) for item in value.get("experience_tags") or value.get("ugc_tags") or []],
        planning_semantics=dict(value.get("planning_semantics") or {}),
        metadata=metadata,
    )


def hotel_anchor_from_legacy(value: dict) -> HotelAnchor:
    location = _coordinates(value.get("location"))
    match_status = str(value.get("match_status") or "matched")
    match_confidence = float(value.get("match_confidence") or 0)
    requested_confidence = str(value.get("confidence") or "")
    if requested_confidence in {"verified", "estimated", "unavailable"}:
        confidence = requested_confidence
    elif location and match_status == "matched" and match_confidence >= 0.8:
        confidence = "verified"
    elif location:
        confidence = "estimated"
    else:
        confidence = "unavailable"
    return HotelAnchor(
        poi_id=str(value.get("poi_id") or "hotel_anchor"),
        standard_name=str(value.get("standard_name") or value.get("name") or "酒店"),
        amap_id=str(value.get("amap_id") or ""),
        city=str(value.get("city") or ""),
        district=str(value.get("district") or ""),
        address=str(value.get("address") or ""),
        location=location,
        confidence=confidence,
        match_status=match_status,
        match_confidence=match_confidence,
        source=str(value.get("source") or "amap"),
        unavailable_reason="" if confidence != "unavailable" else "hotel_location_missing",
    )


def route_edge_from_legacy(value: dict) -> RouteEdge:
    source_value = str(value.get("source") or "unknown")
    source = source_value if source_value in {"amap_direction_api", "route_cache", "spatial_estimate"} else "unknown"
    duration = value.get("duration_min")
    duration = max(0, int(duration)) if duration is not None else None
    cache_metadata = value.get("cache_metadata") or {}
    valid_precise_cache = (
        source == "route_cache"
        and bool(cache_metadata.get("cache_valid"))
        and cache_metadata.get("original_source") == "amap_direction_api"
    )
    if duration is not None and (source == "amap_direction_api" or valid_precise_cache):
        confidence = "verified"
        reason = ""
    elif duration is not None:
        confidence = "estimated"
        reason = "路线使用空间估算，未取得高德精确交通事实。"
    else:
        confidence = "unavailable"
        reason = "交通事实不可用。"
    known = {"origin_poi_id", "destination_poi_id", "mode", "duration_min", "distance_m", "relation", "source", "fetched_at", "cache_metadata"}
    return RouteEdge(
        origin_poi_id=str(value.get("origin_poi_id") or ""),
        destination_poi_id=str(value.get("destination_poi_id") or ""),
        mode=str(value.get("mode") or "unknown"),
        duration_min=duration,
        distance_m=max(0, int(value["distance_m"])) if value.get("distance_m") is not None else None,
        relation=str(value.get("relation") or "unknown"),
        source=source,
        confidence=confidence,
        fetched_at=_datetime(value.get("fetched_at")),
        degradation_reason=reason,
        metadata={"cache_metadata": cache_metadata, **{key: raw for key, raw in value.items() if key not in known}},
    )


def planning_context_from_legacy(
    legacy_context: dict,
    user_profile: dict,
    runtime_pois: list[dict],
    route_matrix: list[dict],
) -> PlanningContext:
    snapshot = fact_snapshot_from_legacy(
        str(legacy_context.get("destination") or user_profile.get("destination") or ""),
        runtime_pois,
        route_matrix,
        legacy_context.get("hotel_anchor"),
    )
    must_ids = set(legacy_context.get("must_poi_ids") or [])
    optional_ids = set(legacy_context.get("optional_poi_ids") or [])
    candidates = []
    for poi in legacy_context.get("plannable_pois") or []:
        poi_id = str(poi.get("poi_id") or "")
        priority = "must" if poi_id in must_ids else "optional" if poi_id in optional_ids else "preferred"
        candidates.append(
            PlanningCandidate(
                poi_id=poi_id,
                name=str(poi.get("name") or poi_id),
                district=str(poi.get("district") or ""),
                category=str(poi.get("category") or "unknown"),
                priority=priority,
                role=str(poi.get("poi_role") or poi.get("role") or "visit"),
                experience_type=str(poi.get("experience_type") or "daytime_visit"),
                duration_min=max(0, int(poi.get("estimated_duration_min") or poi.get("duration_min") or 90)),
                meal_capability=str(poi.get("meal_capability") or "none"),
                time_windows=[str(item) for item in poi.get("time_advice") or poi.get("time_windows") or []],
                planning_function=str(poi.get("planning_function") or "anchor"),
                planning_notes=str(poi.get("planning_notes") or ""),
                quick_stop_eligible=bool(poi.get("quick_stop_eligible")),
            )
        )
    typed_profile = UserProfile.model_validate(user_profile)
    trip_dates = (
        [typed_profile.start_date + timedelta(days=offset) for offset in range(typed_profile.days)]
        if typed_profile.start_date
        else []
    )
    return PlanningContext(
        user_profile=typed_profile,
        fact_snapshot=snapshot,
        candidates=candidates,
        day_budget_min=max(0, int(legacy_context.get("day_budget_min") or 0)),
        order_constraints=list(legacy_context.get("order_constraints") or []),
        time_constraints=list(legacy_context.get("time_constraints") or []),
        planning_preferences=PlanningPreferences.model_validate(legacy_context.get("planning_preferences") or {}),
        trip_dates=trip_dates,
        user_request=str(legacy_context.get("user_request") or "")[:6000],
    )


def plan_blueprint_from_legacy(value: dict) -> PlanBlueprint:
    normalized = dict(value)
    normalized["days"] = [_normalize_blueprint_day(day) for day in value.get("days") or []]
    return PlanBlueprint.model_validate(normalized)


def validation_report_from_legacy(value: dict, fact_version: str = "", repair_attempt: int = 0) -> ValidationReport:
    raw_issues = list(value.get("issues") or [])
    blocking = {str(item.get("type") or item.get("code") or "") for item in value.get("blocking_issues") or []}
    issues = []
    for item in raw_issues:
        code = str(item.get("type") or item.get("code") or "validation_issue")
        severity_value = str(item.get("severity") or "medium")
        severity = severity_value if severity_value in {"info", "low", "medium", "high", "blocking"} else "medium"
        issues.append(
            ValidationIssue(
                code=code,
                severity="blocking" if code in blocking else severity,
                message=str(item.get("message") or item.get("suggestion") or code),
                day=int(item["day"]) if item.get("day") is not None else None,
                poi_ids=[str(item["poi_id"])] if item.get("poi_id") else [],
                evidence={key: raw for key, raw in item.items() if key not in {"type", "code", "severity", "message", "suggestion", "day", "poi_id"}},
                suggestion=str(item.get("suggestion") or ""),
                agent_repairable=bool(item.get("agent_repairable", True)),
                release_blocking=code in blocking or bool(item.get("release_blocking")),
            )
        )
    return ValidationReport(
        passed=bool(value.get("passed")) and not any(issue.release_blocking for issue in issues),
        issues=issues,
        fact_version=fact_version,
        repair_attempt=repair_attempt,
    )


def _normalize_blueprint_day(value: dict) -> dict:
    day = dict(value)
    segments = list(day.get("segments") or [])
    if segments and not day.get("poi_ids"):
        day["poi_ids"] = [poi_id for segment in segments if segment.get("kind") == "outing" for poi_id in segment.get("poi_ids") or []]
    day.setdefault("poi_ids", [])
    day.setdefault("selected_branch_ids", {})
    day.setdefault("scheduled_roles", {})
    day.setdefault("meal_slots", [])
    day.setdefault("unscheduled_poi_ids", [])
    day.setdefault("drop_reason_codes", {})
    day.setdefault("risk_tags", [])
    return day


def _coordinates(value: Any) -> Coordinates | None:
    if isinstance(value, dict) and value.get("lng") is not None and value.get("lat") is not None:
        return Coordinates(lng=float(value["lng"]), lat=float(value["lat"]))
    if isinstance(value, str) and "," in value:
        lng, lat = value.split(",", 1)
        try:
            return Coordinates(lng=float(lng), lat=float(lat))
        except ValueError:
            return None
    return None


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)
