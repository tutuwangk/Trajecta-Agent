from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from app.trip_agent_v3.domain.common import DomainModel
from app.trip_agent_v3.domain.facts import FactGapReport, OperationalFact
from app.trip_agent_v3.domain.plan import CompiledTimeline
from app.trip_agent_v3.domain.requirements import CoverageReport


class FactStatus(StrEnum):
    VERIFIED = "verified"
    DEGRADED = "degraded"
    FAILED = "failed"


class ExperienceStatus(StrEnum):
    GOOD = "good"
    NEEDS_ADJUSTMENT = "needs_adjustment"
    CONFLICT = "conflict"


class DeliveryState(StrEnum):
    PUBLISHABLE = "publishable"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


class RunStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    WAITING_USER = "waiting_user"
    NEEDS_RESUME = "needs_resume"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunRecord(DomainModel):
    run_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    goal_revision_id: str = Field(min_length=1, max_length=200)
    status: RunStatus


class ExperienceIssue(DomainModel):
    severity: Literal["review", "blocking"] = "review"
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2_000)
    recommendation: str = Field(min_length=1, max_length=2_000)
    day_numbers: tuple[int, ...] = Field(default=(), max_length=30)
    obligation_ids: tuple[str, ...] = Field(default=(), max_length=30)
    place_names: tuple[str, ...] = Field(default=(), max_length=30)

    @model_validator(mode="after")
    def require_specific_scope(self) -> "ExperienceIssue":
        if not (
            self.day_numbers
            or self.obligation_ids
            or self.place_names
        ):
            raise ValueError(
                "experience issue requires a day, obligation, or place scope"
            )
        return self


class UnresolvedPlace(DomainModel):
    target_id: str = Field(min_length=1, max_length=200)
    obligation_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    mention: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2_000)
    clarification_question: str | None = Field(
        default=None, min_length=1, max_length=1_000
    )


class GroundingOptionView(DomainModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=500)
    address: str | None = Field(default=None, max_length=1_000)


class GroundingDecisionView(DomainModel):
    target_id: str = Field(min_length=1, max_length=200)
    obligation_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    mentions: tuple[str, ...] = Field(min_length=1, max_length=10)
    status: str = Field(min_length=1, max_length=100)
    selected_candidate_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    selected_name: str | None = Field(
        default=None, min_length=1, max_length=500
    )
    selected_address: str | None = Field(default=None, max_length=1_000)
    rationale: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    options: tuple[GroundingOptionView, ...] = Field(default=(), max_length=3)
    clarification_question: str | None = Field(
        default=None, min_length=1, max_length=1_000
    )


class CandidateSnapshot(DomainModel):
    candidate_snapshot_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    producing_run_id: str = Field(min_length=1, max_length=200)
    goal_revision_id: str = Field(min_length=1, max_length=200)
    requirement_ledger_id: str = Field(min_length=1, max_length=200)
    grounding_registry_id: str = Field(min_length=1, max_length=200)
    timeline: CompiledTimeline
    coverage: CoverageReport
    fact_gap_report: FactGapReport
    operational_facts: tuple[OperationalFact, ...] = ()
    grounding_decisions: tuple[GroundingDecisionView, ...] = ()
    unresolved_places: tuple[UnresolvedPlace, ...] = ()
    fact_status: FactStatus
    experience_status: ExperienceStatus
    experience_issues: tuple[ExperienceIssue, ...] = ()

    @model_validator(mode="after")
    def validate_issue_contract(self) -> "CandidateSnapshot":
        if (
            self.experience_status is not ExperienceStatus.GOOD
            and not self.experience_issues
        ):
            raise ValueError(
                "non-good experience status requires specific experience issues"
            )
        if (
            self.experience_status is ExperienceStatus.GOOD
            and self.experience_issues
        ):
            raise ValueError(
                "good experience status cannot carry experience issues"
            )
        timeline_stops = {
            stop.stop_id: stop
            for day in self.timeline.days
            for stop in day.stops
        }
        operational_stop_ids = [
            fact.stop_id for fact in self.operational_facts
        ]
        if len(operational_stop_ids) != len(set(operational_stop_ids)):
            raise ValueError(
                "a stop may have only one operational fact"
            )
        if set(operational_stop_ids) - timeline_stops.keys():
            raise ValueError(
                "operational fact references a stop outside the timeline"
            )
        for fact in self.operational_facts:
            stop = timeline_stops[fact.stop_id]
            if (
                fact.candidate_id != stop.candidate_id
                or fact.visit_at != stop.arrival_at
            ):
                raise ValueError(
                    "operational fact does not match the compiled stop"
                )
        return self


class DeliveryIssueSeverity(StrEnum):
    REVIEW = "review"
    BLOCKING = "blocking"


class DeliveryIssue(DomainModel):
    code: str = Field(min_length=1, max_length=100)
    severity: DeliveryIssueSeverity
    message: str = Field(min_length=1, max_length=2_000)
    recommendation: str = Field(min_length=1, max_length=2_000)
    day_numbers: tuple[int, ...] = Field(default=(), max_length=30)
    obligation_ids: tuple[str, ...] = Field(default=(), max_length=30)
    place_names: tuple[str, ...] = Field(default=(), max_length=30)


class DeliveryAssessment(DomainModel):
    candidate_snapshot_id: str = Field(min_length=1, max_length=200)
    producing_run_id: str = Field(min_length=1, max_length=200)
    state: DeliveryState
    may_publish: bool
    issues: tuple[DeliveryIssue, ...] = ()

    @model_validator(mode="after")
    def validate_state(self) -> "DeliveryAssessment":
        if self.may_publish is not (
            self.state is DeliveryState.PUBLISHABLE
        ):
            raise ValueError("only publishable assessment may publish")
        if self.state is DeliveryState.PUBLISHABLE and any(
            issue.severity is DeliveryIssueSeverity.BLOCKING
            for issue in self.issues
        ):
            raise ValueError("publishable assessment cannot carry blocking issues")
        if self.state is not DeliveryState.PUBLISHABLE and not self.issues:
            raise ValueError("non-publishable assessment requires issues")
        return self


class ReleaseDayNarrative(DomainModel):
    day_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4_000)


class ReleaseNarrative(DomainModel):
    overview: str = Field(min_length=1, max_length=2_000)
    days: tuple[ReleaseDayNarrative, ...] = Field(min_length=1, max_length=30)


class ReleaseRecord(DomainModel):
    release_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    producing_run_id: str = Field(min_length=1, max_length=200)
    candidate_snapshot_id: str = Field(min_length=1, max_length=200)
    goal_revision_id: str = Field(min_length=1, max_length=200)
    status: Literal["published"] = "published"
    immutable: Literal[True] = True
    narrative: ReleaseNarrative
    published_at: datetime
