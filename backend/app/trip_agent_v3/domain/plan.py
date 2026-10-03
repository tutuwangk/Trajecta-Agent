from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from app.trip_agent_v3.domain.common import DomainModel
from app.trip_agent_v3.domain.facts import FactResolutionStatus, RouteFactSource
from app.trip_agent_v3.domain.requirements import TravelMode


class StopKind(StrEnum):
    LODGING = "lodging"
    AIRPORT = "airport"
    VISIT = "visit"
    MEAL = "meal"
    SHOPPING = "shopping"
    PHOTO = "photo"


_ANCHOR_KINDS = {StopKind.LODGING, StopKind.AIRPORT}


class DraftStop(DomainModel):
    stop_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(min_length=1, max_length=200)
    obligation_ids: tuple[str, ...] = Field(default=(), max_length=20)
    name: str = Field(min_length=1, max_length=500)
    kind: StopKind
    stay_duration_min: int = Field(ge=0, le=720)
    travel_mode_from_previous: TravelMode | None = None
    rationale: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_obligation_ids(self) -> "DraftStop":
        if len(self.obligation_ids) != len(set(self.obligation_ids)):
            raise ValueError("stop obligation ids must be unique")
        if self.kind not in _ANCHOR_KINDS and not self.obligation_ids:
            raise ValueError("non-anchor stop requires at least one obligation id")
        return self


class DraftDay(DomainModel):
    day_number: int = Field(ge=1)
    calendar_date: date
    title: str = Field(min_length=1, max_length=500)
    start_time: time
    stops: tuple[DraftStop, ...] = Field(min_length=2, max_length=30)

    @model_validator(mode="after")
    def validate_route_anchors(self) -> "DraftDay":
        stop_ids = [item.stop_id for item in self.stops]
        if len(stop_ids) != len(set(stop_ids)):
            raise ValueError("stop ids must be unique within a day")
        if (
            self.stops[0].kind not in _ANCHOR_KINDS
            or self.stops[-1].kind not in _ANCHOR_KINDS
        ):
            raise ValueError(
                "each day must start and end with a visible lodging or airport anchor"
            )
        return self


class WorkingDraft(DomainModel):
    draft_id: str = Field(min_length=1, max_length=200)
    goal_revision_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    days: tuple[DraftDay, ...] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_days(self) -> "WorkingDraft":
        expected_numbers = list(range(1, len(self.days) + 1))
        actual_numbers = [item.day_number for item in self.days]
        if actual_numbers != expected_numbers:
            raise ValueError("draft day numbers must be contiguous and start at one")
        actual_dates = [item.calendar_date for item in self.days]
        if any(
            (current - previous).days != 1
            for previous, current in zip(actual_dates, actual_dates[1:])
        ):
            raise ValueError("draft calendar dates must be contiguous")
        all_stop_ids = [
            stop.stop_id for day in self.days for stop in day.stops
        ]
        if len(all_stop_ids) != len(set(all_stop_ids)):
            raise ValueError("stop ids must be globally unique in a draft")
        return self


class StopLocation(DomainModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class TimelineStop(DomainModel):
    stop_id: str
    candidate_id: str
    obligation_ids: tuple[str, ...] = ()
    name: str
    kind: StopKind
    arrival_at: datetime
    departure_at: datetime
    stay_duration_min: int = Field(ge=0, le=720)
    location: StopLocation | None = None
    address: str | None = Field(default=None, max_length=1_000)
    coordinate_system: Literal["gcj02"] = "gcj02"

    @model_validator(mode="after")
    def validate_time_arithmetic(self) -> "TimelineStop":
        duration = int(
            (self.departure_at - self.arrival_at).total_seconds() // 60
        )
        if duration != self.stay_duration_min:
            raise ValueError("stop timestamps do not match stay duration")
        return self


class TimelineLeg(DomainModel):
    leg_id: str = Field(min_length=1, max_length=200)
    from_stop_id: str = Field(min_length=1, max_length=200)
    to_stop_id: str = Field(min_length=1, max_length=200)
    departure_at: datetime
    arrival_at: datetime
    duration_min: int = Field(ge=0, le=1_440)
    mode: str = Field(min_length=1, max_length=100)
    fact_id: str = Field(min_length=1, max_length=200)
    fact_source: RouteFactSource
    fact_status: FactResolutionStatus

    @model_validator(mode="after")
    def validate_time_arithmetic(self) -> "TimelineLeg":
        duration = int(
            (self.arrival_at - self.departure_at).total_seconds() // 60
        )
        if duration != self.duration_min:
            raise ValueError("leg timestamps do not match route duration")
        if self.fact_status not in {
            FactResolutionStatus.VERIFIED,
            FactResolutionStatus.ESTIMATED,
        }:
            raise ValueError("timeline leg requires a usable route fact")
        return self


class DayTimeline(DomainModel):
    day_number: int = Field(ge=1)
    calendar_date: date
    title: str = Field(min_length=1, max_length=500)
    stops: tuple[TimelineStop, ...] = Field(min_length=2, max_length=30)
    legs: tuple[TimelineLeg, ...] = Field(min_length=1, max_length=29)

    @model_validator(mode="after")
    def validate_sequence(self) -> "DayTimeline":
        if len(self.legs) != len(self.stops) - 1:
            raise ValueError("timeline requires one leg between every pair of stops")
        for index, leg in enumerate(self.legs):
            origin = self.stops[index]
            destination = self.stops[index + 1]
            if leg.from_stop_id != origin.stop_id:
                raise ValueError("timeline leg origin does not match stop order")
            if leg.to_stop_id != destination.stop_id:
                raise ValueError(
                    "timeline leg destination does not match stop order"
                )
            if leg.departure_at != origin.departure_at:
                raise ValueError("timeline leg must depart when origin stop ends")
            if leg.arrival_at != destination.arrival_at:
                raise ValueError(
                    "timeline leg must arrive when destination stop begins"
                )
        return self


class CompiledTimeline(DomainModel):
    snapshot_id: str = Field(min_length=1, max_length=200)
    draft_id: str = Field(min_length=1, max_length=200)
    draft_revision: int = Field(ge=1)
    days: tuple[DayTimeline, ...] = Field(min_length=1, max_length=30)
