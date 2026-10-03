from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models import Model

from app.trip_agent_v3.adapters.provider import (
    DeepSeekV4ChatModel,
    deepseek_v4_settings,
)
from app.trip_agent_v3.adapters.web_search import WebSearchClient, WebSearchResult
from app.trip_agent_v3.domain.facts import (
    FactNeed,
    FactNeedKind,
    FactNeedStatus,
    FactResolution,
    FactSourceRecord,
    OperationalClaim,
    OperationalFact,
)
from app.trip_agent_v3.domain.grounding import GroundingCandidate
from app.trip_agent_v3.ports import FactProviderPort
from app.trip_agent_v3.telemetry import RunTelemetry


class _ModelDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ExtractedClaim(_ModelDTO):
    field: Literal[
        "opening_hours", "closure", "last_entry", "reservation"
    ]
    value: str = Field(min_length=1, max_length=2_000)
    source_indexes: list[int] = Field(min_length=1, max_length=5)
    confidence: float = Field(ge=0, le=1)


class _OperationalCompatibility(_ModelDTO):
    compatibility: Literal["compatible", "incompatible", "unknown"]
    rationale: str = Field(min_length=1, max_length=2_000)
    claims: list[_ExtractedClaim] = Field(default_factory=list, max_length=10)


class DeepSeekOperationalFactProvider:
    """Bounded, source-cited operational lookup for scheduled stops only."""

    def __init__(
        self,
        *,
        model: Model,
        web: WebSearchClient | None = None,
        route_provider: FactProviderPort,
        telemetry: RunTelemetry | None = None,
    ) -> None:
        self.web = web or WebSearchClient()
        self.route_provider = route_provider
        self.telemetry = telemetry
        self._source_cache: dict[
            str, tuple[FactSourceRecord, ...]
        ] = {}
        self._expanded_candidates: set[str] = set()
        self.agent = Agent(
            model,
            output_type=_OperationalCompatibility,
            instructions=(
                "Determine whether the named place is operationally compatible "
                "with the exact planned visit date and time. Use only the numbered "
                "source excerpts, including retrieved page text. Extract only opening_hours, closure, last_entry, "
                "or reservation facts explicitly supported by cited snippets. "
                "Every cited fact must apply to the exact selected place and branch/address. "
                "Check snippet title, URL path, and text for a different park, museum branch, "
                "brand outlet, or tenant shop. The same institution or official domain does "
                "not make another site's schedule or reservation policy applicable. "
                "For example, 成都大熊猫繁育研究基地 must not inherit 熊猫谷/pandavalley "
                "ticket policies; a tenant shop's hours do not define an entire district's hours. "
                "Omit claims supported only by such mismatched sources; use unknown when "
                "the remaining correctly scoped evidence cannot execute the planned visit. "
                "Use the current Amap details or retrieved information as the source "
                "of truth. Extract recurring schedules as stated; no second source, "
                "exact-date announcement, last-entry policy, or reservation policy is "
                "required. Return compatible when the stated schedule allows this visit. "
                "Return incompatible only for an explicitly cited closure or a concrete "
                "conflict with stated operating/last-entry hours. Missing information "
                "does not imply a closure or mandatory reservation. "
                "A currently retrieved recurring daily or weekly schedule is "
                "date-applicable when the planned weekday matches and no cited "
                "source states an exception; the schedule does not need to name "
                "the exact calendar date. "
                "Return unknown when the available information does not determine "
                "the visit window; still extract any supported operating claims. "
                "Never infer a fact from the place name. Omit speculative claims. "
                "Write rationale and claim values in concise Chinese. "
                "For an incompatible visit, name the applicable date or hours and "
                "the concrete conflict; keep source indexes in source_indexes."
            ),
            model_settings=(
                deepseek_v4_settings("lightweight")
                if isinstance(model, DeepSeekV4ChatModel)
                else None
            ),
            retries={"output": 1},
        )

    async def resolve(
        self,
        *,
        need: FactNeed,
        candidates: Mapping[str, GroundingCandidate],
    ) -> FactResolution:
        if need.kind is FactNeedKind.ROUTE:
            return await self.route_provider.resolve(
                need=need,
                candidates=candidates,
            )
        if need.visit_at is None:
            raise ValueError("operational need has no visit_at")
        candidate = candidates.get(need.candidate_ids[0])
        if candidate is None:
            return _failed(
                need,
                code="scheduled_candidate_missing",
                message="运营事实请求引用了不存在的已选地点。",
            )
        sources = self._source_cache.get(candidate.candidate_id)
        if sources is None:
            query = (
                f"{candidate.name} {candidate.address or ''} "
                "官方 营业时间"
            )
            retrieved_at = datetime.now(timezone.utc)
            provider_source = _provider_source_record(
                candidate=candidate,
                retrieved_at=retrieved_at,
            )
            try:
                if self.telemetry is not None and provider_source is None:
                    self.telemetry.record_provider_call(
                        "operational_web_search"
                    )
                raw_results = [] if provider_source is not None else await asyncio.to_thread(
                    self.web.search,
                    query,
                    max_results=5,
                )
            except Exception:
                raw_results = []
            web_sources = tuple(
                _source_record(
                    candidate=candidate,
                    index=index,
                    title=item.title,
                    uri=item.url,
                    excerpt=item.snippet,
                    retrieved_at=retrieved_at,
                )
                for index, item in enumerate(raw_results)
                if item.snippet.strip()
            )
            sources = (
                ((provider_source,) if provider_source is not None else ())
                + web_sources
            )[:5]
            self._source_cache[candidate.candidate_id] = sources
        resolution = await self._extract(need=need, candidate=candidate, sources=sources)
        if (
            resolution.need.failure_code in {
                "operational_evidence_insufficient", "operational_sources_missing",
            }
            and candidate.candidate_id not in self._expanded_candidates
        ):
            self._expanded_candidates.add(candidate.candidate_id)
            if self.telemetry is not None:
                self.telemetry.record_provider_call("operational_web_search")
            try:
                results = await asyncio.to_thread(
                    self.web.search,
                    f"{candidate.name} 营业时间 开放时间",
                    max_results=5,
                )
            except Exception:
                results = []
            retrieved_at = datetime.now(timezone.utc)
            follow_up = tuple(
                _source_record(
                    candidate=candidate, index=index, title=item.title,
                    uri=item.url, excerpt=item.snippet or item.title,
                    retrieved_at=retrieved_at,
                )
                for index, item in enumerate(results)
            )
            enriched = await self._read_pages(candidate, follow_up or sources)
            by_uri: dict[str, FactSourceRecord] = {}
            # The focused query can return a better excerpt at the same URL.
            # Prefer page text, then the new snippet, then the earlier evidence.
            for source in (*enriched, *follow_up, *sources):
                by_uri.setdefault(source.uri, source)
            expanded = tuple(by_uri.values())[:5]
            self._source_cache[candidate.candidate_id] = expanded
            if expanded != sources:
                resolution = await self._extract(need=need, candidate=candidate, sources=expanded)
        return resolution

    async def _read_pages(
        self, candidate: GroundingCandidate, sources: tuple[FactSourceRecord, ...],
    ) -> tuple[FactSourceRecord, ...]:
        fetch = getattr(self.web, "fetch", None)
        if fetch is None:
            return ()

        async def read(index: int, source: FactSourceRecord):
            try:
                if self.telemetry is not None:
                    self.telemetry.record_provider_call("operational_web_page")
                page = await asyncio.to_thread(
                    fetch, WebSearchResult(title=source.title, url=source.uri, snippet=source.excerpt),
                )
                return _source_record(
                    candidate=candidate, index=index, title=page.title,
                    uri=page.url, excerpt=page.snippet,
                    retrieved_at=datetime.now(timezone.utc),
                )
            except Exception:
                return source

        return tuple(await asyncio.gather(*(read(i, source) for i, source in enumerate(sources[:2]))))

    async def _extract(
        self, *, need: FactNeed, candidate: GroundingCandidate,
        sources: tuple[FactSourceRecord, ...],
    ) -> FactResolution:
        if not sources:
            return _failed(
                need,
                code="operational_sources_missing",
                message=(
                    f"没有找到足以核验 {candidate.name} 在计划到访时段"
                    "是否可执行的来源。"
                ),
            )
        evidence = "\n\n".join(
            f"[{index}] {source.title}\n{source.uri}\n{source.excerpt}"
            for index, source in enumerate(sources)
        )
        try:
            result = await self.agent.run(
                json.dumps(
                    {
                        "place": candidate.name,
                        "address": candidate.address,
                        "planned_visit_at": need.visit_at.isoformat(),
                        "sources": evidence,
                    },
                    ensure_ascii=False,
                ),
                usage_limits=UsageLimits(request_limit=2),
            )
        except Exception:
            # Keep map-provider values even when the extraction service is unavailable.
            amap_source = next((source for source in sources if source.provider == "amap_place_search"), None)
            if amap_source is not None and candidate.opening_hours:
                return FactResolution(
                    need=need.model_copy(update={"status": FactNeedStatus.SUCCEEDED}),
                    operational_fact=OperationalFact(
                        fact_id=f"operation_{need.need_id}", candidate_id=candidate.candidate_id,
                        stop_id=need.stop_ids[0], visit_at=need.visit_at, visit_compatible=None,
                        claims=(OperationalClaim(field="opening_hours", value=candidate.opening_hours,
                                source_ids=(amap_source.source_id,), confidence=1),),
                        sources=(amap_source,),
                    ),
                )
            return _failed(
                need,
                code="operational_extraction_failed",
                message=(
                    f"{candidate.name} 的来源存在，但未能提取可交付的"
                    "运营事实。"
                ),
            )
        if self.telemetry is not None:
            self.telemetry.record_model_usage(
                result.usage, role="operational_fact_extractor"
            )
        output = result.output
        claims: list[OperationalClaim] = []
        for claim in output.claims:
            indexes = tuple(sorted(set(claim.source_indexes)))
            if any(index < 0 or index >= len(sources) for index in indexes):
                continue
            claims.append(
                OperationalClaim(
                    field=claim.field,
                    value=claim.value,
                    source_ids=tuple(
                        sources[index].source_id for index in indexes
                    ),
                    confidence=claim.confidence,
                )
            )
        if not claims or output.compatibility == "incompatible":
            code = (
                "planned_visit_operationally_incompatible"
                if output.compatibility == "incompatible" and claims
                else "operational_evidence_insufficient"
            )
            return _failed(
                need,
                code=code,
                message=f"{candidate.name}：{output.rationale}",
            )
        return FactResolution(
            need=need.model_copy(
                update={"status": FactNeedStatus.SUCCEEDED}
            ),
            operational_fact=OperationalFact(
                fact_id=f"operation_{need.need_id}",
                candidate_id=candidate.candidate_id,
                stop_id=need.stop_ids[0],
                visit_at=need.visit_at,
                visit_compatible=True if output.compatibility == "compatible" else None,
                claims=tuple(claims),
                sources=sources,
            ),
        )


def _failed(need: FactNeed, *, code: str, message: str) -> FactResolution:
    return FactResolution(
        need=need.model_copy(
            update={
                "status": FactNeedStatus.FAILED,
                "failure_code": code,
                "failure_message": message,
            }
        )
    )


def _source_record(
    *,
    candidate: GroundingCandidate,
    index: int,
    title: str,
    uri: str,
    excerpt: str,
    retrieved_at: datetime,
) -> FactSourceRecord:
    content_hash = sha256(
        f"{uri}\n{title}\n{excerpt}".encode("utf-8")
    ).hexdigest()
    return FactSourceRecord(
        source_id=(
            f"source_web_{candidate.provider_place_id}_"
            f"{index}_{content_hash[:16]}"
        ),
        provider="bounded_web_search",
        uri=uri,
        title=title,
        excerpt=excerpt,
        content_hash=content_hash,
        retrieved_at=retrieved_at,
    )


def _provider_source_record(
    *,
    candidate: GroundingCandidate,
    retrieved_at: datetime,
) -> FactSourceRecord | None:
    details = tuple(
        item
        for item in (
            (
                f"营业状态：{candidate.business_status}"
                if candidate.business_status
                else None
            ),
            (
                f"营业时间：{candidate.opening_hours}"
                if candidate.opening_hours
                else None
            ),
        )
        if item is not None
    )
    if candidate.provider != "amap" or not details:
        return None
    excerpt = "；".join(details)
    uri = f"https://ditu.amap.com/place/{candidate.provider_place_id}"
    content_hash = sha256(
        f"{uri}\n{candidate.name}\n{excerpt}".encode("utf-8")
    ).hexdigest()
    return FactSourceRecord(
        source_id=(
            f"source_amap_{candidate.provider_place_id}_"
            f"{content_hash[:16]}"
        ),
        provider="amap_place_search",
        uri=uri,
        title=f"高德地点详情：{candidate.name}",
        excerpt=excerpt,
        content_hash=content_hash,
        retrieved_at=retrieved_at,
    )
