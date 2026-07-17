from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.adapters.legacy import plan_blueprint_from_legacy, validation_report_from_legacy
from app.agents.itinerary_normalizer import normalize_itinerary
from app.agents.planner import materialize_itinerary_from_skeleton
from app.agents.verifier import verify_itinerary
from app.domain.itinerary import CompiledItinerary
from app.domain.planning import PlanBlueprint, PlanningContext
from app.domain.validation import ValidationReport
from app.planning.segment_boundaries import hotel_rest_boundary_pairs


PrepareItinerary = Callable[[dict], None]


@dataclass(frozen=True)
class CompilationResult:
    itinerary: CompiledItinerary
    validation_report: ValidationReport
    legacy_verification: dict


class FeasibilityCompiler:
    """Deterministically compiles an Agent blueprint into a timed itinerary."""

    def compile(
        self,
        *,
        context: PlanningContext,
        blueprint: PlanBlueprint | dict,
        legacy_context: dict,
        runtime_pois: list[dict],
        route_matrix: list[dict],
        prepare_itinerary: PrepareItinerary | None = None,
        repair_attempt: int = 0,
    ) -> CompilationResult:
        typed_blueprint = blueprint if isinstance(blueprint, PlanBlueprint) else plan_blueprint_from_legacy(blueprint)
        typed_blueprint.validate_against(context)
        itinerary = materialize_itinerary_from_skeleton(legacy_context, typed_blueprint.model_dump(mode="json"))
        if prepare_itinerary:
            prepare_itinerary(itinerary)
        else:
            _sync_transport_edges(itinerary, route_matrix)
            normalize_itinerary(itinerary, context.user_profile.model_dump(mode="json"), runtime_pois, route_matrix)
        typed_itinerary = CompiledItinerary.model_validate(itinerary)
        legacy_itinerary = typed_itinerary.model_dump(mode="json", exclude_none=True)
        verification = verify_itinerary(
            legacy_itinerary,
            context.user_profile.model_dump(mode="json"),
            route_matrix,
            runtime_pois,
            time_constraints=[item.model_dump(mode="json") for item in context.time_constraints],
            order_constraints=[item.model_dump(mode="json") for item in context.order_constraints],
            intent_ledger=context.intent_ledger.model_dump(mode="json"),
        )
        report = validation_report_from_legacy(
            verification,
            fact_version=context.fact_snapshot.version,
            repair_attempt=repair_attempt,
        )
        return CompilationResult(
            itinerary=typed_itinerary,
            validation_report=report,
            legacy_verification=verification,
        )


def _sync_transport_edges(itinerary: dict, route_matrix: list[dict]) -> None:
    route_by_pair = {(edge.get("origin_poi_id"), edge.get("destination_poi_id")): edge for edge in route_matrix}
    for day in itinerary.get("days") or []:
        items = day.get("items") or []
        rest_boundaries = hotel_rest_boundary_pairs(day)
        for index, item in enumerate(items):
            if index >= len(items) - 1:
                item.pop("transport_to_next", None)
                continue
            destination = items[index + 1]
            pair = (str(item.get("poi_id") or ""), str(destination.get("poi_id") or ""))
            if pair in rest_boundaries:
                item.pop("transport_to_next", None)
                continue
            edge = route_by_pair.get(pair)
            if not edge:
                item.pop("transport_to_next", None)
                continue
            item["transport_to_next"] = {
                "mode": edge.get("mode", "unknown"),
                "duration_min": edge.get("duration_min"),
                "distance_m": edge.get("distance_m"),
            }
