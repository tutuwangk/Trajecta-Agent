from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from app.trip_agent.domain.common import DomainModel, GeoPoint, TextSpan


class HypothesisStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    EXCLUDED = "excluded"


class PlaceHypothesis(DomainModel):
    hypothesis_id: str = Field(min_length=1, max_length=200)
    raw_name: str = Field(min_length=1, max_length=500)
    context: str = Field(min_length=1, max_length=4_000)
    spans: tuple[TextSpan, ...] = Field(min_length=1)
    possible_category: str | None = Field(default=None, max_length=100)
    role: Literal["visit", "lodging", "meal", "destination_context", "reference"] = "visit"
    polarity: Literal["requested", "excluded", "neutral"] = "requested"
    route_relevant: bool = True
    brand_only: bool = False
    branch_unspecified: bool = False
    candidate_ids: tuple[str, ...] = ()
    status: HypothesisStatus = HypothesisStatus.OPEN


class PlaceCandidate(DomainModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    hypothesis_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=100)
    provider_place_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=500)
    address: str | None = Field(default=None, max_length=2_000)
    city: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)
    location: GeoPoint | None = None
    parent_provider_place_id: str | None = Field(default=None, max_length=200)
    source_record_id: str = Field(min_length=1, max_length=200)


class ResolutionStatus(StrEnum):
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    RESOLVED = "resolved"
    EXCLUDED = "excluded"


class PlaceResolution(DomainModel):
    hypothesis_id: str = Field(min_length=1, max_length=200)
    status: ResolutionStatus
    candidate_id: str | None = Field(default=None, max_length=200)
    rationale: str = Field(min_length=1, max_length=4_000)
    workspace_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_candidate_reference(self) -> "PlaceResolution":
        if self.status is ResolutionStatus.RESOLVED and not self.candidate_id:
            raise ValueError("resolved place requires candidate_id")
        if self.status is not ResolutionStatus.RESOLVED and self.candidate_id is not None:
            raise ValueError("only resolved place may reference candidate_id")
        return self
