from __future__ import annotations

from datetime import datetime
import json
from typing import Mapping

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart

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
async def test_sourced_hours_are_retained_without_model_confidence_threshold() -> None:
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


@pytest.mark.anyio
@pytest.mark.parametrize("place", ["测试商场", "测试博物馆", "测试餐厅"])
@pytest.mark.parametrize("read_page", [True, False])
async def test_missing_hours_triggers_targeted_search_and_page_lookup(place, read_page):
    class FollowUpWeb:
        def __init__(self):
            self.queries = []
            self.pages = []

        def search(self, query, *, max_results):
            self.queries.append(query)
            snippet = "地址与介绍。" if read_page or len(self.queries) == 1 else "周一至周日10:00-22:00"
            return [WebSearchResult(title=f"{place}官网", url="https://example.test/visit", snippet=snippet)]

        def fetch(self, result):
            self.pages.append(result.url)
            return result.model_copy(update={"snippet": f"{place}：周一至周日营业时间 10:00-22:00。"})

    requests = []
    def extract(messages, info):
        payload = json.loads(next(part.content for part in messages[-1].parts if isinstance(part, UserPromptPart)))
        requests.append(payload)
        sufficient = "10:00-22:00" in payload["sources"]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "compatibility": "compatible" if sufficient else "unknown",
            "rationale": "营业时间覆盖到访时刻。" if sufficient else "介绍页未包含营业时间。",
            "claims": [{"field": "opening_hours", "value": "每日10:00-22:00", "source_indexes": [0], "confidence": 0.99}] if sufficient else [],
        })])

    web = FollowUpWeb()
    if not read_page:
        web.fetch = None
    provider = DeepSeekOperationalFactProvider(model=FunctionModel(extract), web=web, route_provider=UnusedRouteProvider())
    candidate = GroundingCandidate(candidate_id="place", provider="amap", provider_place_id="B-place", name=place,
                                   entity_kind=CandidateEntityKind.PLACE, longitude=104.07, latitude=30.67)
    need = FactNeed(need_id="operation", kind=FactNeedKind.PLACE_OPERATION, day_number=1,
                    candidate_ids=("place",), stop_ids=("stop",), visit_at=datetime(2026, 10, 11, 13, 55))
    result = await provider.resolve(need=need, candidates={"place": candidate})
    assert result.operational_fact is not None
    assert len(web.queries) == 2
    assert "开放时间" in web.queries[1]
    assert web.pages == (["https://example.test/visit"] if read_page else [])
    assert len(requests) == 2
    assert "10:00-22:00" in result.operational_fact.sources[0].excerpt
    # Subsequent visit-time repair reuses the expanded source rather than
    # repeating the unsuccessful initial lookup.
    repaired = await provider.resolve(need=need.model_copy(update={"need_id": "later", "visit_at": datetime(2026, 10, 11, 14, 55)}), candidates={"place": candidate})
    assert repaired.operational_fact is not None
    assert len(web.queries) == 2


@pytest.mark.anyio
async def test_unresolved_public_lookup_has_bounded_search_cost():
    class EmptyWeb:
        def __init__(self): self.calls = 0
        def search(self, query, *, max_results):
            self.calls += 1
            return []
    web = EmptyWeb()
    provider = DeepSeekOperationalFactProvider(model=TestModel(), web=web, route_provider=UnusedRouteProvider())
    candidate = GroundingCandidate(candidate_id="place", provider="amap", provider_place_id="B-place", name="测试商场",
                                   entity_kind=CandidateEntityKind.PLACE, longitude=104.07, latitude=30.67)
    for hour in (12, 13):
        result = await provider.resolve(need=FactNeed(need_id=f"operation-{hour}", kind=FactNeedKind.PLACE_OPERATION, day_number=1,
                    candidate_ids=("place",), stop_ids=("stop",), visit_at=datetime(2026, 10, 11, hour, 0)), candidates={"place": candidate})
        assert result.operational_fact is None
    assert web.calls == 2


@pytest.mark.anyio
@pytest.mark.parametrize("compatibility,claims,expected", [
    ("unknown", [{"field": "opening_hours", "value": "每日10:00-22:00", "source_indexes": [0], "confidence": 0.6}], "succeeded"),
    ("incompatible", [{"field": "closure", "value": "周日闭馆", "source_indexes": [0], "confidence": 0.9}], "failed"),
    ("incompatible", [], "failed"),
])
async def test_source_claims_determine_availability_and_conflict(compatibility, claims, expected):
    provider = DeepSeekOperationalFactProvider(
        model=TestModel(custom_output_args={"compatibility": compatibility, "rationale": "来源中的营业安排。", "claims": claims}),
        web=Web(), route_provider=UnusedRouteProvider(),
    )
    candidate = GroundingCandidate(candidate_id="place", provider="amap", provider_place_id="B-place", name="陈麻婆豆腐",
                                   entity_kind=CandidateEntityKind.PLACE, longitude=104.07, latitude=30.67)
    need = FactNeed(need_id="operation", kind=FactNeedKind.PLACE_OPERATION, day_number=1,
                   candidate_ids=("place",), stop_ids=("stop",), visit_at=datetime(2026, 10, 11, 13, 55))
    result = await provider.resolve(need=need, candidates={"place": candidate})
    assert result.need.status.value == expected
    if compatibility == "unknown":
        assert result.operational_fact.visit_compatible is None
        assert result.operational_fact.claims[0].value == "每日10:00-22:00"
    elif claims:
        assert result.need.failure_code == "planned_visit_operationally_incompatible"
    else:
        assert result.need.failure_code == "operational_evidence_insufficient"


@pytest.mark.anyio
async def test_amap_hours_survive_extraction_service_failure_without_more_searches():
    class NoSearch:
        def search(self, *args, **kwargs):
            raise AssertionError("Existing map data should be reused")
    class UnavailableExtractor:
        async def run(self, *args, **kwargs):
            raise RuntimeError("model unavailable")
    provider = DeepSeekOperationalFactProvider(model=TestModel(), web=NoSearch(), route_provider=UnusedRouteProvider())
    provider.agent = UnavailableExtractor()
    candidate = GroundingCandidate(candidate_id="place", provider="amap", provider_place_id="B-place", name="测试商场",
                                   opening_hours="10:00-22:00", entity_kind=CandidateEntityKind.PLACE, longitude=104.07, latitude=30.67)
    need = FactNeed(need_id="operation", kind=FactNeedKind.PLACE_OPERATION, day_number=1,
                   candidate_ids=("place",), stop_ids=("stop",), visit_at=datetime(2026, 10, 11, 13, 55))
    result = await provider.resolve(need=need, candidates={"place": candidate})
    assert result.operational_fact.claims[0].value == "10:00-22:00"
    assert result.operational_fact.visit_compatible is None
