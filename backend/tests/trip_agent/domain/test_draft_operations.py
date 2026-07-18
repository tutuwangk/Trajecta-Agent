from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from app.trip_agent.domain import (
    DraftDay,
    DraftSnapshot,
    DraftVisit,
    GoalLedger,
    PlaceCandidate,
    RunGoal,
    TripWorkspace,
)
from app.trip_agent.toolsets.planner_tools import (
    AllocateDayOperation,
    ProtectAnchorOperation,
    ReduceIntensityOperation,
    RemoveOptionalPlaceOperation,
    ReorderClusterOperation,
    SetMealStrategyOperation,
    SetVisitWindowOperation,
    _apply_draft_operations,
)


NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)


def _workspace() -> TripWorkspace:
    candidates = tuple(
        PlaceCandidate(
            candidate_id=f"c{index}",
            hypothesis_id=f"h{index}",
            provider="fixture",
            provider_place_id=f"p{index}",
            name=f"地点{index}",
            source_record_id=f"s{index}",
        )
        for index in range(1, 4)
    )
    draft = DraftSnapshot(
        draft_id="draft-1",
        workspace_id="workspace-operations",
        workspace_version=5,
        draft_version=1,
        days=(
            DraftDay(
                day_index=1,
                date=date(2026, 8, 3),
                visits=(
                    DraftVisit(visit_id="v1", place_candidate_id="c1", duration_min=60),
                    DraftVisit(
                        visit_id="v2",
                        place_candidate_id="c2",
                        duration_min=45,
                        optional=True,
                    ),
                ),
            ),
            DraftDay(
                day_index=2,
                date=date(2026, 8, 4),
                visits=(
                    DraftVisit(
                        visit_id="v3",
                        place_candidate_id="c3",
                        duration_min=45,
                        optional=True,
                    ),
                ),
            ),
        ),
    )
    return TripWorkspace(
        workspace_id="workspace-operations",
        version=5,
        goal_ledger=GoalLedger(goal=RunGoal(raw_request="成都两日游")),
        place_candidates=candidates,
        current_draft=draft,
        created_at=NOW,
        updated_at=NOW,
    )


def test_semantic_draft_operations_apply_as_one_new_snapshot():
    workspace = _workspace()
    updated = _apply_draft_operations(
        workspace,
        (
            AllocateDayOperation(
                operation="allocate_day",
                candidate_id="c3",
                day_index=1,
                duration_min=90,
                optional=True,
            ),
            ReorderClusterOperation(
                operation="reorder_cluster",
                day_index=1,
                ordered_candidate_ids=("c3", "c1", "c2"),
            ),
            ProtectAnchorOperation(
                operation="protect_anchor",
                candidate_id="c3",
                fixed_start=time(10, 0),
            ),
            SetVisitWindowOperation(
                operation="set_visit_window",
                candidate_id="c1",
                earliest_start=time(11, 30),
                latest_end=time(14, 0),
            ),
            ReduceIntensityOperation(
                operation="reduce_intensity",
                day_index=1,
                max_visits=2,
            ),
            SetMealStrategyOperation(
                operation="set_meal_strategy",
                day_index=1,
                strategy="在两个访问之间安排午餐",
            ),
        ),
    )

    assert updated.draft_version == 2
    assert updated.workspace_version == workspace.version
    assert [visit.place_candidate_id for visit in updated.days[0].visits] == ["c3", "c1"]
    assert updated.days[0].visits[0].fixed_start == time(10, 0)
    assert updated.days[0].visits[0].optional is False
    assert updated.days[0].visits[1].earliest_start == time(11, 30)
    assert updated.days[0].meal_strategy == "在两个访问之间安排午餐"
    assert updated.days[1].visits == ()


def test_invalid_operation_rejects_batch_without_mutating_original_draft():
    workspace = _workspace()

    with pytest.raises(ValueError, match="protected visit"):
        _apply_draft_operations(
            workspace,
            (
                RemoveOptionalPlaceOperation(
                    operation="remove_optional_place",
                    candidate_id="c1",
                ),
            ),
        )

    assert workspace.current_draft.draft_version == 1
    assert len(workspace.current_draft.days[0].visits) == 2
