from __future__ import annotations

from datetime import date, time

from app.trip_agent_v3.domain.facts import (
    FactNeedKind,
    FactResolutionStatus,
    RouteFact,
    RouteFactSource,
)
from app.trip_agent_v3.domain.plan import (
    DraftDay,
    DraftStop,
    StopKind,
    WorkingDraft,
)
from app.trip_agent_v3.domain.requirements import TravelMode
from app.trip_agent_v3.fact_needs import (
    build_fact_need_plan,
    build_operational_fact_need_plan,
)
from app.trip_agent_v3.timeline import compile_timeline


def _stop(
    stop_id: str,
    candidate_id: str,
    kind: StopKind,
    obligation_ids: tuple[str, ...] = (),
    travel_mode_from_previous: TravelMode | None = None,
) -> DraftStop:
    return DraftStop(
        stop_id=stop_id,
        candidate_id=candidate_id,
        obligation_ids=obligation_ids,
        name=stop_id,
        kind=kind,
        stay_duration_min=0 if kind is StopKind.LODGING else 60,
        travel_mode_from_previous=travel_mode_from_previous,
        rationale="测试。",
    )


def test_fact_needs_are_derived_only_from_scheduled_stops_and_legs() -> None:
    draft = WorkingDraft(
        draft_id="draft-1",
        goal_revision_id="goal-1",
        revision=1,
        days=(
            DraftDay(
                day_number=1,
                calendar_date=date(2026, 8, 1),
                title="第一天",
                start_time=time(9, 0),
                stops=(
                    _stop("hotel-start", "cand-hotel", StopKind.LODGING),
                    _stop(
                        "wuhou",
                        "cand-wuhou",
                        StopKind.VISIT,
                        ("obl-wuhou",),
                        TravelMode.WALK,
                    ),
                    _stop(
                        "meal",
                        "cand-meal",
                        StopKind.MEAL,
                        ("obl-meal",),
                    ),
                    _stop("hotel-end", "cand-hotel", StopKind.LODGING),
                ),
            ),
        ),
    )

    plan = build_fact_need_plan(plan_id="facts-1", draft=draft)

    assert {need.kind for need in plan.needs} == {FactNeedKind.ROUTE}
    queried_candidates = {
        candidate_id
        for need in plan.needs
        for candidate_id in need.candidate_ids
    }
    assert queried_candidates == {"cand-hotel", "cand-wuhou", "cand-meal"}
    assert "cand-ifs-not-scheduled" not in queried_candidates
    assert len(
        [need for need in plan.needs if need.kind is FactNeedKind.ROUTE]
    ) == 3
    route_needs = [
        need for need in plan.needs if need.kind is FactNeedKind.ROUTE
    ]
    assert route_needs[0].requested_mode is TravelMode.WALK
    route_facts = tuple(
        RouteFact(
            fact_id=f"fact-{index}",
            origin_candidate_id=need.candidate_ids[0],
            destination_candidate_id=need.candidate_ids[1],
            duration_min=10,
            mode=(need.requested_mode or TravelMode.TAXI).value,
            source=RouteFactSource.AMAP,
            status=FactResolutionStatus.VERIFIED,
        )
        for index, need in enumerate(route_needs)
    )
    timeline = compile_timeline(
        snapshot_id="timeline-1",
        draft=draft,
        route_facts=route_facts,
    )
    operation_plan = build_operational_fact_need_plan(
        plan_id="operations-1",
        draft=draft,
        timeline=timeline,
    )

    assert [need.kind for need in operation_plan.needs] == [
        FactNeedKind.PLACE_OPERATION,
        FactNeedKind.PLACE_OPERATION,
    ]
    assert [need.stop_ids for need in operation_plan.needs] == [
        ("wuhou",),
        ("meal",),
    ]
    assert all(need.visit_at is not None for need in operation_plan.needs)
