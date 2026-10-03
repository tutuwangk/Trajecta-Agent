from __future__ import annotations

from app.trip_agent_v3.domain.grounding import (
    CandidateSet,
    GroundingRegistry,
    ResolutionStatus,
)
from app.trip_agent_v3.domain.requirements import (
    DayAssignmentRequirement,
    PaceRequirement,
    RequirementLedger,
    TimeWindowRequirement,
    TransportPreferenceRequirement,
)
from app.trip_agent_v3.domain.plan import CompiledTimeline
from app.trip_agent_v3.domain.runtime import (
    ContextConstraint,
    ContextObligation,
    CurrentTimelineStop,
    LocalAgentContext,
    RuntimePhase,
    SelectedPlaceSummary,
)


def _coverage_counts(ledger: RequirementLedger) -> tuple[int, int]:
    report = ledger.coverage_report()
    return report.explicit_place_count, report.disposed_place_count


def _open_obligations(
    ledger: RequirementLedger,
    *,
    offset: int = 0,
) -> tuple[tuple[ContextObligation, ...], int]:
    report = ledger.coverage_report()
    open_ids = set(report.open_obligation_ids)
    open_items = [
        ContextObligation(
            obligation_id=item.obligation_id,
            mention=item.mention,
            role=item.role,
            priority=item.priority,
            query_decision=item.query_decision,
        )
        for item in ledger.obligations
        if item.obligation_id in open_ids
    ]
    visible = tuple(open_items[offset : offset + 10])
    return visible, max(len(open_items) - offset - len(visible), 0)


def _planning_constraints(
    ledger: RequirementLedger,
) -> tuple[ContextConstraint, ...]:
    result: list[ContextConstraint] = []
    for constraint in ledger.constraints:
        common = {
            "constraint_id": constraint.constraint_id,
            "kind": constraint.kind,
            "strength": constraint.strength,
            "evidence_text": tuple(item.text for item in constraint.evidence),
        }
        if isinstance(constraint, TimeWindowRequirement):
            result.append(
                ContextConstraint(
                    **common,
                    subject_obligation_ids=constraint.subject_obligation_ids,
                    fixed_commitment=constraint.fixed_commitment,
                    day_number=constraint.day_number,
                    earliest=constraint.earliest.strftime("%H:%M"),
                    latest=constraint.latest.strftime("%H:%M"),
                )
            )
        elif isinstance(constraint, DayAssignmentRequirement):
            result.append(
                ContextConstraint(
                    **common,
                    subject_obligation_ids=constraint.subject_obligation_ids,
                    fixed_commitment=constraint.fixed_commitment,
                    day_number=constraint.day_number,
                )
            )
        elif isinstance(constraint, PaceRequirement):
            result.append(
                ContextConstraint(
                    **common,
                    max_day_minutes=constraint.max_day_minutes,
                )
            )
        elif isinstance(constraint, TransportPreferenceRequirement):
            result.append(
                ContextConstraint(
                    **common,
                    preferred_modes=constraint.preferred_modes,
                    mode_policy=constraint.mode_policy,
                    max_walk_minutes=constraint.max_walk_minutes,
                )
            )
    return tuple(result)


def build_grounding_context(
    *,
    run_id: str,
    ledger: RequirementLedger,
    active_candidate_set: CandidateSet,
) -> LocalAgentContext:
    explicit_count, disposed_count = _coverage_counts(ledger)
    open_obligations, omitted_count = _open_obligations(ledger)
    return LocalAgentContext(
        run_id=run_id,
        goal_revision_id=ledger.goal_revision_id,
        phase=RuntimePhase.GROUNDING,
        instruction=(
            "只处理 active_candidate_set 对应的原始地点；选择候选时给出"
            "名称、城市、类别和路线影响理由，歧义则请求确认。"
        ),
        explicit_obligation_count=explicit_count,
        disposed_obligation_count=disposed_count,
        open_obligations=open_obligations,
        omitted_open_obligation_count=omitted_count,
        active_candidate_set=active_candidate_set,
    )


def build_planning_context(
    *,
    run_id: str,
    ledger: RequirementLedger,
    grounding: GroundingRegistry,
    active_day_number: int,
    obligation_offset: int = 0,
    timeline: CompiledTimeline | None = None,
    feedback: tuple[str, ...] = (),
) -> LocalAgentContext:
    explicit_count, disposed_count = _coverage_counts(ledger)
    open_obligations, omitted_count = _open_obligations(
        ledger, offset=obligation_offset
    )
    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidate_set in grounding.candidate_sets
        for candidate in candidate_set.candidates
    }
    selected_places: list[SelectedPlaceSummary] = []
    for resolution in grounding.resolutions:
        if (
            resolution.status is not ResolutionStatus.SELECTED
            or resolution.selected_candidate_id is None
        ):
            continue
        candidate = candidate_by_id[resolution.selected_candidate_id]
        selected_places.append(
            SelectedPlaceSummary(
                candidate_id=candidate.candidate_id,
                obligation_ids=resolution.obligation_ids,
                name=candidate.name,
                address=candidate.address,
                longitude=candidate.longitude,
                latitude=candidate.latitude,
            )
        )
    current_day_stops: tuple[CurrentTimelineStop, ...] = ()
    if timeline is not None:
        active_day = next(
            (
                day
                for day in timeline.days
                if day.day_number == active_day_number
            ),
            None,
        )
        if active_day is not None:
            current_day_stops = tuple(
                CurrentTimelineStop(
                    candidate_id=stop.candidate_id,
                    obligation_ids=stop.obligation_ids,
                    name=stop.name,
                    kind=stop.kind,
                    arrival_at=stop.arrival_at,
                    departure_at=stop.departure_at,
                    stay_duration_min=stop.stay_duration_min,
                )
                for stop in active_day.stops
            )
    return LocalAgentContext(
        run_id=run_id,
        goal_revision_id=ledger.goal_revision_id,
        phase=RuntimePhase.PLANNING,
        instruction=(
            "只规划 active_day_number；显式餐厅必须作为 meal stop，"
            "每天必须展示出发与结束锚点，并为每个 obligation 提交处置。"
        ),
        explicit_obligation_count=explicit_count,
        disposed_obligation_count=disposed_count,
        open_obligations=open_obligations,
        omitted_open_obligation_count=omitted_count,
        open_obligation_offset=obligation_offset,
        selected_places=tuple(selected_places),
        constraints=_planning_constraints(ledger),
        current_day_stops=current_day_stops,
        active_day_number=active_day_number,
        feedback=feedback[:5],
    )
