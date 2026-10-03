from __future__ import annotations

from datetime import datetime
from typing import Mapping

import pytest
from pydantic_ai.models.test import TestModel

from app.trip_agent_v3.adapters.web_search import WebSearchResult
from app.trip_agent_v3.adapters.operational_facts import (
    DeepSeekOperationalFactProvider,
)
from app.trip_agent_v3.domain.facts import (
    FactNeed,
    FactNeedKind,
    FactResolution,
)
from app.trip_agent_v3.domain.grounding import (
    CandidateEntityKind,
    GroundingCandidate,
)


class Web:
    def search(self, query: str, *, max_results: int):
        assert "陈麻婆豆腐" in query
        assert "官方 营业时间" in query
        return [
            WebSearchResult(
                title="门店公告",
                url="https://example.test/store",
                snippet="每日 10:00 至 22:00 营业。",
            )
        ]


class UnusedRouteProvider:
    async def resolve(
        self,
        *,
        need: FactNeed,
        candidates: Mapping[str, GroundingCandidate],
    ) -> FactResolution:
        raise AssertionError("route provider must not handle operation lookup")


@pytest.mark.anyio
async def test_operational_fact_requires_cited_visit_compatible_evidence() -> None:
    provider = DeepSeekOperationalFactProvider(
        model=TestModel(
            custom_output_args={
                "compatibility": "compatible",
                "rationale": "计划到访时间落在明确营业时段内。",
                "claims": [
                    {
                        "field": "opening_hours",
                        "value": "每日 10:00 至 22:00",
                        "source_indexes": [0],
                        "confidence": 0.99,
                    }
                ],
            }
        ),
        web=Web(),
        route_provider=UnusedRouteProvider(),
    )
    need = FactNeed(
        need_id="need-operation",
        kind=FactNeedKind.PLACE_OPERATION,
        day_number=1,
        candidate_ids=("cand-meal",),
        stop_ids=("stop-meal",),
        visit_at=datetime(2026, 8, 1, 12, 0),
    )
    candidate = GroundingCandidate(
        candidate_id="cand-meal",
        provider="amap",
        provider_place_id="B-meal",
        name="陈麻婆豆腐",
        entity_kind=CandidateEntityKind.PLACE,
        longitude=104.07,
        latitude=30.67,
    )

    resolution = await provider.resolve(
        need=need,
        candidates={"cand-meal": candidate},
    )

    assert resolution.operational_fact is not None
    assert resolution.operational_fact.visit_compatible is True
    assert resolution.operational_fact.claims[0].source_ids == (
        resolution.operational_fact.sources[0].source_id,
    )
    assert (
        resolution.operational_fact.sources[0].content_hash
        and len(resolution.operational_fact.sources[0].content_hash) == 64
    )


class FailingWeb:
    def search(self, query: str, *, max_results: int):
        raise RuntimeError("network unavailable")


@pytest.mark.anyio
async def test_amap_operating_hours_survive_web_search_failure() -> None:
    provider = DeepSeekOperationalFactProvider(
        model=TestModel(
            custom_output_args={
                "compatibility": "compatible",
                "rationale": "计划到访时间落在高德返回的常规营业时段内。",
                "claims": [
                    {
                        "field": "opening_hours",
                        "value": "周一至周日 08:30-18:30",
                        "source_indexes": [0],
                        "confidence": 0.99,
                    }
                ],
            }
        ),
        web=FailingWeb(),
        route_provider=UnusedRouteProvider(),
    )
    need = FactNeed(
        need_id="need-operation-amap",
        kind=FactNeedKind.PLACE_OPERATION,
        day_number=1,
        candidate_ids=("cand-visit",),
        stop_ids=("stop-visit",),
        visit_at=datetime(2026, 8, 3, 9, 30),
    )
    candidate = GroundingCandidate(
        candidate_id="cand-visit",
        provider="amap",
        provider_place_id="B-visit",
        name="成都武侯祠博物馆",
        opening_hours="周一至周日 08:30-18:30",
        entity_kind=CandidateEntityKind.PLACE,
        longitude=104.04,
        latitude=30.64,
    )

    resolution = await provider.resolve(
        need=need,
        candidates={"cand-visit": candidate},
    )

    assert resolution.operational_fact is not None
    assert resolution.operational_fact.sources[0].provider == (
        "amap_place_search"
    )


@pytest.mark.anyio
async def test_low_confidence_optional_claim_does_not_poison_strong_hours() -> None:
    provider = DeepSeekOperationalFactProvider(
        model=TestModel(
            custom_output_args={
                "compatibility": "compatible",
                "rationale": "到访时间在明确的营业区间内。",
                "claims": [
                    {
                        "field": "opening_hours",
                        "value": "周一至周日 08:30-18:30",
                        "source_indexes": [0],
                        "confidence": 0.98,
                    },
                    {
                        "field": "reservation",
                        "value": "可能需要预约",
                        "source_indexes": [0],
                        "confidence": 0.62,
                    },
                ],
            }
        ),
        web=FailingWeb(),
        route_provider=UnusedRouteProvider(),
    )
    need = FactNeed(
        need_id="need-operation-confidence",
        kind=FactNeedKind.PLACE_OPERATION,
        day_number=1,
        candidate_ids=("cand-visit",),
        stop_ids=("stop-visit",),
        visit_at=datetime(2026, 8, 3, 9, 30),
    )
    candidate = GroundingCandidate(
        candidate_id="cand-visit",
        provider="amap",
        provider_place_id="B-visit",
        name="成都武侯祠博物馆",
        opening_hours="周一至周日 08:30-18:30",
        entity_kind=CandidateEntityKind.PLACE,
        longitude=104.04,
        latitude=30.64,
    )

    resolution = await provider.resolve(
        need=need,
        candidates={"cand-visit": candidate},
    )

    assert resolution.operational_fact is not None
    assert [claim.field for claim in resolution.operational_fact.claims] == [
        "opening_hours"
    ]


class CountingWeb(Web):
    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, *, max_results: int):
        self.calls += 1
        return super().search(query, max_results=max_results)


@pytest.mark.anyio
async def test_operational_sources_are_reused_across_plan_revisions() -> None:
    web = CountingWeb()
    provider = DeepSeekOperationalFactProvider(
        model=TestModel(
            custom_output_args={
                "compatibility": "compatible",
                "rationale": "到访时间在营业时段内。",
                "claims": [
                    {
                        "field": "opening_hours",
                        "value": "每日 10:00 至 22:00",
                        "source_indexes": [0],
                        "confidence": 0.99,
                    }
                ],
            }
        ),
        web=web,
        route_provider=UnusedRouteProvider(),
    )
    candidate = GroundingCandidate(
        candidate_id="cand-meal",
        provider="amap",
        provider_place_id="B-meal",
        name="陈麻婆豆腐",
        entity_kind=CandidateEntityKind.PLACE,
        longitude=104.07,
        latitude=30.67,
    )
    for index, hour in enumerate((12, 13), start=1):
        resolution = await provider.resolve(
            need=FactNeed(
                need_id=f"need-operation-revision-{index}",
                kind=FactNeedKind.PLACE_OPERATION,
                day_number=1,
                candidate_ids=("cand-meal",),
                stop_ids=("stop-meal",),
                visit_at=datetime(2026, 8, 1, hour, 0),
            ),
            candidates={"cand-meal": candidate},
        )
        assert resolution.operational_fact is not None

    assert web.calls == 1
