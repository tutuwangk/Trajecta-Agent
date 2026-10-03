from __future__ import annotations

import pytest

from app.trip_agent_v3.domain.delivery import RunRecord, RunStatus
from app.trip_agent_v3.repository import (
    SqliteTripAgentV3Repository,
    V3RepositoryConflict,
)


def test_terminal_run_status_cannot_be_overwritten(tmp_path) -> None:
    repository = SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3")
    repository.save_run(
        RunRecord(
            run_id="run-1",
            workspace_id="workspace-1",
            goal_revision_id="goal-1",
            status=RunStatus.ACTIVE,
        )
    )
    repository.transition_run("run-1", RunStatus.CANCELLED)

    with pytest.raises(V3RepositoryConflict, match="illegal run transition"):
        repository.transition_run("run-1", RunStatus.SUCCEEDED)

    assert repository.get_run("run-1").status is RunStatus.CANCELLED


def test_resume_must_reenter_active_before_success(tmp_path) -> None:
    repository = SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3")
    repository.save_run(
        RunRecord(
            run_id="run-1",
            workspace_id="workspace-1",
            goal_revision_id="goal-1",
            status=RunStatus.ACTIVE,
        )
    )
    repository.transition_run("run-1", RunStatus.NEEDS_RESUME)

    with pytest.raises(V3RepositoryConflict, match="illegal run transition"):
        repository.transition_run("run-1", RunStatus.SUCCEEDED)

    repository.transition_run("run-1", RunStatus.ACTIVE)
    assert (
        repository.transition_run("run-1", RunStatus.SUCCEEDED).status
        is RunStatus.SUCCEEDED
    )


def test_only_one_executor_can_claim_a_run(tmp_path) -> None:
    repository = SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3")
    repository.save_run(
        RunRecord(
            run_id="run-1",
            workspace_id="workspace-1",
            goal_revision_id="goal-1",
            status=RunStatus.CREATED,
        )
    )

    first = repository.claim_run_for_execution("run-1")
    second = repository.claim_run_for_execution("run-1")

    assert first is not None
    assert first.status is RunStatus.ACTIVE
    assert second is None
