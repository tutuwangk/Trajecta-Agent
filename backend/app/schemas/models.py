from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TravelerProfile(BaseModel):
    count: int = 1
    type: str = "未说明"


class PreferenceWeights(BaseModel):
    food: int = 3
    photo: int = 3
    citywalk: int = 3
    shopping: int = 3
    history: int = 3
    relaxation: int = 3


class TravelConstraints(BaseModel):
    avoid_too_tired: bool = False
    physical_intensity: Literal["high", "medium", "low"] = "medium"
    queue_tolerance: str = "medium"
    must_visit: list[str] = Field(default_factory=list)
    avoid_visit: list[str] = Field(default_factory=list)
    diet_restrictions: list[str] = Field(default_factory=list)
    weather_condition: str | None = None


class UserProfile(BaseModel):
    destination: str = ""
    days: int = 1
    nights: int = 0
    start_date: str | None = None
    hotel_name: str | None = None
    hotel_area: str | None = None
    start_point: str | None = None
    end_point: str | None = None
    travelers: TravelerProfile = Field(default_factory=TravelerProfile)
    budget_level: Literal["low", "medium", "high"] = "medium"
    transport_preference: list[str] = Field(default_factory=lambda: ["walking", "taxi", "public_transport"])
    route_goal: str = "balanced"
    preferences: PreferenceWeights = Field(default_factory=PreferenceWeights)
    constraints: TravelConstraints = Field(default_factory=TravelConstraints)


class SessionCreate(BaseModel):
    raw_input: str
    notes: str = ""
    user_profile: dict[str, Any] | None = None


class PoiDecision(BaseModel):
    poi_id: str
    decision: Literal[
        "keep",
        "delete",
        "must_visit",
        "optional",
        "must_include",
        "remove",
        "rename_confirm",
        "arrange_nearby",
        "none",
    ]
    manual_name: str | None = None


class PoiDecisionUpdate(BaseModel):
    decisions: list[PoiDecision]


class RevisionRequest(BaseModel):
    instruction: str
    quick_action: str | None = None
