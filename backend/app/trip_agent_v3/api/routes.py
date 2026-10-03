from __future__ import annotations

import asyncio
from datetime import date
from functools import lru_cache
import logging
import math
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from openai import APIConnectionError
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core import AppError
from app.trip_agent_v3.adapters.provider import (
    DeepSeekModelVariant,
    build_deepseek_v4_model,
)
from app.trip_agent_v3.adapters.provider_coordinator import (
    ProviderCircuitOpen,
    ProviderRequestCoordinator,
)
from app.trip_agent_v3.adapters import (
    AmapFactProvider,
    AmapPlaceSearchProvider,
    DeepSeekOperationalFactProvider,
    DeepSeekRequirementInterpreter,
    PydanticAIRootTripPlannerAgent,
)
from app.trip_agent_v3.autonomous_runtime import (
    AutonomousRuntimeCancelled,
    execute_autonomous_journey,
)
from app.trip_agent_v3.domain.delivery import RunStatus
from app.trip_agent_v3.domain.execution import (
    JourneyGoal,
    TripWorkspaceRecord,
)
from app.trip_agent_v3.repository import SqliteTripAgentV3Repository
from app.trip_agent_v3.repository import V3RepositoryConflict
from app.trip_agent_v3.requirements import source_document
from app.trip_agent_v3.telemetry import RunTelemetry


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v3", tags=["trip-agent-v3"])


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceCreate(ApiModel):
    raw_request: str = Field(min_length=1, max_length=100_000)
    destination: str = Field(min_length=1, max_length=200)
    start_date: date
    days: int = Field(ge=1, le=30)


class RunCreate(ApiModel):
    idempotency_key: str = Field(min_length=1, max_length=300)


class ClarificationAnswerInput(ApiModel):
    answers: dict[str, str] = Field(min_length=1, max_length=20)


class RevisionCreate(ApiModel):
    raw_request: str = Field(min_length=1, max_length=100_000)
    idempotency_key: str = Field(min_length=1, max_length=300)


def _safe_error_diagnostic(exc: Exception) -> dict[str, object]:
    if isinstance(exc, ValidationError):
        return {
            "validation_issues": [
                {
                    "location": ".".join(str(part) for part in issue["loc"]),
                    "type": issue["type"],
                    "message": issue["msg"],
                }
                for issue in exc.errors(
                    include_input=False,
                    include_url=False,
                )[:20]
            ]
        }
    return {}


def _provider_blocker_diagnostic(
    exc: Exception,
) -> dict[str, object] | None:
    if isinstance(exc, ProviderCircuitOpen):
        return {
            "reason_code": "provider_quota_exhausted" if exc.reason == "DAILY_QUERY_OVER_LIMIT" else "provider_configuration_required",
            "provider": exc.provider,
            "retryable_now": False,
        }
    if isinstance(exc, AppError):
        provider_code = exc.details.get("provider_code")
        if exc.code == "missing_configuration" or provider_code == "DAILY_QUERY_OVER_LIMIT":
            return {
                "reason_code": "provider_quota_exhausted" if provider_code else "provider_configuration_required",
                "provider": "external_provider",
                "retryable_now": False,
            }
        if exc.code == "amap_network_error" or exc.details.get("retryable"):
            return {
                "reason_code": "provider_temporarily_unavailable",
                "provider": "amap",
                "retryable_now": True,
            }
    status_code = (
        exc.details.get("http_status")
        if isinstance(exc, AppError)
        else getattr(exc, "status_code", None)
    )
    if status_code is not None:
        try:
            status_code = int(status_code)
        except (TypeError, ValueError):
            return None
        if status_code not in {401, 402, 408, 409, 429} and status_code < 500:
            return None
        reason_code = {
            401: "provider_authentication_failed",
            402: "provider_balance_insufficient",
        }.get(status_code, "provider_temporarily_unavailable")
        return {
            "reason_code": reason_code,
            "provider": str(
                getattr(exc, "model_name", None) or "external_model"
            ),
            "http_status": status_code,
            "retryable_now": status_code not in {401, 402},
        }
    if isinstance(exc, (TimeoutError, ConnectionError, httpx.TransportError, APIConnectionError)):
        return {
            "reason_code": "provider_temporarily_unavailable",
            "provider": "external_provider",
            "retryable_now": True,
        }
    return None


class AgentV3Runtime:
    def __init__(
        self,
        database: Path,
        *,
        root_model_variant: DeepSeekModelVariant | None = "flash",
        execution_timeout_seconds: float = 300,
    ) -> None:
        if not math.isfinite(execution_timeout_seconds) or execution_timeout_seconds <= 0:
            raise ValueError("execution_timeout_seconds must be positive and finite")
        self.execution_timeout_seconds = execution_timeout_seconds
        self.repository = SqliteTripAgentV3Repository(database)
        self.coordinator = ProviderRequestCoordinator()
        self.root_model_variant = root_model_variant

    async def execute(self, run_id: str) -> None:
        started_at = perf_counter()
        try:
            run = self.repository.claim_run_for_execution(run_id)
        except KeyError:
            return
        if run is None:
            return
        workspace = self.repository.get_workspace(run.workspace_id)
        if workspace is None:
            return
        previous_metrics = self.repository.get_run_metrics(run_id)
        telemetry = RunTelemetry.from_snapshot(previous_metrics)
        previous_wall_clock_ms = int(
            (previous_metrics or {}).get("wall_clock_ms", 0)
        )
        self.repository.append_event(
            run_id,
            {
                "type": "run_started",
                "goal_revision_id": run.goal_revision_id,
            },
        )
        timeout_scope: asyncio.Timeout | None = None
        try:
            root_model = (
                build_deepseek_v4_model("root")
                if self.root_model_variant is None
                else build_deepseek_v4_model(
                    "root",
                    model_variant=self.root_model_variant,
                )
            )
            async with asyncio.timeout(self.execution_timeout_seconds) as timeout_scope:
                result = await execute_autonomous_journey(
                    run=run,
                    goal=workspace.goal,
                    sources=workspace.sources,
                    requirement_interpreter=DeepSeekRequirementInterpreter(
                        build_deepseek_v4_model("lightweight"),
                        telemetry=telemetry,
                    ),
                    root_agent=PydanticAIRootTripPlannerAgent(
                        root_model
                    ),
                    place_provider=AmapPlaceSearchProvider(
                        coordinator=self.coordinator
                    ),
                    fact_provider=DeepSeekOperationalFactProvider(
                        model=build_deepseek_v4_model("lightweight"),
                        route_provider=AmapFactProvider(
                            city=workspace.goal.destination,
                            coordinator=self.coordinator,
                        ),
                        telemetry=telemetry,
                    ),
                    repository=self.repository,
                    telemetry=telemetry,
                )
        except asyncio.CancelledError:
            current = self.repository.get_run(run_id)
            if current is not None and current.status is RunStatus.ACTIVE:
                self.repository.transition_run(
                    run_id, RunStatus.NEEDS_RESUME
                )
            self.repository.append_event(
                run_id,
                {
                    "type": "run_interrupted",
                    "reason": "executor task cancelled before completion",
                },
            )
            raise
        except AutonomousRuntimeCancelled:
            current = self.repository.get_run(run_id)
            if current is not None and current.status is RunStatus.CANCELLED:
                self.repository.append_event(
                    run_id,
                    {"type": "run_cancelled_observed"},
                )
            else:
                raise
        except Exception as exc:
            if isinstance(exc, TimeoutError) and timeout_scope is not None and timeout_scope.expired():
                current = self.repository.get_run(run_id)
                if current is not None and current.status is RunStatus.ACTIVE:
                    self.repository.transition_run(run_id, RunStatus.NEEDS_RESUME)
                    self.repository.append_event(run_id, {
                        "type": "planning_time_budget_exhausted",
                        "reason_code": "planning_time_budget_exhausted",
                        "timeout_seconds": self.execution_timeout_seconds,
                        "message": "本轮规划已达到时间上限，进度已保存，可继续当前运行。",
                    })
                return
            provider_blocker = _provider_blocker_diagnostic(exc)
            current = self.repository.get_run(run_id)
            if provider_blocker is not None:
                logger.warning(
                    "Agent V3 paused by provider for run %s: %s",
                    run_id,
                    provider_blocker["reason_code"],
                )
                if current is not None and current.status is RunStatus.ACTIVE:
                    self.repository.transition_run(
                        run_id, RunStatus.NEEDS_RESUME
                    )
                self.repository.append_event(
                    run_id,
                    {
                        "type": "provider_blocked",
                        **provider_blocker,
                        "message": (
                            "外部模型服务当前不可用，运行已安全暂停；"
                            "当前候选不会发布。"
                        ),
                    },
                )
            else:
                logger.exception(
                    "Agent V3 execution failed for run %s", run_id
                )
                if current is not None and current.status not in {
                    RunStatus.CANCELLED,
                    RunStatus.SUCCEEDED,
                    RunStatus.WAITING_USER,
                }:
                    self.repository.transition_run(
                        run_id, RunStatus.FAILED
                    )
                self.repository.append_event(
                    run_id,
                    {
                        "type": "run_failed",
                        "error_type": type(exc).__name__,
                        "message": "Agent 运行未完成，未产生发布版本。",
                        **_safe_error_diagnostic(exc),
                    },
                )
        finally:
            current = self.repository.get_run(run_id)
            candidate = self.repository.latest_candidate_for_run(run_id)
            assessment = (
                self.repository.assessment_for_candidate(
                    candidate.candidate_snapshot_id
                )
                if candidate is not None
                else None
            )
            release = self.repository.release_for_run(run_id)
            if current is not None:
                self.repository.save_run_metrics(
                    run_id=run_id,
                    payload=telemetry.snapshot(
                        run_status=current.status.value,
                        wall_clock_ms=(
                            previous_wall_clock_ms
                            + int((perf_counter() - started_at) * 1_000)
                        ),
                        delivery_state=(
                            assessment.state.value
                            if assessment is not None
                            else None
                        ),
                        fact_status=(
                            candidate.fact_status.value
                            if candidate is not None
                            else None
                        ),
                        experience_status=(
                            candidate.experience_status.value
                            if candidate is not None
                            else None
                        ),
                        candidate_snapshot_id=(
                            candidate.candidate_snapshot_id
                            if candidate is not None
                            else None
                        ),
                        release_id=(
                            release.release_id
                            if release is not None
                            else None
                        ),
                    ),
                )


@lru_cache(maxsize=1)
def runtime() -> AgentV3Runtime:
    database = Path(
        os.getenv(
            "AGENT_V3_DATABASE_PATH", "data/trip_agent_v3.sqlite3"
        )
    )
    return AgentV3Runtime(database)


def repository() -> SqliteTripAgentV3Repository:
    return runtime().repository


@router.post("/trip-workspaces", status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate):
    workspace_id = f"workspace-{uuid4()}"
    goal = JourneyGoal(
        goal_revision_id=f"goal-{uuid4()}",
        destination=payload.destination,
        start_date=payload.start_date,
        days=payload.days,
    )
    source = source_document(
        source_id=f"source-{uuid4()}",
        kind="user_request",
        content=payload.raw_request,
    )
    workspace = TripWorkspaceRecord(
        workspace_id=workspace_id,
        goal=goal,
        sources=(source,),
    )
    repository().save_workspace(workspace)
    return {
        "workspace_id": workspace.workspace_id,
        "workspace": workspace.model_dump(mode="json"),
    }


@router.get("/trip-workspaces/{workspace_id}")
def get_workspace(workspace_id: str):
    workspace = repository().get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return {"workspace": workspace.model_dump(mode="json")}


@router.post(
    "/trip-workspaces/{workspace_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_run(
    workspace_id: str,
    payload: RunCreate,
    background_tasks: BackgroundTasks,
):
    try:
        run = repository().create_run(
            workspace_id=workspace_id,
            idempotency_key=payload.idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="workspace not found"
        ) from exc
    except V3RepositoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if run.status is RunStatus.CREATED:
        background_tasks.add_task(runtime().execute, run.run_id)
    return {"run": run.model_dump(mode="json")}


@router.get("/agent-runs/{run_id}")
def get_run(run_id: str):
    run = repository().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    clarification_event = next(
        (
            event
            for event in reversed(repository().list_events(run_id))
            if event.get("type") == "clarification_requested"
        ),
        None,
    )
    return {
        "run": run.model_dump(mode="json"),
        "clarification_questions": (
            clarification_event.get("questions", [])
            if clarification_event
            else []
        ),
    }


@router.get("/agent-runs/{run_id}/events")
def get_events(run_id: str, after_event_id: int = Query(default=0, ge=0)):
    if repository().get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "events": repository().list_events(
            run_id, after_event_id=after_event_id
        )
    }


@router.get("/agent-runs/{run_id}/metrics")
def get_run_metrics(run_id: str):
    if repository().get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "run_id": run_id,
        "metrics": repository().get_run_metrics(run_id),
    }


@router.post("/agent-runs/{run_id}/cancel")
def cancel_run(run_id: str):
    run = repository().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status in {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }:
        return {"run": run.model_dump(mode="json")}
    cancelled = repository().transition_run(run_id, RunStatus.CANCELLED)
    repository().append_event(run_id, {"type": "run_cancelled"})
    return {"run": cancelled.model_dump(mode="json")}


@router.post(
    "/agent-runs/{run_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
)
def resume_run(run_id: str, background_tasks: BackgroundTasks):
    run = repository().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status in {RunStatus.CREATED, RunStatus.ACTIVE}:
        return {"run": run.model_dump(mode="json"), "accepted": False}
    if run.status is not RunStatus.NEEDS_RESUME:
        raise HTTPException(
            status_code=409, detail="run is not resumable"
        )
    repository().append_event(
        run_id,
        {"type": "run_resume_requested"},
    )
    background_tasks.add_task(runtime().execute, run_id)
    return {"run": run.model_dump(mode="json"), "accepted": True}


@router.post(
    "/agent-runs/{run_id}/answers",
    status_code=status.HTTP_202_ACCEPTED,
)
def answer_clarification(
    run_id: str,
    payload: ClarificationAnswerInput,
    background_tasks: BackgroundTasks,
):
    run = repository().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status is not RunStatus.WAITING_USER:
        raise HTTPException(
            status_code=409, detail="run is not waiting for answers"
        )
    clarification_event = next(
        (
            event
            for event in reversed(repository().list_events(run_id))
            if event.get("type") == "clarification_requested"
        ),
        None,
    )
    expected_questions = set(
        clarification_event.get("questions", [])
        if clarification_event is not None
        else []
    )
    if not expected_questions or set(payload.answers) != expected_questions:
        raise HTTPException(
            status_code=409,
            detail=(
                "answers must match every question from the current "
                "clarification request"
            ),
        )
    workspace = repository().get_workspace(run.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    answer_text = "\n".join(
        f"{question}: {answer}" for question, answer in payload.answers.items()
    )
    answer_source = source_document(
        source_id=f"source-{uuid4()}",
        kind="user_revision",
        content=f"用户对地点澄清的回答：\n{answer_text}",
    )
    updated_workspace = TripWorkspaceRecord(
        workspace_id=workspace.workspace_id,
        version=workspace.version + 1,
        goal=workspace.goal,
        sources=workspace.sources + (answer_source,),
    )
    try:
        repository().update_workspace(
            updated_workspace, expected_version=workspace.version
        )
    except V3RepositoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    resumed = repository().transition_run(run_id, RunStatus.NEEDS_RESUME)
    repository().append_event(
        run_id,
        {
            "type": "clarification_answered",
            "answer_count": len(payload.answers),
        },
    )
    background_tasks.add_task(runtime().execute, run_id)
    return {"run": resumed.model_dump(mode="json"), "accepted": True}


@router.post(
    "/trip-workspaces/{workspace_id}/revisions",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_revision(
    workspace_id: str,
    payload: RevisionCreate,
    background_tasks: BackgroundTasks,
):
    existing_run = repository().get_run_by_idempotency_key(
        workspace_id=workspace_id,
        idempotency_key=payload.idempotency_key,
    )
    if existing_run is not None:
        return {"run": existing_run.model_dump(mode="json")}
    workspace = repository().get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    active_run = repository().get_active_run(workspace_id)
    if active_run is not None:
        raise HTTPException(
            status_code=409,
            detail=f"workspace already has active run {active_run.run_id}",
        )
    revision_source = source_document(
        source_id=f"source-{uuid4()}",
        kind="user_revision",
        content=payload.raw_request,
    )
    revised_goal = JourneyGoal(
        goal_revision_id=f"goal-{uuid4()}",
        destination=workspace.goal.destination,
        start_date=workspace.goal.start_date,
        days=workspace.goal.days,
    )
    revised_workspace = TripWorkspaceRecord(
        workspace_id=workspace.workspace_id,
        version=workspace.version + 1,
        goal=revised_goal,
        sources=workspace.sources + (revision_source,),
    )
    try:
        repository().update_workspace(
            revised_workspace, expected_version=workspace.version
        )
        run = repository().create_run(
            workspace_id=workspace_id,
            idempotency_key=payload.idempotency_key,
        )
    except V3RepositoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(runtime().execute, run.run_id)
    return {"run": run.model_dump(mode="json")}


@router.get("/agent-runs/{run_id}/delivery")
def get_run_delivery(run_id: str):
    run = repository().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    release = repository().release_for_run(run_id)
    candidate = (
        repository().get_candidate(release.candidate_snapshot_id)
        if release is not None
        else repository().latest_candidate_for_run(run_id)
    )
    assessment = (
        repository().assessment_for_candidate(
            candidate.candidate_snapshot_id
        )
        if candidate is not None
        else None
    )
    if assessment is not None:
        delivery_state = assessment.state.value
    elif run.status in {
        RunStatus.CREATED,
        RunStatus.ACTIVE,
        RunStatus.WAITING_USER,
        RunStatus.NEEDS_RESUME,
    }:
        delivery_state = "working"
    else:
        delivery_state = "not_delivered"
    return {
        "run_id": run.run_id,
        "run_status": run.status.value,
        "delivery_state": delivery_state,
        "candidate": (
            candidate.model_dump(mode="json") if candidate is not None else None
        ),
        "assessment": (
            assessment.model_dump(mode="json")
            if assessment is not None
            else None
        ),
        "release": (
            release.model_dump(mode="json") if release is not None else None
        ),
    }
