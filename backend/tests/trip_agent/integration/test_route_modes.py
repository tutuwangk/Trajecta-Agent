from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.trip_agent.adapters.place_knowledge import DeepSeekAmapPlaceKnowledge
from app.trip_agent.domain import GeoPoint, PlaceCandidate


@dataclass
class FakeAmap:
    called: tuple[str, str] | None = None

    def transit_direction(self, origin: str, destination: str, city: str):
        self.called = ("transit", city)
        return {
            "route": {
                "transits": [
                    {"duration": "1800", "walking_distance": "650"},
                ]
            }
        }

    def walking_direction(self, origin: str, destination: str):
        self.called = ("walking", "")
        return {"route": {"paths": [{"duration": "600", "distance": "800"}]}}

    def driving_direction(self, origin: str, destination: str):
        self.called = ("driving", "")
        return {"route": {"paths": [{"duration": "900", "distance": "5000"}]}}


def _candidate(candidate_id: str, city: str = "成都") -> PlaceCandidate:
    return PlaceCandidate(
        candidate_id=candidate_id,
        hypothesis_id=f"hypothesis-{candidate_id}",
        provider="fixture",
        provider_place_id=f"provider-{candidate_id}",
        name=candidate_id,
        city=city,
        location=GeoPoint(lng=104.0 if candidate_id == "a" else 104.1, lat=30.0),
        source_record_id=f"source-{candidate_id}",
    )


@pytest.mark.anyio
async def test_public_transport_uses_amap_transit_contract_not_driving():
    amap = FakeAmap()
    adapter = DeepSeekAmapPlaceKnowledge(city="成都", amap=amap)  # type: ignore[arg-type]

    acquisition = await adapter.acquire_route_facts(
        _candidate("a"),
        _candidate("b"),
        "public_transport",
    )

    assert amap.called == ("transit", "成都")
    duration = next(claim for claim in acquisition.claims if claim.field == "duration_min")
    assert duration.value == 30
    assert duration.entity_id == "route:a:b:public_transport"
    assert duration.release_eligible is True


@pytest.mark.anyio
async def test_unknown_route_mode_is_rejected_instead_of_silently_using_driving():
    adapter = DeepSeekAmapPlaceKnowledge(city="成都", amap=FakeAmap())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unsupported travel mode"):
        await adapter.acquire_route_facts(_candidate("a"), _candidate("b"), "flying")
