from __future__ import annotations

from datetime import date, datetime

from app.trip_agent_v3.context import (
    build_grounding_context,
    build_planning_context,
)
from app.trip_agent_v3.domain.grounding import (
    CandidateEntityKind,
    CandidateSet,
    GroundingCandidate,
    GroundingRegistry,
    ResolutionStatus,
    TargetResolution,
)
from app.trip_agent_v3.domain.requirements import (
    ConstraintStrength,
    EvidenceSpan,
    ObligationPriority,
    PlaceObligation,
    PlaceRole,
    QueryDecision,
    RequirementLedger,
    TransportPreferenceRequirement,
    TravelMode,
)
from app.trip_agent_v3.domain.plan import (
    CompiledTimeline,
    DayTimeline,
    StopKind,
    TimelineLeg,
    TimelineStop,
)
from app.trip_agent_v3.domain.facts import (
    FactResolutionStatus,
    RouteFactSource,
)


def _obligation(index: int) -> PlaceObligation:
    mention = f"地点{index}"
    return PlaceObligation(
        obligation_id=f"obl-{index}",
        proposal_key=f"key-{index}",
        mention=mention,
        role=PlaceRole.VISIT,
        priority=ObligationPriority.PREFERRED,
        evidence=(
            EvidenceSpan(
                source_id="source",
                start=0,
                end=len(mention),
                text=mention,
            ),
        ),
        query_decision=QueryDecision.QUERY,
        query_text=mention,
    )


def _candidate_set(index: int) -> CandidateSet:
    return CandidateSet(
        target_id=f"query-{index}",
        obligation_ids=(f"obl-{index}",),
        query_text=f"地点{index}",
        candidates=(
            GroundingCandidate(
                candidate_id=f"cand-{index}",
                provider="amap",
                provider_place_id=f"B{index}",
                name=f"真实地点{index}",
                entity_kind=CandidateEntityKind.PLACE,
                longitude=104.0 + index / 100,
                latitude=30.0 + index / 100,
            ),
        ),
        provider_result_count=1,
        eligible_result_count=1,
    )


def test_grounding_context_contains_only_active_candidate_group() -> None:
    ledger = RequirementLedger(
        ledger_id="ledger-1",
        goal_revision_id="goal-1",
        obligations=tuple(_obligation(index) for index in range(12)),
    )
    context = build_grounding_context(
        run_id="run-1",
        ledger=ledger,
        active_candidate_set=_candidate_set(7),
    )
    payload = context.model_dump(mode="json")

    assert payload["active_candidate_set"]["target_id"] == "query-7"
    assert len(payload["open_obligations"]) == 10
    assert payload["omitted_open_obligation_count"] == 2
    serialized = context.model_dump_json()
    assert "query-6" not in serialized
    assert "query-8" not in serialized
    assert "source content" not in serialized
    assert "old_drafts" not in serialized


def test_planning_context_reduces_grounding_to_selected_place_summaries() -> None:
    sets = tuple(_candidate_set(index) for index in range(3))
    registry = GroundingRegistry(
        registry_id="registry-1",
        query_plan_id="query-plan-1",
        candidate_sets=sets,
        resolutions=tuple(
            TargetResolution(
                target_id=item.target_id,
                obligation_ids=item.obligation_ids,
                status=ResolutionStatus.SELECTED,
                selected_candidate_id=item.candidates[0].candidate_id,
                rationale="名称、城市、类别一致。",
                confidence=0.95,
                selection_factors=("name", "city", "category"),
            )
            for item in sets
        ),
    )
    ledger = RequirementLedger(
        ledger_id="ledger-1",
        goal_revision_id="goal-1",
        obligations=tuple(_obligation(index) for index in range(3)),
        constraints=(
            TransportPreferenceRequirement(
                constraint_id="transport-1",
                evidence=(
                    EvidenceSpan(
                        source_id="source",
                        start=0,
                        end=4,
                        text="步行优先",
                    ),
                ),
                strength=ConstraintStrength.REQUIRED,
                preferred_modes=(TravelMode.WALK, TravelMode.TAXI),
                max_walk_minutes=20,
            ),
        ),
    )

    context = build_planning_context(
        run_id="run-1",
        ledger=ledger,
        grounding=registry,
        active_day_number=1,
    )

    assert len(context.selected_places) == 3
    assert context.active_candidate_set is None
    assert context.constraints[0].kind == "transport_preference"
    assert context.constraints[0].preferred_modes == (
        TravelMode.WALK,
        TravelMode.TAXI,
    )
    assert context.constraints[0].max_walk_minutes == 20
    serialized = context.model_dump_json()
    assert "selection_factors" not in serialized
    assert "provider_result_count" not in serialized
    assert "candidate_sets" not in serialized


def test_repair_context_contains_only_current_day_timeline_slice() -> None:
    candidate_set = _candidate_set(0)
    registry = GroundingRegistry(
        registry_id="registry-1",
        query_plan_id="query-plan-1",
        candidate_sets=(candidate_set,),
        resolutions=(
            TargetResolution(
                target_id=candidate_set.target_id,
                obligation_ids=candidate_set.obligation_ids,
                status=ResolutionStatus.SELECTED,
                selected_candidate_id=candidate_set.candidates[0].candidate_id,
                rationale="名称一致。",
                confidence=0.95,
                selection_factors=("name",),
            ),
        ),
    )
    ledger = RequirementLedger(
        ledger_id="ledger-1",
        goal_revision_id="goal-1",
        obligations=(_obligation(0),),
    )
    hotel_start = TimelineStop(
        stop_id="hotel-start",
        candidate_id="hotel",
        name="测试酒店",
        kind=StopKind.LODGING,
        arrival_at=datetime(2026, 8, 1, 9, 0),
        departure_at=datetime(2026, 8, 1, 9, 0),
        stay_duration_min=0,
    )
    visit = TimelineStop(
        stop_id="visit",
        candidate_id="cand-0",
        obligation_ids=("obl-0",),
        name="真实地点0",
        kind=StopKind.VISIT,
        arrival_at=datetime(2026, 8, 1, 9, 20),
        departure_at=datetime(2026, 8, 1, 10, 20),
        stay_duration_min=60,
    )
    timeline = CompiledTimeline(
        snapshot_id="timeline-1",
        draft_id="draft-1",
        draft_revision=1,
        days=(
            DayTimeline(
                day_number=1,
                calendar_date=date(2026, 8, 1),
                title="测试日",
                stops=(hotel_start, visit),
                legs=(
                    TimelineLeg(
                        leg_id="leg-1",
                        from_stop_id="hotel-start",
                        to_stop_id="visit",
                        departure_at=datetime(2026, 8, 1, 9, 0),
                        arrival_at=datetime(2026, 8, 1, 9, 20),
                        duration_min=20,
                        mode="taxi",
                        fact_id="fact-1",
                        fact_source=RouteFactSource.AMAP,
                        fact_status=FactResolutionStatus.VERIFIED,
                    ),
                ),
            ),
        ),
    )

    context = build_planning_context(
        run_id="run-1",
        ledger=ledger,
        grounding=registry,
        active_day_number=1,
        timeline=timeline,
        feedback=("地点在 09:00 前未营业。",),
    )

    assert [item.name for item in context.current_day_stops] == [
        "测试酒店",
        "真实地点0",
    ]
    assert context.feedback == ("地点在 09:00 前未营业。",)
