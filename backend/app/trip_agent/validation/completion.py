from __future__ import annotations

from datetime import timedelta

from pydantic import Field

from app.trip_agent.domain import (
    CommitmentStrength,
    DomainModel,
    ResolutionStatus,
    TripWorkspace,
    expand_visit_candidate_coverage,
)


class CompletionAssessment(DomainModel):
    complete: bool
    issue_codes: tuple[str, ...] = ()
    missing_day_indexes: tuple[int, ...] = ()
    missing_commitment_ids: tuple[str, ...] = ()
    details: tuple[str, ...] = Field(default=(), max_length=20)


class CompletionEvaluator:
    """Check product completeness without turning comfort heuristics into blockers."""

    def evaluate(self, workspace: TripWorkspace) -> CompletionAssessment:
        draft = workspace.current_draft
        issues: set[str] = set()
        details: list[str] = []
        missing_days: list[int] = []
        missing_commitments: list[str] = []
        if draft is None:
            return CompletionAssessment(
                complete=False,
                issue_codes=("draft_missing",),
                details=("No Agent-authored draft exists.",),
            )

        goal = workspace.goal_ledger.goal
        if goal.days is not None:
            actual_indexes = {day.day_index for day in draft.days}
            missing_days = [index for index in range(1, goal.days + 1) if index not in actual_indexes]
            if len(draft.days) != goal.days or missing_days:
                issues.add("trip_days_missing")
                details.append("The draft does not account for every requested trip day.")
        if goal.start_date is not None and goal.days is not None:
            expected_dates = {
                goal.start_date + timedelta(days=index) for index in range(goal.days)
            }
            if {day.date for day in draft.days} != expected_dates:
                issues.add("trip_dates_incomplete")
                details.append("The draft dates do not exactly match the requested trip range.")

        empty_touring = [day.day_index for day in draft.days if day.day_purpose == "touring" and not day.visits]
        if empty_touring:
            issues.add("touring_day_empty")
            details.append(f"Touring days without visits: {empty_touring}.")

        candidates = {item.candidate_id: item for item in workspace.place_candidates}
        resolved = {
            item.candidate_id
            for item in workspace.place_resolutions
            if item.status is ResolutionStatus.RESOLVED and item.candidate_id
        }
        scheduled_visits = {
            visit.place_candidate_id for day in draft.days for visit in day.visits
        }
        scheduled = expand_visit_candidate_coverage(
            workspace.place_candidates, scheduled_visits
        ) | {
            meal.place_candidate_id
            for day in draft.days
            for meal in day.meals
            if meal.place_candidate_id
        }
        lodging = {day.hotel_candidate_id for day in draft.days if day.hotel_candidate_id}
        resolutions_by_hypothesis = {
            item.hypothesis_id: item.candidate_id
            for item in workspace.place_resolutions
            if item.status is ResolutionStatus.RESOLVED and item.candidate_id
        }
        hypotheses = {item.hypothesis_id: item for item in workspace.place_hypotheses}
        for commitment in workspace.goal_ledger.commitments:
            if commitment.strength is not CommitmentStrength.HARD:
                continue
            if commitment.subject_hypothesis_id:
                resolved_candidate_id = resolutions_by_hypothesis.get(
                    commitment.subject_hypothesis_id
                )
                matching = {resolved_candidate_id} if resolved_candidate_id else set()
            else:
                # Compatibility for commitments created before entity-linked intent analysis:
                # first recover their resolved mention identity, then fall back to display text.
                matching_hypotheses = {
                    hypothesis_id
                    for hypothesis_id, hypothesis in hypotheses.items()
                    if commitment.value in hypothesis.raw_name
                    or hypothesis.raw_name in commitment.value
                }
                matching = {
                    resolutions_by_hypothesis[hypothesis_id]
                    for hypothesis_id in matching_hypotheses
                    if hypothesis_id in resolutions_by_hypothesis
                } or {
                    candidate_id
                    for candidate_id, candidate in candidates.items()
                    if commitment.value in candidate.name or candidate.name in commitment.value
                }
            represented = lodging if commitment.field == "lodging" else scheduled
            covered = bool(matching & resolved & represented)
            if not covered:
                missing_commitments.append(commitment.commitment_id)
        if missing_commitments:
            issues.add("core_commitment_missing")
            missing_labels = [
                f"{item.field}={item.value} ({item.commitment_id})"
                for item in workspace.goal_ledger.commitments
                if item.commitment_id in missing_commitments
            ]
            details.append(
                "Hard place commitments absent from the draft: "
                + ", ".join(missing_labels)
                + "."
            )

        return CompletionAssessment(
            complete=not issues,
            issue_codes=tuple(sorted(issues)),
            missing_day_indexes=tuple(missing_days),
            missing_commitment_ids=tuple(missing_commitments),
            details=tuple(details),
        )
