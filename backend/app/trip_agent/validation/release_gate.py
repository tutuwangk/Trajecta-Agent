from __future__ import annotations

from app.trip_agent.domain import FactStatus, TripWorkspace
from app.trip_agent.validation.compiler import SimulationReport


class ReleaseGate:
    def decide_fact_status(
        self, workspace: TripWorkspace, simulation: SimulationReport
    ) -> FactStatus:
        if not simulation.ok:
            return FactStatus.FAILED
        if simulation.fact_issue_codes:
            return FactStatus.DEGRADED
        return FactStatus.VERIFIED

    def blocking_issue_codes(self, simulation: SimulationReport) -> tuple[str, ...]:
        return tuple(sorted({issue.code for issue in simulation.issues if issue.blocking}))
