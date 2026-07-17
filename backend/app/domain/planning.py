from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from app.domain.common import DomainModel
from app.domain.facts import FactSnapshot
from app.domain.validation import ValidationReport
from app.schemas.models import UserProfile


DayStrategy = Literal["continuous", "late_start", "split_day"]
SegmentKind = Literal["outing", "hotel_rest"]
SegmentTime = Literal["", "morning", "midday", "afternoon", "evening", "night"]
ScheduledRole = Literal["quick_stop", "meal_stop", "anchor_visit", "filler_visit", "nightlife_stop"]
MealSource = Literal["poi", "inside_poi", "fallback_nearby"]
MealType = Literal["breakfast", "lunch", "dinner"]


class PlanningCandidate(DomainModel):
    poi_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    district: str = ""
    category: str = "unknown"
    priority: Literal["must", "preferred", "optional"] = "preferred"
    role: str = "visit"
    experience_type: str = "daytime_visit"
    duration_min: int = Field(default=90, ge=0)
    meal_capability: str = "none"
    time_windows: list[str] = Field(default_factory=list)
    planning_function: str = "anchor"
    planning_notes: str = ""
    quick_stop_eligible: bool = False


class OrderConstraint(DomainModel):
    before: str
    after: str
    before_poi_id: str = ""
    after_poi_id: str = ""
    strength: str = "strong_preference"
    source: str = "user_text"


class TimeConstraint(DomainModel):
    poi_id: str
    name: str = ""
    preferred_window: str = ""
    fixed_time: str = ""
    appointment_time: str = ""
    objective_deadline: str = ""
    strength: str = "quasi_hard"
    source_text: str = ""


class PlanningPreferences(DomainModel):
    must_places: str = "keep_must_places"
    time_preferences: str = "keep_time_preferences"
    order_preferences: str = "keep_order_preferences"
    pace: str = "relax_pace"
    meal_arrangement: str = "use_nearby_meal"


class PlanningContext(DomainModel):
    user_profile: UserProfile
    fact_snapshot: FactSnapshot
    candidates: list[PlanningCandidate]
    day_budget_min: int = Field(ge=0)
    order_constraints: list[OrderConstraint] = Field(default_factory=list)
    time_constraints: list[TimeConstraint] = Field(default_factory=list)
    planning_preferences: PlanningPreferences = Field(default_factory=PlanningPreferences)
    previous_blueprint: "PlanBlueprint | None" = None
    validation_report: ValidationReport | None = None
    history: list[str] = Field(default_factory=list)
    trip_dates: list[date] = Field(default_factory=list)
    user_request: str = ""

    @property
    def allowed_poi_ids(self) -> set[str]:
        return {candidate.poi_id for candidate in self.candidates}


class BlueprintSegment(DomainModel):
    kind: SegmentKind
    segment_time: SegmentTime = ""
    poi_ids: list[str] = Field(default_factory=list)
    rest_until: Literal["", "evening_departure"] = ""
    reason: str = ""

    @model_validator(mode="after")
    def validate_segment(self) -> "BlueprintSegment":
        if self.kind == "outing" and not self.poi_ids:
            raise ValueError("outing segment must include at least one poi_id")
        if self.kind == "hotel_rest" and self.poi_ids:
            raise ValueError("hotel_rest segment cannot include poi_ids")
        if self.kind == "hotel_rest" and self.rest_until != "evening_departure":
            raise ValueError("hotel_rest must use rest_until=evening_departure")
        if self.kind == "outing" and self.rest_until:
            raise ValueError("outing segment cannot define rest_until")
        return self


class MealSlot(DomainModel):
    slot: MealType
    requirement: Literal["required", "optional"] = "required"
    source: MealSource
    poi_id: str = ""
    within_poi_id: str = ""

    @model_validator(mode="after")
    def validate_source(self) -> "MealSlot":
        if self.source == "poi" and not self.poi_id:
            raise ValueError("poi meal source requires poi_id")
        if self.source == "inside_poi" and not self.within_poi_id:
            raise ValueError("inside_poi meal source requires within_poi_id")
        if self.source != "poi" and self.poi_id:
            raise ValueError("non-poi meal source cannot include poi_id")
        if self.source != "inside_poi" and self.within_poi_id:
            raise ValueError("non-inside meal source cannot include within_poi_id")
        return self


class BlueprintDay(DomainModel):
    day: int = Field(ge=1)
    theme_hint: str = ""
    day_strategy: DayStrategy = "continuous"
    strategy_reason: str = ""
    night_anchor_poi_id: str = ""
    night_anchor_period: SegmentTime = ""
    early_anchor_poi_id: str = ""
    segments: list[BlueprintSegment] = Field(default_factory=list)
    poi_ids: list[str] = Field(default_factory=list)
    selected_branch_ids: dict[str, str] = Field(default_factory=dict)
    scheduled_roles: dict[str, ScheduledRole] = Field(default_factory=dict)
    meal_slots: list[MealSlot] = Field(default_factory=list)
    unscheduled_poi_ids: list[str] = Field(default_factory=list)
    drop_reason_codes: dict[str, list[str]] = Field(default_factory=dict)
    risk_tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_day(self) -> "BlueprintDay":
        segment_ids = [poi_id for segment in self.segments if segment.kind == "outing" for poi_id in segment.poi_ids]
        if self.segments and segment_ids != self.poi_ids:
            raise ValueError("segments outing poi_ids must exactly match day poi_ids")
        if len(set(self.poi_ids)) != len(self.poi_ids):
            raise ValueError("a day cannot schedule the same poi more than once")
        if set(self.poi_ids).intersection(self.unscheduled_poi_ids):
            raise ValueError("a poi cannot be both scheduled and unscheduled in the same day")
        if not set(self.scheduled_roles).issubset(self.poi_ids):
            raise ValueError("scheduled_roles can only reference scheduled poi_ids")
        if not set(self.selected_branch_ids).issubset(self.poi_ids):
            raise ValueError("selected_branch_ids can only reference scheduled poi_ids")
        hotel_rest_indexes = [index for index, segment in enumerate(self.segments) if segment.kind == "hotel_rest"]
        if len(hotel_rest_indexes) > 1:
            raise ValueError("a day can contain at most one hotel_rest segment")
        for index in hotel_rest_indexes:
            if index == 0 or index == len(self.segments) - 1:
                raise ValueError("hotel_rest must be between two outing segments")
            previous_segment = self.segments[index - 1]
            next_segment = self.segments[index + 1]
            if previous_segment.kind != "outing" or next_segment.kind != "outing":
                raise ValueError("hotel_rest must be directly between outing segments")
            if next_segment.segment_time not in {"evening", "night"}:
                raise ValueError("hotel_rest is only valid before an evening or night outing")
        return self


class UnscheduledPOI(DomainModel):
    poi_id: str = Field(min_length=1)
    reason_codes: list[str] = Field(default_factory=list)


class PlanBlueprint(DomainModel):
    destination: str = ""
    days: list[BlueprintDay]
    unscheduled: list[UnscheduledPOI] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_internal_consistency(self) -> "PlanBlueprint":
        day_numbers = [day.day for day in self.days]
        if day_numbers != list(range(1, len(self.days) + 1)):
            raise ValueError("day numbers must be continuous and ordered from 1")
        scheduled = [poi_id for day in self.days for poi_id in day.poi_ids]
        if len(set(scheduled)) != len(scheduled):
            raise ValueError("a poi cannot be scheduled on multiple days")
        unscheduled = [item.poi_id for item in self.unscheduled]
        if len(set(unscheduled)) != len(unscheduled):
            raise ValueError("unscheduled poi_ids must be unique")
        if set(scheduled).intersection(unscheduled):
            raise ValueError("a poi cannot be both scheduled and globally unscheduled")
        return self

    def validate_against(self, context: PlanningContext) -> None:
        if len(self.days) != context.user_profile.days:
            raise ValueError(f"blueprint must contain exactly {context.user_profile.days} days")
        referenced = {
            poi_id
            for day in self.days
            for poi_id in [*day.poi_ids, *day.unscheduled_poi_ids]
        } | {item.poi_id for item in self.unscheduled}
        meal_references = {
            poi_id
            for day in self.days
            for slot in day.meal_slots
            for poi_id in [slot.poi_id, slot.within_poi_id]
            if poi_id
        }
        referenced |= meal_references
        unknown = referenced - context.allowed_poi_ids
        if unknown:
            raise ValueError(f"unknown candidate poi_ids: {sorted(unknown)}")
        scheduled = {poi_id for day in self.days for poi_id in day.poi_ids}
        missing = context.allowed_poi_ids - referenced
        if missing:
            raise ValueError(f"candidate poi_ids must be scheduled or unscheduled: {sorted(missing)}")
        must_ids = {candidate.poi_id for candidate in context.candidates if candidate.priority == "must"}
        missing_must = must_ids - scheduled
        if missing_must:
            raise ValueError(f"must poi_ids cannot be unscheduled: {sorted(missing_must)}")
        for day in self.days:
            scheduled = set(day.poi_ids)
            if any(segment.kind == "hotel_rest" for segment in day.segments) and not context.fact_snapshot.hotel_anchor:
                raise ValueError("hotel_rest requires a verified hotel anchor")
            meal_poi_ids = [slot.poi_id for slot in day.meal_slots if slot.source == "poi"]
            if len(meal_poi_ids) != len(set(meal_poi_ids)):
                raise ValueError("one scheduled restaurant cannot serve multiple meal slots")
            for slot in day.meal_slots:
                linked_poi_id = slot.poi_id or slot.within_poi_id
                if linked_poi_id and linked_poi_id not in scheduled:
                    raise ValueError("meal slots can only reference a poi scheduled on the same day")
                if slot.slot == "breakfast" and not context.user_profile.constraints.breakfast_required:
                    raise ValueError("breakfast must not be scheduled unless the user explicitly requested it")


FactRequestKind = Literal["opening_hours", "last_entry_time", "date_availability"]


class PlannerFactRequest(DomainModel):
    request_id: str = Field(min_length=1)
    poi_id: str = Field(min_length=1)
    kind: FactRequestKind
    visit_date: date
    decision_reason: str = Field(min_length=1, max_length=240)


class PlannerTurn(DomainModel):
    action: Literal["need_facts", "propose_blueprint", "revise_blueprint"]
    fact_requests: list[PlannerFactRequest] = Field(default_factory=list, max_length=6)
    blueprint: PlanBlueprint | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> "PlannerTurn":
        if self.action == "need_facts":
            if not self.fact_requests or self.blueprint is not None:
                raise ValueError("need_facts requires fact_requests and no blueprint")
        elif self.blueprint is None or self.fact_requests:
            raise ValueError("blueprint actions require one blueprint and no fact_requests")
        return self


PlanningContext.model_rebuild()
