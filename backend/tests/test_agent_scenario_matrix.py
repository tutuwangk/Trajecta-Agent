import pytest

from app.adapters.legacy import fact_snapshot_from_legacy
from app.domain.validation import ValidationIssue, ValidationReport
from app.planning.release_gate import ReleaseGate
from app.schemas.models import UserProfile


def _poi(poi_id: str, name: str) -> dict:
    offset = 1 if poi_id == "p1" else 2
    return {
        "poi_id": poi_id,
        "standard_name": name,
        "match_status": "matched",
        "location": {"lng": 104 + offset / 100, "lat": 30 + offset / 100},
    }


def _edge(source: str = "amap_direction_api", duration: int | None = 20) -> dict:
    return {
        "origin_poi_id": "p1",
        "destination_poi_id": "p2",
        "duration_min": duration,
        "distance_m": 3000,
        "source": source,
    }


def _issue(code: str) -> ValidationReport:
    return ValidationReport(
        passed=False,
        issues=[
            ValidationIssue(
                code=code,
                severity="blocking",
                message=f"模拟问题：{code}",
                release_blocking=True,
            )
        ],
    )


SCENARIOS = [
    pytest.param(
        [_poi("p1", "武侯祠")], [], {"days": [{"day": 1, "items": [{"poi_id": "p1"}]}]}, ValidationReport(passed=True), {}, "verified", id="01-single-place-verified"
    ),
    pytest.param(
        [_poi("p1", "武侯祠"), _poi("p2", "人民公园")], [_edge("spatial_estimate")], {"days": [{"day": 1, "items": [{"poi_id": "p1"}, {"poi_id": "p2"}]}]}, ValidationReport(passed=True), {}, "degraded", id="02-amap-route-degraded"
    ),
    pytest.param(
        [_poi("p1", "武侯祠"), _poi("p2", "人民公园")], [], {"days": [{"day": 1, "items": [{"poi_id": "p1"}, {"poi_id": "p2"}]}]}, ValidationReport(passed=True), {}, "failed", id="03-missing-route-failed"
    ),
    pytest.param(
        [_poi("p1", "武侯祠")], [], {"days": [{"day": 1, "items": [{"poi_id": "p1"}]}]}, ValidationReport(passed=True), {"hotel_name": "春熙路酒店"}, "degraded", id="04-hotel-anchor-degraded"
    ),
    pytest.param(
        [_poi("p1", "欢乐谷")], [], {"days": [{"day": 1, "items": [{"poi_id": "p1"}]}]}, _issue("daily_absolute_limit_exceeded"), {}, "failed", id="05-extreme-duration-failed"
    ),
    pytest.param(
        [_poi("p1", "景区（暂停营业）")], [], {"days": [{"day": 1, "items": [{"poi_id": "p1"}]}]}, _issue("unavailable_place_scheduled"), {}, "failed", id="06-paused-place-failed"
    ),
    pytest.param(
        [_poi("p1", "秦岭景区"), _poi("p2", "秦岭景区")], [_edge()], {"days": [{"day": 1, "items": [{"poi_id": "p1"}, {"poi_id": "p2"}]}]}, _issue("duplicate_place_scheduled"), {}, "failed", id="07-duplicate-place-failed"
    ),
    pytest.param(
        [_poi("p1", "武侯祠"), _poi("p2", "人民公园")], [_edge()], {"days": [{"day": 1, "items": [{"poi_id": "p1"}, {"poi_id": "p2"}]}]}, ValidationReport(passed=True), {}, "verified", id="08-precise-route-verified"
    ),
    pytest.param(
        [_poi("p1", "武侯祠")], [], {"days": [{"day": 1, "items": [{"poi_id": "p1"}], "hotel_departure_transport_min": 20, "hotel_return_transport_min": 20, "hotel_departure_transport_source": "spatial_estimate", "hotel_return_transport_source": "spatial_estimate"}]}, ValidationReport(passed=True, issues=[]), {"hotel_name": "春熙路酒店", "hotel_anchor": {"poi_id": "hotel_anchor", "standard_name": "春熙路酒店", "location": {"lng": 104.08, "lat": 30.65}, "match_confidence": 0.95}}, "degraded", id="09-hotel-route-degraded"
    ),
    pytest.param(
        [_poi("p1", "武侯祠")], [], {"days": [{"day": 1, "items": [{"poi_id": "p1"}]}]}, _issue("meal_slot_missing"), {}, "failed", id="10-meal-missing-failed"
    ),
]


@pytest.mark.parametrize("pois,routes,itinerary,report,options,expected", SCENARIOS)
def test_simulated_real_scenario_release_outcome(pois, routes, itinerary, report, options, expected):
    hotel_anchor = options.get("hotel_anchor")
    facts = fact_snapshot_from_legacy("成都", pois, routes, hotel_anchor=hotel_anchor)
    profile = UserProfile(destination="成都", days=1, hotel_name=options.get("hotel_name"))

    decision = ReleaseGate().decide(
        itinerary=itinerary,
        report=report,
        facts=facts,
        user_profile=profile,
    )

    assert decision.status == expected
