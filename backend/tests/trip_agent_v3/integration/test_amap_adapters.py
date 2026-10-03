from __future__ import annotations

from typing import Awaitable, Callable, Mapping

import pytest

from app.trip_agent_v3.adapters.amap import (
    AmapFactProvider,
    AmapPlaceSearchProvider,
)
from app.trip_agent_v3.domain.facts import (
    FactNeed,
    FactNeedKind,
    FactNeedStatus,
    FactResolutionStatus,
)
from app.trip_agent_v3.domain.grounding import (
    CandidateEntityKind,
    GroundingCandidate,
)
from app.trip_agent_v3.domain.query import QueryTarget
from app.trip_agent_v3.domain.requirements import TravelMode


class Coordinator:
    async def call(
        self,
        provider: str,
        operation: str,
        callback: Callable[[], Awaitable[object]],
    ):
        return await callback()


class Amap:
    def __init__(self, *, fail_route: bool = False) -> None:
        self.fail_route = fail_route

    def search_poi(self, keyword: str, city: str | None):
        return [
            {
                "id": "B1",
                "name": keyword,
                "location": "104.08,30.65",
            }
        ]

    def walking_direction(self, origin: str, destination: str):
        if self.fail_route:
            raise TimeoutError("provider timeout")
        return {"route": {"paths": [{"duration": "600"}]}}

    def driving_direction(self, origin: str, destination: str):
        if self.fail_route:
            raise TimeoutError("provider timeout")
        return {"route": {"paths": [{"duration": "1200"}]}}

    def transit_direction(
        self, origin: str, destination: str, city: str
    ):
        if self.fail_route:
            raise TimeoutError("provider timeout")
        return {"route": {"transits": [{"duration": "900"}]}}


def _candidate(
    candidate_id: str, name: str, longitude: float
) -> GroundingCandidate:
    return GroundingCandidate(
        candidate_id=candidate_id,
        provider="amap",
        provider_place_id=f"B-{candidate_id}",
        name=name,
        entity_kind=CandidateEntityKind.PLACE,
        longitude=longitude,
        latitude=30.65,
    )


def _route_need(
    requested_mode: TravelMode | None = None,
) -> FactNeed:
    return FactNeed(
        need_id="need-route",
        kind=FactNeedKind.ROUTE,
        day_number=1,
        candidate_ids=("cand-a", "cand-b"),
        stop_ids=("stop-a", "stop-b"),
        requested_mode=requested_mode,
    )


@pytest.mark.anyio
async def test_amap_search_consumes_only_query_target_text() -> None:
    provider = AmapPlaceSearchProvider(
        amap=Amap(), coordinator=Coordinator()
    )
    target = QueryTarget(
        target_id="query-1",
        obligation_ids=("obl-1",),
        query_text="武侯祠",
        destination="成都",
        rationale="已批准地点。",
    )

    results = await provider.search(target)

    assert results[0]["name"] == "武侯祠"


@pytest.mark.anyio
async def test_amap_fact_provider_returns_verified_route_without_estimate_fallback() -> None:
    provider = AmapFactProvider(
        city="成都", amap=Amap(), coordinator=Coordinator()
    )
    candidates: Mapping[str, GroundingCandidate] = {
        "cand-a": _candidate("cand-a", "酒店", 104.08),
        "cand-b": _candidate("cand-b", "武侯祠", 104.081),
    }

    resolution = await provider.resolve(
        need=_route_need(), candidates=candidates
    )

    assert resolution.need.status is FactNeedStatus.SUCCEEDED
    assert resolution.route_fact is not None
    assert resolution.route_fact.status is FactResolutionStatus.VERIFIED
    assert resolution.route_fact.duration_min == 10
    assert resolution.route_fact.origin_stop_id == "stop-a"
    assert resolution.route_fact.destination_stop_id == "stop-b"


@pytest.mark.anyio
async def test_amap_fact_provider_honors_requested_transit_mode() -> None:
    provider = AmapFactProvider(
        city="成都", amap=Amap(), coordinator=Coordinator()
    )
    candidates: Mapping[str, GroundingCandidate] = {
        "cand-a": _candidate("cand-a", "酒店", 104.08),
        "cand-b": _candidate("cand-b", "武侯祠", 104.081),
    }

    resolution = await provider.resolve(
        need=_route_need(TravelMode.TRANSIT),
        candidates=candidates,
    )

    assert resolution.route_fact is not None
    assert resolution.route_fact.mode == "transit"
    assert resolution.route_fact.duration_min == 15


@pytest.mark.anyio
async def test_amap_failure_is_specific_and_does_not_become_spatial_estimate() -> None:
    provider = AmapFactProvider(
        city="成都",
        amap=Amap(fail_route=True),
        coordinator=Coordinator(),
    )
    candidates: Mapping[str, GroundingCandidate] = {
        "cand-a": _candidate("cand-a", "酒店", 104.08),
        "cand-b": _candidate("cand-b", "成都博物馆", 104.081),
    }

    resolution = await provider.resolve(
        need=_route_need(), candidates=candidates
    )

    assert resolution.need.status is FactNeedStatus.FAILED
    assert "酒店" in (resolution.need.failure_message or "")
    assert "成都博物馆" in (resolution.need.failure_message or "")
    assert resolution.route_fact is None
