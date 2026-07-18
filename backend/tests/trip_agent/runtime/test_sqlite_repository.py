from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.trip_agent.domain import (
    AgentRun,
    CandidateRejected,
    CandidateSnapshot,
    ClarificationAnswer,
    ClarificationAnswers,
    ClarificationBatch,
    ClarificationQuestion,
    DraftDay,
    DraftSnapshot,
    ExperienceStatus,
    FactStatus,
    GoalLedger,
    ObservedClaim,
    ReleaseRecord,
    ReleaseNarrative,
    NarrativeDay,
    RunGoal,
    RunStatus,
    SourceRecord,
    TripWorkspace,
)
from app.trip_agent.repositories import RepositoryConflict, SqliteTripAgentRepository


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def _workspace() -> TripWorkspace:
    return TripWorkspace(
        workspace_id="workspace-1",
        goal_ledger=GoalLedger(goal=RunGoal(raw_request="成都两日游")),
        created_at=NOW,
        updated_at=NOW,
    )


def _run(run_id: str = "run-1", idempotency_key: str = "idem-1") -> AgentRun:
    return AgentRun(
        run_id=run_id,
        workspace_id="workspace-1",
        idempotency_key=idempotency_key,
        provider_conversation_id=f"conversation-{run_id}",
        created_at=NOW,
        updated_at=NOW,
    )


def test_workspace_and_run_survive_repository_reopen(tmp_path):
    database = tmp_path / "trip-agent.sqlite3"
    repository = SqliteTripAgentRepository(database)
    repository.create_workspace(_workspace())
    repository.create_run(_run())
    repository.close()

    reopened = SqliteTripAgentRepository(database)
    assert reopened.get_workspace("workspace-1") == _workspace()
    assert reopened.get_run("run-1") == _run()
    reopened.close()


def test_workspace_compare_and_swap_rejects_stale_writer(tmp_path):
    repository = SqliteTripAgentRepository(tmp_path / "trip-agent.sqlite3")
    workspace = repository.create_workspace(_workspace())
    draft = DraftSnapshot(
        draft_id="draft-1",
        workspace_id=workspace.workspace_id,
        workspace_version=workspace.version,
        draft_version=1,
        days=(DraftDay(day_index=1, date=date(2026, 8, 3)),),
    )
    updated = workspace.with_draft(draft, at=NOW)
    repository.save_workspace(updated, expected_version=1)

    stale_update = workspace.model_copy(update={"version": 2, "updated_at": NOW})
    with pytest.raises(RepositoryConflict, match="stale workspace version"):
        repository.save_workspace(stale_update, expected_version=1)
    repository.close()


def test_tool_effect_result_is_durable_and_idempotent(tmp_path):
    repository = SqliteTripAgentRepository(tmp_path / "tool-effect.sqlite3")
    workspace = _workspace()
    repository.create_workspace(workspace)
    run = repository.create_run(_run())
    result = {"ok": True, "version": 2, "candidate_ids": ["candidate-1"]}

    first = repository.record_tool_effect(
        tool_call_id="tool-call-1",
        run_id=run.run_id,
        tool_name="search_place_candidates",
        result=result,
    )
    second = repository.record_tool_effect(
        tool_call_id="tool-call-1",
        run_id=run.run_id,
        tool_name="search_place_candidates",
        result={"ok": False},
    )

    assert first == result
    assert second == result
    assert repository.get_tool_effect(
        "tool-call-1", tool_name="search_place_candidates"
    ) == result
    repository.close()


def test_run_budget_survives_repository_reopen(tmp_path):
    database = tmp_path / "run-budget.sqlite3"
    repository = SqliteTripAgentRepository(database)
    repository.create_workspace(_workspace())
    repository.create_run(_run())
    state = {
        "elapsed_seconds": 125.0,
        "signatures": {"search_place_candidates:h1": 1},
        "simulation_no_progress": 2,
    }
    repository.save_run_budget("run-1", state)
    repository.close()

    reopened = SqliteTripAgentRepository(database)
    assert reopened.get_run_budget("run-1") == state
    reopened.close()


def test_run_idempotency_returns_original_run_and_terminal_is_immutable(tmp_path):
    repository = SqliteTripAgentRepository(tmp_path / "trip-agent.sqlite3")
    repository.create_workspace(_workspace())
    original = repository.create_run(_run())
    duplicate = repository.create_run(_run(run_id="run-2", idempotency_key="idem-1"))
    assert duplicate.run_id == original.run_id
    with pytest.raises(RepositoryConflict):
        repository.create_run(_run(run_id="run-3", idempotency_key="idem-3"))

    repository.transition_run("run-1", RunStatus.RUNNING, at=NOW)
    published = repository.transition_run("run-1", RunStatus.PUBLISHED, at=NOW)
    assert published.status is RunStatus.PUBLISHED
    with pytest.raises(ValueError, match="terminal run"):
        repository.transition_run("run-1", RunStatus.FAILED, at=NOW)
    repository.close()


def test_clarification_answer_is_consumed_once_by_tool_call_id(tmp_path):
    repository = SqliteTripAgentRepository(tmp_path / "trip-agent.sqlite3")
    repository.create_workspace(_workspace())
    repository.create_run(_run())
    batch = ClarificationBatch(
        interruption_id="interruption-1",
        run_id="run-1",
        provider_run_id="provider-run-1",
        tool_call_id="tool-call-1",
        questions=(
            ClarificationQuestion(
                question_id="q1",
                prompt="第二天更轻松吗？",
                reason="会改变地点分配",
            ),
        ),
        created_at=NOW,
    )
    repository.create_interruption(batch)
    answers = ClarificationAnswers(
        interruption_id="interruption-1",
        tool_call_id="tool-call-1",
        answers=(ClarificationAnswer(question_id="q1", value="是"),),
        answered_at=NOW,
    )
    assert repository.consume_answers(answers) is True
    assert repository.consume_answers(answers) is False
    repository.close()


def test_clarification_wait_and_answer_resume_are_atomic(tmp_path):
    repository = SqliteTripAgentRepository(tmp_path / "trip-agent.sqlite3")
    repository.create_workspace(_workspace())
    repository.create_run(_run())
    repository.transition_run("run-1", RunStatus.RUNNING, at=NOW)
    batch = ClarificationBatch(
        interruption_id="interruption-atomic",
        run_id="run-1",
        provider_run_id="provider-run-atomic",
        tool_call_id="tool-call-atomic",
        questions=(
            ClarificationQuestion(
                question_id="q1",
                prompt="选择哪一家分店？",
                reason="会改变地点身份",
            ),
        ),
        created_at=NOW,
    )
    repository.create_interruption_and_wait(batch)
    waiting = repository.get_run("run-1")
    assert waiting is not None
    assert waiting.status is RunStatus.WAITING_USER
    assert waiting.active_interruption_id == batch.interruption_id

    answers = ClarificationAnswers(
        interruption_id=batch.interruption_id,
        tool_call_id=batch.tool_call_id,
        answers=(ClarificationAnswer(question_id="q1", value="总店"),),
        answered_at=NOW,
    )
    assert repository.consume_answers_and_resume(answers) is True
    resumed = repository.get_run("run-1")
    assert resumed is not None
    assert resumed.status is RunStatus.RUNNING
    assert resumed.active_interruption_id is None
    assert repository.consume_answers_and_resume(answers) is False
    repository.close()


def test_release_key_is_idempotent(tmp_path):
    repository = SqliteTripAgentRepository(tmp_path / "trip-agent.sqlite3")
    repository.create_workspace(_workspace())
    repository.create_run(_run())
    repository.transition_run("run-1", RunStatus.RUNNING, at=NOW)
    repository.transition_run("run-1", RunStatus.PUBLISHED, at=NOW)
    release = ReleaseRecord(
        release_id="release-1",
        release_key="workspace-1:1",
        workspace_id="workspace-1",
        run_id="run-1",
        candidate_id="candidate-1",
        workspace_version=1,
        fact_version=0,
        fact_status=FactStatus.DEGRADED,
        experience_status=ExperienceStatus.GOOD,
        route_fact_fingerprint="fingerprint-1",
        created_at=NOW,
    )
    assert repository.create_release(release) == release
    duplicate = release.model_copy(update={"release_id": "release-2"})
    assert repository.create_release(duplicate).release_id == "release-1"
    repository.close()


def test_source_backed_claims_advance_fact_and_workspace_versions_atomically(tmp_path):
    repository = SqliteTripAgentRepository(tmp_path / "trip-agent.sqlite3")
    repository.create_workspace(_workspace())
    source = SourceRecord(
        source_record_id="source-1",
        source_type="web",
        provider="fixture",
        uri="https://example.test/place",
        excerpt="09:00-18:00",
        content_hash="hash-1",
        retrieved_at=NOW,
    )
    claim = ObservedClaim(
        claim_id="claim-1",
        entity_id="candidate-1",
        field="opening_hours",
        value="09:00-18:00",
        source_record_ids=(source.source_record_id,),
        extractor="fixture",
        extractor_version="1",
        acquired_at=NOW,
        confidence=1,
        release_eligible=True,
    )

    updated = repository.record_facts(
        "workspace-1",
        expected_version=1,
        sources=(source,),
        claims=(claim,),
    )

    assert updated.version == 2
    assert updated.fact_version == 1
    assert repository.list_sources("workspace-1") == (source,)
    assert repository.list_claims("workspace-1", entity_id="candidate-1") == (claim,)
    repository.close()


def test_candidate_release_and_terminal_run_are_committed_as_one_publication(tmp_path):
    repository = SqliteTripAgentRepository(tmp_path / "trip-agent.sqlite3")
    repository.create_workspace(_workspace())
    repository.create_run(_run())
    repository.transition_run("run-1", RunStatus.RUNNING, at=NOW)
    candidate = CandidateSnapshot(
        candidate_id="candidate-atomic",
        workspace_id="workspace-1",
        agent_run_id="run-1",
        draft_id="draft-atomic",
        workspace_version=1,
        fact_version=0,
        draft_version=1,
        completion_reason="fixture",
        created_at=NOW,
    )
    release = ReleaseRecord(
        release_id="release-atomic",
        release_key="workspace-1:1:0",
        workspace_id="workspace-1",
        run_id="run-1",
        candidate_id=candidate.candidate_id,
        workspace_version=1,
        fact_version=0,
        fact_status=FactStatus.DEGRADED,
        experience_status=ExperienceStatus.GOOD,
        route_fact_fingerprint="fingerprint-atomic",
        created_at=NOW,
    )
    narrative = ReleaseNarrative(
        narrative_id="narrative-atomic",
        release_id=release.release_id,
        route_fact_fingerprint=release.route_fact_fingerprint,
        overview="fixture",
        days=(NarrativeDay(day_index=1, theme="fixture", summary="fixture"),),
        generator="fixture",
        created_at=NOW,
    )

    assert repository.publish_candidate(candidate, release, narrative) == release
    assert repository.get_candidate(candidate.candidate_id) == candidate
    assert repository.get_run("run-1").status is RunStatus.PUBLISHED
    assert repository.get_release_narrative(release.release_id) == narrative
    assert repository.publish_candidate(candidate, release, narrative) == release
    repository.close()


def test_rejected_candidates_are_frozen_and_limited_to_three_per_run(tmp_path):
    repository = SqliteTripAgentRepository(tmp_path / "trip-agent.sqlite3")
    repository.create_workspace(_workspace())
    repository.create_run(_run())

    for attempt in range(1, 4):
        candidate = CandidateSnapshot(
            candidate_id=f"candidate-rejected-{attempt}",
            workspace_id="workspace-1",
            agent_run_id="run-1",
            draft_id=f"draft-{attempt}",
            workspace_version=1,
            fact_version=0,
            draft_version=attempt,
            completion_reason="fixture rejection",
            created_at=NOW,
        )
        rejection = CandidateRejected(
            rejection_id=f"rejection-{attempt}",
            candidate_id=candidate.candidate_id,
            issue_codes=("unresolved_candidate",),
            created_at=NOW,
        )
        assert repository.reject_candidate(candidate, rejection) == rejection

    assert len(repository.list_candidate_rejections("run-1")) == 3
    fourth = CandidateSnapshot(
        candidate_id="candidate-rejected-4",
        workspace_id="workspace-1",
        agent_run_id="run-1",
        draft_id="draft-4",
        workspace_version=1,
        fact_version=0,
        draft_version=4,
        completion_reason="must not be persisted",
        created_at=NOW,
    )
    with pytest.raises(RepositoryConflict, match="submission limit"):
        repository.reject_candidate(
            fourth,
            CandidateRejected(
                rejection_id="rejection-4",
                candidate_id=fourth.candidate_id,
                issue_codes=("unresolved_candidate",),
                created_at=NOW,
            ),
        )
    assert repository.get_candidate(fourth.candidate_id) is None
    repository.close()
