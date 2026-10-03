from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.trip_agent_v3.domain.grounding import (
    CandidateEntityKind,
    CandidateSet,
    GroundingCandidate,
    GroundingRegistry,
    ResolutionStatus,
    TargetResolution,
)


def _candidate(
    candidate_id: str,
    provider_place_id: str,
    name: str,
    *,
    entity_kind: CandidateEntityKind = CandidateEntityKind.PLACE,
) -> GroundingCandidate:
    return GroundingCandidate(
        candidate_id=candidate_id,
        provider="amap",
        provider_place_id=provider_place_id,
        name=name,
        address="成都市",
        entity_kind=entity_kind,
        longitude=104.08,
        latitude=30.65,
    )


def test_registry_keeps_resolution_grouped_by_original_target() -> None:
    candidate_set = CandidateSet(
        target_id="query-ifs",
        obligation_ids=("obl-ifs",),
        query_text="IFS",
        candidates=(
            _candidate("cand-ifs", "B001", "成都IFS国际金融中心"),
            _candidate("cand-office", "B002", "成都IFS办公楼"),
        ),
        provider_result_count=7,
        eligible_result_count=2,
        excluded_result_count=5,
        exclusion_reasons={"auxiliary": 3, "entrance": 2},
    )
    registry = GroundingRegistry(
        registry_id="grounding-1",
        query_plan_id="query-plan-1",
        candidate_sets=(candidate_set,),
        resolutions=(
            TargetResolution(
                target_id="query-ifs",
                obligation_ids=("obl-ifs",),
                status=ResolutionStatus.SELECTED,
                selected_candidate_id="cand-ifs",
                rationale="名称、城市和商场类别均与原始地点一致。",
                confidence=0.96,
                selection_factors=("exact_name", "city_match", "category_match"),
            ),
        ),
    )

    assert registry.resolutions[0].selected_candidate_id == "cand-ifs"
    assert registry.candidate_sets[0].provider_result_count == 7
    assert registry.candidate_sets[0].excluded_result_count == 5


def test_registry_rejects_selected_candidate_from_another_target() -> None:
    with pytest.raises(ValidationError, match="not in candidate set"):
        GroundingRegistry(
            registry_id="grounding-invalid",
            query_plan_id="query-plan-1",
            candidate_sets=(
                CandidateSet(
                    target_id="query-ifs",
                    obligation_ids=("obl-ifs",),
                    query_text="IFS",
                    candidates=(_candidate("cand-ifs", "B001", "成都IFS"),),
                    provider_result_count=1,
                    eligible_result_count=1,
                ),
            ),
            resolutions=(
                TargetResolution(
                    target_id="query-ifs",
                    obligation_ids=("obl-ifs",),
                    status=ResolutionStatus.SELECTED,
                    selected_candidate_id="cand-other",
                    rationale="错误地引用了其他组候选。",
                    confidence=0.9,
                    selection_factors=("name",),
                ),
            ),
        )


def test_ambiguous_restaurant_branch_cannot_be_encoded_as_selected() -> None:
    resolution = TargetResolution(
        target_id="query-restaurant",
        obligation_ids=("obl-restaurant",),
        status=ResolutionStatus.NEEDS_CONFIRMATION,
        rationale="同城有两家名称相同且路线影响不同的分店。",
        confidence=0.58,
        candidate_ids_requiring_confirmation=("cand-a", "cand-b"),
        clarification_question="你指的是太古里店还是 IFS 店？",
    )

    assert resolution.status is ResolutionStatus.NEEDS_CONFIRMATION
    assert resolution.selected_candidate_id is None


def test_candidate_set_hard_caps_context_at_three_candidates() -> None:
    with pytest.raises(ValidationError):
        CandidateSet(
            target_id="query-expanded",
            obligation_ids=("obl-expanded",),
            query_text="武侯祠",
            candidates=tuple(
                _candidate(f"cand-{index}", f"B{index}", f"地点 {index}")
                for index in range(4)
            ),
            provider_result_count=4,
            eligible_result_count=4,
        )
