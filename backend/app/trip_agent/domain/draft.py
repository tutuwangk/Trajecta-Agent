from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import Field, model_validator

from app.trip_agent.domain.common import DomainModel


class DraftVisit(DomainModel):
    visit_id: str = Field(min_length=1, max_length=200)
    place_candidate_id: str = Field(min_length=1, max_length=200)
    duration_min: int = Field(ge=15, le=1_440)
    earliest_start: time | None = None
    latest_end: time | None = None
    fixed_start: time | None = None
    travel_mode_from_previous: str = Field(default="walking", min_length=1, max_length=50)
    optional: bool = False

    @model_validator(mode="after")
    def validate_window(self) -> "DraftVisit":
        if self.earliest_start and self.latest_end and self.latest_end <= self.earliest_start:
            raise ValueError("latest_end must be later than earliest_start")
        return self


class DraftMeal(DomainModel):
    meal_id: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=50)
    duration_min: int = Field(ge=15, le=240)
    after_visit_id: str | None = Field(default=None, max_length=200)
    place_candidate_id: str | None = Field(default=None, max_length=200)
    travel_mode_from_previous: str = Field(default="walking", min_length=1, max_length=50)
    earliest_start: time | None = None
    latest_end: time | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "DraftMeal":
        if self.earliest_start and self.latest_end and self.latest_end <= self.earliest_start:
            raise ValueError("meal latest_end must be later than earliest_start")
        return self


class DraftDay(DomainModel):
    day_index: int = Field(ge=1, le=60)
    date: date
    day_purpose: Literal["touring", "arrival", "departure", "rest"] = "touring"
    visits: tuple[DraftVisit, ...] = ()
    meals: tuple[DraftMeal, ...] = ()
    meal_strategy: str | None = Field(default=None, max_length=1_000)
    hotel_candidate_id: str | None = Field(default=None, max_length=200)
    depart_hotel_mode: str = Field(default="walking", min_length=1, max_length=50)
    return_to_hotel: bool = False
    return_hotel_mode: str = Field(default="walking", min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_meal_positions(self) -> "DraftDay":
        visit_ids = {visit.visit_id for visit in self.visits}
        meal_ids = [meal.meal_id for meal in self.meals]
        if len(meal_ids) != len(set(meal_ids)):
            raise ValueError("meal ids must be unique within a day")
        unknown = {meal.after_visit_id for meal in self.meals if meal.after_visit_id} - visit_ids
        if unknown:
            raise ValueError("meal after_visit_id must reference a visit in the same day")
        if self.return_to_hotel and not self.hotel_candidate_id:
            raise ValueError("return_to_hotel requires hotel_candidate_id")
        return self


class DraftSnapshot(DomainModel):
    draft_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    workspace_version: int = Field(ge=1)
    draft_version: int = Field(ge=1)
    days: tuple[DraftDay, ...]

    @model_validator(mode="after")
    def validate_structure(self) -> "DraftSnapshot":
        indexes = [day.day_index for day in self.days]
        if indexes != list(range(1, len(self.days) + 1)):
            raise ValueError("draft days must be contiguous and one-based")
        dates = [day.date for day in self.days]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ValueError("draft dates must be unique and increasing")
        visit_ids = [visit.visit_id for day in self.days for visit in day.visits]
        if len(visit_ids) != len(set(visit_ids)):
            raise ValueError("visit ids must be unique across the draft")
        place_ids = [visit.place_candidate_id for day in self.days for visit in day.visits] + [
            meal.place_candidate_id
            for day in self.days
            for meal in day.meals
            if meal.place_candidate_id
        ]
        if len(place_ids) != len(set(place_ids)):
            raise ValueError("a place candidate may appear only once as a visit or anchored meal")
        return self
