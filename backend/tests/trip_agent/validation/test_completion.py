from __future__ import annotations

from datetime import date, datetime, timezone

from app.trip_agent.domain import (
    CommitmentStrength,
    DraftDay,
    DraftSnapshot,
    GoalCommitment,
    GoalLedger,
    HypothesisStatus,
    PlaceCandidate,
    PlaceHypothesis,
    PlaceResolution,
    ResolutionStatus,
    RunGoal,
    TripWorkspace,
    TextSpan,
)
from app.trip_agent.validation.completion import CompletionEvaluator


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


def _workspace_with_draft(days: tuple[DraftDay, ...]) -> TripWorkspace:
    workspace = TripWorkspace(
        workspace_id="workspace-completion",
        goal_ledger=GoalLedger(
            goal=RunGoal(
                raw_request="成都两日游",
                destination="成都",
                start_date=date(2026, 8, 3),
                days=2,
            )
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    return workspace.with_draft(
        DraftSnapshot(
            draft_id="draft-completion",
            workspace_id=workspace.workspace_id,
            workspace_version=workspace.version,
            draft_version=1,
            days=days,
        ),
        at=NOW,
    )


def test_partial_trip_draft_is_not_a_complete_checkpoint():
    workspace = _workspace_with_draft(
        (DraftDay(day_index=1, date=date(2026, 8, 3), day_purpose="touring"),)
    )

    assessment = CompletionEvaluator().evaluate(workspace)

    assert assessment.complete is False
    assert "trip_days_missing" in assessment.issue_codes


def test_explicit_rest_day_counts_as_accounted_for_not_missing():
    workspace = _workspace_with_draft(
        (
            DraftDay(day_index=1, date=date(2026, 8, 3), day_purpose="arrival"),
            DraftDay(day_index=2, date=date(2026, 8, 4), day_purpose="rest"),
        )
    )

    assessment = CompletionEvaluator().evaluate(workspace)

    assert "trip_days_missing" not in assessment.issue_codes
    assert "touring_day_empty" not in assessment.issue_codes


def test_entity_linked_lodging_commitment_survives_provider_name_variant():
    candidate = PlaceCandidate(
        candidate_id="hotel-1",
        hypothesis_id="hypothesis-hotel",
        provider="amap",
        provider_place_id="amap-hotel-1",
        name="成都太古里亚朵S酒店",
        source_record_id="source-hotel-1",
    )
    workspace = TripWorkspace(
        workspace_id="workspace-entity-link",
        goal_ledger=GoalLedger(
            goal=RunGoal(
                raw_request="住成都太古里亚朵酒店，成都一日游",
                destination="成都",
                start_date=date(2026, 8, 3),
                days=1,
            ),
            commitments=(
                GoalCommitment(
                    commitment_id="commitment-hotel",
                    field="lodging",
                    value="成都太古里亚朵酒店",
                    evidence_text="住成都太古里亚朵酒店",
                    subject_hypothesis_id="hypothesis-hotel",
                    strength=CommitmentStrength.STRONG,
                ),
            ),
        ),
        place_hypotheses=(
            PlaceHypothesis(
                hypothesis_id="hypothesis-hotel",
                raw_name="成都太古里亚朵酒店",
                context="住成都太古里亚朵酒店",
                spans=(TextSpan(start=1, end=10),),
                role="lodging",
                priority="strong",
                candidate_ids=(candidate.candidate_id,),
                status=HypothesisStatus.RESOLVED,
            ),
        ),
        place_candidates=(candidate,),
        place_resolutions=(
            PlaceResolution(
                hypothesis_id="hypothesis-hotel",
                status=ResolutionStatus.RESOLVED,
                candidate_id=candidate.candidate_id,
                rationale="provider verified branch",
                workspace_version=1,
            ),
        ),
        current_draft=DraftSnapshot(
            draft_id="draft-entity-link",
            workspace_id="workspace-entity-link",
            workspace_version=1,
            draft_version=1,
            days=(
                DraftDay(
                    day_index=1,
                    date=date(2026, 8, 3),
                    day_purpose="arrival",
                    hotel_candidate_id=candidate.candidate_id,
                ),
            ),
        ),
        created_at=NOW,
        updated_at=NOW,
    )

    assessment = CompletionEvaluator().evaluate(workspace)

    assert assessment.complete is True
    assert assessment.missing_commitment_ids == ()

    legacy_commitment = workspace.goal_ledger.commitments[0].model_copy(
        update={"subject_hypothesis_id": None}
    )
    legacy_workspace = workspace.model_copy(
        update={
            "goal_ledger": workspace.goal_ledger.model_copy(
                update={"commitments": (legacy_commitment,)}
            )
        }
    )
    assert CompletionEvaluator().evaluate(legacy_workspace).complete is True


def test_only_explicit_hard_commitment_can_block_publication():
    workspace = _workspace_with_draft(
        (
            DraftDay(day_index=1, date=date(2026, 8, 3), day_purpose="arrival"),
            DraftDay(day_index=2, date=date(2026, 8, 4), day_purpose="rest"),
        )
    )
    strong = GoalCommitment(
        commitment_id="strong-restaurant",
        field="meal",
        value="某指定餐厅",
        evidence_text="想去某指定餐厅",
        strength=CommitmentStrength.STRONG,
    )
    soft_workspace = workspace.model_copy(
        update={
            "goal_ledger": workspace.goal_ledger.model_copy(
                update={"commitments": (strong,)}
            )
        }
    )
    assert CompletionEvaluator().evaluate(soft_workspace).complete is True

    hard = strong.model_copy(
        update={
            "commitment_id": "hard-appointment",
            "field": "appointment",
            "evidence_text": "已预约且不可调整",
            "strength": CommitmentStrength.HARD,
            "immutable": True,
        }
    )
    hard_workspace = workspace.model_copy(
        update={
            "goal_ledger": workspace.goal_ledger.model_copy(
                update={"commitments": (hard,)}
            )
        }
    )
    assessment = CompletionEvaluator().evaluate(hard_workspace)
    assert assessment.complete is False
    assert assessment.missing_commitment_ids == ("hard-appointment",)
