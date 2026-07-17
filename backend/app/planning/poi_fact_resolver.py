from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from app.domain.facts import FactGap, FactResolutionBatch, FactSource, POIAvailabilityFact
from app.domain.planning import PlannerFactRequest, PlanningContext
from app.services.web_search import WebSearchClient, WebSearchResult, default_web_search_client


class POIFactResolver:
    """Resolve only the facts the PlannerAgent says would change its decision."""

    def __init__(self, search_client: WebSearchClient, llm_client, *, max_requests: int = 3):
        self.search_client = search_client
        self.llm_client = llm_client
        self.max_requests = max(1, max_requests)

    def resolve(self, requests: list[PlannerFactRequest], context: PlanningContext) -> FactResolutionBatch:
        allowed = context.allowed_poi_ids
        pois = {poi.poi_id: poi for poi in context.fact_snapshot.pois}
        facts: list[POIAvailabilityFact] = []
        gaps: list[FactGap] = []
        seen: set[tuple[str, str]] = set()
        for request in requests[: self.max_requests]:
            key = (request.poi_id, request.visit_date.isoformat())
            if key in seen or request.poi_id not in allowed:
                continue
            seen.add(key)
            poi = pois.get(request.poi_id)
            if poi is None:
                gaps.append(_gap(request, "请求的地点不在当前事实快照中。"))
                continue
            amap_open_time = str(poi.metadata.get("open_time") or "").strip()
            results: list[WebSearchResult] = []
            try:
                results = self.search_client.search(
                    f"{poi.standard_name} {poi.city or context.user_profile.destination} "
                    f"{request.visit_date.isoformat()} 营业时间 闭馆 停止入场",
                    max_results=5,
                )
            except Exception:
                results = []
            evidence = []
            if amap_open_time:
                evidence.append({"title": "高德地点营业信息", "url": "", "snippet": amap_open_time, "provider": "amap"})
            evidence.extend(
                {"title": item.title, "url": item.url, "snippet": item.snippet, "provider": _provider_for_result(item)}
                for item in results
            )
            if not evidence:
                gaps.append(_gap(request, "地图和网络搜索均未返回可用营业信息。"))
                continue
            extracted = self._extract(request, poi.standard_name, evidence)
            if not extracted:
                gaps.append(_gap(request, "搜索结果存在，但无法提取适用于该日期的营业事实。"))
                continue
            source_index = _bounded_index(extracted.get("source_index"), len(evidence))
            source_row = evidence[source_index]
            status = str(extracted.get("status") or "unknown")
            status = status if status in {"open", "closed", "unknown"} else "unknown"
            provider = str(source_row.get("provider") or "unknown")
            confidence = "verified" if provider in {"official", "government", "amap"} and status != "unknown" else "estimated" if status != "unknown" else "unavailable"
            facts.append(
                POIAvailabilityFact(
                    poi_id=request.poi_id,
                    visit_date=request.visit_date,
                    status=status,
                    open_intervals=[str(item) for item in extracted.get("open_intervals") or []][:3],
                    last_entry_time=str(extracted.get("last_entry_time") or ""),
                    confidence=confidence,
                    source=FactSource(
                        provider=provider if provider in {"official", "government", "amap", "web_search"} else "unknown",
                        title=str(source_row.get("title") or ""),
                        url=str(source_row.get("url") or ""),
                        fetched_at=datetime.now(timezone.utc),
                    ),
                    evidence_summary=str(extracted.get("evidence_summary") or "")[:300],
                    degradation_reason="" if confidence == "verified" else "营业信息来自非官方网络结果，需要出发前复核。",
                )
            )
        return FactResolutionBatch(availability_facts=facts, gaps=gaps)

    def _extract(self, request: PlannerFactRequest, poi_name: str, evidence: list[dict]) -> dict:
        try:
            payload = self.llm_client.json_chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是受限的 POI 营业事实抽取器，不是路线规划 Agent。"
                            "只能依据给定搜索证据提取指定日期的营业状态；证据不足必须返回 unknown。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"地点：{poi_name}\n日期：{request.visit_date.isoformat()}\n"
                            f"事实类型：{request.kind}\n证据：{evidence}\n\n"
                            "输出 JSON：{\"status\":\"open|closed|unknown\","
                            "\"open_intervals\":[\"09:00-17:00\"],\"last_entry_time\":\"16:00\","
                            "\"source_index\":0,\"evidence_summary\":\"简短依据\"}"
                        ),
                    },
                ],
                step="extract_poi_availability_fact",
                temperature=0.0,
            )
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}


def apply_fact_resolution(runtime_pois: list[dict], batch: FactResolutionBatch) -> None:
    by_id = {str(poi.get("poi_id") or ""): poi for poi in runtime_pois}
    for fact in batch.availability_facts:
        poi = by_id.get(fact.poi_id)
        if poi is None:
            continue
        existing = [
            item for item in poi.get("availability_facts") or []
            if str(item.get("visit_date") or "") != fact.visit_date.isoformat()
        ]
        existing.append(fact.model_dump(mode="json"))
        poi["availability_facts"] = existing
    for gap in batch.gaps:
        for poi_id in gap.entity_ids:
            poi = by_id.get(poi_id)
            if poi is None:
                continue
            existing = [item for item in poi.get("fact_gaps") or [] if item.get("code") != gap.code]
            existing.append(gap.model_dump(mode="json"))
            poi["fact_gaps"] = existing


def _gap(request: PlannerFactRequest, message: str) -> FactGap:
    return FactGap(
        code="poi_availability_unresolved",
        message=message,
        entity_ids=[request.poi_id],
        blocking=False,
    )


def _provider_for_result(result: WebSearchResult) -> str:
    host = urlparse(result.url).hostname or ""
    host = host.lower()
    if host.endswith(".gov.cn") or host == "gov.cn":
        return "government"
    if "官方" in result.title and not any(token in host for token in ("weibo.com", "douyin.com", "xiaohongshu.com")):
        return "official"
    return "web_search"


def _bounded_index(value, length: int) -> int:
    try:
        index = int(value)
    except (TypeError, ValueError):
        return 0
    return index if 0 <= index < length else 0


def default_poi_fact_resolver(llm_client) -> POIFactResolver:
    return POIFactResolver(default_web_search_client(), llm_client)
