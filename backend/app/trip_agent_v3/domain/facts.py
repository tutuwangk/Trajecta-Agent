from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from app.trip_agent_v3.domain.common import DomainModel
from app.trip_agent_v3.domain.requirements import TravelMode


class FactNeedKind(StrEnum):
    PLACE_OPERATION = "place_operation"
    ROUTE = "route"


class FactNeedStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FactNeed(DomainModel):
    need_id: str = Field(min_length=1, max_length=200)
    kind: FactNeedKind
    day_number: int = Field(ge=1)
    candidate_ids: tuple[str, ...] = Field(min_length=1, max_length=2)
    stop_ids: tuple[str, ...] = Field(min_length=1, max_length=2)
    requested_mode: TravelMode | None = None
    visit_at: datetime | None = None
    status: FactNeedStatus = FactNeedStatus.PENDING
    failure_code: str | None = Field(default=None, min_length=1, max_length=100)
    failure_message: str | None = Field(
        default=None, min_length=1, max_length=2_000
    )

    @model_validator(mode="after")
    def validate_status_payload(self) -> "FactNeed":
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("fact-need candidate ids must be unique")
        if (
            self.kind is FactNeedKind.ROUTE
            and len(self.candidate_ids) != 2
        ):
            raise ValueError("route fact need requires two candidate ids")
        if (
            self.kind is not FactNeedKind.ROUTE
            and self.requested_mode is not None
        ):
            raise ValueError(
                "requested_mode is only valid for route fact needs"
            )
        if self.kind is FactNeedKind.PLACE_OPERATION:
            if len(self.candidate_ids) != 1 or len(self.stop_ids) != 1:
                raise ValueError(
                    "place-operation fact need requires one candidate and stop"
                )
            if self.visit_at is None:
                raise ValueError(
                    "place-operation fact need requires visit_at"
                )
        elif self.visit_at is not None:
            raise ValueError(
                "visit_at is only valid for place-operation fact needs"
            )
        if self.status is FactNeedStatus.FAILED:
            if not self.failure_code or not self.failure_message:
                raise ValueError(
                    "failed fact need requires failure code and message"
                )
        elif self.failure_code is not None or self.failure_message is not None:
            raise ValueError(
                "only a failed fact need may carry failure information"
            )
        return self


class FactNeedPlan(DomainModel):
    plan_id: str = Field(min_length=1, max_length=200)
    draft_id: str = Field(min_length=1, max_length=200)
    draft_revision: int = Field(ge=1)
    needs: tuple[FactNeed, ...] = ()

    @model_validator(mode="after")
    def validate_need_ids(self) -> "FactNeedPlan":
        need_ids = [item.need_id for item in self.needs]
        if len(need_ids) != len(set(need_ids)):
            raise ValueError("fact need ids must be unique")
        return self


class FactGap(DomainModel):
    need_id: str = Field(min_length=1, max_length=200)
    kind: FactNeedKind
    day_number: int = Field(ge=1)
    stop_ids: tuple[str, ...] = Field(min_length=1, max_length=2)
    place_names: tuple[str, ...] = Field(min_length=1, max_length=2)
    failure_code: str = Field(min_length=1, max_length=100)
    failure_message: str = Field(min_length=1, max_length=2_000)
    still_scheduled: bool
    impact: str = Field(min_length=1, max_length=2_000)


class FactGapReport(DomainModel):
    fact_need_plan_id: str = Field(min_length=1, max_length=200)
    gaps: tuple[FactGap, ...] = ()


class FactResolutionStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    ESTIMATED = "estimated"
    FAILED = "failed"


class RouteFactSource(StrEnum):
    AMAP = "amap"
    OTHER_PROVIDER = "other_provider"
    SPATIAL_ESTIMATE = "spatial_estimate"
    SAME_PLACE = "same_place"


class RouteFact(DomainModel):
    fact_id: str = Field(min_length=1, max_length=200)
    origin_candidate_id: str = Field(min_length=1, max_length=200)
    destination_candidate_id: str = Field(min_length=1, max_length=200)
    origin_stop_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    destination_stop_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    day_number: int | None = Field(default=None, ge=1)
    duration_min: int | None = Field(default=None, ge=0, le=1_440)
    mode: str | None = Field(default=None, min_length=1, max_length=100)
    source: RouteFactSource
    status: FactResolutionStatus
    failure_code: str | None = Field(default=None, min_length=1, max_length=100)
    failure_message: str | None = Field(
        default=None, min_length=1, max_length=2_000
    )

    @model_validator(mode="after")
    def validate_fact(self) -> "RouteFact":
        ownership = (
            self.origin_stop_id,
            self.destination_stop_id,
            self.day_number,
        )
        if any(value is not None for value in ownership) and not all(
            value is not None for value in ownership
        ):
            raise ValueError(
                "route stop ownership fields must be provided together"
            )
        if (
            self.source is RouteFactSource.SPATIAL_ESTIMATE
            and self.status is FactResolutionStatus.VERIFIED
        ):
            raise ValueError("spatial estimate cannot be a verified route fact")
        if self.status in {
            FactResolutionStatus.VERIFIED,
            FactResolutionStatus.ESTIMATED,
        }:
            if self.duration_min is None or self.mode is None:
                raise ValueError("usable route fact requires duration and mode")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError(
                    "usable route fact cannot carry failure information"
                )
        elif self.status is FactResolutionStatus.FAILED:
            if not self.failure_code or not self.failure_message:
                raise ValueError(
                    "failed route fact requires failure code and message"
                )
            if self.duration_min is not None or self.mode is not None:
                raise ValueError("failed route fact cannot carry route values")
        return self


class FactSourceRecord(DomainModel):
    source_id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=100)
    uri: str = Field(min_length=1, max_length=2_000)
    title: str = Field(min_length=1, max_length=1_000)
    excerpt: str = Field(min_length=1, max_length=4_000)
    content_hash: str = Field(min_length=64, max_length=64)
    retrieved_at: datetime


class OperationalClaim(DomainModel):
    field: Literal[
        "opening_hours", "closure", "last_entry", "reservation"
    ]
    value: str = Field(min_length=1, max_length=2_000)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=5)
    confidence: float = Field(ge=0, le=1)


class OperationalFact(DomainModel):
    fact_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(min_length=1, max_length=200)
    stop_id: str = Field(min_length=1, max_length=200)
    visit_at: datetime
    visit_compatible: Literal[True] = True
    claims: tuple[OperationalClaim, ...] = Field(min_length=1, max_length=10)
    sources: tuple[FactSourceRecord, ...] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_claim_sources(self) -> "OperationalFact":
        source_ids = {source.source_id for source in self.sources}
        referenced = {
            source_id
            for claim in self.claims
            for source_id in claim.source_ids
        }
        if not referenced <= source_ids:
            raise ValueError(
                "operational claim references an unknown source record"
            )
        return self


class FactResolution(DomainModel):
    need: FactNeed
    route_fact: RouteFact | None = None
    operational_fact: OperationalFact | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> "FactResolution":
        if self.need.status is FactNeedStatus.PENDING:
            raise ValueError("fact resolution cannot remain pending")
        if self.need.kind is FactNeedKind.ROUTE:
            if (
                self.need.status is FactNeedStatus.SUCCEEDED
                and self.route_fact is None
            ):
                raise ValueError(
                    "successful route need requires a route fact"
                )
            if (
                self.need.status is FactNeedStatus.FAILED
                and (
                    self.route_fact is not None
                    or self.operational_fact is not None
                )
            ):
                raise ValueError(
                    "failed route need cannot carry a route fact"
                )
            if self.operational_fact is not None:
                raise ValueError(
                    "route resolution cannot carry an operational fact"
                )
        elif self.need.kind is FactNeedKind.PLACE_OPERATION:
            if self.route_fact is not None:
                raise ValueError(
                    "place-operation resolution cannot carry a route fact"
                )
            if (
                self.need.status is FactNeedStatus.SUCCEEDED
                and self.operational_fact is None
            ):
                raise ValueError(
                    "successful place-operation need requires an operational fact"
                )
            if (
                self.need.status is FactNeedStatus.FAILED
                and self.operational_fact is not None
            ):
                raise ValueError(
                    "failed place-operation need cannot carry a fact"
                )
        return self
