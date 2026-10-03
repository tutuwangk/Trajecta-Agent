from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from app.trip_agent_v3.domain.common import DomainModel
from app.trip_agent_v3.domain.query import QueryPlan


class CandidateEntityKind(StrEnum):
    PLACE = "place"
    BRANCH = "branch"
    ENTRANCE = "entrance"
    TRANSIT = "transit"
    AUXILIARY = "auxiliary"


class GroundingCandidate(DomainModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=100)
    provider_place_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=500)
    address: str | None = Field(default=None, max_length=1_000)
    category: str | None = Field(default=None, max_length=500)
    business_status: str | None = Field(default=None, max_length=200)
    opening_hours: str | None = Field(default=None, max_length=1_000)
    entity_kind: CandidateEntityKind
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class CandidateSet(DomainModel):
    target_id: str = Field(min_length=1, max_length=200)
    obligation_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    query_text: str = Field(min_length=1, max_length=500)
    candidates: tuple[GroundingCandidate, ...] = Field(max_length=3)
    provider_result_count: int = Field(ge=0)
    eligible_result_count: int = Field(default=0, ge=0)
    excluded_result_count: int = Field(default=0, ge=0)
    truncated_result_count: int = Field(default=0, ge=0)
    exclusion_reasons: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_counts(self) -> "CandidateSet":
        if len(self.obligation_ids) != len(set(self.obligation_ids)):
            raise ValueError("candidate-set obligation ids must be unique")
        candidate_ids = [item.candidate_id for item in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate ids must be unique within a target")
        provider_ids = [item.provider_place_id for item in self.candidates]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider place ids must be unique within a target")
        if self.provider_result_count != (
            self.eligible_result_count + self.excluded_result_count
        ):
            raise ValueError(
                "provider result count must equal eligible plus excluded results"
            )
        if self.eligible_result_count != (
            len(self.candidates) + self.truncated_result_count
        ):
            raise ValueError(
                "eligible result count must equal retained plus truncated results"
            )
        if sum(self.exclusion_reasons.values()) != self.excluded_result_count:
            raise ValueError(
                "exclusion reason counts must equal excluded result count"
            )
        return self


class ResolutionStatus(StrEnum):
    SELECTED = "selected"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NO_MATCH = "no_match"


class TargetResolution(DomainModel):
    target_id: str = Field(min_length=1, max_length=200)
    obligation_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    status: ResolutionStatus
    selected_candidate_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    rationale: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    selection_factors: tuple[str, ...] = Field(default=(), max_length=20)
    candidate_ids_requiring_confirmation: tuple[str, ...] = Field(
        default=(), max_length=3
    )
    clarification_question: str | None = Field(
        default=None, min_length=1, max_length=1_000
    )

    @model_validator(mode="after")
    def validate_resolution_payload(self) -> "TargetResolution":
        if self.status is ResolutionStatus.SELECTED:
            if not self.selected_candidate_id:
                raise ValueError("selected resolution requires selected_candidate_id")
            if not self.selection_factors:
                raise ValueError("selected resolution requires selection_factors")
            if (
                self.candidate_ids_requiring_confirmation
                or self.clarification_question is not None
            ):
                raise ValueError(
                    "selected resolution cannot carry clarification fields"
                )
            return self
        if self.selected_candidate_id is not None:
            raise ValueError(
                "only a selected resolution may carry selected_candidate_id"
            )
        if self.status is ResolutionStatus.NEEDS_CONFIRMATION:
            if not self.candidate_ids_requiring_confirmation:
                raise ValueError(
                    "needs-confirmation resolution requires candidate ids"
                )
            if not self.clarification_question:
                raise ValueError(
                    "needs-confirmation resolution requires a question"
                )
        elif (
            self.candidate_ids_requiring_confirmation
            or self.clarification_question is not None
        ):
            raise ValueError("no-match resolution cannot carry candidate choices")
        return self


class GroundingRegistry(DomainModel):
    registry_id: str = Field(min_length=1, max_length=200)
    query_plan_id: str = Field(min_length=1, max_length=200)
    candidate_sets: tuple[CandidateSet, ...] = ()
    resolutions: tuple[TargetResolution, ...] = ()

    @model_validator(mode="after")
    def validate_registry(self) -> "GroundingRegistry":
        sets_by_target = {item.target_id: item for item in self.candidate_sets}
        resolutions_by_target = {
            item.target_id: item for item in self.resolutions
        }
        if len(sets_by_target) != len(self.candidate_sets):
            raise ValueError("candidate-set target ids must be unique")
        if len(resolutions_by_target) != len(self.resolutions):
            raise ValueError("resolution target ids must be unique")
        if set(sets_by_target) != set(resolutions_by_target):
            raise ValueError(
                "every candidate set requires exactly one target resolution"
            )
        for target_id, resolution in resolutions_by_target.items():
            candidate_set = sets_by_target[target_id]
            if resolution.obligation_ids != candidate_set.obligation_ids:
                raise ValueError(
                    f"resolution obligations do not match target {target_id}"
                )
            candidate_ids = {
                item.candidate_id for item in candidate_set.candidates
            }
            if (
                resolution.selected_candidate_id is not None
                and resolution.selected_candidate_id not in candidate_ids
            ):
                raise ValueError(
                    f"selected candidate is not in candidate set {target_id}"
                )
            unknown_choices = (
                set(resolution.candidate_ids_requiring_confirmation)
                - candidate_ids
            )
            if unknown_choices:
                raise ValueError(
                    f"clarification candidates are not in candidate set "
                    f"{target_id}: {sorted(unknown_choices)}"
                )
        return self

    def validate_against(self, query_plan: QueryPlan) -> None:
        if self.query_plan_id != query_plan.plan_id:
            raise ValueError("grounding registry belongs to another query plan")
        expected = {item.target_id for item in query_plan.targets}
        actual = {item.target_id for item in self.candidate_sets}
        if expected != actual:
            raise ValueError(
                "grounding target coverage mismatch: "
                f"missing={sorted(expected - actual)}, "
                f"unexpected={sorted(actual - expected)}"
            )
