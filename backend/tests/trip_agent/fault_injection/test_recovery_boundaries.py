from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic_ai import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    InMemoryStepStore,
    RunRecord,
    ToolEffectRecord,
)

from app.trip_agent.domain import (
    AgentRun,
    ClarificationAnswer,
    ClarificationAnswers,
    ClarificationBatch,
    ClarificationQuestion,
    GoalLedger,
    RunGoal,
    RunStatus,
    TripWorkspace,
)
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.runtime import TripAgentService
from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter


NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)


class UnusedKnowledge:
    pass


def _running_repository(tmp_path) -> SqliteTripAgentRepository:
    repository = SqliteTripAgentRepository(tmp_path / "domain.sqlite3")
    repository.create_workspace(
        TripWorkspace(
            workspace_id="workspace-recovery",
            goal_ledger=GoalLedger(goal=RunGoal(raw_request="成都一日游")),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    repository.create_run(
        AgentRun(
            run_id="run-recovery",
            workspace_id="workspace-recovery",
            idempotency_key="recovery",
            provider_conversation_id="conversation-recovery",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    repository.transition_run("run-recovery", RunStatus.RUNNING, at=NOW)
    return repository


@pytest.mark.anyio
async def test_recovery_without_provider_valid_snapshot_is_explicitly_incomplete(tmp_path):
    repository = _running_repository(tmp_path)
    service = TripAgentService(
        repository,
        UnusedKnowledge(),  # type: ignore[arg-type]
        HarnessPersistenceAdapter(InMemoryStepStore()),
    )

    outcome = await service.recover(
        run_id="run-recovery",
        model=FunctionModel(lambda messages, info: None),  # never called
    )

    assert outcome.status is RunStatus.INCOMPLETE
    run = repository.get_run("run-recovery")
    assert run is not None
    assert run.error_code == "safe_snapshot_missing"
    repository.close()


@pytest.mark.anyio
async def test_unknown_mutation_effect_is_never_blindly_replayed(tmp_path):
    repository = _running_repository(tmp_path)
    store = InMemoryStepStore()
    await store.register_run(
        RunRecord(
            run_id="provider-crashed",
            conversation_id="conversation-recovery",
            agent_name="trip_planner_v2",
            started_at=NOW,
        )
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="provider-crashed",
            step_index=1,
            messages=[ModelRequest(parts=[UserPromptPart("成都一日游", timestamp=NOW)])],
            conversation_id="conversation-recovery",
            agent_name="trip_planner_v2",
            timestamp=NOW,
        )
    )
    await store.record_tool_effect(
        ToolEffectRecord(
            tool_call_id="call-mutation",
            tool_name="apply_draft_change",
            run_id="provider-crashed",
            status="started",
            started_at=NOW,
        )
    )
    service = TripAgentService(
        repository,
        UnusedKnowledge(),  # type: ignore[arg-type]
        HarnessPersistenceAdapter(store),
    )

    outcome = await service.recover(
        run_id="run-recovery",
        model=FunctionModel(lambda messages, info: None),  # never called
    )

    assert outcome.status is RunStatus.INCOMPLETE
    run = repository.get_run("run-recovery")
    assert run is not None
    assert run.error_code == "unknown_tool_effect_after_crash"
    assert repository.get_workspace("workspace-recovery").version == 1
    repository.close()


@pytest.mark.anyio
async def test_missing_deferred_transcript_does_not_consume_user_answer(tmp_path):
    repository = _running_repository(tmp_path)
    batch = ClarificationBatch(
        interruption_id="interruption-recovery",
        run_id="run-recovery",
        provider_run_id="provider-missing-transcript",
        tool_call_id="tool-clarification",
        questions=(
            ClarificationQuestion(
                question_id="q1",
                prompt="选择哪个地点？",
                reason="地点身份不明确",
            ),
        ),
        created_at=NOW,
    )
    repository.create_interruption_and_wait(batch)
    service = TripAgentService(
        repository,
        UnusedKnowledge(),  # type: ignore[arg-type]
        HarnessPersistenceAdapter(InMemoryStepStore()),
    )

    with pytest.raises(LookupError, match="no deferred provider transcript"):
        await service.resume(
            run_id="run-recovery",
            answers={"q1": "总店"},
            model=FunctionModel(lambda messages, info: None),
        )

    still_waiting = repository.get_run("run-recovery")
    assert still_waiting is not None
    assert still_waiting.status is RunStatus.WAITING_USER
    # The original answer remains consumable because transcript loading precedes the atomic commit.
    assert repository.consume_answers_and_resume(
        ClarificationAnswers(
            interruption_id=batch.interruption_id,
            tool_call_id=batch.tool_call_id,
            answers=(ClarificationAnswer(question_id="q1", value="总店"),),
            answered_at=NOW,
        )
    ) is True
    repository.close()


@pytest.mark.anyio
async def test_unresolved_read_effect_can_resume_from_safe_snapshot(tmp_path):
    repository = _running_repository(tmp_path)
    store = InMemoryStepStore()
    await store.register_run(
        RunRecord(
            run_id="provider-read-crash",
            conversation_id="conversation-recovery",
            agent_name="trip_planner_v2",
            started_at=NOW,
        )
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="provider-read-crash",
            step_index=1,
            messages=[ModelRequest(parts=[UserPromptPart("成都一日游", timestamp=NOW)])],
            conversation_id="conversation-recovery",
            agent_name="trip_planner_v2",
            timestamp=NOW,
        )
    )
    await store.record_tool_effect(
        ToolEffectRecord(
            tool_call_id="call-read",
            tool_name="read_workspace",
            run_id="provider-read-crash",
            status="started",
            started_at=NOW,
        )
    )

    async def end_without_candidate(messages, info):
        return ModelResponse(parts=[TextPart("no candidate")], finish_reason="stop")

    service = TripAgentService(
        repository,
        UnusedKnowledge(),  # type: ignore[arg-type]
        HarnessPersistenceAdapter(store),
    )
    outcome = await service.recover(
        run_id="run-recovery",
        model=FunctionModel(end_without_candidate),
    )

    assert outcome.status is RunStatus.INCOMPLETE
    assert repository.get_run("run-recovery").error_code == "agent_action_repair_exhausted"
    assert [event["type"] for event in outcome.events] == ["run_recovered"]
    repository.close()


@pytest.mark.anyio
async def test_recover_uses_same_transient_provider_failure_policy(tmp_path):
    repository = _running_repository(tmp_path)
    store = InMemoryStepStore()
    await store.register_run(
        RunRecord(
            run_id="provider-recover-transient",
            conversation_id="conversation-recovery",
            agent_name="trip_planner_v2",
            started_at=NOW,
        )
    )
    await store.save_snapshot(
        ContinuableSnapshot(
            run_id="provider-recover-transient",
            step_index=1,
            messages=[ModelRequest(parts=[UserPromptPart("成都一日游", timestamp=NOW)])],
            conversation_id="conversation-recovery",
            agent_name="trip_planner_v2",
            timestamp=NOW,
        )
    )
    attempts = 0

    async def unavailable(messages, info):
        nonlocal attempts
        attempts += 1
        raise ModelHTTPError(503, "fixture-provider", {"error": "temporary"})

    service = TripAgentService(
        repository,
        UnusedKnowledge(),  # type: ignore[arg-type]
        HarnessPersistenceAdapter(store),
    )
    outcome = await service.recover(
        run_id="run-recovery",
        model=FunctionModel(unavailable),
    )

    run = repository.get_run("run-recovery")
    assert outcome.status is RunStatus.INCOMPLETE
    assert run is not None
    assert run.failure_class.value == "transient_external"
    assert run.provider_attempt_count == 2
    assert attempts == 2
    repository.close()


@pytest.mark.anyio
async def test_provider_crash_returns_structured_failed_outcome_without_workspace_mutation(tmp_path):
    repository = _running_repository(tmp_path)
    # A workspace has one logical writer, so provider crash isolation uses another workspace.
    repository.create_workspace(
        TripWorkspace(
            workspace_id="workspace-provider-crash",
            goal_ledger=GoalLedger(goal=RunGoal(raw_request="成都一日游")),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    repository.create_run(
        AgentRun(
            run_id="run-provider-crash",
            workspace_id="workspace-provider-crash",
            idempotency_key="provider-crash",
            provider_conversation_id="conversation-provider-crash",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    async def crash(messages, info):
        raise RuntimeError("injected provider crash")

    service = TripAgentService(repository, UnusedKnowledge())  # type: ignore[arg-type]
    outcome = await service.execute(
        run_id="run-provider-crash",
        model=FunctionModel(crash),
    )

    failed = repository.get_run("run-provider-crash")
    assert failed is not None
    assert outcome.status is RunStatus.FAILED
    assert failed.status is RunStatus.FAILED
    assert failed.error_code == "agent_runtime_error"
    assert failed.failure_class.value == "internal"
    assert failed.retryable is False
    assert repository.get_workspace("workspace-provider-crash").version == 1
    repository.close()
