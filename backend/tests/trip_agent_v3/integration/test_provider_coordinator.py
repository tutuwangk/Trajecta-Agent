from __future__ import annotations

import asyncio

import pytest

from app.core import AppError
from app.trip_agent_v3.adapters.provider_coordinator import (
    ProviderCircuitOpen,
    ProviderRequestCoordinator,
)


@pytest.mark.anyio
async def test_waiting_request_does_not_call_provider_after_quota_circuit_opens():
    coordinator = ProviderRequestCoordinator(qps=1000, max_concurrency=1)
    entered = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def exhaust_quota():
        calls.append("first")
        entered.set()
        await finish.wait()
        raise AppError("quota exhausted", details={"provider_code": "DAILY_QUERY_OVER_LIMIT"})

    async def should_not_spend_request():
        calls.append("queued")

    first = asyncio.create_task(coordinator.call("amap", "search", exhaust_quota))
    await entered.wait()
    queued = asyncio.create_task(coordinator.call("amap", "search", should_not_spend_request))
    await asyncio.sleep(0)
    finish.set()
    results = await asyncio.gather(first, queued, return_exceptions=True)

    assert isinstance(results[0], AppError)
    assert isinstance(results[1], ProviderCircuitOpen)
    assert calls == ["first"]


@pytest.mark.anyio
async def test_request_waiting_for_rate_slot_rechecks_provider_circuit(monkeypatch):
    coordinator = ProviderRequestCoordinator(qps=1, max_concurrency=2)
    entered = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def wait_for_quota_failure(delay):
        entered.set()
        await finish.wait()
        coordinator._open_reasons["amap"] = "DAILY_QUERY_OVER_LIMIT"

    monkeypatch.setattr("app.trip_agent_v3.adapters.provider_coordinator.asyncio.sleep", wait_for_quota_failure)
    coordinator._next_allowed_at = float("inf")

    async def should_not_spend_request():
        calls.append("provider")

    queued = asyncio.create_task(coordinator.call("amap", "search", should_not_spend_request))
    await entered.wait()
    finish.set()
    with pytest.raises(ProviderCircuitOpen):
        await queued
    assert calls == []
