from __future__ import annotations

import asyncio
import importlib
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
import pytest

from app.trip_agent_v3.delivery import assess_delivery, publish_release
from app.trip_agent_v3.domain.delivery import (
    CandidateSnapshot,
    ExperienceStatus,
    FactStatus,
    RunRecord,
    RunStatus,
)
from app.trip_agent_v3.domain.facts import (
    FactGapReport,
    FactResolutionStatus,
    RouteFactSource,
)
from app.trip_agent_v3.domain.execution import (
    JourneyGoal,
    TripWorkspaceRecord,
)
from app.trip_agent_v3.domain.plan import (
    CompiledTimeline,
    DayTimeline,
    StopKind,
    TimelineLeg,
    TimelineStop,
)
from app.trip_agent_v3.domain.requirements import CoverageReport
from app.trip_agent_v3.repository import SqliteTripAgentV3Repository
from app.trip_agent_v3.requirements import source_document
from app.trip_agent_v3.api.routes import (
    AgentV3Runtime,
    ClarificationAnswerInput,
)
from app.trip_agent_v3.autonomous_runtime import AutonomousRuntimeCancelled
from main import create_app


def test_wrapped_provider_connection_failure_is_resumable():
    import httpx
    from openai import APIConnectionError
    from app.trip_agent_v3.api.routes import _provider_blocker_diagnostic

    error = APIConnectionError(request=httpx.Request("POST", "https://provider.test"))
    diagnostic = _provider_blocker_diagnostic(error)
    assert diagnostic["reason_code"] == "provider_temporarily_unavailable"
    assert diagnostic["retryable_now"] is True


def test_provider_authentication_failure_requires_configuration_repair():
    from app.trip_agent_v3.api.routes import _provider_blocker_diagnostic

    class AuthenticationFailure(Exception):
        status_code = 401

    diagnostic = _provider_blocker_diagnostic(AuthenticationFailure())
    assert diagnostic["reason_code"] == "provider_authentication_failed"
    assert diagnostic["retryable_now"] is False


def test_map_quota_and_configuration_failures_are_recoverable_without_immediate_retry():
    from app.core import AppError, MissingConfigurationError
    from app.trip_agent_v3.adapters.provider_coordinator import ProviderCircuitOpen
    from app.trip_agent_v3.api.routes import _provider_blocker_diagnostic

    errors = (
        AppError("quota", details={"provider_code": "DAILY_QUERY_OVER_LIMIT"}),
        ProviderCircuitOpen("amap", "DAILY_QUERY_OVER_LIMIT"),
        MissingConfigurationError("AMAP_API_KEY"),
    )
    assert [ _provider_blocker_diagnostic(error)["reason_code"] for error in errors ] == [
        "provider_quota_exhausted", "provider_quota_exhausted", "provider_configuration_required",
    ]
    assert all(_provider_blocker_diagnostic(error)["retryable_now"] is False for error in errors)


def test_production_runtime_defaults_to_flash_and_preserves_explicit_pro(tmp_path):
    assert AgentV3Runtime(tmp_path / "flash.sqlite3").root_model_variant == "flash"
    assert AgentV3Runtime(tmp_path / "pro.sqlite3", root_model_variant="pro").root_model_variant == "pro"


@pytest.mark.anyio
@pytest.mark.parametrize("terminal", [None, RunStatus.SUCCEEDED, RunStatus.CANCELLED])
async def test_execution_time_budget_preserves_checkpoint_and_terminal_races(tmp_path, monkeypatch, terminal):
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    test_runtime = AgentV3Runtime(tmp_path / "time-budget.sqlite3", execution_timeout_seconds=0.01)
    workspace = TripWorkspaceRecord(
        workspace_id="workspace-time-budget",
        goal=JourneyGoal(goal_revision_id="goal-time-budget", destination="成都", start_date=date(2026, 8, 1), days=1),
        sources=(source_document(source_id="source-time-budget", kind="user_request", content="住测试酒店，去测试博物馆。"),),
    )
    test_runtime.repository.save_workspace(workspace)
    run = test_runtime.repository.create_run(workspace_id=workspace.workspace_id, idempotency_key="time-budget")
    checkpoint = {"completed_targets": ["hotel"], "pending_target": "museum"}

    class SlowProvider:
        async def resolve(self):
            await asyncio.Event().wait()

    async def slow_journey(**kwargs):
        test_runtime.repository.save_checkpoint(run_id=run.run_id, input_fingerprint="same-input", payload=checkpoint)
        try:
            await SlowProvider().resolve()
        except asyncio.CancelledError:
            if terminal is not None:
                test_runtime.repository.transition_run(run.run_id, terminal)
            raise

    monkeypatch.setattr(routes, "execute_autonomous_journey", slow_journey)
    monkeypatch.setattr(routes, "build_deepseek_v4_model", lambda *args, **kwargs: object())
    for name in ("DeepSeekRequirementInterpreter", "PydanticAIRootTripPlannerAgent", "AmapPlaceSearchProvider", "AmapFactProvider", "DeepSeekOperationalFactProvider"):
        monkeypatch.setattr(routes, name, lambda *args, **kwargs: object())

    await test_runtime.execute(run.run_id)

    recovered = test_runtime.repository.get_run(run.run_id)
    assert recovered.status is (terminal or RunStatus.NEEDS_RESUME)
    assert test_runtime.repository.get_checkpoint(run.run_id) == ("same-input", checkpoint)
    assert test_runtime.repository.get_run_metrics(run.run_id)["run_status"] == recovered.status.value
    events = test_runtime.repository.list_events(run.run_id)
    if terminal is None:
        assert events[-1]["type"] == "planning_time_budget_exhausted"
        assert events[-1]["timeout_seconds"] == 0.01
    else:
        assert [event["type"] for event in events] == ["run_started"]
    assert all(event["type"] != "provider_blocked" for event in events)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_execution_timeout_requires_positive_finite_duration(tmp_path, timeout):
    with pytest.raises(ValueError, match="positive and finite"):
        AgentV3Runtime(tmp_path / "invalid-timeout.sqlite3", execution_timeout_seconds=timeout)


def _candidate(run: RunRecord, suffix: str) -> CandidateSnapshot:
    moment = datetime(2026, 8, 1, 9, 0)
    timeline = CompiledTimeline(
        snapshot_id=f"timeline-{suffix}",
        draft_id=f"draft-{suffix}",
        draft_revision=1,
        days=(
            DayTimeline(
                day_number=1,
                calendar_date=date(2026, 8, 1),
                title="酒店休整",
                stops=(
                    TimelineStop(
                        stop_id=f"hotel-start-{suffix}",
                        candidate_id=f"hotel-{suffix}",
                        name="酒店",
                        kind=StopKind.LODGING,
                        arrival_at=moment,
                        departure_at=moment,
                        stay_duration_min=0,
                    ),
                    TimelineStop(
                        stop_id=f"hotel-end-{suffix}",
                        candidate_id=f"hotel-{suffix}",
                        name="酒店",
                        kind=StopKind.LODGING,
                        arrival_at=moment,
                        departure_at=moment,
                        stay_duration_min=0,
                    ),
                ),
                legs=(
                    TimelineLeg(
                        leg_id=f"leg-{suffix}",
                        from_stop_id=f"hotel-start-{suffix}",
                        to_stop_id=f"hotel-end-{suffix}",
                        departure_at=moment,
                        arrival_at=moment,
                        duration_min=0,
                        mode="walk",
                        fact_id=f"fact-{suffix}",
                        fact_source=RouteFactSource.AMAP,
                        fact_status=FactResolutionStatus.VERIFIED,
                    ),
                ),
            ),
        ),
    )
    return CandidateSnapshot(
        candidate_snapshot_id=f"candidate-{suffix}",
        workspace_id=run.workspace_id,
        producing_run_id=run.run_id,
        goal_revision_id=run.goal_revision_id,
        requirement_ledger_id=f"ledger-{suffix}",
        grounding_registry_id=f"grounding-{suffix}",
        timeline=timeline,
        coverage=CoverageReport(
            explicit_place_count=0,
            disposed_place_count=0,
            coverage_ratio=1,
        ),
        fact_gap_report=FactGapReport(
            fact_need_plan_id=f"facts-{suffix}",
        ),
        fact_status=FactStatus.VERIFIED,
        experience_status=ExperienceStatus.GOOD,
    )


def _save_publishable(
    repository: SqliteTripAgentV3Repository,
    run: RunRecord,
    suffix: str,
    *,
    publish: bool,
) -> None:
    repository.save_run(run)
    candidate = _candidate(run, suffix)
    assessment = assess_delivery(run=run, candidate=candidate)
    repository.save_candidate(candidate, assessment)
    if publish:
        release = publish_release(
            release_id=f"release-{suffix}",
            run=run,
            candidate=candidate,
            assessment=assessment,
            published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        repository.save_release(release)


def test_clarification_payload_supports_multiple_specific_blockers() -> None:
    payload = ClarificationAnswerInput(
        answers={
            f"第 {index} 个具体地点问题": f"第 {index} 个回答"
            for index in range(1, 7)
        }
    )

    assert len(payload.answers) == 6


def test_delivery_endpoint_never_falls_back_to_workspace_latest_release(
    tmp_path, monkeypatch
) -> None:
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    test_repository = SqliteTripAgentV3Repository(
        tmp_path / "trip-agent-v3.sqlite3"
    )
    old_run = RunRecord(
        run_id="run-old",
        workspace_id="workspace-1",
        goal_revision_id="goal-old",
        status=RunStatus.SUCCEEDED,
    )
    current_run = RunRecord(
        run_id="run-current",
        workspace_id="workspace-1",
        goal_revision_id="goal-current",
        status=RunStatus.SUCCEEDED,
    )
    _save_publishable(test_repository, old_run, "old", publish=True)
    _save_publishable(
        test_repository, current_run, "current", publish=False
    )
    monkeypatch.setattr(routes, "repository", lambda: test_repository)

    response = TestClient(create_app()).get(
        "/api/v3/agent-runs/run-current/delivery"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == "run-current"
    assert payload["candidate"]["candidate_snapshot_id"] == "candidate-current"
    assert payload["release"] is None
    assert "release-old" not in response.text


def test_cancelled_run_has_run_status_but_no_fake_delivery(
    tmp_path, monkeypatch
) -> None:
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    test_repository = SqliteTripAgentV3Repository(
        tmp_path / "trip-agent-v3.sqlite3"
    )
    test_repository.save_run(
        RunRecord(
            run_id="run-cancelled",
            workspace_id="workspace-1",
            goal_revision_id="goal-1",
            status=RunStatus.CANCELLED,
        )
    )
    monkeypatch.setattr(routes, "repository", lambda: test_repository)

    response = TestClient(create_app()).get(
        "/api/v3/agent-runs/run-cancelled/delivery"
    )

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-cancelled",
        "run_status": "cancelled",
        "delivery_state": "not_delivered",
        "candidate": None,
        "assessment": None,
        "release": None,
    }


def test_published_delivery_uses_its_release_candidate(tmp_path, monkeypatch):
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    repo = SqliteTripAgentV3Repository(tmp_path / "release-candidate.sqlite3")
    run = RunRecord(run_id="run-published", workspace_id="workspace-1",
                    goal_revision_id="goal-1", status=RunStatus.SUCCEEDED)
    _save_publishable(repo, run, "published", publish=True)
    _save_publishable(repo, run, "late", publish=False)
    monkeypatch.setattr(routes, "repository", lambda: repo)
    payload = TestClient(create_app()).get(
        "/api/v3/agent-runs/run-published/delivery"
    ).json()
    assert payload["candidate"]["candidate_snapshot_id"] == "candidate-published"
    assert payload["assessment"]["candidate_snapshot_id"] == "candidate-published"
    assert payload["release"]["candidate_snapshot_id"] == "candidate-published"


def test_publication_rolls_back_run_status_when_release_write_fails(tmp_path, monkeypatch):
    repo = SqliteTripAgentV3Repository(tmp_path / "atomic-release.sqlite3")
    run = RunRecord(run_id="run-atomic", workspace_id="workspace-1",
                    goal_revision_id="goal-1", status=RunStatus.ACTIVE)
    repo.save_run(run)
    candidate = _candidate(run, "atomic")
    succeeded = run.model_copy(update={"status": RunStatus.SUCCEEDED})
    assessment = assess_delivery(run=succeeded, candidate=candidate)
    repo.save_candidate(candidate, assessment)
    release = publish_release(release_id="release-atomic", run=succeeded,
                              candidate=candidate, assessment=assessment)
    insert = repo._insert_release

    def fail(_release):
        raise RuntimeError("interrupted release write")

    monkeypatch.setattr(repo, "_insert_release", fail)
    with pytest.raises(RuntimeError, match="interrupted release write"):
        repo.commit_release(release)
    assert repo.get_run(run.run_id).status is RunStatus.ACTIVE
    assert repo.release_for_run(run.run_id) is None
    monkeypatch.setattr(repo, "_insert_release", insert)
    assert repo.commit_release(release) == release
    assert repo.commit_release(release) == release
    assert repo.get_run(run.run_id).status is RunStatus.SUCCEEDED
    assert repo.release_for_run(run.run_id) == release


def test_metrics_endpoint_is_strictly_bound_to_requested_run(
    tmp_path, monkeypatch
) -> None:
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    test_repository = SqliteTripAgentV3Repository(
        tmp_path / "trip-agent-v3.sqlite3"
    )
    for run_id in ("run-old", "run-current"):
        test_repository.save_run(
            RunRecord(
                run_id=run_id,
                workspace_id="workspace-1",
                goal_revision_id=f"goal-{run_id}",
                status=RunStatus.SUCCEEDED,
            )
        )
    test_repository.save_run_metrics(
        run_id="run-old",
        payload={"provider_result_count": 50, "release_id": "release-old"},
    )
    test_repository.save_run_metrics(
        run_id="run-current",
        payload={"provider_result_count": 12, "release_id": None},
    )
    monkeypatch.setattr(routes, "repository", lambda: test_repository)

    response = TestClient(create_app()).get(
        "/api/v3/agent-runs/run-current/metrics"
    )

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-current",
        "metrics": {
            "provider_result_count": 12,
            "release_id": None,
        },
    }
    assert "release-old" not in response.text


@pytest.mark.anyio
async def test_executor_cancellation_becomes_resumable_and_keeps_metrics(
    tmp_path, monkeypatch
) -> None:
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    test_runtime = AgentV3Runtime(tmp_path / "trip-agent-v3.sqlite3")
    workspace = TripWorkspaceRecord(
        workspace_id="workspace-cancel",
        goal=JourneyGoal(
            goal_revision_id="goal-cancel",
            destination="成都",
            start_date=date(2026, 8, 1),
            days=1,
        ),
        sources=(
            source_document(
                source_id="source-cancel",
                kind="user_request",
                content="住成都博舍酒店，安排成都武侯祠博物馆一日游。",
            ),
        ),
    )
    test_runtime.repository.save_workspace(workspace)
    run = test_runtime.repository.create_run(
        workspace_id=workspace.workspace_id,
        idempotency_key="cancelled-task",
    )

    async def cancelled(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(routes, "execute_autonomous_journey", cancelled)
    monkeypatch.setattr(routes, "build_deepseek_v4_model", lambda role, **kwargs: object())
    monkeypatch.setattr(
        routes, "DeepSeekRequirementInterpreter", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes, "PydanticAIRootTripPlannerAgent", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes, "AmapPlaceSearchProvider", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes, "AmapFactProvider", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes,
        "DeepSeekOperationalFactProvider",
        lambda *args, **kwargs: object(),
    )

    with pytest.raises(asyncio.CancelledError):
        await test_runtime.execute(run.run_id)

    recovered = test_runtime.repository.get_run(run.run_id)
    assert recovered is not None
    assert recovered.status is RunStatus.NEEDS_RESUME
    assert test_runtime.repository.get_run_metrics(run.run_id) is not None
    assert [
        event["type"]
        for event in test_runtime.repository.list_events(run.run_id)
    ] == ["run_started", "run_interrupted"]


@pytest.mark.anyio
async def test_provider_balance_blocker_is_resumable_and_never_publishes(
    tmp_path, monkeypatch
) -> None:
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    test_runtime = AgentV3Runtime(tmp_path / "trip-agent-v3.sqlite3")
    workspace = TripWorkspaceRecord(
        workspace_id="workspace-provider-blocked",
        goal=JourneyGoal(
            goal_revision_id="goal-provider-blocked",
            destination="成都",
            start_date=date(2026, 8, 1),
            days=1,
        ),
        sources=(
            source_document(
                source_id="source-provider-blocked",
                kind="user_request",
                content="住成都博舍酒店，去成都武侯祠博物馆。",
            ),
        ),
    )
    test_runtime.repository.save_workspace(workspace)
    run = test_runtime.repository.create_run(
        workspace_id=workspace.workspace_id,
        idempotency_key="provider-blocked",
    )

    class ProviderBalanceError(Exception):
        status_code = 402
        model_name = "test-model"

    async def provider_blocked(**kwargs):
        raise ProviderBalanceError("sensitive provider response")

    monkeypatch.setattr(
        routes, "execute_autonomous_journey", provider_blocked
    )
    monkeypatch.setattr(routes, "build_deepseek_v4_model", lambda role, **kwargs: object())
    monkeypatch.setattr(
        routes, "DeepSeekRequirementInterpreter", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes, "PydanticAIRootTripPlannerAgent", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes, "AmapPlaceSearchProvider", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes, "AmapFactProvider", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes,
        "DeepSeekOperationalFactProvider",
        lambda *args, **kwargs: object(),
    )

    await test_runtime.execute(run.run_id)

    recovered = test_runtime.repository.get_run(run.run_id)
    assert recovered is not None
    assert recovered.status is RunStatus.NEEDS_RESUME
    assert test_runtime.repository.release_for_run(run.run_id) is None
    events = test_runtime.repository.list_events(run.run_id)
    assert [event["type"] for event in events] == [
        "run_started",
        "provider_blocked",
    ]
    assert events[-1]["reason_code"] == "provider_balance_insufficient"
    assert events[-1]["retryable_now"] is False
    assert "sensitive provider response" not in str(events)


@pytest.mark.anyio
async def test_inflight_user_cancellation_does_not_also_emit_run_failed(
    tmp_path, monkeypatch
) -> None:
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    test_runtime = AgentV3Runtime(tmp_path / "trip-agent-v3.sqlite3")
    workspace = TripWorkspaceRecord(
        workspace_id="workspace-inflight-cancel",
        goal=JourneyGoal(
            goal_revision_id="goal-inflight-cancel",
            destination="成都",
            start_date=date(2026, 8, 1),
            days=1,
        ),
        sources=(
            source_document(
                source_id="source-inflight-cancel",
                kind="user_request",
                content="住成都博舍酒店，去成都武侯祠博物馆。",
            ),
        ),
    )
    test_runtime.repository.save_workspace(workspace)
    run = test_runtime.repository.create_run(
        workspace_id=workspace.workspace_id,
        idempotency_key="inflight-cancel",
    )

    async def cancelled_by_user(**kwargs):
        test_runtime.repository.transition_run(
            run.run_id, RunStatus.CANCELLED
        )
        test_runtime.repository.append_event(
            run.run_id, {"type": "run_cancelled"}
        )
        raise AutonomousRuntimeCancelled(run.run_id)

    monkeypatch.setattr(
        routes, "execute_autonomous_journey", cancelled_by_user
    )
    monkeypatch.setattr(routes, "build_deepseek_v4_model", lambda role, **kwargs: object())
    monkeypatch.setattr(
        routes, "DeepSeekRequirementInterpreter", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes, "PydanticAIRootTripPlannerAgent", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes, "AmapPlaceSearchProvider", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes, "AmapFactProvider", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        routes,
        "DeepSeekOperationalFactProvider",
        lambda *args, **kwargs: object(),
    )

    await test_runtime.execute(run.run_id)

    recovered = test_runtime.repository.get_run(run.run_id)
    assert recovered is not None
    assert recovered.status is RunStatus.CANCELLED
    events = test_runtime.repository.list_events(run.run_id)
    assert [event["type"] for event in events] == [
        "run_started",
        "run_cancelled",
        "run_cancelled_observed",
    ]
    assert all(event["type"] != "run_failed" for event in events)
    assert test_runtime.repository.release_for_run(run.run_id) is None


def test_resume_endpoint_reuses_same_run_and_records_request(
    tmp_path, monkeypatch
) -> None:
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    test_runtime = AgentV3Runtime(tmp_path / "trip-agent-v3.sqlite3")
    run = RunRecord(
        run_id="run-resume",
        workspace_id="workspace-resume",
        goal_revision_id="goal-resume",
        status=RunStatus.NEEDS_RESUME,
    )
    test_runtime.repository.save_run(run)
    executed: list[str] = []

    async def record_execute(run_id: str) -> None:
        executed.append(run_id)

    monkeypatch.setattr(test_runtime, "execute", record_execute)
    monkeypatch.setattr(routes, "repository", lambda: test_runtime.repository)
    monkeypatch.setattr(routes, "runtime", lambda: test_runtime)

    response = TestClient(create_app()).post(
        "/api/v3/agent-runs/run-resume/resume"
    )

    assert response.status_code == 202
    assert response.json()["accepted"] is True
    assert response.json()["run"]["run_id"] == "run-resume"
    assert executed == ["run-resume"]
    assert [
        event["type"]
        for event in test_runtime.repository.list_events("run-resume")
    ] == ["run_resume_requested"]


def test_workspace_run_and_events_api_start_v3_without_v2_workspace_fallback(
    tmp_path, monkeypatch
) -> None:
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    test_runtime = AgentV3Runtime(tmp_path / "trip-agent-v3.sqlite3")

    async def leave_created(run_id: str) -> None:
        return None

    monkeypatch.setattr(test_runtime, "execute", leave_created)
    monkeypatch.setattr(routes, "runtime", lambda: test_runtime)
    monkeypatch.setattr(
        routes, "repository", lambda: test_runtime.repository
    )
    client = TestClient(create_app())
    created = client.post(
        "/api/v3/trip-workspaces",
        json={
            "raw_request": "成都一日游，住测试酒店，武侯祠和指定餐厅必去。",
            "destination": "成都",
            "start_date": "2026-08-01",
            "days": 1,
        },
    )
    assert created.status_code == 201
    workspace_id = created.json()["workspace_id"]

    scheduled = client.post(
        f"/api/v3/trip-workspaces/{workspace_id}/runs",
        json={"idempotency_key": "run-1"},
    )

    assert scheduled.status_code == 202
    run_id = scheduled.json()["run"]["run_id"]
    assert scheduled.json()["run"]["status"] == "created"
    fetched = client.get(f"/api/v3/agent-runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["run"]["workspace_id"] == workspace_id
    assert client.get(
        f"/api/v3/agent-runs/{run_id}/events"
    ).json() == {"events": []}
    delivery = client.get(
        f"/api/v3/agent-runs/{run_id}/delivery"
    ).json()
    assert delivery["delivery_state"] == "working"
    assert delivery["release"] is None

    duplicate = client.post(
        f"/api/v3/trip-workspaces/{workspace_id}/runs",
        json={"idempotency_key": "run-1"},
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["run"]["run_id"] == run_id


def test_clarification_resumes_same_business_run_and_revision_is_idempotent(
    tmp_path, monkeypatch
) -> None:
    routes = importlib.import_module("app.trip_agent_v3.api.routes")
    test_runtime = AgentV3Runtime(tmp_path / "trip-agent-v3.sqlite3")

    async def no_execute(run_id: str) -> None:
        return None

    monkeypatch.setattr(test_runtime, "execute", no_execute)
    monkeypatch.setattr(routes, "runtime", lambda: test_runtime)
    monkeypatch.setattr(
        routes, "repository", lambda: test_runtime.repository
    )
    client = TestClient(create_app())
    workspace_id = client.post(
        "/api/v3/trip-workspaces",
        json={
            "raw_request": "成都一日游，想吃陈麻婆豆腐。",
            "destination": "成都",
            "start_date": "2026-08-01",
            "days": 1,
        },
    ).json()["workspace_id"]
    run_id = client.post(
        f"/api/v3/trip-workspaces/{workspace_id}/runs",
        json={"idempotency_key": "initial"},
    ).json()["run"]["run_id"]
    test_runtime.repository.transition_run(run_id, RunStatus.ACTIVE)
    test_runtime.repository.transition_run(run_id, RunStatus.WAITING_USER)
    test_runtime.repository.append_event(
        run_id,
        {
            "type": "clarification_requested",
            "questions": ["你想去骡马市店还是太古里店？"],
        },
    )
    mismatched = client.post(
        f"/api/v3/agent-runs/{run_id}/answers",
        json={"answers": {"另一个问题": "骡马市店"}},
    )
    assert mismatched.status_code == 409
    assert (
        test_runtime.repository.get_run(run_id).status
        is RunStatus.WAITING_USER
    )

    answered = client.post(
        f"/api/v3/agent-runs/{run_id}/answers",
        json={"answers": {"你想去骡马市店还是太古里店？": "骡马市店"}},
    )

    assert answered.status_code == 202
    assert answered.json()["run"]["run_id"] == run_id
    assert answered.json()["run"]["status"] == "needs_resume"
    after_answer = test_runtime.repository.get_workspace(workspace_id)
    assert after_answer is not None
    assert after_answer.version == 2
    assert after_answer.sources[-1].kind.value == "user_revision"
    assert "骡马市店" in after_answer.sources[-1].content

    test_runtime.repository.transition_run(run_id, RunStatus.ACTIVE)
    test_runtime.repository.transition_run(run_id, RunStatus.SUCCEEDED)
    revision = client.post(
        f"/api/v3/trip-workspaces/{workspace_id}/revisions",
        json={
            "raw_request": "保留餐厅，但把博物馆改到下午。",
            "idempotency_key": "revision-1",
        },
    )
    assert revision.status_code == 202
    revision_run_id = revision.json()["run"]["run_id"]
    revised_workspace = test_runtime.repository.get_workspace(workspace_id)
    assert revised_workspace is not None
    assert revised_workspace.version == 3
    assert (
        revised_workspace.goal.goal_revision_id
        != after_answer.goal.goal_revision_id
    )

    duplicate = client.post(
        f"/api/v3/trip-workspaces/{workspace_id}/revisions",
        json={
            "raw_request": "这次重试不得再次改写工作区。",
            "idempotency_key": "revision-1",
        },
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["run"]["run_id"] == revision_run_id
    assert test_runtime.repository.get_workspace(workspace_id).version == 3
