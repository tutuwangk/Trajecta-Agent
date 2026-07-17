from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from app.domain.common import DomainModel
from app.domain.validation import ValidationReport


ResultStatus = Literal["verified", "degraded", "failed"]


class ReleaseDecision(DomainModel):
    status: ResultStatus
    reasons: list[str] = Field(default_factory=list)
    blocking_issue_codes: list[str] = Field(default_factory=list)
    degradation_reasons: list[str] = Field(default_factory=list)
    user_actions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_details(self) -> "ReleaseDecision":
        if self.status == "failed" and not self.blocking_issue_codes:
            raise ValueError("failed release decision must include blocking_issue_codes")
        if self.status == "degraded" and not self.degradation_reasons:
            raise ValueError("degraded release decision must include degradation_reasons")
        if self.status == "verified" and (self.blocking_issue_codes or self.degradation_reasons):
            raise ValueError("verified result cannot include blockers or degradation reasons")
        return self


class TransportLeg(DomainModel):
    mode: str = "unknown"
    duration_min: int | None = Field(default=None, ge=0)
    distance_m: int | None = Field(default=None, ge=0)
    amap_navigation_link: str = ""


class ItineraryItem(DomainModel):
    poi_id: str = Field(min_length=1)
    name: str = ""
    time_block: str = ""
    selected_branch_id: str | None = None
    scheduled_role: str = "anchor_visit"
    burden_role: str = "normal_load"
    trim_priority: str = "trim_first"
    arrival_time: str = ""
    duration_min: int = Field(default=0, ge=0)
    reason: str = ""
    meal_roles: list[Literal["breakfast", "lunch", "dinner"]] = Field(default_factory=list)
    transport_to_next: TransportLeg | None = None
    risk_notes: list[str] = Field(default_factory=list)
    amap_link: str = ""
    quick_stop_total_cost_min: int | None = Field(default=None, ge=0)
    preferred_time_windows: list[str] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)


class ItinerarySegment(DomainModel):
    kind: Literal["outing", "hotel_rest"]
    segment_time: str = ""
    poi_ids: list[str] = Field(default_factory=list)
    rest_until: str = ""
    duration_min: int | None = Field(default=None, ge=0)
    reason: str = ""


class ItineraryMealSlot(DomainModel):
    slot: Literal["breakfast", "lunch", "dinner"]
    requirement: Literal["required", "optional"] = "required"
    source: Literal["poi", "inside_poi", "fallback_nearby"]
    poi_id: str = ""
    within_poi_id: str = ""


class MealBreak(DomainModel):
    label: str = ""
    slot: Literal["breakfast", "lunch", "dinner"] | None = None
    start_time: str = ""
    duration_min: int = Field(default=0, ge=0)
    duration_minutes: int | None = Field(default=None, ge=0)
    poi_id: str = ""
    within_poi_id: str = ""
    included_in_item_duration: bool = False
    source: str = "fallback_nearby"


class HotelRestBreak(DomainModel):
    after_poi_id: str = ""
    before_poi_id: str = ""
    duration_min: int = Field(default=0, ge=0)
    reason: str = ""
    return_to_hotel_transport_min: int | None = Field(default=None, ge=0)
    depart_from_hotel_transport_min: int | None = Field(default=None, ge=0)
    return_to_hotel_transport_source: str = ""
    depart_from_hotel_transport_source: str = ""
    return_to_hotel_transport_degradation_reason: str = ""
    depart_from_hotel_transport_degradation_reason: str = ""
    dynamic: bool = False
    target_start_min: int | None = Field(default=None, ge=0)
    hotel_arrival_time: str = ""
    rest_end_time: str = ""
    next_departure_time: str = ""


class RemovedPOI(DomainModel):
    poi_id: str = ""
    name: str = ""
    reason_codes: list[str] = Field(default_factory=list)
    reason: str = ""


class NamedReason(DomainModel):
    name: str
    reason: str = ""


class RouteSummary(DomainModel):
    main_message: str = ""
    scheduled_places_count: int = Field(default=0, ge=0)
    unscheduled_places_count: int = Field(default=0, ge=0)
    attention_required_count: int = Field(default=0, ge=0)


class CompiledDay(DomainModel):
    day: int = Field(ge=1)
    theme: str = ""
    day_strategy: str = "continuous"
    strategy_reason: str = ""
    night_anchor_poi_id: str = ""
    night_anchor_period: str = ""
    early_anchor_poi_id: str = ""
    summary: str = ""
    meal_slots: list[ItineraryMealSlot] = Field(default_factory=list)
    selected_branch_ids: dict[str, str] = Field(default_factory=dict)
    scheduled_roles: dict[str, str] = Field(default_factory=dict)
    segments: list[ItinerarySegment] = Field(default_factory=list)
    items: list[ItineraryItem] = Field(default_factory=list)
    removed_pois: list[RemovedPOI] = Field(default_factory=list)
    alternatives: list[Any] = Field(default_factory=list)
    meal_breaks: list[MealBreak] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    hotel_rest_breaks: list[HotelRestBreak] = Field(default_factory=list)
    total_outing_min: int | None = Field(default=None, ge=0)
    intensity_outing_min: int | None = Field(default=None, ge=0)
    total_transfer_min: int | None = Field(default=None, ge=0)
    total_outing_minutes: int | None = Field(default=None, ge=0)
    outing_duration_min: int | None = Field(default=None, ge=0)
    outing_duration_minutes: int | None = Field(default=None, ge=0)
    hotel_departure_transport_min: int | None = Field(default=None, ge=0)
    hotel_return_transport_min: int | None = Field(default=None, ge=0)
    hotel_to_first_transport_min: int | None = Field(default=None, ge=0)
    last_to_hotel_transport_min: int | None = Field(default=None, ge=0)
    hotel_departure_transport_source: str = ""
    hotel_return_transport_source: str = ""
    hotel_departure_transport_degradation_reason: str = ""
    hotel_return_transport_degradation_reason: str = ""


class CompiledItinerary(DomainModel):
    destination: str
    days: list[CompiledDay]
    global_risks: list[str] = Field(default_factory=list)
    uncertain_pois: list[dict[str, Any]] = Field(default_factory=list)
    revision_notes: list[str] = Field(default_factory=list)
    route_summary: RouteSummary | None = None
    unscheduled_places: list[NamedReason] = Field(default_factory=list)
    attention_places: list[NamedReason] = Field(default_factory=list)


class FinalItinerary(CompiledItinerary):
    result_status: ResultStatus
    release_decision: ReleaseDecision
    verification: ValidationReport | None = None
    fact_version: str = ""
