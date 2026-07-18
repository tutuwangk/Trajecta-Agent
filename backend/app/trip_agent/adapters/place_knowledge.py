from __future__ import annotations

import asyncio
from datetime import date
from hashlib import sha256
import json
from math import asin, cos, radians, sin, sqrt
from typing import Literal
from uuid import uuid4

from pydantic import Field
from pydantic_ai import Agent, UnexpectedModelBehavior, UsageLimits

from app.services.amap_client import AmapClient
from app.services.web_search import WebSearchClient
from app.trip_agent.adapters.provider import build_deepseek_v4_model, deepseek_v4_settings
from app.trip_agent.domain import (
    DomainModel,
    EstimateClaim,
    GeoPoint,
    ObservedClaim,
    PlaceCandidate,
    PlaceHypothesis,
    SourceRecord,
    TextSpan,
    utc_now,
)
from app.trip_agent.toolsets import CandidateAdvice, CandidateSearch, FactAcquisition


class MentionSuggestion(DomainModel):
    raw_name: str = Field(min_length=1, max_length=500)
    possible_category: str | None = Field(default=None, max_length=100)
    role: Literal["visit", "lodging", "meal", "destination_context", "reference"] = "visit"
    polarity: Literal["requested", "excluded", "neutral"] = "requested"
    route_relevant: bool = True
    brand_only: bool = False
    branch_unspecified: bool = False


class MentionExtraction(DomainModel):
    mentions: tuple[MentionSuggestion, ...] = ()


class ExtractedFact(DomainModel):
    field: Literal["opening_hours", "closure", "last_entry", "reservation"]
    value: str = Field(min_length=1, max_length=4_000)
    source_indexes: tuple[int, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    release_eligible: bool = False


class ExtractedFacts(DomainModel):
    facts: tuple[ExtractedFact, ...] = ()


class VisitProfile(DomainModel):
    minimum_minutes: int = Field(ge=15, le=720)
    recommended_minutes: int = Field(ge=15, le=720)
    extended_minutes: int = Field(ge=15, le=960)
    preferred_period: Literal["morning", "afternoon", "evening", "any"] = "any"
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=1_000)


class CandidateComparisonOutput(DomainModel):
    status: Literal["resolve", "ambiguous", "search_more"]
    recommended_candidate_id: str | None = Field(default=None, max_length=200)
    evidence: tuple[str, ...] = Field(default=(), max_length=5)
    confidence: float = Field(ge=0, le=1)


class DeepSeekAmapPlaceKnowledge:
    """V2-only external knowledge adapter; it never resolves a place for the root Agent."""

    def __init__(
        self,
        *,
        city: str | None,
        amap: AmapClient | None = None,
        web: WebSearchClient | None = None,
    ) -> None:
        self.city = city
        self.amap = amap or AmapClient()
        self.web = web or WebSearchClient()

    def _lightweight_model(self):
        return build_deepseek_v4_model("lightweight")

    async def analyze_mentions(self, raw_request: str) -> tuple[PlaceHypothesis, ...]:
        agent = Agent(
            self._lightweight_model(),
            output_type=MentionExtraction,
            model_settings=deepseek_v4_settings("lightweight"),
            instructions=(
                "Extract every explicit place, hotel, restaurant, mall, venue, district, or activity name. "
                "Keep the exact substring from the user text. Classify each mention as visit, lodging, meal, "
                "destination_context, or reference; record whether it is requested, excluded, or neutral, and "
                "whether it can affect the route. A destination city used only as context is not route-relevant. "
                "Do not resolve identity and do not invent names."
            ),
            retries=2,
        )
        result = await agent.run(raw_request, usage_limits=UsageLimits(request_limit=3))
        hypotheses: list[PlaceHypothesis] = []
        seen: set[tuple[str, int]] = set()
        for mention in result.output.mentions:
            start = raw_request.find(mention.raw_name)
            if start < 0 or (mention.raw_name, start) in seen:
                continue
            seen.add((mention.raw_name, start))
            stable = sha256(f"{mention.raw_name}:{start}".encode()).hexdigest()[:16]
            hypotheses.append(
                PlaceHypothesis(
                    hypothesis_id=f"hypothesis-{stable}",
                    raw_name=mention.raw_name,
                    context=raw_request,
                    spans=(TextSpan(start=start, end=start + len(mention.raw_name)),),
                    possible_category=mention.possible_category,
                    role=mention.role,
                    polarity=mention.polarity,
                    route_relevant=mention.route_relevant,
                    brand_only=mention.brand_only,
                    branch_unspecified=mention.branch_unspecified,
                )
            )
        return tuple(hypotheses)

    async def search_candidates(self, hypothesis: PlaceHypothesis) -> CandidateSearch:
        raw_results = await asyncio.to_thread(self.amap.search_poi, hypothesis.raw_name, self.city)
        sources: list[SourceRecord] = []
        candidates: list[PlaceCandidate] = []
        now = utc_now()
        for raw in raw_results:
            provider_id = str(raw.get("id") or "").strip()
            name = str(raw.get("name") or "").strip()
            if not provider_id or not name:
                continue
            payload = _json_value(raw)
            content_hash = sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            source_id = f"source-amap-{provider_id}-{content_hash[:12]}"
            sources.append(
                SourceRecord(
                    source_record_id=source_id,
                    source_type="amap",
                    provider="amap-place-text-v3",
                    payload=payload,
                    content_hash=content_hash,
                    retrieved_at=now,
                )
            )
            location = _location(raw.get("location"))
            candidates.append(
                PlaceCandidate(
                    candidate_id=(
                        "candidate-amap-"
                        + sha256(f"{hypothesis.hypothesis_id}:{provider_id}".encode()).hexdigest()[:24]
                    ),
                    hypothesis_id=hypothesis.hypothesis_id,
                    provider="amap",
                    provider_place_id=provider_id,
                    name=name,
                    address=_optional_text(raw.get("address")),
                    city=_optional_text(raw.get("cityname")) or self.city,
                    category=_optional_text(raw.get("type")),
                    location=location,
                    parent_provider_place_id=_optional_text(raw.get("parent")),
                    source_record_id=source_id,
                )
            )
        return CandidateSearch(sources=tuple(sources), candidates=tuple(candidates))

    async def compare_candidates(
        self, hypothesis: PlaceHypothesis, candidates: tuple[PlaceCandidate, ...]
    ) -> CandidateAdvice:
        if (hypothesis.brand_only or hypothesis.branch_unspecified) and len(candidates) > 1:
            return CandidateAdvice(
                status="ambiguous",
                evidence=("The user did not identify a branch.",),
                confidence=1,
            )
        agent = Agent(
            self._lightweight_model(),
            output_type=CandidateComparisonOutput,
            model_settings=deepseek_v4_settings("lightweight"),
            instructions=(
                "Compare place identity only. Preserve explicit branch, venue, and district qualifiers. "
                "A parent place must not be replaced by a child merchant or sub-attraction. "
                "Return ambiguous when evidence cannot safely distinguish candidates. Use only candidate_id values provided."
            ),
            retries=2,
        )
        result = await agent.run(
            json.dumps(
                {
                    "mention": hypothesis.raw_name,
                    "context": hypothesis.context,
                    "candidates": [
                        {
                            "candidate_id": item.candidate_id,
                            "provider_place_id": item.provider_place_id,
                            "name": item.name,
                            "address": item.address,
                            "category": item.category,
                            "parent_provider_place_id": item.parent_provider_place_id,
                        }
                        for item in candidates
                    ],
                },
                ensure_ascii=False,
            ),
            usage_limits=UsageLimits(request_limit=4),
        )
        output = result.output
        ids = {item.candidate_id for item in candidates}
        if output.status == "resolve" and output.recommended_candidate_id not in ids:
            return CandidateAdvice(
                status="search_more",
                evidence=("The suggested candidate ID was not in the searched set.",),
                confidence=0,
            )
        if output.status != "resolve":
            output = output.model_copy(update={"recommended_candidate_id": None})
        if output.status == "resolve" and output.recommended_candidate_id:
            chosen = next(item for item in candidates if item.candidate_id == output.recommended_candidate_id)
            parent = next(
                (
                    item
                    for item in candidates
                    if item.provider_place_id == chosen.parent_provider_place_id
                ),
                None,
            )
            if parent is not None and _identity_text(hypothesis.raw_name) in _identity_text(parent.name):
                output = output.model_copy(update={"recommended_candidate_id": parent.candidate_id})
        return CandidateAdvice.model_validate(output.model_dump())

    async def acquire_place_facts(
        self, candidate: PlaceCandidate, applicable_date: date | None
    ) -> FactAcquisition:
        query = f"{candidate.name} 营业时间 闭馆 停止入场 预约"
        if applicable_date:
            query += f" {applicable_date.isoformat()}"
        results = await asyncio.to_thread(self.web.search, query, max_results=5)
        now = utc_now()
        sources = tuple(
            SourceRecord(
                source_record_id=f"source-web-{uuid4()}",
                source_type="web",
                provider="bounded-web-search",
                uri=result.url,
                excerpt=f"{result.title}\n{result.snippet}"[:20_000],
                content_hash=sha256(f"{result.url}\n{result.snippet}".encode()).hexdigest(),
                retrieved_at=now,
            )
            for result in results
        )
        if not sources:
            return FactAcquisition(sources=(), claims=())
        evidence = "\n\n".join(
            f"[{index}] {source.uri}\n{source.excerpt}" for index, source in enumerate(sources)
        )
        agent = Agent(
            self._lightweight_model(),
            output_type=ExtractedFacts,
            model_settings=deepseek_v4_settings("lightweight"),
            instructions=(
                "Extract only facts directly supported by the numbered snippets. Cite source indexes. "
                "If a snippet is ambiguous, promotional, or date-inapplicable, omit the fact. "
                "Set release_eligible true only for an explicit, date-applicable operational fact."
            ),
            retries=2,
        )
        try:
            result = await agent.run(
                f"Candidate: {candidate.name}\nApplicable date: {applicable_date}\nSources:\n{evidence}",
                usage_limits=UsageLimits(request_limit=4),
            )
        except UnexpectedModelBehavior:
            return FactAcquisition(sources=sources, claims=())
        claims = []
        for fact in result.output.facts:
            indexes = tuple(sorted(set(fact.source_indexes)))
            if any(index < 0 or index >= len(sources) for index in indexes):
                continue
            claims.append(
                ObservedClaim(
                    claim_id=f"claim-{uuid4()}",
                    entity_id=candidate.candidate_id,
                    field=fact.field,
                    value=fact.value,
                    applicable_date=applicable_date,
                    applicable_place_id=candidate.candidate_id,
                    source_record_ids=tuple(sources[index].source_record_id for index in indexes),
                    extractor="deepseek-v4-flash-fact-extractor",
                    extractor_version="1",
                    acquired_at=now,
                    confidence=fact.confidence,
                    release_eligible=fact.release_eligible,
                )
            )
        return FactAcquisition(sources=sources, claims=tuple(claims))

    async def acquire_route_facts(
        self, origin: PlaceCandidate, destination: PlaceCandidate, mode: str
    ) -> FactAcquisition:
        if origin.location is None or destination.location is None:
            return FactAcquisition(sources=(), claims=())
        origin_coord = f"{origin.location.lng},{origin.location.lat}"
        destination_coord = f"{destination.location.lng},{destination.location.lat}"
        if mode == "walking":
            raw = await asyncio.to_thread(self.amap.walking_direction, origin_coord, destination_coord)
        elif mode in {"driving", "taxi"}:
            raw = await asyncio.to_thread(self.amap.driving_direction, origin_coord, destination_coord)
        elif mode in {"transit", "public_transport", "subway"}:
            route_city = origin.city or destination.city or self.city
            raw = (
                await asyncio.to_thread(
                    self.amap.transit_direction,
                    origin_coord,
                    destination_coord,
                    route_city,
                )
                if route_city
                else None
            )
        else:
            raise ValueError(f"unsupported travel mode: {mode}")
        now = utc_now()
        if raw:
            duration_min, distance_m = _route_metrics(raw)
            if duration_min is not None:
                payload = _json_value(raw)
                source = SourceRecord(
                    source_record_id=f"source-route-{uuid4()}",
                    source_type="amap",
                    provider=f"amap-direction-{mode}",
                    payload=payload,
                    content_hash=sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                    retrieved_at=now,
                )
                entity_id = f"route:{origin.candidate_id}:{destination.candidate_id}:{mode}"
                claims = (
                    ObservedClaim(
                        claim_id=f"claim-{uuid4()}",
                        entity_id=entity_id,
                        field="duration_min",
                        value=duration_min,
                        source_record_ids=(source.source_record_id,),
                        extractor="amap-route-parser",
                        extractor_version="1",
                        acquired_at=now,
                        confidence=1,
                        release_eligible=True,
                    ),
                    ObservedClaim(
                        claim_id=f"claim-{uuid4()}",
                        entity_id=entity_id,
                        field="distance_m",
                        value=distance_m or 0,
                        source_record_ids=(source.source_record_id,),
                        extractor="amap-route-parser",
                        extractor_version="1",
                        acquired_at=now,
                        confidence=1,
                        release_eligible=True,
                    ),
                )
                return FactAcquisition(sources=(source,), claims=claims)
        distance_m = round(_haversine_m(origin.location, destination.location))
        duration_min = max(1, round(distance_m / (75 if mode == "walking" else 350)))
        source = SourceRecord(
            source_record_id=f"source-spatial-{uuid4()}",
            source_type="system",
            provider="deterministic-spatial-estimate",
            payload={"origin": origin_coord, "destination": destination_coord, "mode": mode},
            content_hash=sha256(f"{origin_coord}:{destination_coord}:{mode}".encode()).hexdigest(),
            retrieved_at=now,
        )
        claim = EstimateClaim(
            claim_id=f"claim-{uuid4()}",
            entity_id=f"route:{origin.candidate_id}:{destination.candidate_id}:{mode}",
            field="duration_min",
            value=duration_min,
            source_record_ids=(source.source_record_id,),
            extractor="deterministic-spatial-estimate",
            extractor_version="1",
            acquired_at=now,
            confidence=0.4,
            release_eligible=False,
            method="spatial_estimate",
        )
        return FactAcquisition(sources=(source,), claims=(claim,))

    async def estimate_visit_profile(self, candidate: PlaceCandidate) -> FactAcquisition:
        agent = Agent(
            self._lightweight_model(),
            output_type=VisitProfile,
            model_settings=deepseek_v4_settings("lightweight"),
            instructions=(
                "Estimate a conservative visit-duration range from the place identity and category. "
                "This is advice, never a verified operational fact."
            ),
            retries=2,
        )
        try:
            result = await agent.run(
                f"Name: {candidate.name}\nCategory: {candidate.category}\nCity: {candidate.city}",
                usage_limits=UsageLimits(request_limit=4),
            )
        except UnexpectedModelBehavior:
            return FactAcquisition(sources=(), claims=())
        profile = result.output
        now = utc_now()
        source = SourceRecord(
            source_record_id=f"source-visit-profile-{uuid4()}",
            source_type="system",
            provider="deepseek-v4-flash-visit-profile",
            payload=profile.model_dump(mode="json"),
            content_hash=sha256(profile.model_dump_json().encode()).hexdigest(),
            retrieved_at=now,
        )
        claims = tuple(
            EstimateClaim(
                claim_id=f"claim-{uuid4()}",
                entity_id=candidate.candidate_id,
                field=field,
                value=value,
                applicable_place_id=candidate.candidate_id,
                source_record_ids=(source.source_record_id,),
                extractor="deepseek-v4-flash-visit-profile",
                extractor_version="1",
                acquired_at=now,
                confidence=profile.confidence,
                release_eligible=False,
                method="model_visit_profile",
            )
            for field, value in (
                ("minimum_visit_minutes", profile.minimum_minutes),
                ("recommended_visit_minutes", profile.recommended_minutes),
                ("extended_visit_minutes", profile.extended_minutes),
                ("preferred_period", profile.preferred_period),
            )
        )
        return FactAcquisition(sources=(source,), claims=claims)


def _optional_text(value: object) -> str | None:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value if item)
    text = str(value or "").strip()
    return text or None


def _identity_text(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _location(value: object) -> GeoPoint | None:
    try:
        lng, lat = str(value).split(",", 1)
        return GeoPoint(lng=float(lng), lat=float(lat))
    except (TypeError, ValueError):
        return None


def _json_value(value: object):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _route_metrics(raw: dict) -> tuple[int | None, int | None]:
    route = raw.get("route", {})
    paths = route.get("paths") or route.get("transits") or []
    path = paths[0] if paths else {}
    try:
        duration_min = max(1, round(float(path.get("duration")) / 60))
    except (TypeError, ValueError):
        duration_min = None
    try:
        distance_m = round(float(path.get("distance") or path.get("walking_distance")))
    except (TypeError, ValueError):
        distance_m = None
    return duration_min, distance_m


def _haversine_m(origin: GeoPoint, destination: GeoPoint) -> float:
    earth_radius_m = 6_371_000
    lat1, lat2 = radians(origin.lat), radians(destination.lat)
    delta_lat = radians(destination.lat - origin.lat)
    delta_lng = radians(destination.lng - origin.lng)
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lng / 2) ** 2
    return 2 * earth_radius_m * asin(sqrt(value))
