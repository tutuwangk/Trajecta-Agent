from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from pydantic_ai.messages import ModelMessage


@dataclass(frozen=True, slots=True)
class ProviderRunRef:
    business_run_id: str
    provider_run_id: str
    conversation_id: str


class AgentPersistencePort(Protocol):
    """Domain-facing persistence boundary with no Harness types in its API."""

    async def continuable_messages(self, provider_run_id: str) -> Sequence[ModelMessage]: ...

    async def unresolved_effect_ids(self, provider_run_id: str) -> Sequence[str]: ...
