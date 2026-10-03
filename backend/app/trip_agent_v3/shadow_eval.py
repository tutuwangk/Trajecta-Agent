from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any, Iterable

from app.trip_agent_v3.domain.delivery import (
    CandidateSnapshot,
    DeliveryAssessment,
    ReleaseRecord,
    RunRecord,
    RunStatus,
)
from app.trip_agent_v3.domain.plan import StopKind
from app.trip_agent_v3.domain.requirements import (
    DispositionStatus,
    PlaceRole,
)


def validate_shadow_scenarios(
    scenarios: Iterable[dict[str, Any]],
) -> dict[str, object]:
    items = list(scenarios)
    errors: list[str] = []
    ids: list[str] = []
    cities: set[str] = set()
    day_counts: set[int] = set()
    cancellation_case_count = 0
    for index, item in enumerate(items):
        prefix = f"scenario[{index}]"
        case_id = item.get("case_id")
        city = item.get("city")
        days = item.get("days")
        raw_request = item.get("raw_request")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{prefix}: case_id is required")
        else:
            ids.append(case_id)
        if not isinstance(city, str) or not city.strip():
            errors.append(f"{prefix}: city is required")
        else:
            cities.add(city)
        if not isinstance(days, int) or not 1 <= days <= 5:
            errors.append(f"{prefix}: days must be an integer from 1 to 5")
        else:
            day_counts.add(days)
        if not isinstance(raw_request, str) or len(raw_request.strip()) < 20:
            errors.append(f"{prefix}: raw_request is too short")
        try:
            date.fromisoformat(str(item.get("start_date")))
        except ValueError:
            errors.append(f"{prefix}: start_date must be ISO date")
        clarification = item.get("clarification_response")
        if clarification is not None and (
            not isinstance(clarification, str)
            or len(clarification.strip()) < 10
        ):
            errors.append(
                f"{prefix}: clarification_response must be meaningful text"
            )
        evaluation_mode = item.get("evaluation_mode", "journey")
        if evaluation_mode not in {"journey", "cancellation"}:
            errors.append(
                f"{prefix}: evaluation_mode must be journey or cancellation"
            )
        if evaluation_mode == "cancellation":
            cancellation_case_count += 1
            if item.get("cancel_after_event") not in {
                "candidate_group_ready",
                "grounding_registry_compiled",
                "working_draft_committed",
            }:
                errors.append(
                    f"{prefix}: cancellation case requires a supported "
                    "cancel_after_event"
                )
    duplicates = sorted(
        case_id for case_id, count in Counter(ids).items() if count > 1
    )
    if duplicates:
        errors.append(f"duplicate case_id values: {duplicates}")
    if len(items) < 30:
        errors.append("at least 30 scenarios are required")
    if len(cities) < 6:
        errors.append("at least 6 cities are required")
    if day_counts != {1, 2, 3, 4, 5}:
        errors.append("scenario corpus must cover every duration from 1 to 5")
    if cancellation_case_count < 2:
        errors.append("at least two cancellation scenarios are required")
    return {
        "valid": not errors,
        "scenario_count": len(items),
        "city_count": len(cities),
        "cities": sorted(cities),
        "day_counts": sorted(day_counts),
        "cancellation_case_count": cancellation_case_count,
        "errors": errors,
    }


def build_shadow_case_report(
    *,
    scenario: dict[str, Any],
    run: RunRecord,
    candidate: CandidateSnapshot | None,
    assessment: DeliveryAssessment | None,
    release: ReleaseRecord | None,
    metrics: dict[str, object] | None,
    events: tuple[dict[str, object], ...],
    clarification_rounds: int,
) -> dict[str, object]:
    metrics = metrics or {}
    provider_blocker = next(
        (
            {
                "reason_code": event.get("reason_code"),
                "provider": event.get("provider"),
                "http_status": event.get("http_status"),
                "retryable_now": event.get("retryable_now"),
            }
            for event in reversed(events)
            if event.get("type") == "provider_blocked"
        ),
        None,
    )
    candidate_id = (
        candidate.candidate_snapshot_id if candidate is not None else None
    )
    release_lineage_ok = (
        release is None
        or (
            release.producing_run_id == run.run_id
            and release.candidate_snapshot_id == candidate_id
            and release.workspace_id == run.workspace_id
        )
    )
    timeline_complete = (
        _timeline_is_complete(candidate) if candidate is not None else None
    )
    named_meal_complete = (
        _named_meals_are_real_stops(candidate)
        if candidate is not None
        else None
    )
    fact_gaps_explained = (
        _fact_gaps_are_explained(candidate)
        if candidate is not None
        else None
    )
    scheduled_fact_scope_ok = (
        _fact_scope_matches_timeline(candidate, metrics)
        if candidate is not None
        else None
    )
    operational_fact_contract = (
        _operational_facts_match_visits(
            candidate=candidate,
            release=release,
        )
        if candidate is not None
        else None
    )
    blocker_specificity = (
        all(
            issue.day_numbers
            or issue.obligation_ids
            or issue.place_names
            for issue in assessment.issues
            if issue.severity.value == "blocking"
        )
        if assessment is not None
        else None
    )
    strict_publication_ok = (
        release is None
        or (
            run.status is RunStatus.SUCCEEDED
            and candidate is not None
            and candidate.fact_status.value == "verified"
            and candidate.experience_status.value != "conflict"
            and assessment is not None
            and assessment.may_publish
            and assessment.state.value == "publishable"
            and not any(issue.severity.value == "blocking" for issue in assessment.issues)
        )
    )
    cancelled_artifact_ok = None
    if run.status is RunStatus.CANCELLED:
        cancellation_index = next(
            (
                index
                for index, event in enumerate(events)
                if event.get("type") == "run_cancelled"
            ),
            None,
        )
        cancelled_artifact_ok = (
            cancellation_index is not None
            and not any(
                event.get("type")
                in {"candidate_assessed", "release_published"}
                for event in events[cancellation_index + 1 :]
            )
        )
    return {
        "case_id": scenario["case_id"],
        "city": scenario["city"],
        "days": scenario["days"],
        "evaluation_mode": scenario.get("evaluation_mode", "journey"),
        "run_id": run.run_id,
        "workspace_id": run.workspace_id,
        "run_status": run.status.value,
        "candidate_snapshot_id": candidate_id,
        "delivery_state": (
            assessment.state.value if assessment is not None else None
        ),
        "release_id": release.release_id if release is not None else None,
        "fact_status": (
            candidate.fact_status.value if candidate is not None else None
        ),
        "experience_status": (
            candidate.experience_status.value
            if candidate is not None
            else None
        ),
        "coverage_ratio": (
            candidate.coverage.coverage_ratio
            if candidate is not None
            else None
        ),
        "clarification_rounds": clarification_rounds,
        "provider_blocker": provider_blocker,
        "event_types": [str(event.get("type")) for event in events],
        "metrics": metrics,
        "checks": {
            "explicit_place_coverage": (
                candidate.coverage.coverage_ratio == 1
                if candidate is not None
                else None
            ),
            "named_meal_complete": named_meal_complete,
            "candidate_context_cap": (
                int(metrics.get("retained_candidate_count", 0))
                <= 3 * int(metrics.get("query_target_count", 0))
                if metrics
                else None
            ),
            "one_search_per_query_target": (
                all(
                    int(count) <= 1
                    for count in dict(
                        metrics.get("place_search_by_target", {})
                    ).values()
                )
                and int(
                    dict(metrics.get("provider_calls", {})).get(
                        "place_search", 0
                    )
                )
                <= int(metrics.get("query_target_count", 0))
                if metrics
                else None
            ),
            "scheduled_fact_scope": scheduled_fact_scope_ok,
            "timeline_complete": timeline_complete,
            "fact_gaps_explained": fact_gaps_explained,
            "operational_fact_contract": operational_fact_contract,
            "strict_publication": strict_publication_ok,
            "release_lineage": release_lineage_ok,
            "cancelled_artifact_safety": cancelled_artifact_ok,
            "blocker_specificity": blocker_specificity,
        },
    }


def aggregate_shadow_report(
    *,
    corpus_validation: dict[str, object],
    cases: list[dict[str, object]],
) -> dict[str, object]:
    statuses = Counter(str(item["run_status"]) for item in cases)
    delivery_states = Counter(
        str(item["delivery_state"])
        for item in cases
        if item.get("delivery_state") is not None
    )
    wall_clocks = sorted(
        int(dict(item.get("metrics", {})).get("wall_clock_ms", 0))
        for item in cases
        if dict(item.get("metrics", {})).get("wall_clock_ms") is not None
    )
    journey_cases = [
        item
        for item in cases
        if item.get("evaluation_mode", "journey") == "journey"
    ]

    def all_required(check: str) -> bool:
        values = [
            dict(item["checks"]).get(check)
            for item in journey_cases
        ]
        return bool(values) and all(value is True for value in values)

    cancellation_values = [
        dict(item["checks"]).get("cancelled_artifact_safety")
        for item in cases
        if dict(item["checks"]).get("cancelled_artifact_safety") is not None
    ]
    corpus_gate = (
        bool(corpus_validation.get("valid"))
        and len(cases) >= 30
        and all(
            item.get("run_status")
            in {
                RunStatus.SUCCEEDED.value,
                RunStatus.WAITING_USER.value,
                RunStatus.CANCELLED.value,
                RunStatus.FAILED.value,
            }
            and item.get("provider_blocker") is None
            for item in cases
        )
    )
    hard_gates = {
        "01_explicit_place_coverage_ratio_1": all_required(
            "explicit_place_coverage"
        ),
        "02_named_restaurant_meal_stop_100_percent": all_required(
            "named_meal_complete"
        ),
        "03_search_call_policy_limit": all_required(
            "one_search_per_query_target"
        ),
        "04_candidate_context_cap": all_required("candidate_context_cap"),
        "05_facts_only_for_scheduled_stops": all_required(
            "scheduled_fact_scope"
        ),
        "06_complete_timeline": all_required("timeline_complete"),
        "07_fact_failures_explained": all_required(
            "fact_gaps_explained"
        ),
        "08_operational_fact_at_visit_or_no_release": all_required(
            "operational_fact_contract"
        ),
        "09_verified_facts_and_no_blocking_publication": all_required(
            "strict_publication"
        ),
        "10_run_bound_release_lineage": all_required("release_lineage"),
        "11_cancelled_run_artifact_safety": (
            all(bool(value) for value in cancellation_values)
            if cancellation_values
            else None
        ),
        "12_blocker_specificity": all_required("blocker_specificity"),
        "13_human_review_and_seven_day_no_p0_p1": None,
    }
    return {
        "corpus": corpus_validation,
        "case_count": len(cases),
        "run_status_distribution": dict(sorted(statuses.items())),
        "delivery_state_distribution": dict(sorted(delivery_states.items())),
        "published_count": sum(
            item.get("release_id") is not None for item in cases
        ),
        "waiting_user_count": statuses.get("waiting_user", 0),
        "failed_count": statuses.get("failed", 0),
        "p50_wall_clock_ms": _percentile(wall_clocks, 0.5),
        "p95_wall_clock_ms": _percentile(wall_clocks, 0.95),
        "corpus_distribution_gate": corpus_gate,
        "hard_gates": hard_gates,
        "cutover_eligible": corpus_gate
        and all(value is True for value in hard_gates.values()),
        "cases": cases,
    }


def _timeline_is_complete(candidate: CandidateSnapshot) -> bool:
    if not candidate.timeline.days:
        return False
    for day in candidate.timeline.days:
        if not day.title.strip() or len(day.legs) != len(day.stops) - 1:
            return False
        if day.stops[0].kind not in {
            StopKind.LODGING,
            StopKind.AIRPORT,
        }:
            return False
        if day.stops[-1].kind not in {
            StopKind.LODGING,
            StopKind.AIRPORT,
        }:
            return False
        for index, leg in enumerate(day.legs):
            if (
                leg.from_stop_id != day.stops[index].stop_id
                or leg.to_stop_id != day.stops[index + 1].stop_id
                or leg.departure_at != day.stops[index].departure_at
                or leg.arrival_at != day.stops[index + 1].arrival_at
            ):
                return False
    return True


def _named_meals_are_real_stops(candidate: CandidateSnapshot) -> bool:
    stops = {
        stop.stop_id: stop
        for day in candidate.timeline.days
        for stop in day.stops
    }
    meal_entries = [
        entry
        for entry in candidate.coverage.entries
        if entry.role is PlaceRole.MEAL
    ]
    return all(
        entry.status is DispositionStatus.SCHEDULED
        and entry.stop_id in stops
        and stops[entry.stop_id].kind is StopKind.MEAL
        for entry in meal_entries
    )


def _fact_gaps_are_explained(candidate: CandidateSnapshot) -> bool:
    return all(
        bool(gap.place_names)
        and gap.day_number >= 1
        and bool(gap.failure_code)
        and bool(gap.failure_message)
        and bool(gap.impact)
        for gap in candidate.fact_gap_report.gaps
    )


def _fact_scope_matches_timeline(
    candidate: CandidateSnapshot,
    metrics: dict[str, object],
) -> bool:
    leg_count = sum(len(day.legs) for day in candidate.timeline.days)
    non_anchor_stops = sum(
        stop.kind not in {StopKind.LODGING, StopKind.AIRPORT}
        for day in candidate.timeline.days
        for stop in day.stops
    )
    return (
        int(metrics.get("route_fact_need_count", -1)) == leg_count
        and int(metrics.get("operational_fact_need_count", -1))
        == non_anchor_stops
        and int(metrics.get("fact_need_count", -1))
        == leg_count + non_anchor_stops
    )


def _operational_facts_match_visits(
    *,
    candidate: CandidateSnapshot,
    release: ReleaseRecord | None,
) -> bool:
    required = {
        stop.stop_id: stop
        for day in candidate.timeline.days
        for stop in day.stops
        if stop.kind
        not in {StopKind.LODGING, StopKind.AIRPORT}
    }
    actual = {fact.stop_id: fact for fact in candidate.operational_facts}
    coverage_complete = set(actual) == set(required) and all(
        fact.candidate_id == required[stop_id].candidate_id
        and fact.visit_at == required[stop_id].arrival_at
        and bool(fact.claims)
        and bool(fact.sources)
        for stop_id, fact in actual.items()
    )
    return coverage_complete or release is None


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    index = round((len(values) - 1) * fraction)
    return values[index]
