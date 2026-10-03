from __future__ import annotations

from pydantic import Field, model_validator

from app.trip_agent_v3.domain.common import DomainModel
from app.trip_agent_v3.domain.grounding import (
    CandidateSet,
    GroundingRegistry,
    ResolutionStatus,
    TargetResolution,
)
from app.trip_agent_v3.domain.query import QueryPlan


class GroundingCompilationError(ValueError):
    """Raised when an Agent decision is not bound to a retained candidate."""


class GroundingDecisionProposal(DomainModel):
    target_id: str = Field(min_length=1, max_length=200)
    status: ResolutionStatus
    selected_provider_place_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    rationale: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    selection_factors: tuple[str, ...] = Field(default=(), max_length=20)
    provider_place_ids_requiring_confirmation: tuple[str, ...] = Field(
        default=(), max_length=3
    )
    clarification_question: str | None = Field(
        default=None, min_length=1, max_length=1_000
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "GroundingDecisionProposal":
        if self.status is ResolutionStatus.SELECTED:
            if not self.selected_provider_place_id:
                raise ValueError(
                    "selected decision requires selected_provider_place_id"
                )
            if not self.selection_factors:
                raise ValueError("selected decision requires selection_factors")
            if (
                self.provider_place_ids_requiring_confirmation
                or self.clarification_question is not None
            ):
                raise ValueError(
                    "selected decision cannot carry clarification fields"
                )
            return self
        if self.selected_provider_place_id is not None:
            raise ValueError(
                "only selected decisions may reference a selected provider place"
            )
        if self.status is ResolutionStatus.NEEDS_CONFIRMATION:
            if not self.provider_place_ids_requiring_confirmation:
                raise ValueError(
                    "needs-confirmation decision requires provider place ids"
                )
            if not self.clarification_question:
                raise ValueError(
                    "needs-confirmation decision requires clarification_question"
                )
        elif (
            self.provider_place_ids_requiring_confirmation
            or self.clarification_question is not None
        ):
            raise ValueError("no-match decision cannot carry clarification fields")
        return self


def compile_grounding_registry(
    *,
    registry_id: str,
    query_plan: QueryPlan,
    candidate_sets: tuple[CandidateSet, ...],
    decisions: tuple[GroundingDecisionProposal, ...],
) -> GroundingRegistry:
    sets_by_target = {item.target_id: item for item in candidate_sets}
    decisions_by_target = {item.target_id: item for item in decisions}
    if len(sets_by_target) != len(candidate_sets):
        raise GroundingCompilationError("candidate-set target ids must be unique")
    if len(decisions_by_target) != len(decisions):
        raise GroundingCompilationError("decision target ids must be unique")
    expected_targets = {item.target_id for item in query_plan.targets}
    if set(sets_by_target) != expected_targets:
        raise GroundingCompilationError(
            "candidate-set coverage does not match query plan"
        )
    if set(decisions_by_target) != expected_targets:
        raise GroundingCompilationError(
            "grounding-decision coverage does not match query plan"
        )

    resolutions: list[TargetResolution] = []
    for target in query_plan.targets:
        candidate_set = sets_by_target[target.target_id]
        if candidate_set.obligation_ids != target.obligation_ids:
            raise GroundingCompilationError(
                f"candidate set obligations do not match {target.target_id}"
            )
        candidates_by_provider_id = {
            item.provider_place_id: item for item in candidate_set.candidates
        }
        decision = decisions_by_target[target.target_id]
        selected_candidate_id: str | None = None
        if decision.selected_provider_place_id:
            selected = candidates_by_provider_id.get(
                decision.selected_provider_place_id
            )
            if selected is None:
                raise GroundingCompilationError(
                    f"selected provider place is not retained for {target.target_id}"
                )
            selected_candidate_id = selected.candidate_id
        confirmation_candidate_ids: list[str] = []
        for provider_place_id in (
            decision.provider_place_ids_requiring_confirmation
        ):
            candidate = candidates_by_provider_id.get(provider_place_id)
            if candidate is None:
                raise GroundingCompilationError(
                    f"clarification provider place is not retained for "
                    f"{target.target_id}: {provider_place_id}"
                )
            confirmation_candidate_ids.append(candidate.candidate_id)
        resolutions.append(
            TargetResolution(
                target_id=target.target_id,
                obligation_ids=target.obligation_ids,
                status=decision.status,
                selected_candidate_id=selected_candidate_id,
                rationale=decision.rationale,
                confidence=decision.confidence,
                selection_factors=decision.selection_factors,
                candidate_ids_requiring_confirmation=tuple(
                    confirmation_candidate_ids
                ),
                clarification_question=decision.clarification_question,
            )
        )

    registry = GroundingRegistry(
        registry_id=registry_id,
        query_plan_id=query_plan.plan_id,
        candidate_sets=candidate_sets,
        resolutions=tuple(resolutions),
    )
    registry.validate_against(query_plan)
    return registry
