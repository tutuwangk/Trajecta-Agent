from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from pydantic_ai import (
    DeferredToolRequests,
    DeferredToolResults,
    ModelRetry,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UsageLimits,
)
from pydantic_ai.models import Model
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai_harness.step_persistence import InMemoryStepStore

from app.trip_agent.agent import build_trip_planner_agent
from app.trip_agent.adapters.provider import is_complete_final_response
from app.trip_agent.domain import (
    AgentRun,
    ClarificationAnswer,
    ClarificationAnswers,
    GoalLedger,
    RunGoal,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    TripWorkspace,
    utc_now,
)
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter
from app.trip_agent.budget import BudgetExhausted, RuntimeBudget
from app.trip_agent.toolsets import NarrativeGeneratorPort, PlaceKnowledgePort, TripAgentDeps


@dataclass(frozen=True, slots=True)
class TripAgentRunOutcome:
    workspace_id: str
    run_id: str
    status: RunStatus
    output: str
    events: tuple[dict[str, object], ...]


class PersistentEventList(list[dict[str, object]]):
    def __init__(self, repository: SqliteTripAgentRepository, run_id: str) -> None:
        super().__init__()
        self.repository = repository
        self.run_id = run_id

    def append(self, event: dict[str, object]) -> None:
        self.repository.append_event(self.run_id, event)
        super().append(event)


def _is_recoverable_agent_exhaustion(exc: UnexpectedModelBehavior) -> bool:
    """Separate repair exhaustion from provider transcript/protocol corruption."""

    message = str(exc).lower()
    if "output" in message and "retri" in message:
        return True
    cause: BaseException | None = exc.__cause__
    while cause is not None:
        if isinstance(cause, ModelRetry):
            return True
        cause = cause.__cause__
    return False


class TripAgentService:
    def __init__(
        self,
        repository: SqliteTripAgentRepository,
        place_knowledge: PlaceKnowledgePort,
        persistence: HarnessPersistenceAdapter | None = None,
        narrative_generator: NarrativeGeneratorPort | None = None,
    ) -> None:
        self.repository = repository
        self.place_knowledge = place_knowledge
        self.persistence = persistence or HarnessPersistenceAdapter(InMemoryStepStore())
        self.narrative_generator = narrative_generator

    def _budget(self, run_id: str) -> RuntimeBudget:
        return RuntimeBudget.from_state(
            self.repository.get_run_budget(run_id),
            on_change=lambda state: self.repository.save_run_budget(run_id, state),
        )

    def create_workspace(
        self,
        *,
        raw_request: str,
        destination: str | None = None,
        start_date: date | None = None,
        days: int | None = None,
    ) -> TripWorkspace:
        now = utc_now()
        workspace = TripWorkspace(
            workspace_id=f"workspace-{uuid4()}",
            goal_ledger=GoalLedger(
                goal=RunGoal(
                    raw_request=raw_request,
                    destination=destination,
                    start_date=start_date,
                    days=days,
                )
            ),
            created_at=now,
            updated_at=now,
        )
        return self.repository.create_workspace(workspace)

    def create_run(
        self,
        workspace_id: str,
        *,
        idempotency_key: str,
        revision_of_run_id: str | None = None,
    ) -> AgentRun:
        if self.repository.get_workspace(workspace_id) is None:
            raise KeyError(workspace_id)
        now = utc_now()
        run_id = f"run-{uuid4()}"
        return self.repository.create_run(
            AgentRun(
                run_id=run_id,
                workspace_id=workspace_id,
                idempotency_key=idempotency_key,
                provider_conversation_id=f"conversation-{run_id}",
                revision_of_run_id=revision_of_run_id,
                created_at=now,
                updated_at=now,
            )
        )

    def create_revision_run(
        self,
        workspace_id: str,
        *,
        raw_request: str,
        idempotency_key: str,
    ) -> AgentRun:
        existing = self.repository.find_run_by_idempotency(workspace_id, idempotency_key)
        if existing:
            return existing
        workspace = self.repository.get_workspace(workspace_id)
        if workspace is None:
            raise KeyError(workspace_id)
        runs = self.repository.list_runs(workspace_id)
        if runs and runs[-1].status not in TERMINAL_RUN_STATUSES:
            raise ValueError("an Agent run is still active")
        updated_goal = workspace.goal_ledger.goal.model_copy(update={"raw_request": raw_request})
        updated_ledger = GoalLedger(
            goal=RunGoal.model_validate(updated_goal),
            commitments=workspace.goal_ledger.commitments,
            revision=workspace.goal_ledger.revision + 1,
        )
        updated = workspace.with_goal_ledger(updated_ledger)
        now = utc_now()
        run_id = f"run-{uuid4()}"
        run = AgentRun(
            run_id=run_id,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            provider_conversation_id=f"conversation-{run_id}",
            revision_of_run_id=runs[-1].run_id if runs else None,
            created_at=now,
            updated_at=now,
        )
        return self.repository.create_revision_run(
            updated,
            expected_version=workspace.version,
            run=run,
        )

    async def start(
        self,
        *,
        raw_request: str,
        model: Model,
        destination: str | None = None,
        start_date: date | None = None,
        days: int | None = None,
        idempotency_key: str | None = None,
    ) -> TripAgentRunOutcome:
        workspace = self.create_workspace(
            raw_request=raw_request,
            destination=destination,
            start_date=start_date,
            days=days,
        )
        run = self.create_run(
            workspace.workspace_id,
            idempotency_key=idempotency_key or f"start:{workspace.workspace_id}",
        )
        return await self.execute(run_id=run.run_id, model=model)

    async def execute(self, *, run_id: str, model: Model) -> TripAgentRunOutcome:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status is not RunStatus.CREATED:
            raise ValueError(f"run {run_id} is not in created state")
        workspace = self.repository.get_workspace(run.workspace_id)
        if workspace is None:
            raise KeyError(run.workspace_id)
        now = utc_now()
        self.repository.transition_run(run.run_id, RunStatus.RUNNING, at=now)
        events = PersistentEventList(self.repository, run.run_id)
        events.append({"type": "run_started", "run_id": run.run_id})
        provider_run_id = f"provider-{run.run_id}-{uuid4()}"
        deps = TripAgentDeps(
            repository=self.repository,
            place_knowledge=self.place_knowledge,
            workspace_id=workspace.workspace_id,
            run_id=run.run_id,
            provider_run_id=provider_run_id,
            events=events,
            budget=self._budget(run.run_id),
            narrative_generator=self.narrative_generator,
        )
        capabilities = [
            self.persistence.capability(agent_name="trip_planner_v2", run_id=provider_run_id)
        ]
        agent = build_trip_planner_agent(model, capabilities=capabilities)
        output = ""
        try:
            async with asyncio.timeout(deps.budget.max_seconds):
                result = await agent.run(
                    workspace.goal_ledger.goal.raw_request,
                    deps=deps,
                    conversation_id=run.provider_conversation_id,
                    usage_limits=UsageLimits(request_limit=20, tool_calls_limit=32),
                )
            output = "waiting_user" if isinstance(result.output, DeferredToolRequests) else str(result.output)
            current = self.repository.get_run(run.run_id)
            if current is None:
                raise KeyError(run.run_id)
            if isinstance(result.output, DeferredToolRequests):
                if current.status is not RunStatus.WAITING_USER:
                    raise RuntimeError("deferred tool result did not persist a waiting_user run")
                self.persistence.save_deferred_messages(provider_run_id, result.all_messages())
            elif not is_complete_final_response(result.response):
                current = self.repository.transition_run(
                    run.run_id,
                    RunStatus.INCOMPLETE,
                    error_code="provider_incomplete_response",
                    error_message=f"finish_reason={result.response.finish_reason}",
                )
            elif current.status is RunStatus.RUNNING:
                current = self.repository.transition_run(
                    run.run_id,
                    RunStatus.INCOMPLETE,
                    error_code="candidate_not_submitted",
                    error_message="Agent ended without a successful candidate submission.",
                )
            return TripAgentRunOutcome(
                workspace_id=workspace.workspace_id,
                run_id=run.run_id,
                status=current.status,
                output=output,
                events=tuple(events),
            )
        except (BudgetExhausted, UsageLimitExceeded, TimeoutError) as exc:
            current = self.repository.get_run(run.run_id)
            if current and current.status not in {
                RunStatus.PUBLISHED,
                RunStatus.INCOMPLETE,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                current = self.repository.transition_run(
                    run.run_id,
                    RunStatus.INCOMPLETE,
                    error_code="agent_budget_exhausted",
                    error_message=str(exc)[:4_000],
                )
            return TripAgentRunOutcome(
                workspace_id=workspace.workspace_id,
                run_id=run.run_id,
                status=current.status if current else RunStatus.INCOMPLETE,
                output=output,
                events=tuple(events),
            )
        except UnexpectedModelBehavior as exc:
            if not _is_recoverable_agent_exhaustion(exc):
                current = self.repository.get_run(run.run_id)
                if current and current.status not in TERMINAL_RUN_STATUSES:
                    self.repository.transition_run(
                        run.run_id,
                        RunStatus.FAILED,
                        error_code="provider_protocol_error",
                        error_message=str(exc)[:4_000],
                    )
                raise
            current = self.repository.get_run(run.run_id)
            if current and current.status not in TERMINAL_RUN_STATUSES:
                current = self.repository.transition_run(
                    run.run_id,
                    RunStatus.INCOMPLETE,
                    error_code="agent_action_repair_exhausted",
                    error_message=(
                        "Agent exhausted a bounded action repair budget without publishing. "
                        "The latest workspace remains available for a revision run."
                    ),
                )
            return TripAgentRunOutcome(
                workspace_id=workspace.workspace_id,
                run_id=run.run_id,
                status=current.status if current else RunStatus.INCOMPLETE,
                output=output,
                events=tuple(events),
            )
        except Exception as exc:
            current = self.repository.get_run(run.run_id)
            if current and current.status not in {
                RunStatus.PUBLISHED,
                RunStatus.INCOMPLETE,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                current = self.repository.transition_run(
                    run.run_id,
                    RunStatus.FAILED,
                    error_code="agent_runtime_error",
                    error_message=str(exc)[:4_000],
                )
            raise

    async def resume(
        self,
        *,
        run_id: str,
        answers: dict[str, str],
        model: Model,
    ) -> TripAgentRunOutcome:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status is not RunStatus.WAITING_USER or not run.active_interruption_id:
            raise ValueError(f"run {run_id} is not waiting for user clarification")
        interruption = self.repository.get_interruption(run.active_interruption_id)
        if interruption is None:
            raise KeyError(run.active_interruption_id)
        expected_question_ids = {question.question_id for question in interruption.questions}
        if set(answers) != expected_question_ids:
            raise ValueError("answers must match every clarification question exactly")
        now = utc_now()
        answer_record = ClarificationAnswers(
            interruption_id=interruption.interruption_id,
            tool_call_id=interruption.tool_call_id,
            answers=tuple(
                ClarificationAnswer(question_id=question.question_id, value=answers[question.question_id])
                for question in interruption.questions
            ),
            answered_at=now,
        )
        message_history = self.persistence.deferred_messages(interruption.provider_run_id)
        if not self.repository.consume_answers_and_resume(answer_record):
            raise ValueError("clarification answers were already consumed")
        provider_run_id = f"provider-{run_id}-{uuid4()}"
        events = PersistentEventList(self.repository, run_id)
        events.append(
            {
                "type": "run_resumed",
                "run_id": run_id,
                "interruption_id": interruption.interruption_id,
            }
        )
        deps = TripAgentDeps(
            repository=self.repository,
            place_knowledge=self.place_knowledge,
            workspace_id=run.workspace_id,
            run_id=run_id,
            provider_run_id=provider_run_id,
            events=events,
            budget=self._budget(run_id),
            narrative_generator=self.narrative_generator,
        )
        agent = build_trip_planner_agent(
            model,
            capabilities=[
                self.persistence.capability(agent_name="trip_planner_v2", run_id=provider_run_id)
            ],
        )
        try:
            result = await agent.run(
                None,
                deps=deps,
                conversation_id=run.provider_conversation_id,
                message_history=message_history,
                deferred_tool_results=DeferredToolResults(
                    calls={
                        interruption.tool_call_id: {
                            "answers": [item.model_dump(mode="json") for item in answer_record.answers]
                        }
                    }
                ),
                usage_limits=UsageLimits(request_limit=20, tool_calls_limit=32),
            )
        except UnexpectedModelBehavior as exc:
            if not _is_recoverable_agent_exhaustion(exc):
                raise
            current = self.repository.get_run(run_id)
            if current and current.status not in TERMINAL_RUN_STATUSES:
                current = self.repository.transition_run(
                    run_id,
                    RunStatus.INCOMPLETE,
                    error_code="agent_action_repair_exhausted",
                    error_message="Agent exhausted a bounded action repair budget after clarification.",
                )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status if current else RunStatus.INCOMPLETE,
                output="",
                events=tuple(events),
            )
        output = "waiting_user" if isinstance(result.output, DeferredToolRequests) else str(result.output)
        current = self.repository.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        if isinstance(result.output, DeferredToolRequests):
            if current.status is not RunStatus.WAITING_USER:
                raise RuntimeError("deferred tool result did not persist a waiting_user run")
            self.persistence.save_deferred_messages(provider_run_id, result.all_messages())
        elif not is_complete_final_response(result.response):
            current = self.repository.transition_run(
                run_id,
                RunStatus.INCOMPLETE,
                error_code="provider_incomplete_response",
                error_message=f"finish_reason={result.response.finish_reason}",
            )
        elif current.status is RunStatus.RUNNING:
            current = self.repository.transition_run(
                run_id,
                RunStatus.INCOMPLETE,
                error_code="candidate_not_submitted",
                error_message="Agent ended without a successful candidate submission.",
            )
        return TripAgentRunOutcome(
            workspace_id=run.workspace_id,
            run_id=run_id,
            status=current.status,
            output=output,
            events=tuple(events),
        )

    async def recover(self, *, run_id: str, model: Model) -> TripAgentRunOutcome:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status is not RunStatus.RUNNING:
            raise ValueError(f"run {run_id} is not recoverable from status {run.status}")
        try:
            prior_provider_run_id, message_history, unresolved = (
                await self.persistence.latest_recovery_state(run.provider_conversation_id)
            )
        except LookupError as exc:
            current = self.repository.transition_run(
                run_id,
                RunStatus.INCOMPLETE,
                error_code="safe_snapshot_missing",
                error_message=str(exc),
            )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status,
                output="",
                events=(),
            )
        mutation_tools = {
            "analyze_place_mentions",
            "search_place_candidates",
            "resolve_place",
            "acquire_place_facts",
            "acquire_route_facts",
            "estimate_visit_profile",
            "estimate_visit_profiles",
            "apply_draft_change",
            "apply_draft_operations",
            "request_clarification",
            "submit_candidate",
        }
        resolved_mutation_results = [
            (tool_call_id, tool_name, result)
            for tool_call_id, tool_name in unresolved
            if tool_name in mutation_tools
            and (result := self.repository.get_tool_effect(tool_call_id, tool_name=tool_name))
            is not None
        ]
        unknown_mutations = [
            {"tool_call_id": tool_call_id, "tool_name": tool_name}
            for tool_call_id, tool_name in unresolved
            if tool_name in mutation_tools
            and self.repository.get_tool_effect(tool_call_id, tool_name=tool_name) is None
        ]
        if unknown_mutations:
            current = self.repository.transition_run(
                run_id,
                RunStatus.INCOMPLETE,
                error_code="unknown_tool_effect_after_crash",
                error_message=str(unknown_mutations)[:4_000],
            )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status,
                output="",
                events=(),
            )
        if resolved_mutation_results:
            message_history = message_history + (
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name=tool_name,
                            content=result,
                            tool_call_id=tool_call_id,
                        )
                        for tool_call_id, tool_name, result in resolved_mutation_results
                    ]
                ),
            )
        workspace = self.repository.get_workspace(run.workspace_id)
        if workspace is None:
            raise KeyError(run.workspace_id)
        provider_run_id = f"provider-{run_id}-{uuid4()}"
        events = PersistentEventList(self.repository, run_id)
        events.append(
            {
                "type": "run_recovered",
                "provider_snapshot_run_id": prior_provider_run_id,
            }
        )
        deps = TripAgentDeps(
            repository=self.repository,
            place_knowledge=self.place_knowledge,
            workspace_id=run.workspace_id,
            run_id=run_id,
            provider_run_id=provider_run_id,
            events=events,
            budget=self._budget(run_id),
            narrative_generator=self.narrative_generator,
        )
        agent = build_trip_planner_agent(
            model,
            capabilities=[
                self.persistence.capability(agent_name="trip_planner_v2", run_id=provider_run_id)
            ],
        )
        try:
            result = await agent.run(
                None,
                deps=deps,
                conversation_id=run.provider_conversation_id,
                message_history=message_history,
                usage_limits=UsageLimits(request_limit=20, tool_calls_limit=32),
            )
        except UnexpectedModelBehavior as exc:
            if not _is_recoverable_agent_exhaustion(exc):
                raise
            current = self.repository.transition_run(
                run_id,
                RunStatus.INCOMPLETE,
                error_code="agent_action_repair_exhausted",
                error_message="Recovered Agent exhausted a bounded action repair budget.",
            )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status,
                output="",
                events=tuple(events),
            )
        output = "waiting_user" if isinstance(result.output, DeferredToolRequests) else str(result.output)
        current = self.repository.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        if isinstance(result.output, DeferredToolRequests):
            self.persistence.save_deferred_messages(provider_run_id, result.all_messages())
        elif current.status is RunStatus.RUNNING:
            current = self.repository.transition_run(
                run_id,
                RunStatus.INCOMPLETE,
                error_code="candidate_not_submitted",
                error_message="Recovered Agent ended without publishing a candidate.",
            )
        return TripAgentRunOutcome(
            workspace_id=run.workspace_id,
            run_id=run_id,
            status=current.status,
            output=output,
            events=tuple(events),
        )
