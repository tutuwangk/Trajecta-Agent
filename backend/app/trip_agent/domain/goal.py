from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from app.trip_agent.domain.common import DomainModel


class CommitmentStrength(StrEnum):
    HARD = "hard"
    STRONG = "strong"
    SOFT = "soft"


class RunGoal(DomainModel):
    raw_request: str = Field(min_length=1, max_length=50_000)
    destination: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=60)


class GoalCommitment(DomainModel):
    commitment_id: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2_000)
    evidence_text: str = Field(min_length=1, max_length=4_000)
    subject_hypothesis_id: str | None = Field(default=None, min_length=1, max_length=200)
    strength: CommitmentStrength = CommitmentStrength.SOFT
    immutable: bool = False
    source: Literal["user"] = "user"

    @model_validator(mode="after")
    def validate_immutable_strength(self) -> "GoalCommitment":
        if self.immutable and self.strength is not CommitmentStrength.HARD:
            raise ValueError("immutable user commitment must have hard strength")
        return self


class GoalLedger(DomainModel):
    goal: RunGoal
    commitments: tuple[GoalCommitment, ...] = ()
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_unique_commitments(self) -> "GoalLedger":
        ids = [item.commitment_id for item in self.commitments]
        if len(ids) != len(set(ids)):
            raise ValueError("commitment ids must be unique")
        return self
