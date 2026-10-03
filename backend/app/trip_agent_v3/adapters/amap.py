from __future__ import annotations

import asyncio
from math import asin, cos, radians, sin, sqrt
from typing import Mapping, Sequence

from app.trip_agent_v3.adapters.amap_client import AmapClient
from app.trip_agent_v3.adapters.provider_coordinator import (
    ProviderRequestCoordinator,
)
from app.trip_agent_v3.domain.facts import (
    FactNeed,
    FactNeedKind,
    FactNeedStatus,
    FactResolution,
    FactResolutionStatus,
    RouteFact,
    RouteFactSource,
)
from app.trip_agent_v3.domain.grounding import GroundingCandidate
from app.trip_agent_v3.domain.query import QueryTarget
from app.trip_agent_v3.domain.requirements import TravelMode


class AmapPlaceSearchProvider:
    def __init__(
        self,
        *,
        amap: AmapClient | None = None,
        coordinator: ProviderRequestCoordinator | None = None,
    ) -> None:
        self.amap = amap or AmapClient()
        self.coordinator = coordinator or ProviderRequestCoordinator()

    async def search(
        self, target: QueryTarget
    ) -> Sequence[Mapping[str, object]]:
        results = await self.coordinator.call(
            "amap",
            "v3_search_poi",
            lambda: asyncio.to_thread(
                self.amap.search_poi,
                target.query_text,
                target.destination,
            ),
        )
        return tuple(results)


class AmapFactProvider:
    def __init__(
        self,
        *,
        city: str,
        amap: AmapClient | None = None,
        coordinator: ProviderRequestCoordinator | None = None,
    ) -> None:
        self.city = city
        self.amap = amap or AmapClient()
        self.coordinator = coordinator or ProviderRequestCoordinator()

    async def resolve(
        self,
        *,
        need: FactNeed,
        candidates: Mapping[str, GroundingCandidate],
    ) -> FactResolution:
        try:
            selected = tuple(candidates[item] for item in need.candidate_ids)
        except KeyError as exc:
            return FactResolution(
                need=need.model_copy(
                    update={
                        "status": FactNeedStatus.FAILED,
                        "failure_code": "scheduled_candidate_missing",
                        "failure_message": (
                            f"事实请求引用了不存在的已选地点 {exc.args[0]}。"
                        ),
                    }
                )
            )
        if need.kind is FactNeedKind.PLACE_OPERATION:
            return FactResolution(
                need=need.model_copy(
                    update={
                        "status": FactNeedStatus.FAILED,
                        "failure_code": "operational_provider_required",
                        "failure_message": (
                            f"{selected[0].name} 在计划到访时段的营业、闭馆、"
                            "停止入场或预约事实需要有来源的网络查询；"
                            "高德路线接口不能代替该核验。"
                        ),
                    }
                )
            )

        origin, destination = selected
        distance_m = _haversine_m(
            origin.latitude,
            origin.longitude,
            destination.latitude,
            destination.longitude,
        )
        requested_mode = need.requested_mode
        mode = (
            requested_mode.value
            if requested_mode is not None
            else ("walk" if distance_m <= 1_500 else "taxi")
        )
        origin_coord = f"{origin.longitude},{origin.latitude}"
        destination_coord = (
            f"{destination.longitude},{destination.latitude}"
        )
        try:
            if mode == TravelMode.WALK.value:
                raw = await self.coordinator.call(
                    "amap",
                    "v3_walking_direction",
                    lambda: asyncio.to_thread(
                        self.amap.walking_direction,
                        origin_coord,
                        destination_coord,
                    ),
                )
            elif mode == TravelMode.TRANSIT.value:
                raw = await self.coordinator.call(
                    "amap",
                    "v3_transit_direction",
                    lambda: asyncio.to_thread(
                        self.amap.transit_direction,
                        origin_coord,
                        destination_coord,
                        self.city,
                    ),
                )
            else:
                raw = await self.coordinator.call(
                    "amap",
                    "v3_driving_direction",
                    lambda: asyncio.to_thread(
                        self.amap.driving_direction,
                        origin_coord,
                        destination_coord,
                    ),
                )
        except Exception as exc:
            return FactResolution(
                need=need.model_copy(
                    update={
                        "status": FactNeedStatus.FAILED,
                        "failure_code": type(exc).__name__.lower(),
                        "failure_message": (
                            f"{origin.name} 到 {destination.name} 的高德路线查询失败。"
                        ),
                    }
                )
            )
        duration_min = _route_duration_minutes(raw)
        if duration_min is None:
            return FactResolution(
                need=need.model_copy(
                    update={
                        "status": FactNeedStatus.FAILED,
                        "failure_code": "route_duration_missing",
                        "failure_message": (
                            f"高德未返回 {origin.name} 到 {destination.name} "
                            "的可用交通时长。"
                        ),
                    }
                )
            )
        return FactResolution(
            need=need.model_copy(
                update={"status": FactNeedStatus.SUCCEEDED}
            ),
            route_fact=RouteFact(
                fact_id=f"route_{need.need_id}",
                origin_candidate_id=origin.candidate_id,
                destination_candidate_id=destination.candidate_id,
                origin_stop_id=need.stop_ids[0],
                destination_stop_id=need.stop_ids[1],
                day_number=need.day_number,
                duration_min=duration_min,
                mode=mode,
                source=RouteFactSource.AMAP,
                status=FactResolutionStatus.VERIFIED,
            ),
        )


def _route_duration_minutes(raw: object) -> int | None:
    if not isinstance(raw, Mapping):
        return None
    route = raw.get("route")
    if not isinstance(route, Mapping):
        return None
    paths = route.get("paths") or route.get("transits") or ()
    if not isinstance(paths, Sequence) or not paths:
        return None
    first = paths[0]
    if not isinstance(first, Mapping):
        return None
    try:
        return max(1, round(float(first.get("duration")) / 60))
    except (TypeError, ValueError):
        return None


def _haversine_m(
    origin_lat: float,
    origin_lng: float,
    destination_lat: float,
    destination_lng: float,
) -> float:
    earth_radius_m = 6_371_000
    lat1 = radians(origin_lat)
    lat2 = radians(destination_lat)
    delta_lat = radians(destination_lat - origin_lat)
    delta_lng = radians(destination_lng - origin_lng)
    value = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lng / 2) ** 2
    )
    return 2 * earth_radius_m * asin(sqrt(value))
