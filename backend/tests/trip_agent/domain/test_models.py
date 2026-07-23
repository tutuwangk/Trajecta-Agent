from __future__ import annotations

from datetime import date, datetime, time, timezone

from pydantic import ValidationError
import pytest

from app.trip_agent.domain import (
    AgentRun,
    ClarificationBatch,
    ClarificationQuestion,
    CommitmentStrength,
    DraftDay,
    DraftSnapshot,
    DraftVisit,
    EstimateClaim,
    GoalCommitment,
    GoalLedger,
    ObservedClaim,
    PlaceCandidate,
    PlaceHypothesis,
    PlaceResolution,
    ResolutionStatus,
    RunGoal,
    RunStatus,
    TripWorkspace,
    TextSpan,
    expand_visit_candidate_coverage,
)


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def _ledger() -> GoalLedger:
    return GoalLedger(
        goal=RunGoal(raw_request="成都两日游"),
        commitments=(
            GoalCommitment(
                commitment_id="c1",
                field="appointment",
                value="2026-08-03T08:30",
                evidence_text="预约 8:30，不能调整",
                strength=CommitmentStrength.HARD,
                immutable=True,
            ),
        ),
    )


def test_domain_models_are_frozen_and_reject_extra_fields():
    goal = RunGoal(raw_request="成都两日游")
    with pytest.raises(ValidationError):
        RunGoal.model_validate({"raw_request": "成都两日游", "unknown": True})
    with pytest.raises(ValidationError):
        goal.raw_request = "上海"  # type: ignore[misc]


def test_observed_claim_requires_source_and_spatial_estimate_cannot_be_release_fact():
    common = {
        "claim_id": "claim-1",
        "entity_id": "poi-1",
        "field": "opening_hours",
        "value": "09:00-17:00",
        "extractor": "fact_extractor",
        "extractor_version": "1",
        "acquired_at": NOW,
        "confidence": 0.9,
    }
    with pytest.raises(ValidationError):
        ObservedClaim(**common)
    with pytest.raises(ValidationError):
        EstimateClaim(
            **common,
            source_record_ids=(),
            method="spatial_estimate",
            release_eligible=True,
        )


def test_place_resolution_requires_exact_candidate_only_when_resolved():
    with pytest.raises(ValidationError):
        PlaceResolution(
            hypothesis_id="h1",
            status=ResolutionStatus.RESOLVED,
            rationale="matched",
            workspace_version=1,
        )


def test_visit_child_covers_known_parent_but_parent_does_not_cover_child():
    parent = PlaceCandidate(
        candidate_id="west-lake",
        hypothesis_id="hypothesis-west-lake",
        provider="amap",
        provider_place_id="amap-west-lake",
        name="杭州西湖风景名胜区",
        source_record_id="source-west-lake",
    )
    child = PlaceCandidate(
        candidate_id="broken-bridge",
        hypothesis_id="hypothesis-broken-bridge",
        provider="amap",
        provider_place_id="amap-broken-bridge",
        parent_provider_place_id="amap-west-lake",
        name="断桥残雪",
        source_record_id="source-broken-bridge",
    )

    assert expand_visit_candidate_coverage((parent, child), {child.candidate_id}) == {
        child.candidate_id,
        parent.candidate_id,
    }
    assert expand_visit_candidate_coverage((parent, child), {parent.candidate_id}) == {
        parent.candidate_id
    }
    with pytest.raises(ValidationError):
        PlaceResolution(
            hypothesis_id="h1",
            status=ResolutionStatus.AMBIGUOUS,
            candidate_id="candidate-1",
            rationale="still ambiguous",
            workspace_version=1,
        )


def test_draft_rejects_duplicate_place_and_non_contiguous_days():
    visit = DraftVisit(visit_id="v1", place_candidate_id="p1", duration_min=60)
    with pytest.raises(ValidationError):
        DraftSnapshot(
            draft_id="d1",
            workspace_id="w1",
            workspace_version=1,
            draft_version=1,
            days=(
                DraftDay(day_index=1, date=date(2026, 8, 3), visits=(visit,)),
                DraftDay(
                    day_index=3,
                    date=date(2026, 8, 4),
                    visits=(visit.model_copy(update={"visit_id": "v2"}),),
                ),
            ),
        )


def test_terminal_run_cannot_be_overwritten_and_waiting_run_can_resume():
    run = AgentRun(
        run_id="r1",
        workspace_id="w1",
        idempotency_key="k1",
        provider_conversation_id="conv1",
        created_at=NOW,
        updated_at=NOW,
    )
    running = run.transition(RunStatus.RUNNING, at=NOW)
    waiting = running.transition(RunStatus.WAITING_USER, active_interruption_id="i1", at=NOW)
    resumed = waiting.transition(RunStatus.RUNNING, at=NOW)
    published = resumed.transition(RunStatus.PUBLISHED, at=NOW)
    assert published.status is RunStatus.PUBLISHED
    with pytest.raises(ValueError, match="terminal run"):
        published.transition(RunStatus.FAILED, at=NOW)


def test_workspace_observations_are_idempotent_but_identity_conflicts_fail():
    workspace = TripWorkspace(
        workspace_id="w-observation",
        goal_ledger=GoalLedger(goal=RunGoal(raw_request="去武侯祠")),
        created_at=NOW,
        updated_at=NOW,
    )
    hypothesis = PlaceHypothesis(
        hypothesis_id="h1",
        raw_name="武侯祠",
        context="去武侯祠",
        spans=(TextSpan(start=1, end=4),),
    )
    observed = workspace.with_hypotheses((hypothesis,), at=NOW)
    assert observed.with_hypotheses((hypothesis,), at=NOW) is observed

    candidate = PlaceCandidate(
        candidate_id="candidate-stable",
        hypothesis_id="h1",
        provider="amap",
        provider_place_id="provider-1",
        name="成都武侯祠博物馆",
        source_record_id="source-1",
    )
    with_candidate = observed.with_candidates((candidate,), at=NOW)
    assert with_candidate.with_candidates((candidate,), at=NOW) is with_candidate

    refreshed = candidate.model_copy(update={"address": "武侯祠大街231号"})
    updated = with_candidate.with_candidates((refreshed,), at=NOW)
    assert updated.version == with_candidate.version + 1
    assert updated.place_candidates == (refreshed,)

    alternate_local_id = refreshed.model_copy(update={"candidate_id": "cache-local-id"})
    stable = updated.with_candidates((alternate_local_id,), at=NOW)
    assert stable.version == updated.version
    assert stable.place_candidates[0].candidate_id == "candidate-stable"

    conflicting = candidate.model_copy(update={"provider_place_id": "provider-2"})
    with pytest.raises(ValueError, match="identity conflict"):
        updated.with_candidates((conflicting,), at=NOW)


def test_workspace_rejects_stale_draft_and_advances_version_for_current_draft():
    workspace = TripWorkspace(
        workspace_id="w1",
        goal_ledger=_ledger(),
        created_at=NOW,
        updated_at=NOW,
    )
    current = DraftSnapshot(
        draft_id="d1",
        workspace_id="w1",
        workspace_version=1,
        draft_version=1,
        days=(
            DraftDay(
                day_index=1,
                date=date(2026, 8, 3),
                visits=(
                    DraftVisit(
                        visit_id="v1",
                        place_candidate_id="p1",
                        duration_min=60,
                        earliest_start=time(9, 0),
                        latest_end=time(11, 0),
                    ),
                ),
            ),
        ),
    )
    updated = workspace.with_draft(current, at=NOW)
    assert updated.version == 2
    stale = current.model_copy(update={"draft_id": "d2"})
    with pytest.raises(ValueError, match="current workspace version"):
        updated.with_draft(stale, at=NOW)


def test_clarification_batch_is_bounded_to_five_questions():
    question = ClarificationQuestion(question_id="q0", prompt="Which?", reason="changes route")
    with pytest.raises(ValidationError):
        ClarificationBatch(
            interruption_id="i1",
            run_id="r1",
            provider_run_id="provider-r1",
            tool_call_id="tool-1",
            questions=tuple(question.model_copy(update={"question_id": f"q{i}"}) for i in range(6)),
            created_at=NOW,
        )
