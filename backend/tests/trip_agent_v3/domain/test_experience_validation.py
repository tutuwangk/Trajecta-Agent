from __future__ import annotations

from datetime import date, datetime, time
from datetime import timedelta
import json
from pathlib import Path
import pytest

from app.trip_agent_v3.domain.facts import (
    FactResolutionStatus,
    RouteFactSource,
)
from app.trip_agent_v3.domain.plan import (
    CompiledTimeline,
    DayTimeline,
    StopKind,
    TimelineLeg,
    TimelineStop,
)
from app.trip_agent_v3.domain.requirements import (
    ConstraintStrength,
    DayAssignmentRequirement,
    EvidenceSpan,
    ObligationPriority,
    PaceRequirement,
    PlaceObligation,
    PlaceRole,
    QueryDecision,
    RequirementLedger,
    TimeWindowRequirement,
    TransportPreferenceRequirement,
    TravelMode,
)
from app.trip_agent_v3.experience import evaluate_timeline_constraints
from app.trip_agent_v3.experience import _meal_window_advice


@pytest.mark.parametrize("start,end,kind,expected", [
    (time(9), time(11, 29), StopKind.VISIT, ()),
    (time(11, 31), time(14), StopKind.VISIT, ()),
    (time(11), time(14), StopKind.VISIT, ("lunch_not_separately_planned",)),
    (time(11), time(14), StopKind.MEAL, ()),
    (time(11), time(14), StopKind.LODGING, ()),
    (time(18, 1), time(21), StopKind.VISIT, ()),
    (time(17), time(20), StopKind.VISIT, ("dinner_not_separately_planned",)),
])
def test_meal_advice_requires_complete_window_and_no_meal_or_hotel_break(start, end, kind, expected):
    begun = datetime.combine(date(2026, 8, 1), start)
    ended = datetime.combine(date(2026, 8, 1), end)
    arrived = begun + timedelta(minutes=5)
    departed = ended - timedelta(minutes=5)
    middle_minutes = int((departed-arrived).total_seconds() // 60)
    timeline = CompiledTimeline(snapshot_id="meal-timeline", draft_id="meal-draft", draft_revision=1, days=(DayTimeline(
        day_number=1, calendar_date=begun.date(), title="用餐窗口检查",
        stops=(TimelineStop(stop_id="start", candidate_id="hotel", name="酒店", kind=StopKind.LODGING, arrival_at=begun, departure_at=begun, stay_duration_min=0),
            TimelineStop(stop_id="middle", candidate_id="middle", name="当前停靠", kind=kind, arrival_at=arrived, departure_at=departed, stay_duration_min=middle_minutes),
            TimelineStop(stop_id="end", candidate_id="hotel", name="酒店", kind=StopKind.LODGING, arrival_at=ended, departure_at=ended, stay_duration_min=0)),
        legs=(TimelineLeg(leg_id="out", from_stop_id="start", to_stop_id="middle", departure_at=begun, arrival_at=arrived, duration_min=5, mode="walk", fact_id="out", fact_source=RouteFactSource.AMAP, fact_status=FactResolutionStatus.VERIFIED),
            TimelineLeg(leg_id="back", from_stop_id="middle", to_stop_id="end", departure_at=departed, arrival_at=ended, duration_min=5, mode="walk", fact_id="back", fact_source=RouteFactSource.AMAP, fact_status=FactResolutionStatus.VERIFIED)),
    ),))
    issues = _meal_window_advice(timeline)
    assert tuple(issue.code for issue in issues) == expected
    assert all(issue.severity == "review" and issue.day_numbers == (1,) for issue in issues)


def test_real_two_day_snapshot_gains_lunch_advice_and_keeps_publishable():
    from app.trip_agent_v3.domain.delivery import CandidateSnapshot, RunRecord, RunStatus
    from app.trip_agent_v3.delivery import assess_delivery

    evidence_path = Path(__file__).resolve().parents[4] / "docs/agent-v3/evidence/2026-10-02/two-day-delivery.json"
    payload = json.loads(evidence_path.read_text())
    candidate = CandidateSnapshot.model_validate_json(json.dumps(payload["candidate"]))
    ledger = RequirementLedger(ledger_id=candidate.requirement_ledger_id, goal_revision_id=candidate.goal_revision_id)
    status, issues = evaluate_timeline_constraints(ledger=ledger, timeline=candidate.timeline)
    assert status.value == "needs_adjustment"
    lunch = next(issue for issue in issues if issue.code == "lunch_not_separately_planned")
    assert lunch.day_numbers == (2,)
    assert "11:30–14:00" in lunch.message
    assert "尚未单独安排午餐" in lunch.message
    assert "杜甫草堂" in "、".join(lunch.place_names)
    run = RunRecord(run_id=candidate.producing_run_id, workspace_id=candidate.workspace_id, goal_revision_id=candidate.goal_revision_id, status=RunStatus.SUCCEEDED)
    updated = candidate.model_copy(update={"experience_status": status, "experience_issues": issues})
    assert assess_delivery(run=run, candidate=updated).may_publish is True

    visit = candidate.timeline.days[1].stops[1]
    fixed_evidence = EvidenceSpan(source_id="appointment-source", start=0, end=5, text="已预约上午")
    ledger = ledger.model_copy(update={
        "obligations": (PlaceObligation(obligation_id=visit.obligation_ids[0], mention=visit.name, role=PlaceRole.VISIT, priority=ObligationPriority.REQUIRED, evidence=(fixed_evidence,), query_decision=QueryDecision.QUERY, query_text=visit.name),),
        "constraints": (TimeWindowRequirement(constraint_id="fixed-window", subject_obligation_ids=(visit.obligation_ids[0],), evidence=(fixed_evidence,), strength=ConstraintStrength.REQUIRED, fixed_commitment=True, day_number=2, earliest=time(9), latest=time(10)),),
    })
    conflict, mixed_issues = evaluate_timeline_constraints(ledger=ledger, timeline=candidate.timeline)
    assert conflict.value == "conflict"
    assert any(issue.severity == "blocking" for issue in mixed_issues)
    assert any(issue.code == "lunch_not_separately_planned" and issue.severity == "review" for issue in mixed_issues)
    conflicted = candidate.model_copy(update={"experience_status": conflict, "experience_issues": mixed_issues})
    assert assess_delivery(run=run, candidate=conflicted).may_publish is False


@pytest.mark.parametrize("fixed", [False, True])
def test_compiled_arrival_is_checked_against_source_time_window(fixed) -> None:
    evidence = EvidenceSpan(
        source_id="source-1",
        start=0,
        end=2,
        text="上午",
    )
    ledger = RequirementLedger(
        ledger_id="ledger-1",
        goal_revision_id="goal-1",
        obligations=(
            PlaceObligation(
                obligation_id="obl-wuhou",
                mention="武侯祠",
                role=PlaceRole.VISIT,
                priority=ObligationPriority.REQUIRED,
                evidence=(
                    EvidenceSpan(
                        source_id="source-1",
                        start=3,
                        end=6,
                        text="武侯祠",
                    ),
                ),
                query_decision=QueryDecision.QUERY,
                query_text="武侯祠",
            ),
        ),
        constraints=(
            TimeWindowRequirement(
                constraint_id="constraint-morning",
                subject_obligation_ids=("obl-wuhou",),
                evidence=(evidence,),
                strength=ConstraintStrength.REQUIRED,
                fixed_commitment=fixed,
                day_number=1,
                earliest=time(8, 0),
                latest=time(12, 0),
            ),
            DayAssignmentRequirement(
                constraint_id="constraint-booked-day", subject_obligation_ids=("obl-wuhou",),
                evidence=(evidence,), strength=ConstraintStrength.REQUIRED,
                fixed_commitment=fixed, day_number=2,
            ),
            PaceRequirement(
                constraint_id="constraint-pace",
                evidence=(
                    EvidenceSpan(
                        source_id="source-1",
                        start=7,
                        end=9,
                        text="轻松",
                    ),
                ),
                strength=ConstraintStrength.REQUIRED,
                max_day_minutes=180,
            ),
            TransportPreferenceRequirement(
                constraint_id="constraint-transport",
                evidence=(
                    EvidenceSpan(
                        source_id="source-1",
                        start=10,
                        end=14,
                        text="步行优先",
                    ),
                ),
                strength=ConstraintStrength.REQUIRED,
                preferred_modes=(TravelMode.WALK,),
                max_walk_minutes=15,
            ),
        ),
    )
    hotel_depart = datetime(2026, 8, 1, 12, 40)
    wuhou_arrive = datetime(2026, 8, 1, 13, 0)
    wuhou_depart = datetime(2026, 8, 1, 17, 0)
    hotel_arrive = datetime(2026, 8, 1, 17, 20)
    timeline = CompiledTimeline(
        snapshot_id="timeline-1",
        draft_id="draft-1",
        draft_revision=1,
        days=(
            DayTimeline(
                day_number=1,
                calendar_date=date(2026, 8, 1),
                title="武侯祠",
                stops=(
                    TimelineStop(
                        stop_id="hotel",
                        candidate_id="cand-hotel",
                        name="酒店",
                        kind=StopKind.LODGING,
                        arrival_at=hotel_depart,
                        departure_at=hotel_depart,
                        stay_duration_min=0,
                    ),
                    TimelineStop(
                        stop_id="wuhou",
                        candidate_id="cand-wuhou",
                        obligation_ids=("obl-wuhou",),
                        name="武侯祠",
                        kind=StopKind.VISIT,
                        arrival_at=wuhou_arrive,
                        departure_at=wuhou_depart,
                        stay_duration_min=240,
                    ),
                    TimelineStop(
                        stop_id="hotel-return",
                        candidate_id="cand-hotel",
                        name="酒店",
                        kind=StopKind.LODGING,
                        arrival_at=hotel_arrive,
                        departure_at=hotel_arrive,
                        stay_duration_min=0,
                    ),
                ),
                legs=(
                    TimelineLeg(
                        leg_id="leg-1",
                        from_stop_id="hotel",
                        to_stop_id="wuhou",
                        departure_at=hotel_depart,
                        arrival_at=wuhou_arrive,
                        duration_min=20,
                        mode="taxi",
                        fact_id="route-1",
                        fact_source=RouteFactSource.AMAP,
                        fact_status=FactResolutionStatus.VERIFIED,
                    ),
                    TimelineLeg(
                        leg_id="leg-2",
                        from_stop_id="wuhou",
                        to_stop_id="hotel-return",
                        departure_at=wuhou_depart,
                        arrival_at=hotel_arrive,
                        duration_min=20,
                        mode="taxi",
                        fact_id="route-2",
                        fact_source=RouteFactSource.AMAP,
                        fact_status=FactResolutionStatus.VERIFIED,
                    ),
                ),
            ),
        ),
    )

    status, issues = evaluate_timeline_constraints(
        ledger=ledger, timeline=timeline
    )

    assert status.value == ("conflict" if fixed else "needs_adjustment")
    assert issues[0].code == "required_time_window_violated"
    assert issues[0].severity == ("blocking" if fixed else "review")
    assert issues[1].code == "required_day_assignment_violated"
    assert issues[1].severity == ("blocking" if fixed else "review")
    assert all(issue.severity == "review" for issue in issues[2:])
    assert issues[0].day_numbers == (1,)
    assert issues[0].obligation_ids == ("obl-wuhou",)
    assert "13:00" in issues[0].message
    assert "上午" in issues[0].message
    pace_issue = next(
        issue for issue in issues if issue.code == "required_pace_limit_violated"
    )
    assert "280 分钟" in pace_issue.message
    assert "第 1 天" in pace_issue.recommendation
    transport_issues = [
        issue
        for issue in issues
        if issue.code == "required_transport_mode_violated"
    ]
    assert transport_issues == []
    # An omitted appointment must remain blocking even when its place has an
    # explicit disposition and therefore does not appear in timeline locations.
    omitted_timeline = timeline.model_copy(update={"days": tuple(
        day.model_copy(update={"stops": tuple(
            stop.model_copy(update={"obligation_ids": (), "candidate_id": "unrelated"})
            for stop in day.stops
        )}) for day in timeline.days
    )})
    omitted_status, omitted_issues = evaluate_timeline_constraints(ledger=ledger, timeline=omitted_timeline)
    missing = tuple(issue for issue in omitted_issues if issue.code == "fixed_commitment_not_scheduled")
    assert omitted_status.value == ("conflict" if fixed else "needs_adjustment")
    assert len(missing) == (2 if fixed else 0)
    assert all(issue.severity == "blocking" and issue.place_names == ("武侯祠",) for issue in missing)


def test_repeated_anchor_candidate_satisfies_each_assigned_day() -> None:
    obligation = PlaceObligation(
        obligation_id="obl-hotel",
        mention="成都瑞吉酒店",
        role=PlaceRole.LODGING,
        priority=ObligationPriority.REQUIRED,
        evidence=(
            EvidenceSpan(
                source_id="source-1",
                start=0,
                end=6,
                text="成都瑞吉酒店",
            ),
        ),
        query_decision=QueryDecision.QUERY,
        query_text="成都瑞吉酒店",
    )
    ledger = RequirementLedger(
        ledger_id="ledger-anchor",
        goal_revision_id="goal-anchor",
        obligations=(obligation,),
        constraints=tuple(
            DayAssignmentRequirement(
                constraint_id=f"constraint-day-{day_number}",
                subject_obligation_ids=("obl-hotel",),
                evidence=(
                    EvidenceSpan(
                        source_id="source-1",
                        start=7 + day_number,
                        end=8 + day_number,
                        text=str(day_number),
                    ),
                ),
                strength=ConstraintStrength.REQUIRED,
                day_number=day_number,
            )
            for day_number in (1, 2)
        ),
    )
    days = []
    for day_number in (1, 2):
        started_at = datetime(2026, 8, day_number, 9, 0)
        first = TimelineStop(
            stop_id=f"hotel-{day_number}-start",
            candidate_id="cand-hotel",
            obligation_ids=(
                ("obl-hotel",) if day_number == 1 else ()
            ),
            name="成都瑞吉酒店",
            kind=StopKind.LODGING,
            arrival_at=started_at,
            departure_at=started_at,
            stay_duration_min=0,
        )
        second = TimelineStop(
            stop_id=f"hotel-{day_number}-end",
            candidate_id="cand-hotel",
            name="成都瑞吉酒店",
            kind=StopKind.LODGING,
            arrival_at=started_at,
            departure_at=started_at,
            stay_duration_min=0,
        )
        days.append(
            DayTimeline(
                day_number=day_number,
                calendar_date=date(2026, 8, day_number),
                title=f"第 {day_number} 天",
                stops=(first, second),
                legs=(
                    TimelineLeg(
                        leg_id=f"leg-{day_number}",
                        from_stop_id=first.stop_id,
                        to_stop_id=second.stop_id,
                        departure_at=started_at,
                        arrival_at=started_at,
                        duration_min=0,
                        mode="stay",
                        fact_id=f"fact-{day_number}",
                        fact_source=RouteFactSource.AMAP,
                        fact_status=FactResolutionStatus.VERIFIED,
                    ),
                ),
            )
        )
    timeline = CompiledTimeline(
        snapshot_id="timeline-anchor",
        draft_id="draft-anchor",
        draft_revision=1,
        days=tuple(days),
    )

    status, issues = evaluate_timeline_constraints(
        ledger=ledger,
        timeline=timeline,
    )

    assert status.value == "good"
    assert issues == ()
