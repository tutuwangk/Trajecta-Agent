from __future__ import annotations

from hashlib import sha256

from app.trip_agent_v3.domain.facts import (
    FactGap,
    FactGapReport,
    FactNeed,
    FactNeedKind,
    FactNeedPlan,
    FactNeedStatus,
)
from app.trip_agent_v3.domain.plan import (
    CompiledTimeline,
    StopKind,
    WorkingDraft,
)


def _need_id(*parts: str) -> str:
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"need_{digest}"


def build_fact_need_plan(
    *, plan_id: str, draft: WorkingDraft
) -> FactNeedPlan:
    needs: list[FactNeed] = []
    for day in draft.days:
        for origin, destination in zip(day.stops, day.stops[1:]):
            if origin.candidate_id == destination.candidate_id:
                continue
            needs.append(
                FactNeed(
                    need_id=_need_id(
                        "route",
                        str(day.day_number),
                        origin.candidate_id,
                        destination.candidate_id,
                        origin.stop_id,
                        destination.stop_id,
                    ),
                    kind=FactNeedKind.ROUTE,
                    day_number=day.day_number,
                    candidate_ids=(
                        origin.candidate_id,
                        destination.candidate_id,
                    ),
                    stop_ids=(origin.stop_id, destination.stop_id),
                    requested_mode=(
                        destination.travel_mode_from_previous
                    ),
                )
            )
    return FactNeedPlan(
        plan_id=plan_id,
        draft_id=draft.draft_id,
        draft_revision=draft.revision,
        needs=tuple(needs),
    )


def build_operational_fact_need_plan(
    *,
    plan_id: str,
    draft: WorkingDraft,
    timeline: CompiledTimeline,
) -> FactNeedPlan:
    if (
        timeline.draft_id != draft.draft_id
        or timeline.draft_revision != draft.revision
    ):
        raise ValueError(
            "timeline and draft lineage do not match for operational facts"
        )
    needs = tuple(
        FactNeed(
            need_id=_need_id(
                "operation",
                str(day.day_number),
                stop.stop_id,
                stop.candidate_id,
                stop.arrival_at.isoformat(),
            ),
            kind=FactNeedKind.PLACE_OPERATION,
            day_number=day.day_number,
            candidate_ids=(stop.candidate_id,),
            stop_ids=(stop.stop_id,),
            visit_at=stop.arrival_at,
        )
        for day in timeline.days
        for stop in day.stops
        if stop.kind
        in {
            StopKind.VISIT,
            StopKind.MEAL,
            StopKind.SHOPPING,
            StopKind.PHOTO,
        }
    )
    return FactNeedPlan(
        plan_id=plan_id,
        draft_id=draft.draft_id,
        draft_revision=draft.revision,
        needs=needs,
    )


def build_fact_gap_report(
    *, draft: WorkingDraft, fact_plan: FactNeedPlan
) -> FactGapReport:
    stops = {
        stop.stop_id: stop for day in draft.days for stop in day.stops
    }
    gaps: list[FactGap] = []
    for need in fact_plan.needs:
        if need.status is not FactNeedStatus.FAILED:
            continue
        place_names = tuple(
            stops[stop_id].name
            for stop_id in need.stop_ids
            if stop_id in stops
        )
        still_scheduled = all(stop_id in stops for stop_id in need.stop_ids)
        impact = (
            "无法确认该段交通方式与时长，完整时间线不可发布。"
            if need.kind is FactNeedKind.ROUTE
            else "无法确认该地点在计划到访时段可执行，发布前需要复核。"
        )
        gaps.append(
            FactGap(
                need_id=need.need_id,
                kind=need.kind,
                day_number=need.day_number,
                stop_ids=need.stop_ids,
                place_names=place_names,
                failure_code=need.failure_code or "unknown_failure",
                failure_message=need.failure_message or "事实查询失败。",
                still_scheduled=still_scheduled,
                impact=impact,
            )
        )
    return FactGapReport(
        fact_need_plan_id=fact_plan.plan_id,
        gaps=tuple(gaps),
    )
