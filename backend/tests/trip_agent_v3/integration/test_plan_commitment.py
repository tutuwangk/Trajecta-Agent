from __future__ import annotations

from datetime import date, time

import pytest

from app.trip_agent_v3.commitments import (
    PlanCommitmentError,
    commit_plan_dispositions,
)
from app.trip_agent_v3.domain.grounding import (
    CandidateEntityKind,
    CandidateSet,
    GroundingCandidate,
    GroundingRegistry,
    ResolutionStatus,
    TargetResolution,
)
from app.trip_agent_v3.domain.plan import (
    DraftDay,
    DraftStop,
    StopKind,
    WorkingDraft,
)
from app.trip_agent_v3.domain.requirements import (
    CoverageDisposition,
    DispositionStatus,
    EvidenceSpan,
    ObligationPriority,
    PlaceObligation,
    PlaceRole,
    QueryDecision,
    RequirementLedger,
)


def _obligation(
    obligation_id: str, mention: str, role: PlaceRole
) -> PlaceObligation:
    return PlaceObligation(
        obligation_id=obligation_id,
        mention=mention,
        role=role,
        priority=ObligationPriority.REQUIRED,
        evidence=(
            EvidenceSpan(
                source_id="source-1",
                start=0,
                end=len(mention),
                text=mention,
            ),
        ),
        query_decision=QueryDecision.QUERY,
        query_text=mention,
    )


def _candidate(
    candidate_id: str, provider_id: str, name: str
) -> GroundingCandidate:
    return GroundingCandidate(
        candidate_id=candidate_id,
        provider="amap",
        provider_place_id=provider_id,
        name=name,
        entity_kind=CandidateEntityKind.PLACE,
        longitude=104.08,
        latitude=30.65,
    )


def _registry() -> GroundingRegistry:
    sets = (
        CandidateSet(
            target_id="q-hotel",
            obligation_ids=("obl-hotel",),
            query_text="酒店",
            candidates=(_candidate("cand-hotel", "B-hotel", "酒店"),),
            provider_result_count=1,
            eligible_result_count=1,
        ),
        CandidateSet(
            target_id="q-meal",
            obligation_ids=("obl-meal",),
            query_text="陈麻婆豆腐",
            candidates=(
                _candidate("cand-meal", "B-meal", "陈麻婆豆腐(骡马市店)"),
            ),
            provider_result_count=1,
            eligible_result_count=1,
        ),
    )
    return GroundingRegistry(
        registry_id="grounding-1",
        query_plan_id="query-plan-1",
        candidate_sets=sets,
        resolutions=tuple(
            TargetResolution(
                target_id=item.target_id,
                obligation_ids=item.obligation_ids,
                status=ResolutionStatus.SELECTED,
                selected_candidate_id=item.candidates[0].candidate_id,
                rationale="名称和城市匹配。",
                confidence=0.95,
                selection_factors=("name", "city"),
            )
            for item in sets
        ),
    )


def _draft(meal_candidate_id: str = "cand-meal") -> WorkingDraft:
    return WorkingDraft(
        draft_id="draft-1",
        goal_revision_id="goal-1",
        revision=1,
        days=(
            DraftDay(
                day_number=1,
                calendar_date=date(2026, 8, 1),
                title="指定川菜午餐",
                start_time=time(9, 0),
                stops=(
                    DraftStop(
                        stop_id="hotel-start",
                        candidate_id="cand-hotel",
                        obligation_ids=("obl-hotel",),
                        name="酒店",
                        kind=StopKind.LODGING,
                        stay_duration_min=0,
                        rationale="出发锚点。",
                    ),
                    DraftStop(
                        stop_id="meal",
                        candidate_id=meal_candidate_id,
                        obligation_ids=("obl-meal",),
                        name="陈麻婆豆腐(骡马市店)",
                        kind=StopKind.MEAL,
                        stay_duration_min=60,
                        rationale="用户明确指定。",
                    ),
                    DraftStop(
                        stop_id="hotel-end",
                        candidate_id="cand-hotel",
                        name="酒店",
                        kind=StopKind.LODGING,
                        stay_duration_min=0,
                        rationale="返程锚点。",
                    ),
                ),
            ),
        ),
    )


def _ledger() -> RequirementLedger:
    return RequirementLedger(
        ledger_id="ledger-1",
        goal_revision_id="goal-1",
        obligations=(
            _obligation("obl-hotel", "酒店", PlaceRole.LODGING),
            _obligation("obl-meal", "陈麻婆豆腐", PlaceRole.MEAL),
        ),
    )


def test_every_explicit_place_gets_a_checked_disposition() -> None:
    committed = commit_plan_dispositions(
        ledger=_ledger(),
        grounding=_registry(),
        draft=_draft(),
        dispositions=(
            CoverageDisposition(
                obligation_id="obl-hotel",
                status=DispositionStatus.SCHEDULED,
                stop_id="hotel-start",
            ),
            CoverageDisposition(
                obligation_id="obl-meal",
                status=DispositionStatus.SCHEDULED,
                stop_id="meal",
            ),
        ),
    )

    assert committed.coverage_report().coverage_ratio == 1
    assert committed.coverage_report().open_obligation_ids == ()


def test_merged_mentions_share_one_selected_candidate_and_stop() -> None:
    root = _obligation("obl-meal", "陈麻婆豆腐", PlaceRole.MEAL)
    alias = PlaceObligation(
        obligation_id="obl-meal-alias",
        mention="陈麻婆",
        role=PlaceRole.MEAL,
        priority=ObligationPriority.REQUIRED,
        evidence=(
            EvidenceSpan(
                source_id="source-1",
                start=10,
                end=13,
                text="陈麻婆",
            ),
        ),
        query_decision=QueryDecision.MERGE,
        decision_reason="同一餐厅的简称。",
        merged_into_obligation_id=root.obligation_id,
    )
    ledger = RequirementLedger(
        ledger_id="ledger-merged",
        goal_revision_id="goal-1",
        obligations=(
            _obligation("obl-hotel", "酒店", PlaceRole.LODGING),
            root,
            alias,
        ),
    )
    registry = _registry()
    meal_set = registry.candidate_sets[1].model_copy(
        update={
            "obligation_ids": ("obl-meal", "obl-meal-alias")
        }
    )
    meal_resolution = registry.resolutions[1].model_copy(
        update={
            "obligation_ids": ("obl-meal", "obl-meal-alias")
        }
    )
    registry = registry.model_copy(
        update={
            "candidate_sets": (
                registry.candidate_sets[0],
                meal_set,
            ),
            "resolutions": (
                registry.resolutions[0],
                meal_resolution,
            ),
        }
    )
    draft = _draft()
    meal_stop = draft.days[0].stops[1].model_copy(
        update={"obligation_ids": ("obl-meal", "obl-meal-alias")}
    )
    draft = draft.model_copy(
        update={
            "days": (
                draft.days[0].model_copy(
                    update={
                        "stops": (
                            draft.days[0].stops[0],
                            meal_stop,
                            draft.days[0].stops[2],
                        )
                    }
                ),
            )
        }
    )

    committed = commit_plan_dispositions(
        ledger=ledger,
        grounding=registry,
        draft=draft,
        dispositions=(
            CoverageDisposition(
                obligation_id="obl-hotel",
                status=DispositionStatus.SCHEDULED,
                stop_id="hotel-start",
            ),
            CoverageDisposition(
                obligation_id="obl-meal",
                status=DispositionStatus.SCHEDULED,
                stop_id="meal",
            ),
            CoverageDisposition(
                obligation_id="obl-meal-alias",
                status=DispositionStatus.SCHEDULED,
                stop_id="meal",
            ),
        ),
    )

    assert committed.coverage_report().coverage_ratio == 1
    assert committed.dispositions[1].stop_id == committed.dispositions[2].stop_id


def test_draft_cannot_schedule_candidate_not_selected_by_grounding() -> None:
    with pytest.raises(PlanCommitmentError, match="not a selected grounding"):
        commit_plan_dispositions(
            ledger=_ledger(),
            grounding=_registry(),
            draft=_draft(meal_candidate_id="cand-unresolved"),
            dispositions=(
                CoverageDisposition(
                    obligation_id="obl-hotel",
                    status=DispositionStatus.SCHEDULED,
                    stop_id="hotel-start",
                ),
                CoverageDisposition(
                    obligation_id="obl-meal",
                    status=DispositionStatus.SCHEDULED,
                    stop_id="meal",
                ),
            ),
        )


def test_meal_obligation_cannot_hide_in_non_meal_stop() -> None:
    bad_draft = _draft()
    meal_stop = bad_draft.days[0].stops[1].model_copy(
        update={"kind": StopKind.VISIT}
    )
    bad_draft = bad_draft.model_copy(
        update={
            "days": (
                bad_draft.days[0].model_copy(
                    update={
                        "stops": (
                            bad_draft.days[0].stops[0],
                            meal_stop,
                            bad_draft.days[0].stops[2],
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(PlanCommitmentError, match="meal obligation"):
        commit_plan_dispositions(
            ledger=_ledger(),
            grounding=_registry(),
            draft=bad_draft,
            dispositions=(
                CoverageDisposition(
                    obligation_id="obl-hotel",
                    status=DispositionStatus.SCHEDULED,
                    stop_id="hotel-start",
                ),
                CoverageDisposition(
                    obligation_id="obl-meal",
                    status=DispositionStatus.SCHEDULED,
                    stop_id="meal",
                ),
            ),
        )
