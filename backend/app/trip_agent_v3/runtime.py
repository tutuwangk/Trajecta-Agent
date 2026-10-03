from __future__ import annotations

from app.trip_agent_v3.domain.runtime import (
    GoalProgress,
    RuntimeBoundaryResult,
    RuntimeDisposition,
)


def evaluate_budget_boundary(
    *,
    progress: GoalProgress,
    budget_exhausted: bool,
    checkpoint_id: str | None,
) -> RuntimeBoundaryResult:
    if progress.is_complete:
        return RuntimeBoundaryResult(
            disposition=RuntimeDisposition.READY_FOR_DELIVERY,
            may_publish=True,
            blockers=(),
        )
    if budget_exhausted:
        if not checkpoint_id:
            raise ValueError(
                "incomplete budget exhaustion requires a resumable checkpoint"
            )
        return RuntimeBoundaryResult(
            disposition=RuntimeDisposition.NEEDS_RESUME,
            may_publish=False,
            checkpoint_id=checkpoint_id,
            blockers=progress.blockers,
        )
    return RuntimeBoundaryResult(
        disposition=RuntimeDisposition.CONTINUE,
        may_publish=False,
        blockers=progress.blockers,
    )
