from __future__ import annotations

from datetime import date, datetime

import pytest

from app.trip_agent_v3.delivery import (
    ReleasePublicationError,
    assess_delivery,
    publish_release,
    release_for_run,
)
from app.trip_agent_v3.domain.delivery import (
    CandidateSnapshot,
    DeliveryState,
    ExperienceIssue,
    ExperienceStatus,
    FactStatus,
    RunRecord,
    RunStatus,
)
from app.trip_agent_v3.domain.facts import (
    FactGap,
    FactNeedKind,
    FactGapReport,
    FactSourceRecord,
    OperationalClaim,
    OperationalFact,
)
from app.trip_agent_v3.domain.plan import (
    CompiledTimeline,
    DayTimeline,
    StopKind,
    TimelineLeg,
    TimelineStop,
)
from app.trip_agent_v3.domain.requirements import (
    CoverageEntry,
    CoverageReport,
    DispositionStatus,
    ObligationPriority,
    PlaceRole,
)
from app.trip_agent_v3.domain.facts import (
    FactResolutionStatus,
    RouteFactSource,
)


def _timeline(
    *, fact_status: FactResolutionStatus = FactResolutionStatus.VERIFIED
) -> CompiledTimeline:
    start = datetime(2026, 8, 1, 9, 0)
    arrive = datetime(2026, 8, 1, 9, 20)
    depart = datetime(2026, 8, 1, 10, 50)
    end = datetime(2026, 8, 1, 11, 10)
    return CompiledTimeline(
        snapshot_id="timeline-1",
        draft_id="draft-1",
        draft_revision=1,
        days=(
            DayTimeline(
                day_number=1,
                calendar_date=date(2026, 8, 1),
                title="武侯祠半日",
                stops=(
                    TimelineStop(
                        stop_id="hotel-start",
                        candidate_id="cand-hotel",
                        name="酒店",
                        kind=StopKind.LODGING,
                        arrival_at=start,
                        departure_at=start,
                        stay_duration_min=0,
                    ),
                    TimelineStop(
                        stop_id="wuhou",
                        candidate_id="cand-wuhou",
                        obligation_ids=("obl-wuhou",),
                        name="武侯祠",
                        kind=StopKind.VISIT,
                        arrival_at=arrive,
                        departure_at=depart,
                        stay_duration_min=90,
                    ),
                    TimelineStop(
                        stop_id="hotel-end",
                        candidate_id="cand-hotel",
                        name="酒店",
                        kind=StopKind.LODGING,
                        arrival_at=end,
                        departure_at=end,
                        stay_duration_min=0,
                    ),
                ),
                legs=(
                    TimelineLeg(
                        leg_id="leg-1",
                        from_stop_id="hotel-start",
                        to_stop_id="wuhou",
                        departure_at=start,
                        arrival_at=arrive,
                        duration_min=20,
                        mode="taxi",
                        fact_id="route-1",
                        fact_source=(
                            RouteFactSource.AMAP
                            if fact_status is FactResolutionStatus.VERIFIED
                            else RouteFactSource.SPATIAL_ESTIMATE
                        ),
                        fact_status=fact_status,
                    ),
                    TimelineLeg(
                        leg_id="leg-2",
                        from_stop_id="wuhou",
                        to_stop_id="hotel-end",
                        departure_at=depart,
                        arrival_at=end,
                        duration_min=20,
                        mode="taxi",
                        fact_id="route-2",
                        fact_source=(
                            RouteFactSource.AMAP
                            if fact_status is FactResolutionStatus.VERIFIED
                            else RouteFactSource.SPATIAL_ESTIMATE
                        ),
                        fact_status=fact_status,
                    ),
                ),
            ),
        ),
    )


def _coverage(
    status: DispositionStatus = DispositionStatus.SCHEDULED,
) -> CoverageReport:
    disposed = status is not DispositionStatus.UNHANDLED
    return CoverageReport(
        explicit_place_count=1,
        disposed_place_count=1 if disposed else 0,
        coverage_ratio=1 if disposed else 0,
        open_obligation_ids=() if disposed else ("obl-wuhou",),
        entries=(
            CoverageEntry(
                obligation_id="obl-wuhou",
                mention="武侯祠",
                role=PlaceRole.VISIT,
                priority=ObligationPriority.REQUIRED,
                status=status,
                stop_id="wuhou" if status is DispositionStatus.SCHEDULED else None,
                reason_code=(
                    None
                    if status is DispositionStatus.SCHEDULED
                    else "capacity_conflict"
                ),
                rationale=(
                    None
                    if status is DispositionStatus.SCHEDULED
                    else "当天容量不足。"
                ),
            ),
        ),
    )


def _candidate(
    *,
    fact_status: FactStatus = FactStatus.VERIFIED,
    experience_status: ExperienceStatus = ExperienceStatus.GOOD,
    coverage: CoverageReport | None = None,
    experience_issues: tuple[ExperienceIssue, ...] = (),
    timeline_fact_status: FactResolutionStatus = FactResolutionStatus.VERIFIED,
) -> CandidateSnapshot:
    source = FactSourceRecord(
        source_id="source-wuhou",
        provider="test",
        uri="https://example.test/wuhou",
        title="武侯祠运营信息",
        excerpt="计划到访时段开放。",
        content_hash="a" * 64,
        retrieved_at=datetime(2026, 7, 30, 9, 0),
    )
    return CandidateSnapshot(
        candidate_snapshot_id="candidate-1",
        workspace_id="workspace-1",
        producing_run_id="run-current",
        goal_revision_id="goal-1",
        requirement_ledger_id="ledger-1",
        grounding_registry_id="grounding-1",
        timeline=_timeline(fact_status=timeline_fact_status),
        coverage=coverage or _coverage(),
        fact_gap_report=FactGapReport(
            fact_need_plan_id="facts-1",
            gaps=(),
        ),
        operational_facts=(
            OperationalFact(
                fact_id="operation-wuhou",
                candidate_id="cand-wuhou",
                stop_id="wuhou",
                visit_at=datetime(2026, 8, 1, 9, 20),
                claims=(
                    OperationalClaim(
                        field="opening_hours",
                        value="计划到访时段开放",
                        source_ids=(source.source_id,),
                        confidence=1,
                    ),
                ),
                sources=(source,),
            ),
        ),
        fact_status=fact_status,
        experience_status=experience_status,
        experience_issues=experience_issues,
    )


def _run(status: RunStatus = RunStatus.SUCCEEDED) -> RunRecord:
    return RunRecord(
        run_id="run-current",
        workspace_id="workspace-1",
        goal_revision_id="goal-1",
        status=status,
    )


def test_only_verified_good_closed_candidate_is_publishable() -> None:
    assessment = assess_delivery(run=_run(), candidate=_candidate())

    assert assessment.state is DeliveryState.PUBLISHABLE
    assert assessment.may_publish is True
    assert assessment.issues == ()


@pytest.mark.parametrize("code", [
    "operational_sources_missing", "operational_evidence_insufficient",
    "operational_extraction_failed", "planned_visit_operationally_incompatible",
    "scheduled_candidate_missing",
])
def test_operating_information_availability_and_visit_conflicts(code) -> None:
    candidate = _candidate().model_copy(update={
        "fact_status": FactStatus.DEGRADED,
        "operational_facts": (),
        "fact_gap_report": FactGapReport(fact_need_plan_id="operations", gaps=(FactGap(
            need_id="hours", kind=FactNeedKind.PLACE_OPERATION, day_number=1,
            stop_ids=("stop-visit",), place_names=("武侯祠",),
            failure_code=code, failure_message="运营查询结果。",
            still_scheduled=True, impact="运营信息。",
        ),)),
    })
    assessment = assess_delivery(run=_run(), candidate=candidate)
    expected = code in {
        "operational_sources_missing", "operational_evidence_insufficient",
        "operational_extraction_failed",
    }
    assert assessment.may_publish is expected
    if expected:
        release = publish_release(release_id="release-partial-hours", run=_run(),
                                  candidate=candidate, assessment=assessment)
        assert release.candidate_snapshot_id == candidate.candidate_snapshot_id


def test_sourced_operating_information_with_unspecified_window_can_publish():
    base = _candidate()
    candidate = base.model_copy(update={
        "fact_status": FactStatus.DEGRADED,
        "operational_facts": (base.operational_facts[0].model_copy(update={"visit_compatible": None}),),
    })
    assessment = assess_delivery(run=_run(), candidate=candidate)
    assert assessment.may_publish is True
    assert candidate.fact_status is FactStatus.DEGRADED


def test_degraded_fact_or_estimated_leg_cannot_be_immutable_release() -> None:
    assessment = assess_delivery(
        run=_run(),
        candidate=_candidate(
            fact_status=FactStatus.DEGRADED,
            timeline_fact_status=FactResolutionStatus.ESTIMATED,
        ),
    )

    assert assessment.state is DeliveryState.REVIEW_REQUIRED
    assert assessment.may_publish is False
    assert {issue.code for issue in assessment.issues} == {
        "estimated_route_leg",
    }
    assert assessment.issues[0].place_names == ("酒店", "武侯祠")
    assert assessment.issues[0].day_numbers == (1,)


def test_experience_advice_is_preserved_in_a_publishable_trip() -> None:
    issue = ExperienceIssue(
        code="day_too_long",
        message="第 1 天从 09:00 持续到 22:30，超过低强度目标。",
        recommendation="移除晚间购物或拆到第 2 天。",
        day_numbers=(1,),
        place_names=("IFS",),
    )
    candidate = _candidate(
            experience_status=ExperienceStatus.NEEDS_ADJUSTMENT,
            experience_issues=(issue,),
    )
    assessment = assess_delivery(run=_run(), candidate=candidate)

    assert assessment.state is DeliveryState.PUBLISHABLE
    assert assessment.may_publish is True
    assert candidate.experience_status is ExperienceStatus.NEEDS_ADJUSTMENT
    assert assessment.issues[0].message.startswith("第 1 天")
    assert assessment.issues[0].recommendation.startswith("移除")
    release = publish_release(
        release_id="release-with-advice", run=_run(),
        candidate=candidate, assessment=assessment,
    )
    assert release.producing_run_id == _run().run_id


def test_advice_cannot_hide_an_unverified_route() -> None:
    assessment = assess_delivery(
        run=_run(),
        candidate=_candidate(
            timeline_fact_status=FactResolutionStatus.ESTIMATED,
            fact_status=FactStatus.DEGRADED,
            experience_status=ExperienceStatus.NEEDS_ADJUSTMENT,
            experience_issues=(ExperienceIssue(
                code="day_too_long", message="第 1 天行程较长。",
                recommendation="减少一个停留点。", day_numbers=(1,),
            ),),
        ),
    )
    assert assessment.state is DeliveryState.REVIEW_REQUIRED
    assert assessment.may_publish is False
    assert {item.code for item in assessment.issues} == {
        "estimated_route_leg", "day_too_long",
    }


def test_explicit_hard_conflict_still_prevents_publication() -> None:
    assessment = assess_delivery(
        run=_run(),
        candidate=_candidate(
            experience_status=ExperienceStatus.CONFLICT,
            experience_issues=(ExperienceIssue(
                severity="blocking",
                code="required_time_window_violated",
                message="第 1 天未赶上固定预约时间。",
                recommendation="调整出发时间。", day_numbers=(1,),
            ),),
        ),
    )
    assert assessment.state is DeliveryState.BLOCKED
    assert assessment.may_publish is False


def test_fixed_booking_does_not_turn_ordinary_advice_into_blockers() -> None:
    assessment = assess_delivery(
        run=_run(),
        candidate=_candidate(
            experience_status=ExperienceStatus.CONFLICT,
            experience_issues=(
                ExperienceIssue(
                    severity="blocking", code="fixed_booking_missed",
                    message="第 1 天未赶上已确认的预约。",
                    recommendation="调整出发时间。", day_numbers=(1,),
                ),
                ExperienceIssue(
                    code="preferred_transport_mode_deviated",
                    message="第 1 天有一段短途步行。",
                    recommendation="可按需打车。", day_numbers=(1,),
                ),
            ),
        ),
    )
    assert assessment.state is DeliveryState.BLOCKED
    assert [(item.code, item.severity.value) for item in assessment.issues] == [
        ("fixed_booking_missed", "blocking"),
        ("preferred_transport_mode_deviated", "review"),
    ]


def test_ordinary_place_omission_keeps_specific_advice() -> None:
    assessment = assess_delivery(
        run=_run(),
        candidate=_candidate(
            coverage=_coverage(DispositionStatus.NOT_SCHEDULED)
        ),
    )

    assert assessment.state is DeliveryState.PUBLISHABLE
    assert assessment.may_publish is True
    assert assessment.issues[0].severity.value == "review"
    assert assessment.issues[0].obligation_ids == ("obl-wuhou",)
    assert assessment.issues[0].place_names == ("武侯祠",)


def test_preferred_meal_omission_preserves_advice_without_blocking() -> None:
    coverage = CoverageReport(
        explicit_place_count=1,
        disposed_place_count=1,
        coverage_ratio=1,
        entries=(
            CoverageEntry(
                obligation_id="obl-meal",
                mention="陈麻婆豆腐",
                role=PlaceRole.MEAL,
                priority=ObligationPriority.PREFERRED,
                status=DispositionStatus.NOT_SCHEDULED,
                reason_code="capacity_conflict",
                rationale="当天容量不足。",
            ),
        ),
    )

    assessment = assess_delivery(
        run=_run(),
        candidate=_candidate(coverage=coverage),
    )

    assert assessment.state is DeliveryState.PUBLISHABLE
    assert assessment.may_publish is True
    issue = next(
        item
        for item in assessment.issues
        if item.code == "named_meal_not_scheduled"
    )
    assert issue.obligation_ids == ("obl-meal",)
    assert "陈麻婆豆腐" in issue.message
    assert issue.severity.value == "review"


def test_cancelled_run_cannot_publish_and_old_workspace_release_is_not_returned() -> None:
    publishable = assess_delivery(run=_run(), candidate=_candidate())
    release = publish_release(
        release_id="release-old",
        run=_run().model_copy(update={"run_id": "run-old"}),
        candidate=_candidate().model_copy(
            update={"producing_run_id": "run-old"}
        ),
        assessment=publishable.model_copy(
            update={"producing_run_id": "run-old"}
        ),
    )

    assert release_for_run((release,), run_id="run-current") is None
    cancelled_assessment = assess_delivery(
        run=_run(RunStatus.CANCELLED),
        candidate=_candidate(),
    )
    assert cancelled_assessment.state is DeliveryState.BLOCKED
    with pytest.raises(ReleasePublicationError):
        publish_release(
            release_id="release-current",
            run=_run(RunStatus.CANCELLED),
            candidate=_candidate(),
            assessment=cancelled_assessment,
        )
