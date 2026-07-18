from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from app.trip_agent.domain.common import DomainModel


class NarrativeDay(DomainModel):
    day_index: int = Field(ge=1, le=60)
    theme: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1_000)


class NarrativeVisit(DomainModel):
    visit_id: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=1_000)


class ReleaseNarrative(DomainModel):
    narrative_id: str = Field(min_length=1, max_length=200)
    release_id: str = Field(min_length=1, max_length=200)
    route_fact_fingerprint: str = Field(min_length=1, max_length=256)
    overview: str = Field(min_length=1, max_length=2_000)
    days: tuple[NarrativeDay, ...]
    visits: tuple[NarrativeVisit, ...] = ()
    risk_notes: tuple[str, ...] = Field(default=(), max_length=20)
    generator: str = Field(min_length=1, max_length=100)
    created_at: datetime

    @model_validator(mode="after")
    def validate_unique_references(self) -> "ReleaseNarrative":
        day_indexes = [day.day_index for day in self.days]
        if len(day_indexes) != len(set(day_indexes)):
            raise ValueError("narrative day indexes must be unique")
        visit_ids = [visit.visit_id for visit in self.visits]
        if len(visit_ids) != len(set(visit_ids)):
            raise ValueError("narrative visit ids must be unique")
        return self
