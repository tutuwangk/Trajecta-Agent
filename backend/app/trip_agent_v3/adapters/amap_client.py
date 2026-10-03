from __future__ import annotations

import os
import random
import time
from typing import Any

import httpx

from app.core import AppError, MissingConfigurationError


class AmapClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("AMAP_API_KEY")
        self.base_url = (
            base_url
            or os.getenv("AMAP_BASE_URL", "https://restapi.amap.com/v3")
        ).rstrip("/")

    def require_configured(self, step: str) -> None:
        if not self.api_key:
            raise MissingConfigurationError("AMAP_API_KEY", step=step)

    def search_poi(
        self,
        keyword: str,
        city: str | None = None,
    ) -> list[dict[str, Any]]:
        self.require_configured("ground_pois")
        data = self._get(
            "/place/text",
            {
                "keywords": keyword,
                "city": city or "",
                "citylimit": "true",
                "offset": 10,
                "page": 1,
                "extensions": "all",
            },
            step="ground_pois",
        )
        return list(data.get("pois", []))

    def walking_direction(
        self,
        origin: str,
        destination: str,
    ) -> dict[str, Any] | None:
        self.require_configured("build_route_matrix")
        return self._get(
            "/direction/walking",
            {"origin": origin, "destination": destination},
            step="build_route_matrix",
        )

    def driving_direction(
        self,
        origin: str,
        destination: str,
    ) -> dict[str, Any] | None:
        self.require_configured("build_route_matrix")
        return self._get(
            "/direction/driving",
            {
                "origin": origin,
                "destination": destination,
                "extensions": "base",
            },
            step="build_route_matrix",
        )

    def transit_direction(
        self,
        origin: str,
        destination: str,
        city: str,
    ) -> dict[str, Any] | None:
        self.require_configured("build_route_matrix")
        return self._get(
            "/direction/transit/integrated",
            {
                "origin": origin,
                "destination": destination,
                "city": city,
            },
            step="build_route_matrix",
        )

    def _get(
        self,
        path: str,
        params: dict[str, Any],
        step: str,
    ) -> dict[str, Any]:
        max_attempts = _positive_int_env("AMAP_MAX_RETRIES", 2) + 1
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = httpx.get(
                    f"{self.base_url}{path}",
                    params={
                        **params,
                        "key": self.api_key,
                        "output": "json",
                    },
                    timeout=30,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < max_attempts - 1:
                    time.sleep(_retry_delay_seconds(attempt))
                    continue
                raise AppError(
                    "高德 API 网络请求失败，已达到重试上限。",
                    code="amap_network_error",
                    step=step,
                    details={"request_attempts": attempt + 1},
                ) from exc
            if response.status_code >= 400:
                if (
                    response.status_code
                    in {408, 425, 429, 500, 502, 503, 504}
                    and attempt < max_attempts - 1
                ):
                    time.sleep(_retry_after_or_delay(response, attempt))
                    continue
                raise AppError(
                    "高德 API 请求失败，请检查网络或服务状态。",
                    code="amap_http_error",
                    step=step,
                    details={
                        "http_status": response.status_code,
                        "request_attempts": attempt + 1,
                    },
                )
            try:
                data = response.json()
            except ValueError as exc:
                last_error = exc
                if attempt < max_attempts - 1:
                    time.sleep(_retry_delay_seconds(attempt))
                    continue
                raise AppError(
                    "高德 API 返回了无法解析的数据。",
                    code="amap_invalid_response",
                    step=step,
                    details={"request_attempts": attempt + 1},
                ) from exc
            if data.get("status") == "1":
                return dict(data)
            info = str(data.get("info") or "未知错误")
            if (
                info in {"CUQPS_HAS_EXCEEDED_THE_LIMIT", "UNKNOWN_ERROR"}
                and attempt < min(1, max_attempts - 1)
            ):
                time.sleep(_retry_delay_seconds(attempt))
                continue
            raise AppError(
                f"高德 API 返回错误：{info}",
                code="amap_api_error",
                step=step,
                details={
                    "request_attempts": attempt + 1,
                    "provider_code": info,
                    "retryable": info
                    in {"CUQPS_HAS_EXCEEDED_THE_LIMIT", "UNKNOWN_ERROR"},
                },
            )
        raise AppError(
            "高德 API 返回错误：未知错误",
            code="amap_api_error",
            step=step,
            details={"request_attempts": max_attempts},
        ) from last_error


def _retry_after_or_delay(
    response: httpx.Response,
    attempt: int,
) -> float:
    retry_after = response.headers.get("Retry-After")
    try:
        parsed = float(retry_after) if retry_after else 0
    except ValueError:
        parsed = 0
    return (
        min(30.0, parsed)
        if parsed > 0
        else _retry_delay_seconds(attempt)
    )


def _retry_delay_seconds(attempt: int) -> float:
    return min(8.0, 2**attempt) + random.uniform(0, 0.25)


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value >= 0 else default
