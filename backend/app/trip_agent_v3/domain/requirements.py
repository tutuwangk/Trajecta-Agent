from __future__ import annotations

from datetime import time
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.trip_agent_v3.domain.common import DomainModel


class PlaceRole(StrEnum):
    VISIT = "visit"
    MEAL = "meal"
    LODGING = "lodging"
    SHOPPING = "shopping"
    PHOTO = "photo"
    AIRPORT = "airport"
    TRANSPORT = "transport"
    REFERENCE = "reference"


_SCHEDULABLE_PLACE_ROLES = {
    PlaceRole.VISIT,
    PlaceRole.MEAL,
    PlaceRole.LODGING,
    PlaceRole.SHOPPING,
    PlaceRole.PHOTO,
    PlaceRole.AIRPORT,
}


class ObligationPriority(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    OPTIONAL = "optional"


class QueryDecision(StrEnum):
    PENDING = "pending"
    QUERY = "query"
    MERGE = "merge"
    REFERENCE = "reference"
    NOT_A_PLACE = "not_a_place"
    CLARIFY = "clarify"


class DispositionStatus(StrEnum):
    UNHANDLED = "unhandled"
    SCHEDULED = "scheduled"
    PENDING_CONFIRMATION = "pending_confirmation"
    NOT_SCHEDULED = "not_scheduled"
    EXCLUDED = "excluded"


class EvidenceSpan(DomainModel):
    source_id: str = Field(min_length=1, max_length=200)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def validate_span(self) -> "EvidenceSpan":
        if self.end <= self.start:
            raise ValueError("evidence span end must be greater than start")
        if self.end - self.start < len(self.text):
            raise ValueError("evidence span cannot be shorter than its text")
        return self


class PlaceObligation(DomainModel):
    obligation_id: str = Field(min_length=1, max_length=200)
    proposal_key: str | None = Field(default=None, min_length=1, max_length=200)
    mention: str = Field(min_length=1, max_length=500)
    role: PlaceRole
    priority: ObligationPriority
    evidence: tuple[EvidenceSpan, ...] = Field(min_length=1)
    explicit: bool = True
    query_decision: QueryDecision = QueryDecision.PENDING
    query_text: str | None = Field(default=None, min_length=1, max_length=500)
    decision_reason: str | None = Field(default=None, min_length=1, max_length=2_000)
    merged_into_obligation_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    aliases: tuple[str, ...] = Field(default=(), max_length=20)
    category_hints: tuple[str, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def validate_query_decision(self) -> "PlaceObligation":
        if (
            self.explicit
            and self.role in _SCHEDULABLE_PLACE_ROLES
            and self.query_decision
            in {QueryDecision.REFERENCE, QueryDecision.NOT_A_PLACE}
        ):
            raise ValueError(
                "an explicit schedulable place cannot bypass grounding as "
                "reference or not_a_place"
            )
        if self.query_decision is QueryDecision.QUERY:
            if not self.query_text:
                raise ValueError("query decision requires query_text")
        elif self.query_text is not None:
            raise ValueError("query_text is only valid for query decisions")
        if self.query_decision is QueryDecision.MERGE:
            if not self.merged_into_obligation_id:
                raise ValueError(
                    "merge query decision requires merged_into_obligation_id"
                )
            if self.merged_into_obligation_id == self.obligation_id:
                raise ValueError("obligation cannot merge into itself")
        elif self.merged_into_obligation_id is not None:
            raise ValueError(
                "merged_into_obligation_id is only valid for merge decisions"
            )
        if self.query_decision in {
            QueryDecision.REFERENCE,
            QueryDecision.NOT_A_PLACE,
            QueryDecision.CLARIFY,
        } and not self.decision_reason:
            raise ValueError(
                f"{self.query_decision.value} query decision requires decision_reason"
            )
        return self

    @property
    def requires_coverage_disposition(self) -> bool:
        return (
            self.explicit
            and self.role is not PlaceRole.REFERENCE
            and self.query_decision
            not in {QueryDecision.REFERENCE, QueryDecision.NOT_A_PLACE}
        )


class CoverageDisposition(DomainModel):
    obligation_id: str = Field(min_length=1, max_length=200)
    status: DispositionStatus
    stop_id: str | None = Field(default=None, min_length=1, max_length=200)
    reason_code: str | None = Field(default=None, min_length=1, max_length=100)
    rationale: str | None = Field(default=None, min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "CoverageDisposition":
        if self.status is DispositionStatus.SCHEDULED:
            if not self.stop_id:
                raise ValueError("scheduled disposition requires stop_id")
            if self.reason_code is not None or self.rationale is not None:
                raise ValueError(
                    "scheduled disposition cannot carry an omission reason"
                )
            return self
        if self.stop_id is not None:
            raise ValueError("only a scheduled disposition may reference stop_id")
        if self.status is not DispositionStatus.UNHANDLED:
            if not self.reason_code:
                raise ValueError(
                    f"{self.status.value} disposition requires reason_code"
                )
            if not self.rationale:
                raise ValueError(
                    f"{self.status.value} disposition requires rationale"
                )
        return self


class CoverageEntry(DomainModel):
    obligation_id: str
    mention: str
    role: PlaceRole
    priority: ObligationPriority
    status: DispositionStatus
    stop_id: str | None = None
    reason_code: str | None = None
    rationale: str | None = None


class CoverageReport(DomainModel):
    explicit_place_count: int = Field(ge=0)
    disposed_place_count: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    open_obligation_ids: tuple[str, ...] = ()
    entries: tuple[CoverageEntry, ...] = ()


class ConstraintStrength(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"


class TravelMode(StrEnum):
    WALK = "walk"
    TAXI = "taxi"
    TRANSIT = "transit"
    DRIVING = "driving"


class TimeWindowRequirement(DomainModel):
    kind: Literal["time_window"] = "time_window"
    constraint_id: str = Field(min_length=1, max_length=200)
    subject_obligation_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    evidence: tuple[EvidenceSpan, ...] = Field(min_length=1)
    strength: ConstraintStrength
    fixed_commitment: bool = False
    day_number: int | None = Field(default=None, ge=1)
    earliest: time
    latest: time

    @model_validator(mode="after")
    def validate_window(self) -> "TimeWindowRequirement":
        if self.latest <= self.earliest:
            raise ValueError("time-window latest must be after earliest")
        if len(self.subject_obligation_ids) != len(
            set(self.subject_obligation_ids)
        ):
            raise ValueError("time-window subject obligations must be unique")
        return self


class DayAssignmentRequirement(DomainModel):
    kind: Literal["day_assignment"] = "day_assignment"
    constraint_id: str = Field(min_length=1, max_length=200)
    subject_obligation_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    evidence: tuple[EvidenceSpan, ...] = Field(min_length=1)
    strength: ConstraintStrength
    fixed_commitment: bool = False
    day_number: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_subjects(self) -> "DayAssignmentRequirement":
        if len(self.subject_obligation_ids) != len(
            set(self.subject_obligation_ids)
        ):
            raise ValueError("day-assignment subject obligations must be unique")
        return self


class PaceRequirement(DomainModel):
    kind: Literal["pace"] = "pace"
    constraint_id: str = Field(min_length=1, max_length=200)
    evidence: tuple[EvidenceSpan, ...] = Field(min_length=1)
    strength: ConstraintStrength
    max_day_minutes: int = Field(ge=180, le=900)


class TransportPreferenceRequirement(DomainModel):
    kind: Literal["transport_preference"] = "transport_preference"
    constraint_id: str = Field(min_length=1, max_length=200)
    evidence: tuple[EvidenceSpan, ...] = Field(min_length=1)
    strength: ConstraintStrength
    mode_policy: Literal["prefer", "only"] = "prefer"
    preferred_modes: tuple[TravelMode, ...] = Field(default=(), max_length=4)
    max_walk_minutes: int | None = Field(default=None, ge=5, le=180)

    @model_validator(mode="after")
    def validate_modes(self) -> "TransportPreferenceRequirement":
        if self.mode_policy == "only" and not self.preferred_modes:
            raise ValueError("exclusive transport policy requires modes")
        if not self.preferred_modes and self.max_walk_minutes is None:
            raise ValueError("transport constraint requires a mode preference or walking limit")
        if len(self.preferred_modes) != len(set(self.preferred_modes)):
            raise ValueError("preferred transport modes must be unique")
        return self


PlanningRequirement = Annotated[
    TimeWindowRequirement
    | DayAssignmentRequirement
    | PaceRequirement
    | TransportPreferenceRequirement,
    Field(discriminator="kind"),
]


class RequirementLedger(DomainModel):
    ledger_id: str = Field(min_length=1, max_length=200)
    goal_revision_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(default=1, ge=1)
    obligations: tuple[PlaceObligation, ...] = ()
    constraints: tuple[PlanningRequirement, ...] = ()
    dispositions: tuple[CoverageDisposition, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> "RequirementLedger":
        obligation_ids = [item.obligation_id for item in self.obligations]
        if len(obligation_ids) != len(set(obligation_ids)):
            raise ValueError("obligation ids must be unique")
        disposition_ids = [item.obligation_id for item in self.dispositions]
        if len(disposition_ids) != len(set(disposition_ids)):
            raise ValueError("each obligation may have only one disposition")
        unknown = set(disposition_ids) - set(obligation_ids)
        if unknown:
            raise ValueError(
                f"disposition references unknown obligation: {sorted(unknown)}"
            )
        obligations_by_id = {item.obligation_id: item for item in self.obligations}
        constraint_ids = [item.constraint_id for item in self.constraints]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("constraint ids must be unique")
        for constraint in self.constraints:
            if isinstance(
                constraint,
                (PaceRequirement, TransportPreferenceRequirement),
            ):
                continue
            unknown_subjects = (
                set(constraint.subject_obligation_ids) - obligations_by_id.keys()
            )
            if unknown_subjects:
                raise ValueError(
                    f"constraint references unknown obligations: "
                    f"{sorted(unknown_subjects)}"
                )
        for obligation in self.obligations:
            if obligation.query_decision is not QueryDecision.MERGE:
                continue
            target = obligation.merged_into_obligation_id or ""
            if target not in obligations_by_id:
                raise ValueError(
                    f"merged_into_obligation_id references unknown obligation: {target}"
                )
            if obligations_by_id[target].query_decision is QueryDecision.MERGE:
                raise ValueError("merge chains are not allowed")
            if obligations_by_id[target].query_decision is not QueryDecision.QUERY:
                raise ValueError(
                    "merged obligation must target a query obligation"
                )
        return self

    def coverage_report(self) -> CoverageReport:
        dispositions = {item.obligation_id: item for item in self.dispositions}
        obligations = tuple(
            item for item in self.obligations if item.requires_coverage_disposition
        )
        entries = tuple(
            CoverageEntry(
                obligation_id=obligation.obligation_id,
                mention=obligation.mention,
                role=obligation.role,
                priority=obligation.priority,
                status=(
                    dispositions[obligation.obligation_id].status
                    if obligation.obligation_id in dispositions
                    else DispositionStatus.UNHANDLED
                ),
                stop_id=(
                    dispositions[obligation.obligation_id].stop_id
                    if obligation.obligation_id in dispositions
                    else None
                ),
                reason_code=(
                    dispositions[obligation.obligation_id].reason_code
                    if obligation.obligation_id in dispositions
                    else None
                ),
                rationale=(
                    dispositions[obligation.obligation_id].rationale
                    if obligation.obligation_id in dispositions
                    else None
                ),
            )
            for obligation in obligations
        )
        disposed = sum(
            entry.status is not DispositionStatus.UNHANDLED for entry in entries
        )
        total = len(entries)
        return CoverageReport(
            explicit_place_count=total,
            disposed_place_count=disposed,
            coverage_ratio=disposed / total if total else 1,
            open_obligation_ids=tuple(
                entry.obligation_id
                for entry in entries
                if entry.status is DispositionStatus.UNHANDLED
            ),
            entries=entries,
        )
