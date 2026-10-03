from __future__ import annotations

from datetime import time
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.trip_agent_v3.domain.common import DomainModel
from app.trip_agent_v3.domain.requirements import (
    ObligationPriority,
    PlaceRole,
    QueryDecision,
    ConstraintStrength,
    TravelMode,
)


class SourceKind(StrEnum):
    USER_REQUEST = "user_request"
    RAW_MATERIAL = "raw_material"
    USER_REVISION = "user_revision"


class SourceDocument(DomainModel):
    source_id: str = Field(min_length=1, max_length=200)
    kind: SourceKind
    content: str = Field(min_length=1, max_length=100_000)
    content_hash: str = Field(min_length=64, max_length=64)


class EvidenceSpanProposal(DomainModel):
    source_id: str = Field(min_length=1, max_length=200)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_span(self) -> "EvidenceSpanProposal":
        if self.end <= self.start:
            raise ValueError("evidence span end must be greater than start")
        return self


class PlaceMentionProposal(DomainModel):
    mention_key: str = Field(min_length=1, max_length=200)
    mention: str = Field(min_length=1, max_length=500)
    role: PlaceRole
    priority: ObligationPriority
    evidence: tuple[EvidenceSpanProposal, ...] = Field(min_length=1)
    explicit: bool = True
    query_decision: QueryDecision
    decision_reason: str | None = Field(default=None, min_length=1, max_length=2_000)
    merge_into_mention_key: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    query_text: str | None = Field(default=None, min_length=1, max_length=500)
    aliases: tuple[str, ...] = Field(default=(), max_length=20)
    category_hints: tuple[str, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def validate_decision_payload(self) -> "PlaceMentionProposal":
        if (
            self.explicit
            and self.role
            in {
                PlaceRole.VISIT,
                PlaceRole.MEAL,
                PlaceRole.LODGING,
                PlaceRole.SHOPPING,
                PlaceRole.PHOTO,
                PlaceRole.AIRPORT,
            }
            and self.query_decision
            in {QueryDecision.REFERENCE, QueryDecision.NOT_A_PLACE}
        ):
            raise ValueError(
                "an explicit schedulable place must be queried, merged, or "
                "clarified"
            )
        if self.query_decision is QueryDecision.PENDING:
            raise ValueError("a requirement proposal cannot contain pending decisions")
        if self.query_decision is QueryDecision.QUERY:
            if not self.query_text:
                raise ValueError("query decisions require query_text")
        elif self.query_text is not None:
            raise ValueError("query_text is only valid for query decisions")
        if self.query_decision is QueryDecision.MERGE:
            if not self.merge_into_mention_key:
                raise ValueError("merge decisions require merge_into_mention_key")
            if self.merge_into_mention_key == self.mention_key:
                raise ValueError("a mention cannot merge into itself")
        elif self.merge_into_mention_key is not None:
            raise ValueError(
                "merge_into_mention_key is only valid for merge decisions"
            )
        if self.query_decision in {
            QueryDecision.MERGE,
            QueryDecision.REFERENCE,
            QueryDecision.NOT_A_PLACE,
            QueryDecision.CLARIFY,
        } and not self.decision_reason:
            raise ValueError(
                f"{self.query_decision.value} decisions require decision_reason"
            )
        return self


class TimeWindowProposal(DomainModel):
    kind: Literal["time_window"] = "time_window"
    proposal_key: str = Field(min_length=1, max_length=200)
    subject_mention_keys: tuple[str, ...] = Field(min_length=1, max_length=20)
    evidence: tuple[EvidenceSpanProposal, ...] = Field(min_length=1)
    strength: ConstraintStrength
    fixed_commitment: bool = False
    day_number: int | None = Field(default=None, ge=1)
    earliest: time
    latest: time

    @model_validator(mode="after")
    def validate_window(self) -> "TimeWindowProposal":
        if self.latest <= self.earliest:
            raise ValueError("time-window latest must be after earliest")
        return self


class DayAssignmentProposal(DomainModel):
    kind: Literal["day_assignment"] = "day_assignment"
    proposal_key: str = Field(min_length=1, max_length=200)
    subject_mention_keys: tuple[str, ...] = Field(min_length=1, max_length=20)
    evidence: tuple[EvidenceSpanProposal, ...] = Field(min_length=1)
    strength: ConstraintStrength
    fixed_commitment: bool = False
    day_number: int = Field(ge=1)


class PaceProposal(DomainModel):
    kind: Literal["pace"] = "pace"
    proposal_key: str = Field(min_length=1, max_length=200)
    evidence: tuple[EvidenceSpanProposal, ...] = Field(min_length=1)
    strength: ConstraintStrength
    max_day_minutes: int = Field(ge=180, le=900)


class TransportPreferenceProposal(DomainModel):
    kind: Literal["transport_preference"] = "transport_preference"
    proposal_key: str = Field(min_length=1, max_length=200)
    evidence: tuple[EvidenceSpanProposal, ...] = Field(min_length=1)
    strength: ConstraintStrength
    mode_policy: Literal["prefer", "only"] = "prefer"
    preferred_modes: tuple[TravelMode, ...] = Field(default=(), max_length=4)
    max_walk_minutes: int | None = Field(default=None, ge=5, le=180)

    @model_validator(mode="after")
    def validate_transport_payload(self) -> "TransportPreferenceProposal":
        if self.mode_policy == "only" and not self.preferred_modes:
            raise ValueError("exclusive transport policy requires modes")
        if not self.preferred_modes and self.max_walk_minutes is None:
            raise ValueError("transport constraint requires a mode preference or walking limit")
        if len(self.preferred_modes) != len(set(self.preferred_modes)):
            raise ValueError("preferred transport modes must be unique")
        return self


PlanningConstraintProposal = Annotated[
    TimeWindowProposal
    | DayAssignmentProposal
    | PaceProposal
    | TransportPreferenceProposal,
    Field(discriminator="kind"),
]


class RequirementProposal(DomainModel):
    proposal_id: str = Field(min_length=1, max_length=200)
    goal_revision_id: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    mentions: tuple[PlaceMentionProposal, ...] = ()
    constraints: tuple[PlanningConstraintProposal, ...] = ()

    @model_validator(mode="after")
    def validate_keys(self) -> "RequirementProposal":
        keys = [item.mention_key for item in self.mentions]
        if len(keys) != len(set(keys)):
            raise ValueError("mention keys must be unique")
        by_key = {item.mention_key: item for item in self.mentions}
        constraint_keys = [item.proposal_key for item in self.constraints]
        if len(constraint_keys) != len(set(constraint_keys)):
            raise ValueError("constraint proposal keys must be unique")
        for constraint in self.constraints:
            if isinstance(
                constraint,
                (PaceProposal, TransportPreferenceProposal),
            ):
                continue
            unknown_subjects = (
                set(constraint.subject_mention_keys) - by_key.keys()
            )
            if unknown_subjects:
                raise ValueError(
                    "constraint proposal references unknown mention keys: "
                    f"{sorted(unknown_subjects)}"
                )
        for mention in self.mentions:
            if mention.query_decision is not QueryDecision.MERGE:
                continue
            target = mention.merge_into_mention_key or ""
            if target not in by_key:
                raise ValueError(
                    f"merge target references unknown mention key: {target}"
                )
            if by_key[target].query_decision is not QueryDecision.QUERY:
                raise ValueError(
                    "merge target must be a query mention"
                )
        return self
