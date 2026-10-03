from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import TypeVar

from app.core import AppError


T = TypeVar("T")


class ProviderCircuitOpen(RuntimeError):
    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(f"{provider} circuit open: {reason}")
        self.provider = provider
        self.reason = reason


@dataclass(slots=True)
class ProviderRequestCoordinator:
    """Process-wide rate control and circuit breaking for provider I/O."""

    qps: float = 1.0
    max_concurrency: int = 2
    _next_allowed_at: float = 0.0
    _rate_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _semaphore: asyncio.Semaphore = field(init=False)
    _open_reasons: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    async def call(
        self,
        provider: str,
        operation: str,
        function: Callable[[], Awaitable[T]],
    ) -> T:
        del operation
        if reason := self._open_reasons.get(provider):
            raise ProviderCircuitOpen(provider, reason)
        async with self._semaphore:
            if reason := self._open_reasons.get(provider):
                raise ProviderCircuitOpen(provider, reason)
            async with self._rate_lock:
                now = monotonic()
                delay = max(0.0, self._next_allowed_at - now)
                self._next_allowed_at = (
                    max(now, self._next_allowed_at)
                    + 1 / max(self.qps, 0.1)
                )
            if delay:
                await asyncio.sleep(delay)
            # A concurrent request may have exhausted the provider while this
            # request waited for its rate slot. Do not spend another request.
            if reason := self._open_reasons.get(provider):
                raise ProviderCircuitOpen(provider, reason)
            try:
                return await function()
            except AppError as exc:
                provider_code = str(
                    exc.details.get("provider_code") or ""
                )
                if provider_code == "DAILY_QUERY_OVER_LIMIT" or (
                    exc.code == "missing_configuration"
                ):
                    self._open_reasons[provider] = (
                        provider_code or exc.code
                    )
                raise

    def circuit_reason(self, provider: str) -> str | None:
        return self._open_reasons.get(provider)
