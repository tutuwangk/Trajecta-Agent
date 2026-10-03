from __future__ import annotations

from datetime import date, time

import pytest

from app.trip_agent_v3.domain.facts import (
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
from app.trip_agent_v3.timeline import TimelineCompilationError, compile_timeline


def _stop(
    stop_id: str,
    candidate_id: str,
    name: str,
    kind: StopKind,
    duration: int,
    obligation_ids: tuple[str, ...] = (),
) -> DraftStop:
    return DraftStop(
        stop_id=stop_id,
        candidate_id=candidate_id,
        obligation_ids=obligation_ids,
        name=name,
        kind=kind,
        stay_duration_min=duration,
        rationale="测试路线。",
    )


def _draft() -> WorkingDraft:
    return WorkingDraft(
        draft_id="draft-chengdu",
        goal_revision_id="goal-1",
        revision=1,
        days=(
            DraftDay(
                day_number=1,
                calendar_date=date(2026, 8, 1),
                title="武侯祠与川味午餐",
                start_time=time(9, 0),
                stops=(
                    _stop(
                        "hotel-start",
                        "cand-hotel",
                        "成都太古里亚朵S酒店",
                        StopKind.LODGING,
                        0,
                    ),
                    _stop(
                        "wuhou",
                        "cand-wuhou",
                        "成都武侯祠博物馆",
                        StopKind.VISIT,
                        90,
                        ("obl-wuhou",),
                    ),
                    _stop(
                        "restaurant",
                        "cand-restaurant",
                        "陈麻婆豆腐(骡马市店)",
                        StopKind.MEAL,
                        60,
                        ("obl-restaurant",),
                    ),
                    _stop(
                        "hotel-end",
                        "cand-hotel",
                        "成都太古里亚朵S酒店",
                        StopKind.LODGING,
                        0,
                    ),
                ),
            ),
        ),
    )


def _route(
    fact_id: str,
    origin: str,
    destination: str,
    duration: int,
    mode: str,
) -> RouteFact:
    return RouteFact(
        fact_id=fact_id,
        origin_candidate_id=origin,
        destination_candidate_id=destination,
        duration_min=duration,
        mode=mode,
        source=RouteFactSource.AMAP,
        status=FactResolutionStatus.VERIFIED,
    )


def test_compiler_emits_complete_depart_arrive_transport_stay_timeline() -> None:
    timeline = compile_timeline(
        snapshot_id="timeline-1",
        draft=_draft(),
        route_facts=(
            _route("r1", "cand-hotel", "cand-wuhou", 20, "taxi"),
            _route("r2", "cand-wuhou", "cand-restaurant", 15, "walk"),
            _route("r3", "cand-restaurant", "cand-hotel", 25, "taxi"),
        ),
    )

    day = timeline.days[0]
    assert day.title == "武侯祠与川味午餐"
    assert day.stops[0].departure_at.isoformat() == "2026-08-01T09:00:00"
    assert day.legs[0].departure_at.isoformat() == "2026-08-01T09:00:00"
    assert day.legs[0].arrival_at.isoformat() == "2026-08-01T09:20:00"
    assert day.stops[1].arrival_at.isoformat() == "2026-08-01T09:20:00"
    assert day.stops[1].departure_at.isoformat() == "2026-08-01T10:50:00"
    assert day.legs[1].mode == "walk"
    assert day.stops[2].kind is StopKind.MEAL
    assert day.stops[-1].name == "成都太古里亚朵S酒店"
    assert day.stops[-1].arrival_at.isoformat() == "2026-08-01T12:30:00"


def test_compiler_refuses_missing_or_failed_route_fact() -> None:
    with pytest.raises(TimelineCompilationError, match="missing route fact"):
        compile_timeline(
            snapshot_id="timeline-1",
            draft=_draft(),
            route_facts=(
                _route("r1", "cand-hotel", "cand-wuhou", 20, "taxi"),
            ),
        )


def test_spatial_estimate_never_becomes_verified_route_fact() -> None:
    with pytest.raises(ValueError, match="spatial estimate"):
        RouteFact(
            fact_id="r-estimate",
            origin_candidate_id="cand-a",
            destination_candidate_id="cand-b",
            duration_min=30,
            mode="estimated",
            source=RouteFactSource.SPATIAL_ESTIMATE,
            status=FactResolutionStatus.VERIFIED,
        )


def test_same_candidate_anchors_compile_as_zero_travel_without_provider_fact() -> None:
    draft = WorkingDraft(
        draft_id="draft-rest",
        goal_revision_id="goal-1",
        revision=1,
        days=(
            DraftDay(
                day_number=1,
                calendar_date=date(2026, 8, 1),
                title="酒店休整",
                start_time=time(9, 0),
                stops=(
                    DraftStop(
                        stop_id="hotel-start",
                        candidate_id="cand-hotel",
                        name="酒店",
                        kind=StopKind.LODGING,
                        stay_duration_min=0,
                        rationale="出发锚点。",
                    ),
                    DraftStop(
                        stop_id="hotel-end",
                        candidate_id="cand-hotel",
                        name="酒店",
                        kind=StopKind.LODGING,
                        stay_duration_min=0,
                        rationale="结束锚点。",
                    ),
                ),
            ),
        ),
    )

    timeline = compile_timeline(
        snapshot_id="timeline-rest",
        draft=draft,
        route_facts=(),
    )

    assert timeline.days[0].legs[0].duration_min == 0
    assert timeline.days[0].legs[0].mode == "stay"
    assert (
        timeline.days[0].legs[0].fact_source
        is RouteFactSource.SAME_PLACE
    )


def test_repeated_candidate_pair_on_different_days_uses_stop_owned_facts() -> None:
    draft = WorkingDraft(
        draft_id="draft-repeat",
        goal_revision_id="goal-1",
        revision=1,
        days=tuple(
            DraftDay(
                day_number=day_number,
                calendar_date=date(2026, 8, day_number),
                title=f"第 {day_number} 天",
                start_time=time(9, 0),
                stops=(
                    _stop(
                        f"hotel-{day_number}",
                        "cand-hotel",
                        "酒店",
                        StopKind.LODGING,
                        0,
                    ),
                    _stop(
                        f"museum-{day_number}",
                        "cand-museum",
                        "博物馆",
                        StopKind.VISIT,
                        60,
                        (f"obl-{day_number}",),
                    ),
                    _stop(
                        f"return-{day_number}",
                        "cand-hotel",
                        "酒店",
                        StopKind.LODGING,
                        0,
                    ),
                ),
            )
            for day_number in (1, 2)
        ),
    )
    facts = tuple(
        RouteFact(
            fact_id=f"fact-{day_number}-{direction}",
            origin_candidate_id=origin_candidate,
            destination_candidate_id=destination_candidate,
            origin_stop_id=origin_stop,
            destination_stop_id=destination_stop,
            day_number=day_number,
            duration_min=10 + day_number,
            mode="taxi",
            source=RouteFactSource.AMAP,
            status=FactResolutionStatus.VERIFIED,
        )
        for day_number in (1, 2)
        for (
            direction,
            origin_candidate,
            destination_candidate,
            origin_stop,
            destination_stop,
        ) in (
            (
                "out",
                "cand-hotel",
                "cand-museum",
                f"hotel-{day_number}",
                f"museum-{day_number}",
            ),
            (
                "back",
                "cand-museum",
                "cand-hotel",
                f"museum-{day_number}",
                f"return-{day_number}",
            ),
        )
    )

    timeline = compile_timeline(
        snapshot_id="timeline-repeat",
        draft=draft,
        route_facts=facts,
    )

    assert timeline.days[0].legs[0].duration_min == 11
    assert timeline.days[1].legs[0].duration_min == 12
