from __future__ import annotations

from datetime import date

from pydantic import Field

from app.trip_agent_v3.domain.common import DomainModel
from app.trip_agent_v3.domain.plan import WorkingDraft
from app.trip_agent_v3.domain.requirements import CoverageDisposition
from app.trip_agent_v3.domain.sources import SourceDocument


class JourneyGoal(DomainModel):
    goal_revision_id: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    start_date: date
    days: int = Field(ge=1, le=30)


class PlanningSubmission(DomainModel):
    draft: WorkingDraft
    dispositions: tuple[CoverageDisposition, ...]


class TripWorkspaceRecord(DomainModel):
    workspace_id: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1)
    goal: JourneyGoal
    sources: tuple[SourceDocument, ...] = Field(min_length=1, max_length=20)
