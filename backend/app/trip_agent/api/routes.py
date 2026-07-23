from __future__ import annotations

from datetime import date
from functools import lru_cache
import logging
import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai_harness.step_persistence import SqliteStepStore

from app.trip_agent.adapters.place_knowledge import DeepSeekAmapPlaceKnowledge
from app.trip_agent.adapters.narrative import DeepSeekNarrativeGenerator
from app.trip_agent.adapters.provider import build_deepseek_v4_model
from app.trip_agent.adapters.provider_coordinator import ProviderRequestCoordinator
from app.trip_agent.domain import RunStatus, TERMINAL_RUN_STATUSES
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.runtime import TripAgentService
from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["trip-agent-v2"])


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceCreate(ApiModel):
    raw_request: str = Field(min_length=1, max_length=50_000)
    destination: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=60)


class RunCreate(ApiModel):
    idempotency_key: str = Field(min_length=1, max_length=300)


class ClarificationAnswerInput(ApiModel):
    answers: dict[str, str] = Field(min_length=1, max_length=5)


class RevisionCreate(ApiModel):
    raw_request: str = Field(min_length=1, max_length=50_000)
    idempotency_key: str = Field(min_length=1, max_length=300)


class AgentV2Runtime:
    def __init__(self, database: Path, provider_database: Path, deferred_database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self.repository = SqliteTripAgentRepository(database)
        self.persistence = HarnessPersistenceAdapter(
            SqliteStepStore(database=provider_database),
            deferred_database=deferred_database,
        )
        self.provider_coordinator = ProviderRequestCoordinator()

    def service(self, destination: str | None) -> TripAgentService:
        return TripAgentService(
            self.repository,
            DeepSeekAmapPlaceKnowledge(
                city=destination, coordinator=self.provider_coordinator
            ),
            self.persistence,
            DeepSeekNarrativeGenerator(),
        )

    async def execute(self, run_id: str) -> None:
        run = self.repository.get_run(run_id)
        if run is None:
            return
        workspace = self.repository.get_workspace(run.workspace_id)
        if workspace is None:
            return
        try:
            await self.service(workspace.goal_ledger.goal.destination).execute(
                run_id=run_id,
                model=build_deepseek_v4_model("root"),
            )
        except Exception:
            logger.exception("Agent V2 background execution failed for run %s", run_id)

    async def resume(self, run_id: str, answers: dict[str, str]) -> None:
        run = self.repository.get_run(run_id)
        if run is None:
            return
        workspace = self.repository.get_workspace(run.workspace_id)
        if workspace is None:
            return
        try:
            await self.service(workspace.goal_ledger.goal.destination).resume(
                run_id=run_id,
                answers=answers,
                model=build_deepseek_v4_model("root"),
            )
        except Exception:
            logger.exception("Agent V2 background resume failed for run %s", run_id)

    async def recover(self, run_id: str) -> None:
        run = self.repository.get_run(run_id)
        if run is None:
            return
        workspace = self.repository.get_workspace(run.workspace_id)
        if workspace is None:
            return
        try:
            await self.service(workspace.goal_ledger.goal.destination).recover(
                run_id=run_id,
                model=build_deepseek_v4_model("root"),
            )
        except Exception:
            logger.exception("Agent V2 background recovery failed for run %s", run_id)


@lru_cache(maxsize=1)
def runtime() -> AgentV2Runtime:
    domain_db = Path(os.getenv("AGENT_V2_DATABASE_PATH", "data/trip_agent_v2.sqlite3"))
    provider_db = Path(os.getenv("AGENT_V2_PROVIDER_DATABASE_PATH", "data/trip_agent_v2_provider.sqlite3"))
    deferred_db = Path(os.getenv("AGENT_V2_DEFERRED_DATABASE_PATH", "data/trip_agent_v2_deferred.sqlite3"))
    return AgentV2Runtime(domain_db, provider_db, deferred_db)


@router.post("/trip-workspaces", status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate):
    service = runtime().service(payload.destination)
    workspace = service.create_workspace(**payload.model_dump())
    return {"workspace_id": workspace.workspace_id, "workspace": workspace.model_dump(mode="json")}


@router.get("/trip-workspaces/{workspace_id}")
def get_workspace(workspace_id: str):
    current = runtime().repository.get_workspace(workspace_id)
    if current is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    releases = runtime().repository.list_releases(workspace_id)
    return {
        "workspace": current.model_dump(mode="json"),
        "claims": [
            claim.model_dump(mode="json")
            for claim in runtime().repository.list_claims(workspace_id)
        ],
        "releases": [
            release.model_dump(mode="json")
            for release in releases
        ],
        "narratives": [
            narrative.model_dump(mode="json")
            for release in releases
            if (narrative := runtime().repository.get_release_narrative(release.release_id))
        ],
    }


@router.post("/trip-workspaces/{workspace_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def create_run(workspace_id: str, payload: RunCreate, background_tasks: BackgroundTasks):
    try:
        run = runtime().service(None).create_run(
            workspace_id, idempotency_key=payload.idempotency_key
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    if run.status is RunStatus.CREATED:
        background_tasks.add_task(runtime().execute, run.run_id)
    return {"run": run.model_dump(mode="json")}


@router.get("/agent-runs/{run_id}")
def get_run(run_id: str):
    run = runtime().repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    interruption = (
        runtime().repository.get_interruption(run.active_interruption_id)
        if run.active_interruption_id
        else None
    )
    return {
        "run": run.model_dump(mode="json"),
        "interruption": interruption.model_dump(mode="json") if interruption else None,
    }


@router.get("/agent-runs/{run_id}/events")
def get_events(run_id: str, after_event_id: int = Query(default=0, ge=0)):
    if runtime().repository.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"events": runtime().repository.list_events(run_id, after_event_id=after_event_id)}


@router.post("/agent-runs/{run_id}/answers", status_code=status.HTTP_202_ACCEPTED)
def answer_clarification(
    run_id: str,
    payload: ClarificationAnswerInput,
    background_tasks: BackgroundTasks,
):
    run = runtime().repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status is not RunStatus.WAITING_USER:
        raise HTTPException(status_code=409, detail="run is not waiting for answers")
    background_tasks.add_task(runtime().resume, run_id, payload.answers)
    return {"run_id": run_id, "accepted": True}


@router.post("/agent-runs/{run_id}/cancel")
def cancel_run(run_id: str):
    run = runtime().repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status in TERMINAL_RUN_STATUSES:
        return {"run": run.model_dump(mode="json")}
    cancelled = runtime().repository.transition_run(run_id, RunStatus.CANCELLED)
    runtime().repository.append_event(run_id, {"type": "run_cancelled"})
    return {"run": cancelled.model_dump(mode="json")}


@router.post("/agent-runs/{run_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_run(run_id: str, background_tasks: BackgroundTasks):
    run = runtime().repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status is not RunStatus.RUNNING:
        raise HTTPException(status_code=409, detail="only an interrupted running run can recover")
    background_tasks.add_task(runtime().recover, run_id)
    return {"run_id": run_id, "accepted": True}


@router.post("/trip-workspaces/{workspace_id}/revisions", status_code=status.HTTP_202_ACCEPTED)
def create_revision(
    workspace_id: str,
    payload: RevisionCreate,
    background_tasks: BackgroundTasks,
):
    workspace = runtime().repository.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    try:
        run = runtime().service(workspace.goal_ledger.goal.destination).create_revision_run(
            workspace_id,
            raw_request=payload.raw_request,
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(runtime().execute, run.run_id)
    return {"run": run.model_dump(mode="json")}
