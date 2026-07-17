from app.adapters.legacy import fact_snapshot_from_legacy
from app.domain.validation import ValidationReport
from app.planning.release_gate import ReleaseGate
from app.schemas.models import UserProfile


def _profile():
    return UserProfile(destination="成都", days=1)


def _pois():
    return [
        {"poi_id": "p1", "standard_name": "A", "match_status": "matched", "location": {"lng": 1, "lat": 1}},
        {"poi_id": "p2", "standard_name": "B", "match_status": "matched", "location": {"lng": 2, "lat": 2}},
    ]


def test_release_gate_marks_selected_spatial_edge_as_degraded():
    facts = fact_snapshot_from_legacy(
        "成都",
        _pois(),
        [{"origin_poi_id": "p1", "destination_poi_id": "p2", "duration_min": 20, "source": "spatial_estimate"}],
    )
    itinerary = {"days": [{"day": 1, "items": [{"poi_id": "p1"}, {"poi_id": "p2"}]}]}

    decision = ReleaseGate().decide(
        itinerary=itinerary,
        report=ValidationReport(passed=True, issues=[]),
        facts=facts,
        user_profile=_profile(),
    )

    assert decision.status == "degraded"
    assert decision.degradation_reasons


def test_release_gate_checks_route_across_adjacent_outing_segments():
    facts = fact_snapshot_from_legacy(
        "成都",
        _pois(),
        [{"origin_poi_id": "p1", "destination_poi_id": "p2", "duration_min": 20, "source": "spatial_estimate"}],
    )
    itinerary = {
        "days": [
            {
                "day": 1,
                "segments": [
                    {"kind": "outing", "segment_time": "morning", "poi_ids": ["p1"]},
                    {"kind": "outing", "segment_time": "afternoon", "poi_ids": ["p2"]},
                ],
                "items": [{"poi_id": "p1"}, {"poi_id": "p2"}],
            }
        ]
    }

    decision = ReleaseGate().decide(
        itinerary=itinerary,
        report=ValidationReport(passed=True, issues=[]),
        facts=facts,
        user_profile=_profile(),
    )

    assert decision.status == "degraded"


def test_release_gate_marks_precise_selected_edge_as_verified():
    facts = fact_snapshot_from_legacy(
        "成都",
        _pois(),
        [{"origin_poi_id": "p1", "destination_poi_id": "p2", "duration_min": 20, "source": "amap_direction_api"}],
    )
    itinerary = {"days": [{"day": 1, "items": [{"poi_id": "p1"}, {"poi_id": "p2"}]}]}

    decision = ReleaseGate().decide(
        itinerary=itinerary,
        report=ValidationReport(passed=True, issues=[]),
        facts=facts,
        user_profile=_profile(),
    )

    assert decision.status == "verified"


def test_release_gate_never_verifies_spatial_hotel_rest_routes():
    facts = fact_snapshot_from_legacy(
        "成都",
        _pois(),
        [{"origin_poi_id": "p1", "destination_poi_id": "p2", "duration_min": 20, "source": "amap_direction_api"}],
        hotel_anchor={
            "poi_id": "hotel",
            "standard_name": "春熙路酒店",
            "location": {"lng": 104.08, "lat": 30.65},
            "match_confidence": 0.95,
        },
    )
    itinerary = {
        "days": [
            {
                "day": 1,
                "items": [{"poi_id": "p1"}, {"poi_id": "p2"}],
                "hotel_departure_transport_min": 15,
                "hotel_return_transport_min": 18,
                "hotel_departure_transport_source": "amap_direction_api",
                "hotel_return_transport_source": "amap_direction_api",
                "hotel_rest_breaks": [
                    {
                        "return_to_hotel_transport_min": 12,
                        "depart_from_hotel_transport_min": 14,
                        "return_to_hotel_transport_source": "spatial_estimate",
                        "depart_from_hotel_transport_source": "amap_direction_api",
                        "return_to_hotel_transport_degradation_reason": "回酒店路段使用空间估算。",
                    }
                ],
            }
        ]
    }

    decision = ReleaseGate().decide(
        itinerary=itinerary,
        report=ValidationReport(passed=True, issues=[]),
        facts=facts,
        user_profile=UserProfile(destination="成都", days=1, hotel_name="春熙路酒店"),
    )

    assert decision.status == "degraded"
    assert "回酒店路段使用空间估算。" in decision.degradation_reasons
