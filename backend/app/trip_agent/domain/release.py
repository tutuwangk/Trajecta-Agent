from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.trip_agent.domain.common import DomainModel


class FactStatus(StrEnum):
    VERIFIED = "verified"
    DEGRADED = "degraded"
    FAILED = "failed"


class ExperienceStatus(StrEnum):
    GOOD = "good"
    NEEDS_ADJUSTMENT = "needs_adjustment"
    CONFLICT = "conflict"


class ReleaseRecord(DomainModel):
    release_id: str = Field(min_length=1, max_length=200)
    release_key: str = Field(min_length=1, max_length=300)
    workspace_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(min_length=1, max_length=200)
    workspace_version: int = Field(ge=1)
    fact_version: int = Field(ge=0)
    fact_status: FactStatus
    experience_status: ExperienceStatus
    issue_codes: tuple[str, ...] = ()
    route_fact_fingerprint: str = Field(min_length=1, max_length=256)
    created_at: datetime
