from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.trip_agent_v3.domain.common import DomainModel
from app.trip_agent_v3.domain.grounding import CandidateSet
from app.trip_agent_v3.domain.plan import StopKind
from app.trip_agent_v3.domain.requirements import (
    ConstraintStrength,
    ObligationPriority,
    PlaceRole,
    QueryDecision,
    TravelMode,
)


class RuntimePhase(StrEnum):
    REQUIREMENTS = "requirements"
    GROUNDING = "grounding"
    PLANNING = "planning"
    FACTS = "facts"
    DELIVERY = "delivery"


class ContextObligation(DomainModel):
    obligation_id: str = Field(min_length=1, max_length=200)
    mention: str = Field(min_length=1, max_length=500)
    role: PlaceRole
    priority: ObligationPriority
    query_decision: QueryDecision


class SelectedPlaceSummary(DomainModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    obligation_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    name: str = Field(min_length=1, max_length=500)
    address: str | None = Field(default=None, max_length=1_000)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class ContextConstraint(DomainModel):
    constraint_id: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    strength: ConstraintStrength
    fixed_commitment: bool = False
    evidence_text: tuple[str, ...] = Field(min_length=1, max_length=20)
    subject_obligation_ids: tuple[str, ...] = Field(default=(), max_length=20)
    day_number: int | None = Field(default=None, ge=1)
    earliest: str | None = Field(default=None, max_length=8)
    latest: str | None = Field(default=None, max_length=8)
    max_day_minutes: int | None = Field(default=None, ge=180, le=900)
    preferred_modes: tuple[TravelMode, ...] = Field(default=(), max_length=4)
    mode_policy: Literal["prefer", "only"] = "prefer"
    max_walk_minutes: int | None = Field(default=None, ge=5, le=180)


class CurrentTimelineStop(DomainModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    obligation_ids: tuple[str, ...] = Field(default=(), max_length=20)
    name: str = Field(min_length=1, max_length=500)
    kind: StopKind
    arrival_at: datetime
    departure_at: datetime
    stay_duration_min: int = Field(ge=0, le=720)


class LocalAgentContext(DomainModel):
    run_id: str = Field(min_length=1, max_length=200)
    goal_revision_id: str = Field(min_length=1, max_length=200)
    phase: RuntimePhase
    instruction: str = Field(min_length=1, max_length=2_000)
    explicit_obligation_count: int = Field(ge=0)
    disposed_obligation_count: int = Field(ge=0)
    open_obligations: tuple[ContextObligation, ...] = Field(
        default=(), max_length=10
    )
    omitted_open_obligation_count: int = Field(default=0, ge=0)
    open_obligation_offset: int = Field(default=0, ge=0)
    active_candidate_set: CandidateSet | None = None
    selected_places: tuple[SelectedPlaceSummary, ...] = Field(
        default=(), max_length=50
    )
    constraints: tuple[ContextConstraint, ...] = Field(
        default=(), max_length=50
    )
    current_day_stops: tuple[CurrentTimelineStop, ...] = Field(
        default=(), max_length=30
    )
    active_day_number: int | None = Field(default=None, ge=1)
    feedback: tuple[str, ...] = Field(default=(), max_length=5)
