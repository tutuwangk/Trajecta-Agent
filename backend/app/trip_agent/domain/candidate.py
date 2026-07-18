from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.trip_agent.domain.common import DomainModel


class CandidateSnapshot(DomainModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    agent_run_id: str = Field(min_length=1, max_length=200)
    draft_id: str = Field(min_length=1, max_length=200)
    workspace_version: int = Field(ge=1)
    fact_version: int = Field(ge=0)
    draft_version: int = Field(ge=1)
    completion_reason: str = Field(min_length=1, max_length=2_000)
    created_at: datetime


class CandidateRejected(DomainModel):
    rejection_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(min_length=1, max_length=200)
    issue_codes: tuple[str, ...] = Field(min_length=1)
    created_at: datetime
