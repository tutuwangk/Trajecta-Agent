from __future__ import annotations

from datetime import date, time

from app.trip_agent_v3.domain.facts import FactNeedStatus
from app.trip_agent_v3.domain.plan import (
    DraftDay,
    DraftStop,
    StopKind,
    WorkingDraft,
)
from app.trip_agent_v3.fact_needs import build_fact_gap_report, build_fact_need_plan


def test_failed_fact_is_reported_with_places_day_and_schedule_impact() -> None:
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
                    DraftStop(
                        stop_id="hotel",
                        candidate_id="cand-hotel",
                        name="酒店",
                        kind=StopKind.LODGING,
                        stay_duration_min=0,
                        rationale="出发。",
                    ),
                    DraftStop(
                        stop_id="museum",
                        candidate_id="cand-museum",
                        obligation_ids=("obl-museum",),
                        name="成都博物馆",
                        kind=StopKind.VISIT,
                        stay_duration_min=120,
                        rationale="用户明确地点。",
                    ),
                    DraftStop(
                        stop_id="hotel-return",
                        candidate_id="cand-hotel",
                        name="酒店",
                        kind=StopKind.LODGING,
                        stay_duration_min=0,
                        rationale="返回。",
                    ),
                ),
            ),
        ),
    )
    fact_plan = build_fact_need_plan(plan_id="facts-1", draft=draft)
    route_need = next(
        need
        for need in fact_plan.needs
        if need.stop_ids == ("hotel", "museum")
    )
    failed_plan = fact_plan.model_copy(
        update={
            "needs": tuple(
                need.model_copy(
                    update={
                        "status": FactNeedStatus.FAILED,
                        "failure_code": "provider_timeout",
                        "failure_message": "高德路线查询超时。",
                    }
                )
                if need.need_id == route_need.need_id
                else need
                for need in fact_plan.needs
            )
        }
    )

    report = build_fact_gap_report(draft=draft, fact_plan=failed_plan)

    assert len(report.gaps) == 1
    assert report.gaps[0].day_number == 1
    assert report.gaps[0].place_names == ("酒店", "成都博物馆")
    assert report.gaps[0].still_scheduled is True
    assert report.gaps[0].impact == "无法确认该段交通方式与时长，完整时间线不可发布。"
