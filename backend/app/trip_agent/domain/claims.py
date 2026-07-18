from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from app.trip_agent.domain.common import DomainModel


class SourceRecord(DomainModel):
    source_record_id: str = Field(min_length=1, max_length=200)
    source_type: Literal["amap", "web", "user", "system"]
    provider: str = Field(min_length=1, max_length=100)
    uri: str | None = Field(default=None, max_length=4_000)
    excerpt: str | None = Field(default=None, max_length=20_000)
    payload: JsonValue | None = None
    content_hash: str = Field(min_length=1, max_length=128)
    retrieved_at: datetime
    valid_until: datetime | None = None


class ClaimBase(DomainModel):
    claim_id: str = Field(min_length=1, max_length=200)
    entity_id: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=100)
    value: JsonValue
    applicable_date: date | None = None
    applicable_place_id: str | None = Field(default=None, max_length=200)
    source_record_ids: tuple[str, ...] = ()
    extractor: str = Field(min_length=1, max_length=100)
    extractor_version: str = Field(min_length=1, max_length=100)
    acquired_at: datetime
    valid_until: datetime | None = None
    confidence: float = Field(ge=0, le=1)
    conflict_claim_ids: tuple[str, ...] = ()
    release_eligible: bool = False


class ObservedClaim(ClaimBase):
    kind: Literal["observed"] = "observed"

    @model_validator(mode="after")
    def require_sources(self) -> "ObservedClaim":
        if not self.source_record_ids:
            raise ValueError("observed claim requires at least one source record")
        return self


class DerivedClaim(ClaimBase):
    kind: Literal["derived"] = "derived"
    derivation: str = Field(min_length=1, max_length=2_000)


class EstimateClaim(ClaimBase):
    kind: Literal["estimate"] = "estimate"
    method: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def prevent_verified_estimate(self) -> "EstimateClaim":
        if self.release_eligible and self.method == "spatial_estimate":
            raise ValueError("spatial_estimate cannot be release-eligible as verified fact")
        return self


class DecisionClaim(ClaimBase):
    kind: Literal["decision"] = "decision"
    agent_run_id: str = Field(min_length=1, max_length=200)
    release_eligible: Literal[False] = False


class UserCommitmentClaim(ClaimBase):
    kind: Literal["user_commitment"] = "user_commitment"
    evidence_text: str = Field(min_length=1, max_length=4_000)
    immutable: bool = False


KnowledgeClaim = Annotated[
    ObservedClaim | DerivedClaim | EstimateClaim | DecisionClaim | UserCommitmentClaim,
    Field(discriminator="kind"),
]
