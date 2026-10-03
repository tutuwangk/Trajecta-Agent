from __future__ import annotations

from app.trip_agent_v3.domain.delivery import (
    CandidateSnapshot,
    FactStatus,
    GroundingDecisionView,
    GroundingOptionView,
    UnresolvedPlace,
)
from app.trip_agent_v3.domain.facts import (
    FactGapReport,
    FactResolutionStatus,
    OperationalFact,
)
from app.trip_agent_v3.domain.grounding import (
    GroundingRegistry,
    ResolutionStatus,
)
from app.trip_agent_v3.domain.plan import CompiledTimeline, StopLocation
from app.trip_agent_v3.domain.plan import StopKind
from app.trip_agent_v3.domain.requirements import RequirementLedger
from app.trip_agent_v3.experience import evaluate_timeline_constraints
from app.trip_agent_v3.fact_policy import gap_blocks_delivery


def assemble_candidate_snapshot(
    *,
    candidate_snapshot_id: str,
    workspace_id: str,
    producing_run_id: str,
    ledger: RequirementLedger,
    grounding: GroundingRegistry,
    timeline: CompiledTimeline,
    fact_gap_report: FactGapReport,
    operational_facts: tuple[OperationalFact, ...] = (),
) -> CandidateSnapshot:
    obligations = {
        item.obligation_id: item for item in ledger.obligations
    }
    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidate_set in grounding.candidate_sets
        for candidate in candidate_set.candidates
    }
    selected_candidate_ids = {
        resolution.selected_candidate_id
        for resolution in grounding.resolutions
        if resolution.status is ResolutionStatus.SELECTED
    }
    # Coordinates are copied from selected map identities after compilation.
    # The planner never supplies or edits map coordinates.
    mapped_days = []
    for day in timeline.days:
        mapped_stops = []
        for stop in day.stops:
            place = candidate_by_id.get(stop.candidate_id)
            if (
                place is not None
                and place.candidate_id in selected_candidate_ids
                and place.provider == "amap"
            ):
                mapped_stops.append(stop.model_copy(update={
                    "location": StopLocation(
                        longitude=place.longitude, latitude=place.latitude,
                    ),
                    "address": place.address,
                }))
            else:
                mapped_stops.append(stop.model_copy(update={
                    "location": None, "address": None,
                }))
        mapped_days.append(day.model_copy(update={"stops": tuple(mapped_stops)}))
    timeline = timeline.model_copy(update={"days": tuple(mapped_days)})
    candidate_set_by_target = {
        item.target_id: item for item in grounding.candidate_sets
    }
    decision_views: list[GroundingDecisionView] = []
    unresolved_places: list[UnresolvedPlace] = []
    for resolution in grounding.resolutions:
        mentions = tuple(
            obligations[obligation_id].mention
            for obligation_id in resolution.obligation_ids
            if obligation_id in obligations
        )
        selected = (
            candidate_by_id.get(resolution.selected_candidate_id)
            if resolution.selected_candidate_id
            else None
        )
        candidate_set = candidate_set_by_target[resolution.target_id]
        decision_views.append(
            GroundingDecisionView(
                target_id=resolution.target_id,
                obligation_ids=resolution.obligation_ids,
                mentions=mentions or (resolution.target_id,),
                status=resolution.status.value,
                selected_candidate_id=resolution.selected_candidate_id,
                selected_name=selected.name if selected else None,
                selected_address=selected.address if selected else None,
                rationale=resolution.rationale,
                confidence=resolution.confidence,
                options=tuple(
                    GroundingOptionView(
                        candidate_id=candidate.candidate_id,
                        name=candidate.name,
                        address=candidate.address,
                    )
                    for candidate in candidate_set.candidates
                ),
                clarification_question=resolution.clarification_question,
            )
        )
        if resolution.status is ResolutionStatus.SELECTED:
            continue
        unresolved_places.append(
            UnresolvedPlace(
                target_id=resolution.target_id,
                obligation_ids=resolution.obligation_ids,
                mention=" / ".join(mentions) or resolution.target_id,
                reason=resolution.rationale,
                clarification_question=resolution.clarification_question,
            )
        )

    required_operational_stop_ids = {
        stop.stop_id
        for day in timeline.days
        for stop in day.stops
        if stop.kind
        in {
            StopKind.VISIT,
            StopKind.MEAL,
            StopKind.SHOPPING,
            StopKind.PHOTO,
        }
    }
    operational_stop_ids = {fact.stop_id for fact in operational_facts}
    if any(gap_blocks_delivery(gap) for gap in fact_gap_report.gaps):
        fact_status = FactStatus.FAILED
    elif (
        operational_stop_ids != required_operational_stop_ids
        or any(fact.visit_compatible is None for fact in operational_facts)
        or any(
            leg.fact_status is FactResolutionStatus.ESTIMATED
            for day in timeline.days
            for leg in day.legs
        )
    ):
        fact_status = FactStatus.DEGRADED
    else:
        fact_status = FactStatus.VERIFIED
    experience_status, experience_issues = evaluate_timeline_constraints(
        ledger=ledger,
        timeline=timeline,
    )
    return CandidateSnapshot(
        candidate_snapshot_id=candidate_snapshot_id,
        workspace_id=workspace_id,
        producing_run_id=producing_run_id,
        goal_revision_id=ledger.goal_revision_id,
        requirement_ledger_id=ledger.ledger_id,
        grounding_registry_id=grounding.registry_id,
        timeline=timeline,
        coverage=ledger.coverage_report(),
        fact_gap_report=fact_gap_report,
        operational_facts=operational_facts,
        grounding_decisions=tuple(decision_views),
        unresolved_places=tuple(unresolved_places),
        fact_status=fact_status,
        experience_status=experience_status,
        experience_issues=experience_issues,
    )
