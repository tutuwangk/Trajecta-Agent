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
    ModelAPIError,
    ModelHTTPError,
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
    RunFailureClass,
    TERMINAL_RUN_STATUSES,
    TripWorkspace,
    utc_now,
)
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter
from app.trip_agent.budget import BudgetExhausted, MUTATION_TOOLS, RuntimeBudget
from app.trip_agent.toolsets import (
    NarrativeGeneratorPort,
    PlaceKnowledgePort,
    TripAgentDeps,
    publish_latest_complete_checkpoint,
)


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


class EpisodeDeadlineExceeded(TimeoutError):
    """The Harness ended an episode to preserve time for a compact continuation."""


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
        place_cache_repository: SqliteTripAgentRepository | None = None,
    ) -> None:
        self.repository = repository
        self.place_knowledge = place_knowledge
        self.persistence = persistence or HarnessPersistenceAdapter(InMemoryStepStore())
        self.narrative_generator = narrative_generator
        self.place_cache_repository = place_cache_repository

    def _budget(self, run_id: str, workspace: TripWorkspace) -> RuntimeBudget:
        on_change = lambda state: self.repository.save_run_budget(run_id, state)
        state = self.repository.get_run_budget(run_id)
        if state is not None:
            return RuntimeBudget.from_state(state, on_change=on_change)
        days = workspace.goal_ledger.goal.days or 1
        max_seconds = 480 if days == 1 else 600 if days == 2 else 720
        return RuntimeBudget(max_seconds=max_seconds, on_change=on_change)

    @staticmethod
    def _episode_timeout(budget: RuntimeBudget) -> float:
        remaining = max(1.0, budget.max_seconds - budget.elapsed_seconds)
        if budget.convergence_mode:
            return remaining
        exploration_remaining = (
            budget.max_seconds * (1 - budget.reserve_ratio) - budget.elapsed_seconds
        )
        if exploration_remaining <= 0:
            budget.enter_convergence()
            return remaining
        return max(1.0, min(remaining, exploration_remaining))

    async def _run_episode(
        self,
        *,
        agent,
        user_prompt,
        deps: TripAgentDeps,
        run: AgentRun,
        events: PersistentEventList,
        timeout_seconds: float,
        message_history=None,
        deferred_tool_results=None,
    ):
        attempt = self.repository.increment_provider_attempt(run.run_id)
        usage = deps.budget.run_usage()
        deps.budget.begin_episode()
        deadline = asyncio.timeout(timeout_seconds)
        try:
            async with deadline:
                return await agent.run(
                    user_prompt,
                    deps=deps,
                    conversation_id=run.provider_conversation_id,
                    message_history=message_history,
                    deferred_tool_results=deferred_tool_results,
                    usage_limits=UsageLimits(
                        request_limit=deps.budget.max_model_requests,
                        tool_calls_limit=deps.budget.max_tool_calls,
                    ),
                    usage=usage,
                )
        except TimeoutError as exc:
            if deadline.expired():
                events.append(
                    {
                        "type": "episode_deadline_reached",
                        "attempt": attempt.provider_attempt_count,
                        "elapsed_seconds": round(deps.budget.elapsed_seconds, 2),
                    }
                )
                raise EpisodeDeadlineExceeded(
                    "episode exploration window ended"
                ) from exc
            events.append(
                {
                    "type": "provider_attempt_failed",
                    "attempt": attempt.provider_attempt_count,
                    "error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                }
            )
            raise
        except Exception as exc:
            if isinstance(exc, ModelAPIError):
                events.append(
                    {
                        "type": "provider_attempt_failed",
                        "attempt": attempt.provider_attempt_count,
                        "error_type": type(exc).__name__,
                        "status_code": getattr(exc, "status_code", None),
                    }
                )
            raise
        finally:
            deps.budget.observe_run_usage(usage)
            deps.budget.end_episode()

    @staticmethod
    def _classify_provider_error(
        exc: ModelAPIError,
    ) -> tuple[RunFailureClass, bool, str]:
        if isinstance(exc, ModelHTTPError):
            status = exc.status_code
            if status == 429 or status >= 500:
                return RunFailureClass.TRANSIENT_EXTERNAL, True, "external_dependency_unavailable"
            if status in {401, 402, 403}:
                return RunFailureClass.PERMANENT_EXTERNAL, False, "external_account_unavailable"
            return RunFailureClass.PROVIDER_PROTOCOL, False, "provider_protocol_error"
        return RunFailureClass.TRANSIENT_EXTERNAL, True, "external_dependency_unavailable"

    @staticmethod
    def _blocking_tool_provider_failure(
        workspace: TripWorkspace, events: list[dict[str, object]]
    ) -> tuple[RunFailureClass, bool, str] | None:
        if workspace.current_draft is not None:
            return None
        failures = [
            event
            for event in events
            if event.get("type") in {"provider_attempt_failed", "provider_circuit_open"}
        ]
        if not failures:
            return None
        retryable = any(bool(event.get("retryable", True)) for event in failures)
        if retryable:
            return (
                RunFailureClass.TRANSIENT_EXTERNAL,
                True,
                "external_dependency_unavailable",
            )
        return RunFailureClass.PERMANENT_EXTERNAL, False, "external_account_unavailable"

    async def _try_provider_recovery_episode(
        self,
        *,
        run: AgentRun,
        model: Model,
        budget: RuntimeBudget,
        events: PersistentEventList,
    ) -> str | None:
        if any(event.get("type") == "provider_retry_scheduled" for event in events):
            return None
        workspace = self.repository.get_workspace(run.workspace_id)
        current = self.repository.get_run(run.run_id)
        remaining_seconds = budget.max_seconds - budget.elapsed_seconds
        if (
            workspace is None
            or current is None
            or current.status is not RunStatus.RUNNING
            or remaining_seconds < 30
        ):
            return None
        events.append(
            {
                "type": "provider_retry_scheduled",
                "remaining_seconds": round(remaining_seconds, 2),
            }
        )
        provider_run_id = f"provider-{run.run_id}-retry-{uuid4()}"
        deps = TripAgentDeps(
            repository=self.repository,
            place_cache_repository=self.place_cache_repository,
            place_knowledge=self.place_knowledge,
            workspace_id=workspace.workspace_id,
            run_id=run.run_id,
            provider_run_id=provider_run_id,
            events=events,
            budget=budget,
            narrative_generator=self.narrative_generator,
        )
        agent = build_trip_planner_agent(
            model,
            capabilities=[
                self.persistence.capability(
                    agent_name="trip_planner_v2_provider_retry", run_id=provider_run_id
                )
            ],
        )
        try:
            result = await self._run_episode(
                agent=agent,
                user_prompt=(
                    "Continue the same business run from the current TripWorkspace after a transient provider "
                    "failure. Read the workspace, reuse completed work, avoid repeating discovery, form an early "
                    "complete draft, validate it, and submit."
                ),
                deps=deps,
                run=run,
                events=events,
                timeout_seconds=remaining_seconds,
            )
        except Exception:
            return None
        output = "waiting_user" if isinstance(result.output, DeferredToolRequests) else str(result.output)
        current = self.repository.get_run(run.run_id)
        if isinstance(result.output, DeferredToolRequests):
            self.persistence.save_deferred_messages(provider_run_id, result.all_messages())
            return output
        if current and current.status is RunStatus.PUBLISHED:
            events.append({"type": "provider_retry_recovered"})
            return output
        return None

    async def _provider_failure_outcome(
        self,
        *,
        run: AgentRun,
        workspace: TripWorkspace,
        model: Model,
        deps: TripAgentDeps,
        events: PersistentEventList,
        exc: ModelAPIError,
    ) -> TripAgentRunOutcome:
        failure_class, retryable, error_code = self._classify_provider_error(exc)
        if retryable:
            recovered_output = await self._try_provider_recovery_episode(
                run=run, model=model, budget=deps.budget, events=events
            )
            if recovered_output is not None:
                current = self.repository.get_run(run.run_id)
                return TripAgentRunOutcome(
                    workspace_id=workspace.workspace_id,
                    run_id=run.run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output=recovered_output,
                    events=tuple(events),
                )
        if retryable and await publish_latest_complete_checkpoint(deps) is not None:
            current = self.repository.get_run(run.run_id)
            return TripAgentRunOutcome(
                workspace_id=workspace.workspace_id,
                run_id=run.run_id,
                status=current.status if current else RunStatus.PUBLISHED,
                output="published_complete_checkpoint",
                events=tuple(events),
            )
        current = self.repository.get_run(run.run_id)
        target = RunStatus.INCOMPLETE if retryable else RunStatus.FAILED
        if current and current.status not in TERMINAL_RUN_STATUSES:
            current = self.repository.transition_run(
                run.run_id,
                target,
                error_code=error_code,
                error_message=str(exc)[:4_000],
                failure_class=failure_class,
                retryable=retryable,
            )
        return TripAgentRunOutcome(
            workspace_id=workspace.workspace_id,
            run_id=run.run_id,
            status=current.status if current else target,
            output="",
            events=tuple(events),
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

    async def _try_convergence_episode(
        self,
        *,
        run: AgentRun,
        model: Model,
        budget: RuntimeBudget,
        events: PersistentEventList,
        continuation_context: str | None = None,
    ) -> str | None:
        """Continue the same business run with a compact, publication-focused episode."""

        workspace = self.repository.get_workspace(run.workspace_id)
        current = self.repository.get_run(run.run_id)
        if workspace is None or current is None or current.status is not RunStatus.RUNNING:
            return None
        remaining_seconds = budget.max_seconds - budget.elapsed_seconds
        if remaining_seconds < 30:
            return None
        budget.enter_convergence()
        provider_run_id = f"provider-{run.run_id}-convergence-{uuid4()}"
        events.append(
            {
                "type": "convergence_episode_started",
                "remaining_seconds": round(remaining_seconds, 2),
            }
        )
        deps = TripAgentDeps(
            repository=self.repository,
            place_cache_repository=self.place_cache_repository,
            place_knowledge=self.place_knowledge,
            workspace_id=workspace.workspace_id,
            run_id=run.run_id,
            provider_run_id=provider_run_id,
            events=events,
            budget=budget,
            narrative_generator=self.narrative_generator,
        )
        agent = build_trip_planner_agent(
            model,
            capabilities=[
                self.persistence.capability(
                    agent_name="trip_planner_v2_convergence", run_id=provider_run_id
                )
            ],
        )
        prompt = (
            "This is the convergence episode for the same TripWorkspace. Read the current workspace. "
            "Do not restart discovery or optimize optional references. Close only hard execution gaps; "
            "keep omitted strong preferences as explicit experience issues rather than publication blockers. "
            "create a complete draft that accounts for every trip day, acquire only facts required by the "
            "current route, simulate, repair the returned structural/completeness counterexamples, and submit. "
            "Prefer a complete honest route with explicit estimates over further candidate expansion."
        )
        if continuation_context:
            prompt = f"{prompt}\nContinuation context: {continuation_context}"
        try:
            result = await self._run_episode(
                agent=agent,
                user_prompt=prompt,
                deps=deps,
                run=run,
                events=events,
                timeout_seconds=remaining_seconds,
            )
        except Exception as exc:
            events.append(
                {
                    "type": "convergence_episode_failed",
                    "error_type": type(exc).__name__,
                }
            )
            return None
        output = "waiting_user" if isinstance(result.output, DeferredToolRequests) else str(result.output)
        current = self.repository.get_run(run.run_id)
        if isinstance(result.output, DeferredToolRequests):
            self.persistence.save_deferred_messages(provider_run_id, result.all_messages())
            return output
        if current and current.status is RunStatus.PUBLISHED:
            events.append({"type": "convergence_episode_published"})
            return output
        events.append({"type": "convergence_episode_ended_without_publication"})
        return None

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
            place_cache_repository=self.place_cache_repository,
            place_knowledge=self.place_knowledge,
            workspace_id=workspace.workspace_id,
            run_id=run.run_id,
            provider_run_id=provider_run_id,
            events=events,
            budget=self._budget(run.run_id, workspace),
            narrative_generator=self.narrative_generator,
        )
        capabilities = [
            self.persistence.capability(agent_name="trip_planner_v2", run_id=provider_run_id)
        ]
        agent = build_trip_planner_agent(model, capabilities=capabilities)
        output = ""
        try:
            result = await self._run_episode(
                agent=agent,
                user_prompt=workspace.goal_ledger.goal.raw_request,
                deps=deps,
                run=run,
                events=events,
                timeout_seconds=self._episode_timeout(deps.budget),
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
        except (UsageLimitExceeded, EpisodeDeadlineExceeded) as exc:
            convergence_output = await self._try_convergence_episode(
                run=run,
                model=model,
                budget=deps.budget,
                events=events,
            )
            if convergence_output is not None:
                current = self.repository.get_run(run.run_id)
                return TripAgentRunOutcome(
                    workspace_id=workspace.workspace_id,
                    run_id=run.run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output=convergence_output,
                    events=tuple(events),
                )
            if await publish_latest_complete_checkpoint(deps) is not None:
                current = self.repository.get_run(run.run_id)
                return TripAgentRunOutcome(
                    workspace_id=workspace.workspace_id,
                    run_id=run.run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output="published_complete_checkpoint",
                    events=tuple(events),
                )
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
                    failure_class=RunFailureClass.BUDGET,
                    retryable=True,
                )
            return TripAgentRunOutcome(
                workspace_id=workspace.workspace_id,
                run_id=run.run_id,
                status=current.status if current else RunStatus.INCOMPLETE,
                output=output,
                events=tuple(events),
            )
        except (BudgetExhausted, TimeoutError) as exc:
            if await publish_latest_complete_checkpoint(deps) is not None:
                current = self.repository.get_run(run.run_id)
                return TripAgentRunOutcome(
                    workspace_id=workspace.workspace_id,
                    run_id=run.run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output="published_complete_checkpoint",
                    events=tuple(events),
                )
            current = self.repository.get_run(run.run_id)
            if current and current.status not in TERMINAL_RUN_STATUSES:
                current = self.repository.transition_run(
                    run.run_id,
                    RunStatus.INCOMPLETE,
                    error_code="agent_budget_exhausted",
                    error_message=str(exc)[:4_000],
                    failure_class=RunFailureClass.BUDGET,
                    retryable=True,
                )
            return TripAgentRunOutcome(
                workspace_id=workspace.workspace_id,
                run_id=run.run_id,
                status=current.status if current else RunStatus.INCOMPLETE,
                output=output,
                events=tuple(events),
            )
        except ModelAPIError as exc:
            return await self._provider_failure_outcome(
                run=run,
                workspace=workspace,
                model=model,
                deps=deps,
                events=events,
                exc=exc,
            )
        except UnexpectedModelBehavior as exc:
            if not _is_recoverable_agent_exhaustion(exc):
                current = self.repository.get_run(run.run_id)
                if current and current.status not in TERMINAL_RUN_STATUSES:
                    current = self.repository.transition_run(
                        run.run_id,
                        RunStatus.FAILED,
                        error_code="provider_protocol_error",
                        error_message=str(exc)[:4_000],
                        failure_class=RunFailureClass.PROVIDER_PROTOCOL,
                        retryable=False,
                    )
                return TripAgentRunOutcome(
                    workspace_id=workspace.workspace_id,
                    run_id=run.run_id,
                    status=current.status if current else RunStatus.FAILED,
                    output="",
                    events=tuple(events),
                )
            if await publish_latest_complete_checkpoint(deps) is not None:
                current = self.repository.get_run(run.run_id)
                return TripAgentRunOutcome(
                    workspace_id=workspace.workspace_id,
                    run_id=run.run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output="published_complete_checkpoint",
                    events=tuple(events),
                )
            current_workspace = self.repository.get_workspace(workspace.workspace_id) or workspace
            provider_failure = self._blocking_tool_provider_failure(
                current_workspace, events
            )
            current = self.repository.get_run(run.run_id)
            if current and current.status not in TERMINAL_RUN_STATUSES:
                failure_class, retryable, error_code = provider_failure or (
                    RunFailureClass.BUDGET,
                    True,
                    "agent_action_repair_exhausted",
                )
                current = self.repository.transition_run(
                    run.run_id,
                    RunStatus.INCOMPLETE if retryable else RunStatus.FAILED,
                    error_code=error_code,
                    error_message=(
                        "Agent exhausted a bounded action repair budget without publishing. "
                        "The latest workspace remains available for a revision run."
                    ),
                    failure_class=failure_class,
                    retryable=retryable,
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
                    failure_class=RunFailureClass.INTERNAL,
                    retryable=False,
                )
            return TripAgentRunOutcome(
                workspace_id=workspace.workspace_id,
                run_id=run.run_id,
                status=current.status if current else RunStatus.FAILED,
                output="",
                events=tuple(events),
            )

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
        workspace = self.repository.get_workspace(run.workspace_id)
        if workspace is None:
            raise KeyError(run.workspace_id)
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
            place_cache_repository=self.place_cache_repository,
            place_knowledge=self.place_knowledge,
            workspace_id=run.workspace_id,
            run_id=run_id,
            provider_run_id=provider_run_id,
            events=events,
            budget=self._budget(run_id, workspace),
            narrative_generator=self.narrative_generator,
        )
        agent = build_trip_planner_agent(
            model,
            capabilities=[
                self.persistence.capability(agent_name="trip_planner_v2", run_id=provider_run_id)
            ],
        )
        try:
            result = await self._run_episode(
                agent=agent,
                user_prompt=None,
                deps=deps,
                run=run,
                events=events,
                timeout_seconds=self._episode_timeout(deps.budget),
                message_history=message_history,
                deferred_tool_results=DeferredToolResults(
                    calls={
                        interruption.tool_call_id: {
                            "answers": [item.model_dump(mode="json") for item in answer_record.answers]
                        }
                    }
                ),
            )
        except ModelAPIError as exc:
            return await self._provider_failure_outcome(
                run=run,
                workspace=workspace,
                model=model,
                deps=deps,
                events=events,
                exc=exc,
            )
        except (UsageLimitExceeded, EpisodeDeadlineExceeded) as exc:
            convergence_output = await self._try_convergence_episode(
                run=run,
                model=model,
                budget=deps.budget,
                events=events,
                continuation_context=(
                    "The user answered the pending clarification with: "
                    + "; ".join(
                        f"{item.question_id}={item.value}" for item in answer_record.answers
                    )
                ),
            )
            if convergence_output is not None:
                current = self.repository.get_run(run_id)
                return TripAgentRunOutcome(
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output=convergence_output,
                    events=tuple(events),
                )
            if await publish_latest_complete_checkpoint(deps) is not None:
                current = self.repository.get_run(run_id)
                return TripAgentRunOutcome(
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output="published_complete_checkpoint",
                    events=tuple(events),
                )
            current = self.repository.get_run(run_id)
            if current and current.status not in TERMINAL_RUN_STATUSES:
                current = self.repository.transition_run(
                    run_id,
                    RunStatus.INCOMPLETE,
                    error_code="agent_budget_exhausted",
                    error_message=str(exc)[:4_000],
                    failure_class=RunFailureClass.BUDGET,
                    retryable=True,
                )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status if current else RunStatus.INCOMPLETE,
                output="",
                events=tuple(events),
            )
        except (BudgetExhausted, TimeoutError) as exc:
            if await publish_latest_complete_checkpoint(deps) is not None:
                current = self.repository.get_run(run_id)
                return TripAgentRunOutcome(
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output="published_complete_checkpoint",
                    events=tuple(events),
                )
            current = self.repository.get_run(run_id)
            if current and current.status not in TERMINAL_RUN_STATUSES:
                current = self.repository.transition_run(
                    run_id,
                    RunStatus.INCOMPLETE,
                    error_code="agent_budget_exhausted",
                    error_message=str(exc)[:4_000],
                    failure_class=RunFailureClass.BUDGET,
                    retryable=True,
                )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status if current else RunStatus.INCOMPLETE,
                output="",
                events=tuple(events),
            )
        except UnexpectedModelBehavior as exc:
            if not _is_recoverable_agent_exhaustion(exc):
                current = self.repository.get_run(run_id)
                if current and current.status not in TERMINAL_RUN_STATUSES:
                    current = self.repository.transition_run(
                        run_id,
                        RunStatus.FAILED,
                        error_code="provider_protocol_error",
                        error_message=str(exc)[:4_000],
                        failure_class=RunFailureClass.PROVIDER_PROTOCOL,
                        retryable=False,
                    )
                return TripAgentRunOutcome(
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    status=current.status if current else RunStatus.FAILED,
                    output="",
                    events=tuple(events),
                )
            current_workspace = self.repository.get_workspace(run.workspace_id) or workspace
            provider_failure = self._blocking_tool_provider_failure(
                current_workspace, events
            )
            current = self.repository.get_run(run_id)
            if current and current.status not in TERMINAL_RUN_STATUSES:
                failure_class, retryable, error_code = provider_failure or (
                    RunFailureClass.BUDGET,
                    True,
                    "agent_action_repair_exhausted",
                )
                current = self.repository.transition_run(
                    run_id,
                    RunStatus.INCOMPLETE if retryable else RunStatus.FAILED,
                    error_code=error_code,
                    error_message="Agent exhausted a bounded action repair budget after clarification.",
                    failure_class=failure_class,
                    retryable=retryable,
                )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status if current else RunStatus.INCOMPLETE,
                output="",
                events=tuple(events),
            )
        except Exception as exc:
            current = self.repository.get_run(run_id)
            if current and current.status not in TERMINAL_RUN_STATUSES:
                current = self.repository.transition_run(
                    run_id,
                    RunStatus.FAILED,
                    error_code="internal_error",
                    error_message=str(exc)[:4_000],
                    failure_class=RunFailureClass.INTERNAL,
                    retryable=False,
                )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status if current else RunStatus.FAILED,
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
        mutation_tools = MUTATION_TOOLS
        resolved_mutation_results = [
            (tool_call_id, tool_name, result)
            for tool_call_id, tool_name in unresolved
            if tool_name in mutation_tools
            and (
                result := self.repository.get_tool_effect(
                    tool_call_id, run_id=run_id, tool_name=tool_name
                )
            )
            is not None
        ]
        unknown_mutations = [
            {"tool_call_id": tool_call_id, "tool_name": tool_name}
            for tool_call_id, tool_name in unresolved
            if tool_name in mutation_tools
            and self.repository.get_tool_effect(
                tool_call_id, run_id=run_id, tool_name=tool_name
            )
            is None
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
            place_cache_repository=self.place_cache_repository,
            place_knowledge=self.place_knowledge,
            workspace_id=run.workspace_id,
            run_id=run_id,
            provider_run_id=provider_run_id,
            events=events,
            budget=self._budget(run_id, workspace),
            narrative_generator=self.narrative_generator,
        )
        agent = build_trip_planner_agent(
            model,
            capabilities=[
                self.persistence.capability(agent_name="trip_planner_v2", run_id=provider_run_id)
            ],
        )
        try:
            result = await self._run_episode(
                agent=agent,
                user_prompt=None,
                deps=deps,
                run=run,
                events=events,
                timeout_seconds=self._episode_timeout(deps.budget),
                message_history=message_history,
            )
        except ModelAPIError as exc:
            return await self._provider_failure_outcome(
                run=run,
                workspace=workspace,
                model=model,
                deps=deps,
                events=events,
                exc=exc,
            )
        except (UsageLimitExceeded, EpisodeDeadlineExceeded) as exc:
            convergence_output = await self._try_convergence_episode(
                run=run,
                model=model,
                budget=deps.budget,
                events=events,
            )
            if convergence_output is not None:
                current = self.repository.get_run(run_id)
                return TripAgentRunOutcome(
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output=convergence_output,
                    events=tuple(events),
                )
            if await publish_latest_complete_checkpoint(deps) is not None:
                current = self.repository.get_run(run_id)
                return TripAgentRunOutcome(
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output="published_complete_checkpoint",
                    events=tuple(events),
                )
            current = self.repository.transition_run(
                run_id,
                RunStatus.INCOMPLETE,
                error_code="agent_budget_exhausted",
                error_message=str(exc)[:4_000],
                failure_class=RunFailureClass.BUDGET,
                retryable=True,
            )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status,
                output="",
                events=tuple(events),
            )
        except (BudgetExhausted, TimeoutError) as exc:
            if await publish_latest_complete_checkpoint(deps) is not None:
                current = self.repository.get_run(run_id)
                return TripAgentRunOutcome(
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    status=current.status if current else RunStatus.PUBLISHED,
                    output="published_complete_checkpoint",
                    events=tuple(events),
                )
            current = self.repository.transition_run(
                run_id,
                RunStatus.INCOMPLETE,
                error_code="agent_budget_exhausted",
                error_message=str(exc)[:4_000],
                failure_class=RunFailureClass.BUDGET,
                retryable=True,
            )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status,
                output="",
                events=tuple(events),
            )
        except UnexpectedModelBehavior as exc:
            recoverable = _is_recoverable_agent_exhaustion(exc)
            current_workspace = self.repository.get_workspace(run.workspace_id) or workspace
            provider_failure = (
                self._blocking_tool_provider_failure(current_workspace, events)
                if recoverable
                else None
            )
            failure_class, retryable, error_code = provider_failure or (
                RunFailureClass.BUDGET if recoverable else RunFailureClass.PROVIDER_PROTOCOL,
                recoverable,
                "agent_action_repair_exhausted" if recoverable else "provider_protocol_error",
            )
            current = self.repository.transition_run(
                run_id,
                RunStatus.INCOMPLETE if retryable else RunStatus.FAILED,
                error_code=error_code,
                error_message=str(exc)[:4_000],
                failure_class=failure_class,
                retryable=retryable,
            )
            return TripAgentRunOutcome(
                workspace_id=run.workspace_id,
                run_id=run_id,
                status=current.status,
                output="",
                events=tuple(events),
            )
        except Exception as exc:
            current = self.repository.transition_run(
                run_id,
                RunStatus.FAILED,
                error_code="internal_error",
                error_message=str(exc)[:4_000],
                failure_class=RunFailureClass.INTERNAL,
                retryable=False,
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
