from __future__ import annotations

from datetime import date, datetime, time, timezone

from app.trip_agent.domain import (
    DraftDay,
    DraftMeal,
    DraftSnapshot,
    DraftVisit,
    EstimateClaim,
    GeoPoint,
    GoalLedger,
    ObservedClaim,
    PlaceCandidate,
    PlaceHypothesis,
    PlaceResolution,
    ResolutionStatus,
    RunGoal,
    TextSpan,
    TripWorkspace,
)
from app.trip_agent.validation import FeasibilityCompiler, ReleaseGate


NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)


def _workspace() -> TripWorkspace:
    workspace = TripWorkspace(
        workspace_id="workspace-compiler",
        goal_ledger=GoalLedger(
            goal=RunGoal(
                raw_request="2026年8月3日成都一日游",
                start_date=date(2026, 8, 3),
                days=1,
            )
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    hypotheses = (
        PlaceHypothesis(
            hypothesis_id="h1", raw_name="甲", context="甲乙", spans=(TextSpan(start=0, end=1),)
        ),
        PlaceHypothesis(
            hypothesis_id="h2", raw_name="乙", context="甲乙", spans=(TextSpan(start=1, end=2),)
        ),
    )
    workspace = workspace.with_hypotheses(hypotheses, at=NOW)
    candidates = (
        PlaceCandidate(
            candidate_id="c1",
            hypothesis_id="h1",
            provider="fixture",
            provider_place_id="p1",
            name="甲",
            location=GeoPoint(lng=104.0, lat=30.0),
            source_record_id="s1",
        ),
        PlaceCandidate(
            candidate_id="c2",
            hypothesis_id="h2",
            provider="fixture",
            provider_place_id="p2",
            name="乙",
            location=GeoPoint(lng=104.1, lat=30.1),
            source_record_id="s2",
        ),
    )
    workspace = workspace.with_candidates(candidates, at=NOW)
    workspace = workspace.with_resolution(
        PlaceResolution(
            hypothesis_id="h1",
            status=ResolutionStatus.RESOLVED,
            candidate_id="c1",
            rationale="fixture",
            workspace_version=workspace.version,
        ),
        at=NOW,
    )
    workspace = workspace.with_resolution(
        PlaceResolution(
            hypothesis_id="h2",
            status=ResolutionStatus.RESOLVED,
            candidate_id="c2",
            rationale="fixture",
            workspace_version=workspace.version,
        ),
        at=NOW,
    )
    draft = DraftSnapshot(
        draft_id="draft-1",
        workspace_id=workspace.workspace_id,
        workspace_version=workspace.version,
        draft_version=1,
        days=(
            DraftDay(
                day_index=1,
                date=date(2026, 8, 3),
                visits=(
                    DraftVisit(visit_id="v1", place_candidate_id="c1", duration_min=60),
                    DraftVisit(visit_id="v2", place_candidate_id="c2", duration_min=60),
                ),
            ),
        ),
    )
    return workspace.with_draft(draft, at=NOW)


def _operational(candidate_id: str) -> ObservedClaim:
    return ObservedClaim(
        claim_id=f"opening-{candidate_id}",
        entity_id=candidate_id,
        field="opening_hours",
        value="09:00-18:00",
        applicable_date=date(2026, 8, 3),
        source_record_ids=(f"source-{candidate_id}",),
        extractor="fixture",
        extractor_version="1",
        acquired_at=NOW,
        confidence=1,
        release_eligible=True,
    )


def test_missing_route_fact_is_a_structural_publication_blocker():
    report = FeasibilityCompiler().compile(_workspace(), (_operational("c1"), _operational("c2")))

    assert report.ok is False
    assert "route_fact_missing" in {issue.code for issue in report.issues}


def test_spatial_route_estimate_compiles_but_can_never_be_verified():
    route = EstimateClaim(
        claim_id="route-estimate",
        entity_id="route:c1:c2:walking",
        field="duration_min",
        value=25,
        source_record_ids=("source-spatial",),
        extractor="fixture",
        extractor_version="1",
        acquired_at=NOW,
        confidence=0.4,
        release_eligible=False,
        method="spatial_estimate",
    )
    workspace = _workspace()
    report = FeasibilityCompiler().compile(
        workspace, (_operational("c1"), _operational("c2"), route)
    )

    assert report.ok is True
    assert "route_spatial_estimate" in report.fact_issue_codes
    assert ReleaseGate().decide_fact_status(workspace, report).value == "degraded"


def test_explicit_closure_blocks_release_even_when_route_is_verified():
    route = ObservedClaim(
        claim_id="route-observed",
        entity_id="route:c1:c2:walking",
        field="duration_min",
        value=20,
        source_record_ids=("source-route",),
        extractor="fixture",
        extractor_version="1",
        acquired_at=NOW,
        confidence=1,
        release_eligible=True,
    )
    closure = ObservedClaim(
        claim_id="closure-c2",
        entity_id="c2",
        field="closure",
        value="闭馆",
        applicable_date=date(2026, 8, 3),
        source_record_ids=("source-c2-closure",),
        extractor="fixture",
        extractor_version="1",
        acquired_at=NOW,
        confidence=1,
        release_eligible=True,
    )
    report = FeasibilityCompiler().compile(
        _workspace(), (_operational("c1"), _operational("c2"), route, closure)
    )

    assert report.ok is False
    assert "known_closed" in {issue.code for issue in report.issues}


def test_recommended_window_and_long_day_are_experience_issues_not_release_blockers():
    workspace = _workspace()
    draft = DraftSnapshot(
        draft_id="draft-long-day",
        workspace_id=workspace.workspace_id,
        workspace_version=workspace.version,
        draft_version=workspace.current_draft.draft_version + 1,
        days=(
            DraftDay(
                day_index=1,
                date=date(2026, 8, 3),
                visits=(
                    DraftVisit(
                        visit_id="v-long",
                        place_candidate_id="c1",
                        duration_min=15 * 60,
                        latest_end=time(10, 0),
                    ),
                ),
            ),
        ),
    )
    workspace = workspace.with_draft(draft, at=NOW)

    report = FeasibilityCompiler().compile(workspace, (_operational("c1"),))

    issues = {issue.code: issue for issue in report.issues}
    assert report.ok is True
    assert issues["visit_window_missed"].blocking is False
    assert issues["extreme_outing_day"].blocking is False


def test_hotel_routes_and_meal_blocks_are_part_of_deterministic_timeline():
    workspace = _workspace()
    workspace = workspace.with_hypotheses(
        (
            PlaceHypothesis(
                hypothesis_id="h3",
                raw_name="酒店",
                context="酒店",
                spans=(TextSpan(start=0, end=2),),
            ),
        ),
        at=NOW,
    )
    workspace = workspace.with_candidates(
        (
            PlaceCandidate(
                candidate_id="c3",
                hypothesis_id="h3",
                provider="fixture",
                provider_place_id="p3",
                name="酒店",
                location=GeoPoint(lng=104.05, lat=30.05),
                source_record_id="s3",
            ),
        ),
        at=NOW,
    )
    workspace = workspace.with_resolution(
        PlaceResolution(
            hypothesis_id="h3",
            status=ResolutionStatus.RESOLVED,
            candidate_id="c3",
            rationale="fixture hotel",
            workspace_version=workspace.version,
        ),
        at=NOW,
    )
    draft = DraftSnapshot(
        draft_id="draft-hotel",
        workspace_id=workspace.workspace_id,
        workspace_version=workspace.version,
        draft_version=2,
        days=(
            DraftDay(
                day_index=1,
                date=date(2026, 8, 3),
                hotel_candidate_id="c3",
                depart_hotel_mode="driving",
                return_to_hotel=True,
                return_hotel_mode="driving",
                visits=(
                    DraftVisit(visit_id="v1", place_candidate_id="c1", duration_min=60),
                    DraftVisit(visit_id="v2", place_candidate_id="c2", duration_min=60),
                ),
                meals=(
                    DraftMeal(
                        meal_id="lunch",
                        kind="lunch",
                        duration_min=45,
                        after_visit_id="v1",
                        earliest_start=time(12, 0),
                        latest_end=time(14, 0),
                    ),
                ),
            ),
        ),
    )
    workspace = workspace.with_draft(draft, at=NOW)
    routes = (
        ObservedClaim(
            claim_id="hotel-out",
            entity_id="route:c3:c1:driving",
            field="duration_min",
            value=20,
            source_record_ids=("source-hotel-out",),
            extractor="fixture",
            extractor_version="1",
            acquired_at=NOW,
            confidence=1,
            release_eligible=True,
        ),
        ObservedClaim(
            claim_id="between",
            entity_id="route:c1:c2:walking",
            field="duration_min",
            value=30,
            source_record_ids=("source-between",),
            extractor="fixture",
            extractor_version="1",
            acquired_at=NOW,
            confidence=1,
            release_eligible=True,
        ),
        ObservedClaim(
            claim_id="hotel-back",
            entity_id="route:c2:c3:driving",
            field="duration_min",
            value=25,
            source_record_ids=("source-hotel-back",),
            extractor="fixture",
            extractor_version="1",
            acquired_at=NOW,
            confidence=1,
            release_eligible=True,
        ),
    )

    report = FeasibilityCompiler().compile(
        workspace,
        (_operational("c1"), _operational("c2"), *routes),
    )

    assert report.ok is True
    assert report.days[0].hotel_departure_minutes == 20
    assert report.days[0].hotel_return_minutes == 25
    assert report.days[0].meals[0].start_minute == 12 * 60
    assert report.days[0].meals[0].end_minute == 12 * 60 + 45
    assert report.days[0].outing_minutes == 340
