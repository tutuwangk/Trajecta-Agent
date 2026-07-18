from __future__ import annotations

from datetime import datetime, timezone
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.trip_agent.domain import (
    AgentRun,
    CandidateRejected,
    CandidateSnapshot,
    ClarificationAnswer,
    ClarificationAnswers,
    ClarificationBatch,
    ClarificationQuestion,
    ExperienceStatus,
    FactStatus,
    GoalLedger,
    NarrativeDay,
    ObservedClaim,
    ReleaseNarrative,
    ReleaseRecord,
    RunGoal,
    RunStatus,
    SourceRecord,
    TERMINAL_RUN_STATUSES,
    TripWorkspace,
)
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.toolsets.planner_tools import _workspace as load_mutable_workspace


NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)
KILL_EXIT_CODE = 91


def _workspace() -> TripWorkspace:
    return TripWorkspace(
        workspace_id="workspace-kill",
        goal_ledger=GoalLedger(goal=RunGoal(raw_request="成都一日游")),
        created_at=NOW,
        updated_at=NOW,
    )


def _run() -> AgentRun:
    return AgentRun(
        run_id="run-kill",
        workspace_id="workspace-kill",
        idempotency_key="initial",
        provider_conversation_id="conversation-kill",
        created_at=NOW,
        updated_at=NOW,
    )


def _batch() -> ClarificationBatch:
    return ClarificationBatch(
        interruption_id="interruption-kill",
        run_id="run-kill",
        provider_run_id="provider-kill",
        tool_call_id="tool-kill",
        questions=(
            ClarificationQuestion(
                question_id="q1",
                prompt="选择哪家分店？",
                reason="身份歧义会改变路线",
            ),
        ),
        created_at=NOW,
    )


def _answers() -> ClarificationAnswers:
    return ClarificationAnswers(
        interruption_id="interruption-kill",
        tool_call_id="tool-kill",
        answers=(ClarificationAnswer(question_id="q1", value="总店"),),
        answered_at=NOW,
    )


def _candidate() -> CandidateSnapshot:
    return CandidateSnapshot(
        candidate_id="candidate-kill",
        workspace_id="workspace-kill",
        agent_run_id="run-kill",
        draft_id="draft-kill",
        workspace_version=1,
        fact_version=0,
        draft_version=1,
        completion_reason="process kill",
        created_at=NOW,
    )


def _release() -> ReleaseRecord:
    return ReleaseRecord(
        release_id="release-kill",
        release_key="workspace-kill:1:0",
        workspace_id="workspace-kill",
        run_id="run-kill",
        candidate_id="candidate-kill",
        workspace_version=1,
        fact_version=0,
        fact_status=FactStatus.DEGRADED,
        experience_status=ExperienceStatus.GOOD,
        route_fact_fingerprint="fingerprint-kill",
        created_at=NOW,
    )


def _narrative() -> ReleaseNarrative:
    return ReleaseNarrative(
        narrative_id="narrative-kill",
        release_id="release-kill",
        route_fact_fingerprint="fingerprint-kill",
        overview="fixture",
        days=(NarrativeDay(day_index=1, theme="fixture", summary="fixture"),),
        generator="fixture",
        created_at=NOW,
    )


def _install_kill_trigger(repository: SqliteTripAgentRepository, sql: str) -> None:
    def kill_process() -> None:
        os._exit(KILL_EXIT_CODE)

    repository._connection.create_function("fault_kill", 0, kill_process)
    repository._connection.execute(f"CREATE TEMP TRIGGER process_kill {sql}")


def _kill_during_operation(database: str, operation: str) -> None:
    repository = SqliteTripAgentRepository(database)
    if operation == "facts":
        _install_kill_trigger(
            repository,
            "AFTER INSERT ON knowledge_claims BEGIN SELECT fault_kill(); END",
        )
        source = SourceRecord(
            source_record_id="source-kill",
            source_type="web",
            provider="fixture",
            uri="https://example.test",
            excerpt="09:00-18:00",
            content_hash="hash-kill",
            retrieved_at=NOW,
        )
        claim = ObservedClaim(
            claim_id="claim-kill",
            entity_id="candidate-kill",
            field="opening_hours",
            value="09:00-18:00",
            source_record_ids=(source.source_record_id,),
            extractor="fixture",
            extractor_version="1",
            acquired_at=NOW,
            confidence=1,
            release_eligible=True,
        )
        repository.record_facts(
            "workspace-kill", expected_version=1, sources=(source,), claims=(claim,)
        )
    elif operation == "interruption":
        _install_kill_trigger(
            repository,
            "AFTER INSERT ON agent_interruptions BEGIN SELECT fault_kill(); END",
        )
        repository.create_interruption_and_wait(_batch())
    elif operation == "answer":
        _install_kill_trigger(
            repository,
            "AFTER UPDATE OF answered_at ON agent_interruptions "
            "WHEN NEW.answered_at IS NOT NULL BEGIN SELECT fault_kill(); END",
        )
        repository.consume_answers_and_resume(_answers())
    elif operation == "revision":
        _install_kill_trigger(
            repository,
            "AFTER INSERT ON workspace_snapshots WHEN NEW.version = 2 "
            "BEGIN SELECT fault_kill(); END",
        )
        current = repository.get_workspace("workspace-kill")
        assert current is not None
        ledger = current.goal_ledger.model_copy(update={"revision": 2})
        revised = current.with_goal_ledger(ledger, at=NOW)
        revision_run = AgentRun(
            run_id="run-revision-kill",
            workspace_id="workspace-kill",
            idempotency_key="revision-kill",
            provider_conversation_id="conversation-revision-kill",
            revision_of_run_id="run-kill",
            created_at=NOW,
            updated_at=NOW,
        )
        repository.create_revision_run(revised, expected_version=1, run=revision_run)
    elif operation == "rejection":
        _install_kill_trigger(
            repository,
            "AFTER INSERT ON agent_candidates BEGIN SELECT fault_kill(); END",
        )
        candidate = _candidate()
        repository.reject_candidate(
            candidate,
            CandidateRejected(
                rejection_id="rejection-kill",
                candidate_id=candidate.candidate_id,
                issue_codes=("route_fact_missing",),
                created_at=NOW,
            ),
        )
    elif operation == "publication":
        _install_kill_trigger(
            repository,
            "AFTER INSERT ON release_narratives BEGIN SELECT fault_kill(); END",
        )
        repository.publish_candidate(_candidate(), _release(), _narrative())
    else:  # pragma: no cover - the parent controls the operation matrix
        raise AssertionError(operation)


def _prepare_database(path: Path, operation: str) -> None:
    repository = SqliteTripAgentRepository(path)
    repository.create_workspace(_workspace())
    repository.create_run(_run())
    repository.transition_run("run-kill", RunStatus.RUNNING, at=NOW)
    if operation == "answer":
        repository.create_interruption_and_wait(_batch())
    elif operation == "revision":
        repository.transition_run("run-kill", RunStatus.CANCELLED, at=NOW)
    repository.close()


@pytest.mark.parametrize(
    "operation",
    ("facts", "interruption", "answer", "revision", "rejection", "publication"),
)
def test_process_kill_rolls_back_atomic_domain_boundary(tmp_path, operation: str):
    database = tmp_path / f"{operation}.sqlite3"
    _prepare_database(database, operation)
    process = multiprocessing.get_context("spawn").Process(
        target=_kill_during_operation,
        args=(str(database), operation),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == KILL_EXIT_CODE

    repository = SqliteTripAgentRepository(database)
    run = repository.get_run("run-kill")
    workspace = repository.get_workspace("workspace-kill")
    assert run is not None
    assert workspace is not None

    if operation == "facts":
        assert workspace.version == 1
        assert repository.list_sources("workspace-kill") == ()
        assert repository.list_claims("workspace-kill") == ()
    elif operation == "interruption":
        assert repository.get_interruption("interruption-kill") is None
        assert run.status is RunStatus.RUNNING
    elif operation == "answer":
        assert run.status is RunStatus.WAITING_USER
        assert repository.consume_answers_and_resume(_answers()) is True
    elif operation == "revision":
        assert workspace.version == 1
        assert repository.get_run("run-revision-kill") is None
    elif operation == "rejection":
        assert repository.get_candidate("candidate-kill") is None
        assert repository.list_candidate_rejections("run-kill") == ()
    elif operation == "publication":
        assert repository.get_candidate("candidate-kill") is None
        assert repository.get_release_by_key("workspace-kill:1:0") is None
        assert repository.get_release_narrative("release-kill") is None
        assert run.status is RunStatus.RUNNING
    repository.close()


@pytest.mark.parametrize("terminal_status", tuple(TERMINAL_RUN_STATUSES))
def test_late_tool_cannot_mutate_after_any_terminal_status(tmp_path, terminal_status: RunStatus):
    repository = SqliteTripAgentRepository(tmp_path / f"{terminal_status.value}.sqlite3")
    repository.create_workspace(_workspace())
    repository.create_run(_run())
    repository.transition_run("run-kill", RunStatus.RUNNING, at=NOW)
    repository.transition_run("run-kill", terminal_status, at=NOW)
    context = SimpleNamespace(
        deps=SimpleNamespace(
            repository=repository,
            run_id="run-kill",
            workspace_id="workspace-kill",
        )
    )

    with pytest.raises(RuntimeError, match=f"terminal \\({terminal_status.value}\\)"):
        load_mutable_workspace(context)
    assert repository.get_workspace("workspace-kill").version == 1
    repository.close()
