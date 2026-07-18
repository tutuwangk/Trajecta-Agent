from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

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
    TripWorkspace,
)
from app.trip_agent.repositories import SqliteTripAgentRepository


NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)


def _repository(tmp_path) -> SqliteTripAgentRepository:
    repository = SqliteTripAgentRepository(tmp_path / "fault.sqlite3")
    repository.create_workspace(
        TripWorkspace(
            workspace_id="workspace-fault",
            goal_ledger=GoalLedger(goal=RunGoal(raw_request="成都一日游")),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    repository.create_run(
        AgentRun(
            run_id="run-fault",
            workspace_id="workspace-fault",
            idempotency_key="initial",
            provider_conversation_id="conversation-fault",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    repository.transition_run("run-fault", RunStatus.RUNNING, at=NOW)
    return repository


def _trigger(repository, name: str, sql: str) -> None:
    repository._connection.execute(f"CREATE TRIGGER {name} {sql}")


def test_fact_transaction_rolls_back_source_claim_workspace_and_snapshot(tmp_path):
    repository = _repository(tmp_path)
    _trigger(
        repository,
        "abort_fact_snapshot",
        "BEFORE INSERT ON workspace_snapshots WHEN NEW.version = 2 "
        "BEGIN SELECT RAISE(ABORT, 'fault snapshot'); END",
    )
    source = SourceRecord(
        source_record_id="source-fault",
        source_type="web",
        provider="fixture",
        uri="https://example.test",
        excerpt="09:00-18:00",
        content_hash="hash-fault",
        retrieved_at=NOW,
    )
    claim = ObservedClaim(
        claim_id="claim-fault",
        entity_id="candidate-fault",
        field="opening_hours",
        value="09:00-18:00",
        source_record_ids=(source.source_record_id,),
        extractor="fixture",
        extractor_version="1",
        acquired_at=NOW,
        confidence=1,
        release_eligible=True,
    )

    with pytest.raises(sqlite3.IntegrityError, match="fault snapshot"):
        repository.record_facts(
            "workspace-fault",
            expected_version=1,
            sources=(source,),
            claims=(claim,),
        )

    assert repository.get_workspace("workspace-fault").version == 1
    assert repository.list_sources("workspace-fault") == ()
    assert repository.list_claims("workspace-fault") == ()
    repository.close()


def test_interruption_insert_rolls_back_when_waiting_transition_fails(tmp_path):
    repository = _repository(tmp_path)
    _trigger(
        repository,
        "abort_waiting",
        "BEFORE UPDATE ON agent_runs WHEN NEW.status = 'waiting_user' "
        "BEGIN SELECT RAISE(ABORT, 'fault waiting'); END",
    )
    batch = _batch()

    with pytest.raises(sqlite3.IntegrityError, match="fault waiting"):
        repository.create_interruption_and_wait(batch)

    assert repository.get_interruption(batch.interruption_id) is None
    assert repository.get_run("run-fault").status is RunStatus.RUNNING
    repository.close()


def test_answer_consumption_rolls_back_when_resume_transition_fails(tmp_path):
    repository = _repository(tmp_path)
    batch = _batch()
    repository.create_interruption_and_wait(batch)
    answers = _answers(batch)
    _trigger(
        repository,
        "abort_resume",
        "BEFORE UPDATE ON agent_runs WHEN OLD.status = 'waiting_user' AND NEW.status = 'running' "
        "BEGIN SELECT RAISE(ABORT, 'fault resume'); END",
    )

    with pytest.raises(sqlite3.IntegrityError, match="fault resume"):
        repository.consume_answers_and_resume(answers)

    assert repository.get_run("run-fault").status is RunStatus.WAITING_USER
    repository._connection.execute("DROP TRIGGER abort_resume")
    assert repository.consume_answers_and_resume(answers) is True
    repository.close()


def test_revision_workspace_rolls_back_when_run_insert_fails(tmp_path):
    repository = _repository(tmp_path)
    repository.transition_run("run-fault", RunStatus.CANCELLED, at=NOW)
    current = repository.get_workspace("workspace-fault")
    ledger = current.goal_ledger.model_copy(update={"revision": 2})
    revised = current.with_goal_ledger(ledger, at=NOW)
    run = AgentRun(
        run_id="run-revision",
        workspace_id="workspace-fault",
        idempotency_key="revision-fault",
        provider_conversation_id="conversation-revision",
        revision_of_run_id="run-fault",
        created_at=NOW,
        updated_at=NOW,
    )
    _trigger(
        repository,
        "abort_revision_run",
        "BEFORE INSERT ON agent_runs WHEN NEW.idempotency_key = 'revision-fault' "
        "BEGIN SELECT RAISE(ABORT, 'fault revision'); END",
    )

    with pytest.raises(sqlite3.IntegrityError, match="fault revision"):
        repository.create_revision_run(revised, expected_version=1, run=run)

    assert repository.get_workspace("workspace-fault").version == 1
    assert repository.get_run("run-revision") is None
    repository.close()


def test_candidate_rejection_rolls_back_candidate_when_rejection_insert_fails(tmp_path):
    repository = _repository(tmp_path)
    candidate = _candidate()
    rejection = CandidateRejected(
        rejection_id="rejection-fault",
        candidate_id=candidate.candidate_id,
        issue_codes=("route_fact_missing",),
        created_at=NOW,
    )
    _trigger(
        repository,
        "abort_rejection",
        "BEFORE INSERT ON candidate_rejections "
        "BEGIN SELECT RAISE(ABORT, 'fault rejection'); END",
    )

    with pytest.raises(sqlite3.IntegrityError, match="fault rejection"):
        repository.reject_candidate(candidate, rejection)

    assert repository.get_candidate(candidate.candidate_id) is None
    assert repository.list_candidate_rejections("run-fault") == ()
    repository.close()


def test_publication_rolls_back_candidate_release_narrative_and_terminal(tmp_path):
    repository = _repository(tmp_path)
    candidate = _candidate()
    release = ReleaseRecord(
        release_id="release-fault",
        release_key="workspace-fault:1:0",
        workspace_id="workspace-fault",
        run_id="run-fault",
        candidate_id=candidate.candidate_id,
        workspace_version=1,
        fact_version=0,
        fact_status=FactStatus.DEGRADED,
        experience_status=ExperienceStatus.GOOD,
        route_fact_fingerprint="fingerprint-fault",
        created_at=NOW,
    )
    narrative = ReleaseNarrative(
        narrative_id="narrative-fault",
        release_id=release.release_id,
        route_fact_fingerprint=release.route_fact_fingerprint,
        overview="fixture",
        days=(NarrativeDay(day_index=1, theme="fixture", summary="fixture"),),
        generator="fixture",
        created_at=NOW,
    )
    _trigger(
        repository,
        "abort_narrative",
        "BEFORE INSERT ON release_narratives "
        "BEGIN SELECT RAISE(ABORT, 'fault narrative'); END",
    )

    with pytest.raises(sqlite3.IntegrityError, match="fault narrative"):
        repository.publish_candidate(candidate, release, narrative)

    assert repository.get_candidate(candidate.candidate_id) is None
    assert repository.get_release_by_key(release.release_key) is None
    assert repository.get_release_narrative(release.release_id) is None
    assert repository.get_run("run-fault").status is RunStatus.RUNNING
    repository.close()


def _batch() -> ClarificationBatch:
    return ClarificationBatch(
        interruption_id="interruption-fault",
        run_id="run-fault",
        provider_run_id="provider-fault",
        tool_call_id="tool-fault",
        questions=(
            ClarificationQuestion(
                question_id="q1",
                prompt="选择哪家分店？",
                reason="身份歧义会改变路线",
            ),
        ),
        created_at=NOW,
    )


def _answers(batch: ClarificationBatch) -> ClarificationAnswers:
    return ClarificationAnswers(
        interruption_id=batch.interruption_id,
        tool_call_id=batch.tool_call_id,
        answers=(ClarificationAnswer(question_id="q1", value="总店"),),
        answered_at=NOW,
    )


def _candidate() -> CandidateSnapshot:
    return CandidateSnapshot(
        candidate_id="candidate-fault",
        workspace_id="workspace-fault",
        agent_run_id="run-fault",
        draft_id="draft-fault",
        workspace_version=1,
        fact_version=0,
        draft_version=1,
        completion_reason="fault injection",
        created_at=NOW,
    )
