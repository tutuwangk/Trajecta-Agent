from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.core import AppError, MissingConfigurationError


class AmapClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or os.getenv("AMAP_API_KEY")
        self.base_url = (base_url or os.getenv("AMAP_BASE_URL", "https://restapi.amap.com/v3")).rstrip("/")

    def require_configured(self, step: str) -> None:
        if not self.api_key:
            raise MissingConfigurationError("AMAP_API_KEY", step=step)

    def search_poi(self, keyword: str, city: str | None = None) -> list[dict]:
        self.require_configured("ground_pois")
        data = self._get(
            "/place/text",
            {"keywords": keyword, "city": city or "", "citylimit": "true", "offset": 10, "page": 1},
            step="ground_pois",
        )
        return data.get("pois", [])

    def geocode(self, address: str, city: str | None = None) -> dict | None:
        self.require_configured("geocode")
        data = self._get("/geocode/geo", {"address": address, "city": city or ""}, step="geocode")
        geocodes = data.get("geocodes") or []
        return geocodes[0] if geocodes else None

    def walking_direction(self, origin: str, destination: str) -> dict | None:
        self.require_configured("build_route_matrix")
        return self._get("/direction/walking", {"origin": origin, "destination": destination}, step="build_route_matrix")

    def driving_direction(self, origin: str, destination: str) -> dict | None:
        self.require_configured("build_route_matrix")
        return self._get(
            "/direction/driving",
            {"origin": origin, "destination": destination, "extensions": "base"},
            step="build_route_matrix",
        )

    def transit_direction(self, origin: str, destination: str, city: str) -> dict | None:
        self.require_configured("build_route_matrix")
        return self._get(
            "/direction/transit/integrated",
            {"origin": origin, "destination": destination, "city": city},
            step="build_route_matrix",
        )

    def _get(self, path: str, params: dict[str, Any], step: str) -> dict:
        for attempt in range(3):
            try:
                response = httpx.get(
                    f"{self.base_url}{path}",
                    params={**params, "key": self.api_key, "output": "json"},
                    timeout=30,
                )
            except httpx.HTTPError as exc:
                raise AppError("高德 API 网络请求失败。", code="amap_network_error", step=step) from exc
            if response.status_code >= 400:
                raise AppError("高德 API 请求失败，请检查网络或服务状态。", code="amap_http_error", step=step)
            data = response.json()
            if data.get("status") == "1":
                return data
            info = data.get("info") or "未知错误"
            if info == "CUQPS_HAS_EXCEEDED_THE_LIMIT" and attempt < 2:
                time.sleep(1.0)
                continue
            raise AppError(f"高德 API 返回错误：{info}", code="amap_api_error", step=step)
        raise AppError("高德 API 返回错误：未知错误", code="amap_api_error", step=step)


def default_amap_client() -> AmapClient:
    return AmapClient()
