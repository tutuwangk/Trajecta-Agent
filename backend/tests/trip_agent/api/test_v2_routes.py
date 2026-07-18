from __future__ import annotations

import importlib
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.trip_agent.api.routes import AgentV2Runtime
from app.trip_agent.domain import ClarificationBatch, ClarificationQuestion, RunStatus
from main import create_app


def test_workspace_and_run_api_do_not_require_legacy_poi_confirmation(tmp_path, monkeypatch):
    routes = importlib.import_module("app.trip_agent.api.routes")
    test_runtime = AgentV2Runtime(
        tmp_path / "domain.sqlite3",
        tmp_path / "provider.sqlite3",
        tmp_path / "deferred.sqlite3",
    )

    async def leave_created(run_id: str) -> None:
        return None

    monkeypatch.setattr(test_runtime, "execute", leave_created)
    monkeypatch.setattr(routes, "runtime", lambda: test_runtime)
    client = TestClient(create_app())

    created = client.post(
        "/api/v2/trip-workspaces",
        json={
            "raw_request": "2026年8月3日成都一日游，武侯祠必去。",
            "destination": "成都",
            "start_date": "2026-08-03",
            "days": 1,
        },
    )
    assert created.status_code == 201
    workspace_id = created.json()["workspace_id"]
    persisted_workspace = test_runtime.repository.get_workspace(workspace_id)
    assert persisted_workspace is not None
    assert persisted_workspace.goal_ledger.goal.start_date.isoformat() == "2026-08-03"
    assert persisted_workspace.goal_ledger.goal.days == 1

    scheduled = client.post(
        f"/api/v2/trip-workspaces/{workspace_id}/runs",
        json={"idempotency_key": "initial-run"},
    )
    assert scheduled.status_code == 202
    run_id = scheduled.json()["run"]["run_id"]
    assert scheduled.json()["run"]["status"] == "created"

    fetched = client.get(f"/api/v2/agent-runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["run"]["workspace_id"] == workspace_id
    assert client.get(f"/api/v2/agent-runs/{run_id}/events").json() == {"events": []}

    cancelled = client.post(f"/api/v2/agent-runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"

    revision = client.post(
        f"/api/v2/trip-workspaces/{workspace_id}/revisions",
        json={
            "raw_request": "仍去武侯祠，但把游览时长改为两小时。",
            "idempotency_key": "revision-1",
        },
    )
    assert revision.status_code == 202
    revision_run = revision.json()["run"]
    assert revision_run["revision_of_run_id"] == run_id
    revised_workspace = test_runtime.repository.get_workspace(workspace_id)
    assert revised_workspace is not None
    assert revised_workspace.goal_ledger.revision == 2
    assert revised_workspace.goal_ledger.goal.raw_request.startswith("仍去武侯祠")
    assert revised_workspace.goal_ledger.goal.start_date.isoformat() == "2026-08-03"

    duplicate_revision = client.post(
        f"/api/v2/trip-workspaces/{workspace_id}/revisions",
        json={
            "raw_request": "这段不同文本不得重复改写已提交 revision。",
            "idempotency_key": "revision-1",
        },
    )
    assert duplicate_revision.status_code == 202
    assert duplicate_revision.json()["run"]["run_id"] == revision_run["run_id"]
    after_duplicate = test_runtime.repository.get_workspace(workspace_id)
    assert after_duplicate is not None
    assert after_duplicate.goal_ledger.revision == 2
    assert after_duplicate.goal_ledger.goal.raw_request.startswith("仍去武侯祠")


def test_clarification_answer_endpoint_requires_waiting_run(tmp_path, monkeypatch):
    routes = importlib.import_module("app.trip_agent.api.routes")
    test_runtime = AgentV2Runtime(
        tmp_path / "domain.sqlite3",
        tmp_path / "provider.sqlite3",
        tmp_path / "deferred.sqlite3",
    )

    async def no_resume(run_id: str, answers: dict[str, str]) -> None:
        return None

    monkeypatch.setattr(test_runtime, "resume", no_resume)
    monkeypatch.setattr(routes, "runtime", lambda: test_runtime)
    service = test_runtime.service("成都")
    workspace = service.create_workspace(raw_request="成都一日游")
    run = service.create_run(workspace.workspace_id, idempotency_key="answer-run")
    test_runtime.repository.transition_run(run.run_id, RunStatus.RUNNING)
    batch = ClarificationBatch(
        interruption_id="interruption-api",
        run_id=run.run_id,
        provider_run_id="provider-api",
        tool_call_id="tool-api",
        questions=(
            ClarificationQuestion(
                question_id="q1",
                prompt="选择哪家分店？",
                reason="地点身份会改变路线",
            ),
        ),
        created_at=datetime.now(timezone.utc),
    )
    test_runtime.repository.create_interruption_and_wait(batch)
    client = TestClient(create_app())

    fetched = client.get(f"/api/v2/agent-runs/{run.run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["interruption"]["questions"][0]["question_id"] == "q1"
    accepted = client.post(
        f"/api/v2/agent-runs/{run.run_id}/answers",
        json={"answers": {"q1": "总店"}},
    )
    assert accepted.status_code == 202
    assert accepted.json()["accepted"] is True

    test_runtime.repository.transition_run(run.run_id, RunStatus.CANCELLED)
    rejected = client.post(
        f"/api/v2/agent-runs/{run.run_id}/answers",
        json={"answers": {"q1": "总店"}},
    )
    assert rejected.status_code == 409
