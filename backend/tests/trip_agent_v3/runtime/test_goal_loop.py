from __future__ import annotations

from app.trip_agent_v3.domain.runtime import (
    GoalProgress,
    RuntimeDisposition,
)
from app.trip_agent_v3.runtime import evaluate_budget_boundary


def test_budget_exhaustion_never_forces_incomplete_plan_to_delivery() -> None:
    progress = GoalProgress(
        explicit_obligation_count=10,
        disposed_obligation_count=8,
        unresolved_grounding_count=1,
        fact_gap_count=0,
        timeline_compiled=False,
        delivery_eligible=False,
        blockers=("2 个显式地点尚未处置。", "1 个地点仍有歧义。"),
    )

    result = evaluate_budget_boundary(
        progress=progress,
        budget_exhausted=True,
        checkpoint_id="checkpoint-8",
    )

    assert result.disposition is RuntimeDisposition.NEEDS_RESUME
    assert result.checkpoint_id == "checkpoint-8"
    assert result.may_publish is False
    assert result.blockers == progress.blockers


def test_tool_volume_does_not_override_goal_completion() -> None:
    progress = GoalProgress(
        explicit_obligation_count=10,
        disposed_obligation_count=9,
        unresolved_grounding_count=0,
        fact_gap_count=0,
        timeline_compiled=True,
        delivery_eligible=False,
        blockers=("餐厅 obligation 尚未处置。",),
    )

    result = evaluate_budget_boundary(
        progress=progress,
        budget_exhausted=False,
        checkpoint_id=None,
    )

    assert result.disposition is RuntimeDisposition.CONTINUE
    assert result.may_publish is False


def test_only_delivery_eligible_goal_can_leave_runtime_ready() -> None:
    progress = GoalProgress(
        explicit_obligation_count=10,
        disposed_obligation_count=10,
        unresolved_grounding_count=0,
        fact_gap_count=0,
        timeline_compiled=True,
        delivery_eligible=True,
        blockers=(),
    )

    result = evaluate_budget_boundary(
        progress=progress,
        budget_exhausted=False,
        checkpoint_id=None,
    )

    assert result.disposition is RuntimeDisposition.READY_FOR_DELIVERY
    assert result.may_publish is True
