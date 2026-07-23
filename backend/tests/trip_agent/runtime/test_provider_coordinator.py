from __future__ import annotations

import pytest

from app.core import AppError
from app.trip_agent.adapters.provider_coordinator import (
    ProviderCircuitOpen,
    ProviderRequestCoordinator,
)


@pytest.mark.anyio
async def test_daily_quota_failure_opens_shared_provider_circuit():
    coordinator = ProviderRequestCoordinator(qps=1000, max_concurrency=2)
    calls = 0

    async def exhausted():
        nonlocal calls
        calls += 1
        raise AppError(
            "quota exhausted",
            code="provider_error",
            details={"provider_code": "DAILY_QUERY_OVER_LIMIT"},
        )

    with pytest.raises(AppError):
        await coordinator.call("amap", "search", exhausted)
    with pytest.raises(ProviderCircuitOpen, match="DAILY_QUERY_OVER_LIMIT"):
        await coordinator.call("amap", "search", exhausted)

    assert calls == 1
    assert coordinator.circuit_reason("amap") == "DAILY_QUERY_OVER_LIMIT"


@pytest.mark.anyio
async def test_provider_success_is_not_cached_as_a_failure():
    coordinator = ProviderRequestCoordinator(qps=1000, max_concurrency=2)

    async def success():
        return {"ok": True}

    assert await coordinator.call("amap", "search", success) == {"ok": True}
    assert coordinator.circuit_reason("amap") is None
