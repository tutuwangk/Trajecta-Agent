from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TextSpan(DomainModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> "TextSpan":
        if self.end <= self.start:
            raise ValueError("text span end must be greater than start")
        return self


class GeoPoint(DomainModel):
    lng: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
