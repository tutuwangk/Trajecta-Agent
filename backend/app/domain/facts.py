from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import Field, model_validator

from app.domain.common import DomainModel


FactConfidence = Literal["verified", "estimated", "unavailable"]
RouteSource = Literal["amap_direction_api", "route_cache", "spatial_estimate", "unknown"]
AvailabilityStatus = Literal["open", "closed", "unknown"]


class Coordinates(DomainModel):
    lng: float
    lat: float


class GroundedPOI(DomainModel):
    poi_id: str = Field(min_length=1)
    standard_name: str = Field(min_length=1)
    raw_name: str = ""
    amap_id: str = ""
    address: str = ""
    city: str = ""
    district: str = ""
    category: str = "unknown"
    location: Coordinates | None = None
    match_status: str = "matched"
    final_decision: str = "include"
    user_override: str = "none"
    estimated_duration_min: int = Field(default=90, ge=0)
    confidence: FactConfidence = "verified"
    source: str = "amap"
    unavailable_reason: str = ""
    experience_tags: list[str] = Field(default_factory=list)
    planning_semantics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HotelAnchor(DomainModel):
    poi_id: str = "hotel_anchor"
    standard_name: str = Field(min_length=1)
    amap_id: str = ""
    city: str = ""
    district: str = ""
    address: str = ""
    location: Coordinates | None = None
    confidence: FactConfidence = "verified"
    match_status: str = "matched"
    match_confidence: float = Field(default=1.0, ge=0, le=1)
    source: str = "amap"
    unavailable_reason: str = ""


class RouteEdge(DomainModel):
    origin_poi_id: str = Field(min_length=1)
    destination_poi_id: str = Field(min_length=1)
    mode: str = "unknown"
    duration_min: int | None = Field(default=None, ge=0)
    distance_m: int | None = Field(default=None, ge=0)
    relation: str = "unknown"
    source: RouteSource = "unknown"
    confidence: FactConfidence = "unavailable"
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    degradation_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_fact_state(self) -> "RouteEdge":
        if self.confidence == "verified" and self.duration_min is None:
            raise ValueError("verified route edge must include duration_min")
        if self.source == "spatial_estimate" and self.confidence == "verified":
            raise ValueError("spatial estimate cannot be marked verified")
        if self.confidence == "estimated" and not self.degradation_reason:
            raise ValueError("estimated route edge must explain degradation_reason")
        return self


class FactSource(DomainModel):
    provider: Literal["official", "government", "amap", "web_search", "unknown"] = "unknown"
    title: str = ""
    url: str = ""
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class POIAvailabilityFact(DomainModel):
    poi_id: str = Field(min_length=1)
    visit_date: date
    status: AvailabilityStatus = "unknown"
    open_intervals: list[str] = Field(default_factory=list)
    last_entry_time: str = ""
    confidence: FactConfidence = "unavailable"
    source: FactSource = Field(default_factory=FactSource)
    evidence_summary: str = ""
    degradation_reason: str = ""

    @model_validator(mode="after")
    def validate_availability(self) -> "POIAvailabilityFact":
        if self.status in {"open", "closed"} and self.confidence == "unavailable":
            raise ValueError("known availability status requires usable evidence")
        if self.confidence == "estimated" and not self.degradation_reason:
            raise ValueError("estimated availability must explain degradation_reason")
        return self


class FactGap(DomainModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    entity_ids: list[str] = Field(default_factory=list)
    blocking: bool = False


class FactResolutionBatch(DomainModel):
    availability_facts: list[POIAvailabilityFact] = Field(default_factory=list)
    gaps: list[FactGap] = Field(default_factory=list)


class FactSnapshot(DomainModel):
    version: str = Field(min_length=1)
    destination: str = ""
    pois: list[GroundedPOI] = Field(default_factory=list)
    hotel_anchor: HotelAnchor | None = None
    route_edges: list[RouteEdge] = Field(default_factory=list)
    availability_facts: list[POIAvailabilityFact] = Field(default_factory=list)
    gaps: list[FactGap] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def poi_ids(self) -> set[str]:
        return {poi.poi_id for poi in self.pois}

    @property
    def blocking_gaps(self) -> list[FactGap]:
        return [gap for gap in self.gaps if gap.blocking]

    @property
    def degraded(self) -> bool:
        return (
            bool(self.gaps)
            or any(edge.confidence != "verified" for edge in self.route_edges)
            or any(fact.confidence != "verified" for fact in self.availability_facts)
        )
