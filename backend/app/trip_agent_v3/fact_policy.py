"""Publication policy for route facts and optional operating information."""

from app.trip_agent_v3.domain.facts import FactGap, FactNeedKind


# Missing public information is an availability limit, not a visit conflict.
# Identity errors and explicitly sourced conflicts still require draft repair.
OPTIONAL_OPERATION_FAILURES = frozenset({
    "operational_sources_missing",
    "operational_evidence_insufficient",
    "operational_extraction_failed",
})


def gap_blocks_delivery(gap: FactGap) -> bool:
    return gap.still_scheduled and (
        gap.kind is FactNeedKind.ROUTE
        or gap.failure_code not in OPTIONAL_OPERATION_FAILURES
    )
