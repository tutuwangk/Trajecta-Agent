from __future__ import annotations

from datetime import date, time, timedelta
from typing import Iterable

from pydantic import Field

from app.trip_agent.domain import (
    DomainModel,
    EstimateClaim,
    KnowledgeClaim,
    ObservedClaim,
    ResolutionStatus,
    TripWorkspace,
)


class SimulationIssue(DomainModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2_000)
    day_index: int | None = None
    candidate_id: str | None = Field(default=None, max_length=200)
    blocking: bool


class CompiledVisit(DomainModel):
    visit_id: str
    candidate_id: str
    start_minute: int = Field(ge=0, le=2_880)
    end_minute: int = Field(ge=0, le=2_880)
    route_minutes_from_previous: int = Field(ge=0, le=1_440)
    route_fact_kind: str | None = None


class CompiledMeal(DomainModel):
    meal_id: str
    kind: str
    start_minute: int = Field(ge=0, le=2_880)
    end_minute: int = Field(ge=0, le=2_880)


class CompiledDay(DomainModel):
    day_index: int
    date: date
    visits: tuple[CompiledVisit, ...]
    meals: tuple[CompiledMeal, ...] = ()
    hotel_departure_minutes: int = Field(default=0, ge=0, le=1_440)
    hotel_return_minutes: int = Field(default=0, ge=0, le=1_440)
    outing_minutes: int = Field(ge=0, le=2_880)


class SimulationReport(DomainModel):
    ok: bool
    days: tuple[CompiledDay, ...]
    issues: tuple[SimulationIssue, ...]
    fact_issue_codes: tuple[str, ...] = ()


class FeasibilityCompiler:
    def compile(
        self, workspace: TripWorkspace, claims: Iterable[KnowledgeClaim]
    ) -> SimulationReport:
        issues: list[SimulationIssue] = []
        fact_issues: set[str] = set()
        draft = workspace.current_draft
        if draft is None:
            return SimulationReport(
                ok=False,
                days=(),
                issues=(
                    SimulationIssue(code="draft_missing", message="No draft exists.", blocking=True),
                ),
            )
        if not any(day.visits for day in draft.days):
            return SimulationReport(
                ok=False,
                days=(),
                issues=(
                    SimulationIssue(
                        code="draft_empty",
                        message="A publishable draft must contain at least one resolved visit.",
                        blocking=True,
                    ),
                ),
            )
        resolved = {
            item.candidate_id
            for item in workspace.place_resolutions
            if item.status is ResolutionStatus.RESOLVED and item.candidate_id
        }
        candidates = {item.candidate_id: item for item in workspace.place_candidates}
        claim_list = tuple(claims)
        compiled_days: list[CompiledDay] = []
        goal = workspace.goal_ledger.goal
        allowed_dates: set[date] | None = None
        if goal.start_date and goal.days:
            allowed_dates = {goal.start_date + timedelta(days=index) for index in range(goal.days)}
        for day in draft.days:
            if allowed_dates is not None and day.date not in allowed_dates:
                issues.append(
                    SimulationIssue(
                        code="date_out_of_range",
                        message=f"Day {day.day_index} is outside the requested trip dates.",
                        day_index=day.day_index,
                        blocking=True,
                    )
                )
            cursor = 9 * 60
            day_start = cursor
            prior_candidate_id: str | None = None
            compiled_visits: list[CompiledVisit] = []
            compiled_meals: list[CompiledMeal] = []
            meals_by_after: dict[str | None, list] = {}
            for meal in day.meals:
                meals_by_after.setdefault(meal.after_visit_id, []).append(meal)
            hotel_departure_minutes = 0
            hotel_return_minutes = 0
            if day.hotel_candidate_id:
                if day.hotel_candidate_id not in candidates:
                    issues.append(
                        SimulationIssue(
                            code="unknown_hotel_candidate",
                            message=day.hotel_candidate_id,
                            day_index=day.day_index,
                            candidate_id=day.hotel_candidate_id,
                            blocking=True,
                        )
                    )
                elif day.hotel_candidate_id not in resolved:
                    issues.append(
                        SimulationIssue(
                            code="unresolved_hotel_candidate",
                            message=day.hotel_candidate_id,
                            day_index=day.day_index,
                            candidate_id=day.hotel_candidate_id,
                            blocking=True,
                        )
                    )
                if day.visits:
                    hotel_departure_minutes, hotel_departure_kind = _route_duration(
                        claim_list,
                        day.hotel_candidate_id,
                        day.visits[0].place_candidate_id,
                        day.depart_hotel_mode,
                    )
                    if hotel_departure_kind is None:
                        issues.append(
                            SimulationIssue(
                                code="hotel_departure_route_missing",
                                message="Missing route from hotel to first visit.",
                                day_index=day.day_index,
                                candidate_id=day.hotel_candidate_id,
                                blocking=True,
                            )
                        )
                        fact_issues.add("hotel_departure_route_missing")
                    elif hotel_departure_kind == "estimate":
                        fact_issues.add("route_spatial_estimate")
                    cursor += hotel_departure_minutes
            cursor = _compile_meals(
                meals_by_after.get(None, []),
                cursor,
                day.day_index,
                issues,
                compiled_meals,
            )
            for visit in day.visits:
                if visit.place_candidate_id not in candidates:
                    issues.append(
                        SimulationIssue(
                            code="unknown_candidate",
                            message=visit.place_candidate_id,
                            day_index=day.day_index,
                            candidate_id=visit.place_candidate_id,
                            blocking=True,
                        )
                    )
                    continue
                if visit.place_candidate_id not in resolved:
                    issues.append(
                        SimulationIssue(
                            code="unresolved_candidate",
                            message=visit.place_candidate_id,
                            day_index=day.day_index,
                            candidate_id=visit.place_candidate_id,
                            blocking=True,
                        )
                    )
                route_minutes = 0
                route_kind: str | None = None
                if prior_candidate_id is not None:
                    route_entity = (
                        f"route:{prior_candidate_id}:{visit.place_candidate_id}:"
                        f"{visit.travel_mode_from_previous}"
                    )
                    route_claim = _best_claim(claim_list, route_entity, "duration_min")
                    if route_claim is None:
                        issues.append(
                            SimulationIssue(
                                code="route_fact_missing",
                                message=f"Missing route duration for {route_entity}.",
                                day_index=day.day_index,
                                candidate_id=visit.place_candidate_id,
                                blocking=True,
                            )
                        )
                        fact_issues.add("route_fact_missing")
                    else:
                        route_minutes = _positive_int(route_claim.value)
                        route_kind = route_claim.kind
                        if isinstance(route_claim, EstimateClaim):
                            fact_issues.add("route_spatial_estimate")
                    cursor += route_minutes
                if visit.earliest_start:
                    cursor = max(cursor, _minute(visit.earliest_start))
                if visit.fixed_start:
                    fixed = _minute(visit.fixed_start)
                    if cursor > fixed:
                        issues.append(
                            SimulationIssue(
                                code="fixed_appointment_overlap",
                                message=f"Cannot reach fixed visit {visit.visit_id} by {visit.fixed_start}.",
                                day_index=day.day_index,
                                candidate_id=visit.place_candidate_id,
                                blocking=True,
                            )
                        )
                    cursor = max(cursor, fixed)
                start = cursor
                end = start + visit.duration_min
                if visit.latest_end and end > _minute(visit.latest_end):
                    issues.append(
                        SimulationIssue(
                            code="visit_window_missed",
                            message=f"Visit {visit.visit_id} ends after its latest end.",
                            day_index=day.day_index,
                            candidate_id=visit.place_candidate_id,
                            blocking=False,
                        )
                    )
                if _closed_on(claim_list, visit.place_candidate_id, day.date):
                    issues.append(
                        SimulationIssue(
                            code="known_closed",
                            message=f"{visit.place_candidate_id} is explicitly closed on {day.date}.",
                            day_index=day.day_index,
                            candidate_id=visit.place_candidate_id,
                            blocking=True,
                        )
                    )
                if not _has_operational_fact(claim_list, visit.place_candidate_id, day.date):
                    fact_issues.add("place_operational_fact_missing")
                compiled_visits.append(
                    CompiledVisit(
                        visit_id=visit.visit_id,
                        candidate_id=visit.place_candidate_id,
                        start_minute=start,
                        end_minute=end,
                        route_minutes_from_previous=route_minutes,
                        route_fact_kind=route_kind,
                    )
                )
                cursor = end
                prior_candidate_id = visit.place_candidate_id
                cursor = _compile_meals(
                    meals_by_after.get(visit.visit_id, []),
                    cursor,
                    day.day_index,
                    issues,
                    compiled_meals,
                )
            if day.return_to_hotel and day.hotel_candidate_id and prior_candidate_id:
                hotel_return_minutes, hotel_return_kind = _route_duration(
                    claim_list,
                    prior_candidate_id,
                    day.hotel_candidate_id,
                    day.return_hotel_mode,
                )
                if hotel_return_kind is None:
                    issues.append(
                        SimulationIssue(
                            code="hotel_return_route_missing",
                            message="Missing route from last visit back to hotel.",
                            day_index=day.day_index,
                            candidate_id=day.hotel_candidate_id,
                            blocking=True,
                        )
                    )
                    fact_issues.add("hotel_return_route_missing")
                elif hotel_return_kind == "estimate":
                    fact_issues.add("route_spatial_estimate")
                cursor += hotel_return_minutes
            outing = max(0, cursor - day_start)
            if outing > 14 * 60:
                issues.append(
                    SimulationIssue(
                        code="extreme_outing_day",
                        message=f"Day {day.day_index} exceeds 14 outing hours and should be reviewed.",
                        day_index=day.day_index,
                        blocking=False,
                    )
                )
            compiled_days.append(
                CompiledDay(
                    day_index=day.day_index,
                    date=day.date,
                    visits=tuple(compiled_visits),
                    meals=tuple(compiled_meals),
                    hotel_departure_minutes=hotel_departure_minutes,
                    hotel_return_minutes=hotel_return_minutes,
                    outing_minutes=outing,
                )
            )
        return SimulationReport(
            ok=not any(issue.blocking for issue in issues),
            days=tuple(compiled_days),
            issues=tuple(issues),
            fact_issue_codes=tuple(sorted(fact_issues)),
        )


def _best_claim(
    claims: tuple[KnowledgeClaim, ...], entity_id: str, field: str
) -> KnowledgeClaim | None:
    matching = [claim for claim in claims if claim.entity_id == entity_id and claim.field == field]
    if not matching:
        return None
    return max(matching, key=lambda claim: (claim.release_eligible, claim.confidence, claim.acquired_at))


def _route_duration(
    claims: tuple[KnowledgeClaim, ...], origin: str, destination: str, mode: str
) -> tuple[int, str | None]:
    claim = _best_claim(claims, f"route:{origin}:{destination}:{mode}", "duration_min")
    if claim is None:
        return 0, None
    return _positive_int(claim.value), claim.kind


def _compile_meals(
    meals: list,
    cursor: int,
    day_index: int,
    issues: list[SimulationIssue],
    compiled: list[CompiledMeal],
) -> int:
    for meal in meals:
        if meal.earliest_start:
            cursor = max(cursor, _minute(meal.earliest_start))
        start = cursor
        end = start + meal.duration_min
        if meal.latest_end and end > _minute(meal.latest_end):
            issues.append(
                SimulationIssue(
                    code="meal_window_missed",
                    message=f"Meal {meal.meal_id} ends after its latest end.",
                    day_index=day_index,
                    blocking=False,
                )
            )
        compiled.append(
            CompiledMeal(
                meal_id=meal.meal_id,
                kind=meal.kind,
                start_minute=start,
                end_minute=end,
            )
        )
        cursor = end
    return cursor


def _has_operational_fact(
    claims: tuple[KnowledgeClaim, ...], candidate_id: str, applicable_date: date
) -> bool:
    return any(
        isinstance(claim, ObservedClaim)
        and claim.entity_id == candidate_id
        and claim.field in {"opening_hours", "closure", "last_entry", "reservation"}
        and claim.release_eligible
        and (claim.applicable_date is None or claim.applicable_date == applicable_date)
        for claim in claims
    )


def _closed_on(claims: tuple[KnowledgeClaim, ...], candidate_id: str, applicable_date: date) -> bool:
    for claim in claims:
        if (
            isinstance(claim, ObservedClaim)
            and claim.entity_id == candidate_id
            and claim.field == "closure"
            and claim.release_eligible
            and (claim.applicable_date is None or claim.applicable_date == applicable_date)
        ):
            text = str(claim.value).lower()
            if any(marker in text for marker in ("closed", "closure", "闭馆", "不开放")):
                return True
    return False


def _positive_int(value: object) -> int:
    try:
        return max(0, round(float(value)))
    except (TypeError, ValueError):
        return 0


def _minute(value: time) -> int:
    return value.hour * 60 + value.minute
