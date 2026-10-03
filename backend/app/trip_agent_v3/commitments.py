from __future__ import annotations

from app.trip_agent_v3.domain.grounding import (
    GroundingRegistry,
    ResolutionStatus,
)
from app.trip_agent_v3.domain.plan import StopKind, WorkingDraft
from app.trip_agent_v3.domain.requirements import (
    CoverageDisposition,
    DispositionStatus,
    PlaceRole,
    RequirementLedger,
)


class PlanCommitmentError(ValueError):
    """Raised when a draft can make an explicit requirement disappear."""


_ROLE_TO_STOP_KIND = {
    PlaceRole.MEAL: StopKind.MEAL,
    PlaceRole.LODGING: StopKind.LODGING,
    PlaceRole.AIRPORT: StopKind.AIRPORT,
    PlaceRole.SHOPPING: StopKind.SHOPPING,
    PlaceRole.PHOTO: StopKind.PHOTO,
}


def commit_plan_dispositions(
    *,
    ledger: RequirementLedger,
    grounding: GroundingRegistry,
    draft: WorkingDraft,
    dispositions: tuple[CoverageDisposition, ...],
) -> RequirementLedger:
    obligations = {item.obligation_id: item for item in ledger.obligations}
    coverage_obligation_ids = {
        item.obligation_id
        for item in ledger.obligations
        if item.requires_coverage_disposition
    }
    disposition_by_obligation = {
        item.obligation_id: item for item in dispositions
    }
    if len(disposition_by_obligation) != len(dispositions):
        raise PlanCommitmentError(
            "every obligation must have exactly one disposition"
        )
    if set(disposition_by_obligation) != coverage_obligation_ids:
        raise PlanCommitmentError(
            "disposition coverage mismatch: "
            f"missing={sorted(coverage_obligation_ids - set(disposition_by_obligation))}, "
            f"unexpected={sorted(set(disposition_by_obligation) - coverage_obligation_ids)}"
        )

    selected_by_obligation: dict[str, str] = {}
    selected_candidate_ids: set[str] = set()
    for resolution in grounding.resolutions:
        if resolution.status is not ResolutionStatus.SELECTED:
            continue
        selected_candidate_id = resolution.selected_candidate_id
        if selected_candidate_id is None:
            continue
        selected_candidate_ids.add(selected_candidate_id)
        for obligation_id in resolution.obligation_ids:
            selected_by_obligation[obligation_id] = selected_candidate_id

    stops = {
        stop.stop_id: stop for day in draft.days for stop in day.stops
    }
    for stop in stops.values():
        if stop.candidate_id not in selected_candidate_ids:
            raise PlanCommitmentError(
                f"stop {stop.stop_id} is not a selected grounding candidate"
            )
        for obligation_id in stop.obligation_ids:
            obligation = obligations.get(obligation_id)
            if obligation is None:
                raise PlanCommitmentError(
                    f"stop {stop.stop_id} references unknown obligation "
                    f"{obligation_id}"
                )
            selected_candidate = selected_by_obligation.get(obligation_id)
            if selected_candidate != stop.candidate_id:
                raise PlanCommitmentError(
                    f"stop {stop.stop_id} does not use the candidate selected "
                    f"for obligation {obligation_id}"
                )
            disposition = disposition_by_obligation[obligation_id]
            if disposition.status is not DispositionStatus.SCHEDULED:
                raise PlanCommitmentError(
                    f"stop {stop.stop_id} carries obligation {obligation_id} "
                    "but its disposition is not scheduled"
                )
            expected_kind = _ROLE_TO_STOP_KIND.get(obligation.role)
            if expected_kind is not None and stop.kind is not expected_kind:
                label = (
                    "meal obligation"
                    if obligation.role is PlaceRole.MEAL
                    else f"{obligation.role.value} obligation"
                )
                raise PlanCommitmentError(
                    f"{label} {obligation_id} is encoded as {stop.kind.value}"
                )

    for obligation_id, disposition in disposition_by_obligation.items():
        if disposition.status is not DispositionStatus.SCHEDULED:
            continue
        stop = stops.get(disposition.stop_id or "")
        if stop is None:
            raise PlanCommitmentError(
                f"scheduled obligation {obligation_id} references unknown stop"
            )
        if obligation_id not in stop.obligation_ids:
            raise PlanCommitmentError(
                f"scheduled obligation {obligation_id} is missing from "
                f"stop {stop.stop_id}"
            )

    return RequirementLedger(
        ledger_id=ledger.ledger_id,
        goal_revision_id=ledger.goal_revision_id,
        revision=ledger.revision,
        obligations=ledger.obligations,
        constraints=ledger.constraints,
        dispositions=dispositions,
    )
