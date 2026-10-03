from __future__ import annotations

from typing import Mapping, Protocol, Sequence, TYPE_CHECKING

from app.trip_agent_v3.domain.execution import JourneyGoal
from app.trip_agent_v3.domain.facts import FactNeed, FactResolution
from app.trip_agent_v3.domain.grounding import GroundingCandidate
from app.trip_agent_v3.domain.query import QueryTarget
from app.trip_agent_v3.domain.sources import (
    RequirementProposal,
    SourceDocument,
)

if TYPE_CHECKING:
    from app.trip_agent_v3.autonomous_runtime import AutonomousTripRuntime


class RequirementInterpreterPort(Protocol):
    async def interpret(
        self,
        *,
        goal: JourneyGoal,
        sources: tuple[SourceDocument, ...],
    ) -> RequirementProposal: ...


class PlaceSearchProviderPort(Protocol):
    async def search(
        self, target: QueryTarget
    ) -> Sequence[Mapping[str, object]]: ...


class FactProviderPort(Protocol):
    async def resolve(
        self,
        *,
        need: FactNeed,
        candidates: Mapping[str, GroundingCandidate],
    ) -> FactResolution: ...


class RootTripPlannerAgentPort(Protocol):
    async def run(self, runtime: "AutonomousTripRuntime") -> None: ...
