from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from hashlib import sha256
import json
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from pydantic import Field
from pydantic_ai import CallDeferred, FunctionToolset, RunContext

from app.trip_agent.domain import (
    CandidateRejected,
    CandidateSnapshot,
    ClarificationBatch,
    ClarificationQuestion,
    DomainModel,
    DraftDay,
    DraftMeal,
    DraftSnapshot,
    DraftVisit,
    ExperienceStatus,
    FactStatus,
    KnowledgeClaim,
    PlaceCandidate,
    PlaceHypothesis,
    PlaceResolution,
    ReleaseRecord,
    ReleaseNarrative,
    NarrativeDay,
    NarrativeVisit,
    ResolutionStatus,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    TripWorkspace,
    SourceRecord,
    utc_now,
)
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.budget import RuntimeBudget
from app.trip_agent.validation import FeasibilityCompiler, ReleaseGate, SimulationReport


TravelMode = Literal["walking", "driving", "taxi", "transit", "public_transport", "subway"]


class RouteFactRequest(DomainModel):
    origin_candidate_id: str = Field(min_length=1, max_length=200)
    destination_candidate_id: str = Field(min_length=1, max_length=200)
    mode: TravelMode


class PlaceKnowledgePort(Protocol):
    async def analyze_mentions(self, raw_request: str) -> tuple[PlaceHypothesis, ...]: ...

    async def search_candidates(self, hypothesis: PlaceHypothesis) -> "CandidateSearch": ...

    async def compare_candidates(
        self, hypothesis: PlaceHypothesis, candidates: tuple[PlaceCandidate, ...]
    ) -> "CandidateAdvice": ...

    async def acquire_place_facts(
        self, candidate: PlaceCandidate, applicable_date: date | None
    ) -> "FactAcquisition": ...

    async def acquire_route_facts(
        self, origin: PlaceCandidate, destination: PlaceCandidate, mode: str
    ) -> "FactAcquisition": ...

    async def estimate_visit_profile(self, candidate: PlaceCandidate) -> "FactAcquisition": ...


class NarrativeGeneratorPort(Protocol):
    async def generate(
        self,
        release: ReleaseRecord,
        workspace: TripWorkspace,
        simulation: SimulationReport,
    ) -> ReleaseNarrative: ...


@dataclass(frozen=True, slots=True)
class FactAcquisition:
    sources: tuple[SourceRecord, ...]
    claims: tuple[KnowledgeClaim, ...]


@dataclass(frozen=True, slots=True)
class CandidateSearch:
    sources: tuple[SourceRecord, ...]
    candidates: tuple[PlaceCandidate, ...]


class CandidateAdvice(DomainModel):
    status: Literal["resolve", "ambiguous", "search_more"]
    recommended_candidate_id: str | None = Field(default=None, max_length=200)
    evidence: tuple[str, ...] = Field(default=(), max_length=5)
    confidence: float = Field(ge=0, le=1)


@dataclass(slots=True)
class TripAgentDeps:
    repository: SqliteTripAgentRepository
    place_knowledge: PlaceKnowledgePort
    workspace_id: str
    run_id: str
    provider_run_id: str
    events: list[dict[str, object]]
    budget: RuntimeBudget
    narrative_generator: NarrativeGeneratorPort | None = None


class DraftVisitInput(DomainModel):
    place_candidate_id: str = Field(min_length=1, max_length=200)
    duration_min: int = Field(ge=15, le=1_440)
    earliest_start: time | None = None
    latest_end: time | None = None
    fixed_start: time | None = None
    travel_mode_from_previous: TravelMode = "walking"
    optional: bool = False


class DraftMealInput(DomainModel):
    kind: str = Field(min_length=1, max_length=50)
    duration_min: int = Field(ge=15, le=240)
    after_visit_index: int | None = Field(default=None, ge=1)
    earliest_start: time | None = None
    latest_end: time | None = None


class DraftDayInput(DomainModel):
    day_index: int = Field(ge=1, le=60)
    date: date
    visits: tuple[DraftVisitInput, ...] = ()
    meals: tuple[DraftMealInput, ...] = ()
    meal_strategy: str | None = Field(default=None, max_length=1_000)
    hotel_candidate_id: str | None = Field(default=None, max_length=200)
    depart_hotel_mode: TravelMode = "walking"
    return_to_hotel: bool = False
    return_hotel_mode: TravelMode = "walking"


class ClarificationQuestionInput(DomainModel):
    question_id: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4_000)
    reason: str = Field(min_length=1, max_length=2_000)
    options: tuple[str, ...] = Field(default=(), max_length=3)
    allow_other: bool = True


class AllocateDayOperation(DomainModel):
    operation: Literal["allocate_day"]
    candidate_id: str
    day_index: int = Field(ge=1, le=60)
    duration_min: int = Field(default=60, ge=15, le=1_440)
    optional: bool = False


class ReorderClusterOperation(DomainModel):
    operation: Literal["reorder_cluster"]
    day_index: int = Field(ge=1, le=60)
    ordered_candidate_ids: tuple[str, ...] = Field(min_length=1)


class ProtectAnchorOperation(DomainModel):
    operation: Literal["protect_anchor"]
    candidate_id: str
    fixed_start: time


class SetVisitWindowOperation(DomainModel):
    operation: Literal["set_visit_window"]
    candidate_id: str
    earliest_start: time | None = None
    latest_end: time | None = None


class ReduceIntensityOperation(DomainModel):
    operation: Literal["reduce_intensity"]
    day_index: int = Field(ge=1, le=60)
    max_visits: int = Field(ge=1, le=20)


class ReplacePlaceOperation(DomainModel):
    operation: Literal["replace_place"]
    old_candidate_id: str
    new_candidate_id: str


class RemoveOptionalPlaceOperation(DomainModel):
    operation: Literal["remove_optional_place"]
    candidate_id: str


class SetMealStrategyOperation(DomainModel):
    operation: Literal["set_meal_strategy"]
    day_index: int = Field(ge=1, le=60)
    strategy: str = Field(min_length=1, max_length=1_000)


DraftOperation = Annotated[
    AllocateDayOperation
    | ReorderClusterOperation
    | ProtectAnchorOperation
    | SetVisitWindowOperation
    | ReduceIntensityOperation
    | ReplacePlaceOperation
    | RemoveOptionalPlaceOperation
    | SetMealStrategyOperation,
    Field(discriminator="operation"),
]


def _workspace(ctx: RunContext[TripAgentDeps]) -> TripWorkspace:
    run = ctx.deps.repository.get_run(ctx.deps.run_id)
    if run is None:
        raise KeyError(ctx.deps.run_id)
    if run.status in TERMINAL_RUN_STATUSES:
        raise RuntimeError(
            f"run {ctx.deps.run_id} is terminal ({run.status.value})"
        )
    workspace = ctx.deps.repository.get_workspace(ctx.deps.workspace_id)
    if not workspace:
        raise KeyError(ctx.deps.workspace_id)
    return workspace


def _authorize(
    ctx: RunContext[TripAgentDeps], tool_name: str, signature: str
) -> dict[str, object] | None:
    reason = ctx.deps.budget.authorize(tool_name, signature)
    if reason is None:
        return None
    ctx.deps.events.append(
        {"type": "tool_deferred_for_convergence", "tool_name": tool_name, "reason": reason}
    )
    workspace = _workspace(ctx)
    return {
        "ok": False,
        "code": "tool_unavailable_in_convergence",
        "reason": reason,
        "version": workspace.version,
        "next_actions": ["simulate_candidate", "apply_draft_operations", "submit_candidate"],
    }


def _cached_effect(
    ctx: RunContext[TripAgentDeps], tool_name: str
) -> dict[str, object] | None:
    if not ctx.tool_call_id:
        return None
    return ctx.deps.repository.get_tool_effect(ctx.tool_call_id, tool_name=tool_name)


def _remember_effect(
    ctx: RunContext[TripAgentDeps], tool_name: str, result: dict[str, object]
) -> dict[str, object]:
    if not ctx.tool_call_id:
        return result
    return ctx.deps.repository.record_tool_effect(
        tool_call_id=ctx.tool_call_id,
        run_id=ctx.deps.run_id,
        tool_name=tool_name,
        result=result,
    )


def _simulation(
    repository: SqliteTripAgentRepository, workspace: TripWorkspace
) -> dict[str, object]:
    report = FeasibilityCompiler().compile(
        workspace, repository.list_claims(workspace.workspace_id)
    )
    return report.model_dump(mode="json")


def build_planner_toolset() -> FunctionToolset[TripAgentDeps]:
    toolset: FunctionToolset[TripAgentDeps] = FunctionToolset()

    @toolset.tool
    async def read_workspace(ctx: RunContext[TripAgentDeps]) -> dict[str, object]:
        """Read the current goal, versions, places, draft, and run state."""

        workspace = _workspace(ctx)
        if denied := _authorize(ctx, "read_workspace", str(workspace.version)):
            return denied
        run = ctx.deps.repository.get_run(ctx.deps.run_id)
        return {
            "workspace_id": workspace.workspace_id,
            "version": workspace.version,
            "place_set_revision": workspace.place_set_revision,
            "fact_version": workspace.fact_version,
            "goal": workspace.goal_ledger.model_dump(mode="json"),
            "hypotheses": [item.model_dump(mode="json") for item in workspace.place_hypotheses],
            "candidates": [item.model_dump(mode="json") for item in workspace.place_candidates],
            "resolutions": [item.model_dump(mode="json") for item in workspace.place_resolutions],
            "draft": workspace.current_draft.model_dump(mode="json") if workspace.current_draft else None,
            "claims": [
                item.model_dump(mode="json")
                for item in ctx.deps.repository.list_claims(workspace.workspace_id)[-20:]
            ],
            "run_status": run.status.value if run else None,
        }

    @toolset.tool(sequential=True, retries=2)
    async def analyze_place_mentions(ctx: RunContext[TripAgentDeps]) -> dict[str, object]:
        """Analyze place mentions from the original goal and persist hypotheses."""

        if cached := _cached_effect(ctx, "analyze_place_mentions"):
            return cached
        workspace = _workspace(ctx)
        if denied := _authorize(
            ctx, "analyze_place_mentions", str(workspace.goal_ledger.revision)
        ):
            return denied
        hypotheses = await ctx.deps.place_knowledge.analyze_mentions(workspace.goal_ledger.goal.raw_request)
        if not hypotheses:
            return {"ok": False, "code": "no_place_mentions", "version": workspace.version}
        updated = workspace.with_hypotheses(hypotheses)
        ctx.deps.repository.save_workspace(updated, expected_version=workspace.version)
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.events.append({"type": "place_mentions_analyzed", "count": len(hypotheses)})
        return _remember_effect(ctx, "analyze_place_mentions", {
            "ok": True,
            "version": updated.version,
            "hypothesis_ids": [item.hypothesis_id for item in hypotheses],
        })

    @toolset.tool(sequential=True, retries=2)
    async def search_place_candidates(
        ctx: RunContext[TripAgentDeps], hypothesis_id: str
    ) -> dict[str, object]:
        """Search bounded real candidates for one place hypothesis."""

        if cached := _cached_effect(ctx, "search_place_candidates"):
            return cached
        workspace = _workspace(ctx)
        if denied := _authorize(ctx, "search_place_candidates", hypothesis_id):
            return denied
        hypothesis = next(
            (item for item in workspace.place_hypotheses if item.hypothesis_id == hypothesis_id), None
        )
        if not hypothesis:
            return {"ok": False, "code": "hypothesis_not_found", "version": workspace.version}
        search = await ctx.deps.place_knowledge.search_candidates(hypothesis)
        if not search.candidates:
            return {"ok": False, "code": "candidate_not_found", "version": workspace.version}
        updated = workspace.with_candidates(search.candidates)
        ctx.deps.repository.save_workspace_with_sources(
            updated,
            expected_version=workspace.version,
            sources=search.sources,
        )
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.events.append({"type": "place_candidates_found", "count": len(search.candidates)})
        return _remember_effect(ctx, "search_place_candidates", {
            "ok": True,
            "version": updated.version,
            "candidate_ids": [item.candidate_id for item in search.candidates],
        })

    @toolset.tool
    async def compare_place_candidates(
        ctx: RunContext[TripAgentDeps], hypothesis_id: str
    ) -> dict[str, object]:
        """Ask the lightweight model for bounded identity advice; this tool never resolves a place."""

        workspace = _workspace(ctx)
        if denied := _authorize(
            ctx, "compare_place_candidates", f"{hypothesis_id}:{workspace.version}"
        ):
            return denied
        hypothesis = next(
            (item for item in workspace.place_hypotheses if item.hypothesis_id == hypothesis_id),
            None,
        )
        if hypothesis is None:
            return {"ok": False, "code": "hypothesis_not_found"}
        candidates = tuple(
            item for item in workspace.place_candidates if item.hypothesis_id == hypothesis_id
        )
        if not candidates:
            return {"ok": False, "code": "candidates_missing"}
        advice = await ctx.deps.place_knowledge.compare_candidates(hypothesis, candidates)
        return {"ok": True, **advice.model_dump(mode="json")}

    @toolset.tool(sequential=True, retries=2)
    async def resolve_place(
        ctx: RunContext[TripAgentDeps],
        hypothesis_id: str,
        candidate_id: str | None,
        rationale: str,
        action: Literal["resolve", "ambiguous", "exclude"] = "resolve",
    ) -> dict[str, object]:
        """Resolve, preserve ambiguity, or exclude a hypothesis; never create a new place."""

        if cached := _cached_effect(ctx, "resolve_place"):
            return cached
        if denied := _authorize(
            ctx,
            "resolve_place",
            f"{hypothesis_id}:{action}:{candidate_id}",
        ):
            return denied
        workspace = _workspace(ctx)
        hypothesis = next(
            (item for item in workspace.place_hypotheses if item.hypothesis_id == hypothesis_id),
            None,
        )
        if hypothesis is None:
            return {"ok": False, "code": "hypothesis_not_found", "version": workspace.version}
        status = {
            "resolve": ResolutionStatus.RESOLVED,
            "ambiguous": ResolutionStatus.AMBIGUOUS,
            "exclude": ResolutionStatus.EXCLUDED,
        }[action]
        if status is not ResolutionStatus.RESOLVED:
            candidate_id = None
        else:
            if candidate_id is None:
                return {"ok": False, "code": "candidate_required", "version": workspace.version}
            candidate = next(
                (item for item in workspace.place_candidates if item.candidate_id == candidate_id),
                None,
            )
            if candidate is None or candidate.hypothesis_id != hypothesis_id:
                return {"ok": False, "code": "candidate_not_found", "version": workspace.version}
            if hypothesis.brand_only or hypothesis.branch_unspecified:
                distinct = {
                    item.provider_place_id
                    for item in workspace.place_candidates
                    if item.hypothesis_id == hypothesis_id
                }
                if len(distinct) > 1:
                    return {
                        "ok": False,
                        "code": "branch_ambiguous",
                        "version": workspace.version,
                        "required_action": "ambiguous_or_clarification",
                    }
            parent = next(
                (
                    item
                    for item in workspace.place_candidates
                    if item.hypothesis_id == hypothesis_id
                    and item.provider_place_id == candidate.parent_provider_place_id
                ),
                None,
            )
            if parent is not None and _identity_text(hypothesis.raw_name) in _identity_text(parent.name):
                return {
                    "ok": False,
                    "code": "parent_entity_preferred",
                    "version": workspace.version,
                    "candidate_id": parent.candidate_id,
                }
        resolution = PlaceResolution(
            hypothesis_id=hypothesis_id,
            status=status,
            candidate_id=candidate_id,
            rationale=rationale,
            workspace_version=workspace.version,
        )
        updated = workspace.with_resolution(resolution)
        ctx.deps.repository.save_workspace(updated, expected_version=workspace.version)
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.events.append(
            {
                "type": (
                    "place_resolved"
                    if status is ResolutionStatus.RESOLVED
                    else "place_resolution_changed"
                ),
                "status": status.value,
                "candidate_id": candidate_id,
            }
        )
        return _remember_effect(ctx, "resolve_place", {
            "ok": True,
            "version": updated.version,
            "status": status.value,
            "candidate_id": candidate_id,
        })

    @toolset.tool(sequential=True, retries=2)
    async def acquire_place_facts(
        ctx: RunContext[TripAgentDeps],
        candidate_id: str,
        applicable_date: date | None = None,
    ) -> dict[str, object]:
        """Acquire source-backed opening and admission facts for a searched candidate."""

        if cached := _cached_effect(ctx, "acquire_place_facts"):
            return cached
        workspace = _workspace(ctx)
        if denied := _authorize(
            ctx, "acquire_place_facts", f"{candidate_id}:{applicable_date}"
        ):
            return denied
        candidate = next(
            (item for item in workspace.place_candidates if item.candidate_id == candidate_id), None
        )
        if candidate is None:
            return {"ok": False, "code": "candidate_not_found", "version": workspace.version}
        acquisition = await ctx.deps.place_knowledge.acquire_place_facts(
            candidate, applicable_date
        )
        if not acquisition.claims:
            return {"ok": False, "code": "fact_not_found", "version": workspace.version}
        updated = ctx.deps.repository.record_facts(
            workspace.workspace_id,
            expected_version=workspace.version,
            sources=acquisition.sources,
            claims=acquisition.claims,
        )
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.events.append(
            {"type": "place_facts_acquired", "candidate_id": candidate_id, "count": len(acquisition.claims)}
        )
        return _remember_effect(ctx, "acquire_place_facts", {
            "ok": True,
            "version": updated.version,
            "fact_version": updated.fact_version,
            "claim_ids": [claim.claim_id for claim in acquisition.claims],
        })

    @toolset.tool(sequential=True, retries=2)
    async def estimate_visit_profiles(
        ctx: RunContext[TripAgentDeps], candidate_ids: tuple[str, ...]
    ) -> dict[str, object]:
        """Estimate several visit profiles and commit all available estimates once."""

        if cached := _cached_effect(ctx, "estimate_visit_profiles"):
            return cached
        if not candidate_ids:
            return {"ok": False, "code": "candidate_ids_required"}
        if len(candidate_ids) > 20:
            return {"ok": False, "code": "visit_profile_batch_too_large", "maximum": 20}
        if len(candidate_ids) != len(set(candidate_ids)):
            return {"ok": False, "code": "duplicate_candidate_ids"}
        workspace = _workspace(ctx)
        if denied := _authorize(
            ctx, "estimate_visit_profiles", ";".join(candidate_ids)
        ):
            return denied
        candidates = {item.candidate_id: item for item in workspace.place_candidates}
        unknown = [candidate_id for candidate_id in candidate_ids if candidate_id not in candidates]
        if unknown:
            return {
                "ok": False,
                "code": "candidate_not_found",
                "candidate_ids": unknown,
                "version": workspace.version,
            }
        acquisitions = [
            await ctx.deps.place_knowledge.estimate_visit_profile(candidates[candidate_id])
            for candidate_id in candidate_ids
        ]
        sources = tuple(source for item in acquisitions for source in item.sources)
        claims = tuple(claim for item in acquisitions for claim in item.claims)
        unavailable = [
            candidate_id
            for candidate_id, acquisition in zip(candidate_ids, acquisitions, strict=True)
            if not acquisition.claims
        ]
        if not claims:
            return {
                "ok": False,
                "code": "visit_profiles_unavailable",
                "candidate_ids": unavailable,
                "version": workspace.version,
            }
        updated = ctx.deps.repository.record_facts(
            workspace.workspace_id,
            expected_version=workspace.version,
            sources=sources,
            claims=claims,
        )
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.events.append(
            {
                "type": "visit_profiles_estimated",
                "candidate_ids": list(candidate_ids),
                "unavailable_candidate_ids": unavailable,
            }
        )
        return _remember_effect(ctx, "estimate_visit_profiles", {
            "ok": True,
            "version": updated.version,
            "fact_version": updated.fact_version,
            "claim_ids": [claim.claim_id for claim in claims],
            "unavailable_candidate_ids": unavailable,
        })

    @toolset.tool(sequential=True, retries=2)
    async def acquire_route_facts(
        ctx: RunContext[TripAgentDeps],
        routes: tuple[RouteFactRequest, ...],
    ) -> dict[str, object]:
        """Acquire one or more directional routes and commit all returned facts atomically."""

        if cached := _cached_effect(ctx, "acquire_route_facts"):
            return cached
        if not routes:
            return {"ok": False, "code": "routes_required"}
        if len(routes) > 20:
            return {"ok": False, "code": "route_batch_too_large", "maximum": 20}
        route_signature = ";".join(
            f"{route.origin_candidate_id}>{route.destination_candidate_id}:{route.mode}"
            for route in routes
        )
        if denied := _authorize(
            ctx,
            "acquire_route_facts",
            route_signature,
        ):
            return denied
        workspace = _workspace(ctx)
        candidates = {item.candidate_id: item for item in workspace.place_candidates}
        resolved_candidate_ids = {
            resolution.candidate_id
            for resolution in workspace.place_resolutions
            if resolution.status is ResolutionStatus.RESOLVED
            and resolution.candidate_id is not None
        }
        for route in routes:
            if (
                route.origin_candidate_id not in candidates
                or route.destination_candidate_id not in candidates
            ):
                return {
                    "ok": False,
                    "code": "candidate_not_found",
                    "version": workspace.version,
                }
            if (
                route.origin_candidate_id not in resolved_candidate_ids
                or route.destination_candidate_id not in resolved_candidate_ids
            ):
                return {
                    "ok": False,
                    "code": "candidate_not_resolved",
                    "version": workspace.version,
                }

        acquisitions = []
        for route in routes:
            acquisitions.append(
                await ctx.deps.place_knowledge.acquire_route_facts(
                    candidates[route.origin_candidate_id],
                    candidates[route.destination_candidate_id],
                    route.mode,
                )
            )
        sources = tuple(source for item in acquisitions for source in item.sources)
        claims = tuple(claim for item in acquisitions for claim in item.claims)
        if not claims:
            return {"ok": False, "code": "route_not_found", "version": workspace.version}
        updated = ctx.deps.repository.record_facts(
            workspace.workspace_id,
            expected_version=workspace.version,
            sources=sources,
            claims=claims,
        )
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.events.append(
            {
                "type": "route_facts_acquired",
                "routes": [route.model_dump(mode="json") for route in routes],
            }
        )
        return _remember_effect(ctx, "acquire_route_facts", {
            "ok": True,
            "version": updated.version,
            "fact_version": updated.fact_version,
            "claim_ids": [claim.claim_id for claim in claims],
            "route_count": len(routes),
        })

    @toolset.tool(sequential=True, retries=2)
    async def estimate_visit_profile(
        ctx: RunContext[TripAgentDeps], candidate_id: str
    ) -> dict[str, object]:
        """Acquire a bounded visit-duration estimate; estimates never become verified facts."""

        if cached := _cached_effect(ctx, "estimate_visit_profile"):
            return cached
        workspace = _workspace(ctx)
        if denied := _authorize(ctx, "estimate_visit_profile", candidate_id):
            return denied
        candidate = next(
            (item for item in workspace.place_candidates if item.candidate_id == candidate_id), None
        )
        if candidate is None:
            return {"ok": False, "code": "candidate_not_found", "version": workspace.version}
        acquisition = await ctx.deps.place_knowledge.estimate_visit_profile(candidate)
        if not acquisition.claims:
            return {"ok": False, "code": "visit_profile_unavailable", "version": workspace.version}
        updated = ctx.deps.repository.record_facts(
            workspace.workspace_id,
            expected_version=workspace.version,
            sources=acquisition.sources,
            claims=acquisition.claims,
        )
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.events.append({"type": "visit_profile_estimated", "candidate_id": candidate_id})
        return _remember_effect(ctx, "estimate_visit_profile", {
            "ok": True,
            "version": updated.version,
            "fact_version": updated.fact_version,
            "claim_ids": [claim.claim_id for claim in acquisition.claims],
        })

    @toolset.tool(sequential=True, retries=2)
    async def apply_draft_change(
        ctx: RunContext[TripAgentDeps], days: tuple[DraftDayInput, ...]
    ) -> dict[str, object]:
        """Atomically replace the working draft with a high-level day allocation."""

        if cached := _cached_effect(ctx, "apply_draft_change"):
            return cached
        workspace = _workspace(ctx)
        if denied := _authorize(
            ctx,
            "apply_draft_change",
            f"{workspace.current_draft.draft_version if workspace.current_draft else 0}:{len(days)}",
        ):
            return denied
        candidate_ids = {item.candidate_id for item in workspace.place_candidates}
        proposed_ids = {visit.place_candidate_id for day in days for visit in day.visits}
        proposed_ids.update(
            day.hotel_candidate_id for day in days if day.hotel_candidate_id is not None
        )
        unknown = sorted(proposed_ids - candidate_ids)
        if unknown:
            return {"ok": False, "code": "unknown_candidate", "candidate_ids": unknown}
        prior_version = workspace.current_draft.draft_version if workspace.current_draft else 0
        draft = DraftSnapshot(
            draft_id=f"draft-{uuid4()}",
            workspace_id=workspace.workspace_id,
            workspace_version=workspace.version,
            draft_version=prior_version + 1,
            days=tuple(
                _build_draft_day(day)
                for day in days
            ),
        )
        updated = workspace.with_draft(draft)
        ctx.deps.repository.save_workspace(updated, expected_version=workspace.version)
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.events.append({"type": "draft_changed", "draft_id": draft.draft_id})
        return _remember_effect(
            ctx,
            "apply_draft_change",
            {"ok": True, "version": updated.version, "draft_id": draft.draft_id},
        )

    @toolset.tool(sequential=True, retries=2)
    async def apply_draft_operations(
        ctx: RunContext[TripAgentDeps],
        operations: tuple[DraftOperation, ...],
    ) -> dict[str, object]:
        """Atomically apply a batch of semantic repairs to the current working draft."""

        if cached := _cached_effect(ctx, "apply_draft_operations"):
            return cached
        workspace = _workspace(ctx)
        if denied := _authorize(
            ctx,
            "apply_draft_operations",
            f"{workspace.current_draft.draft_version if workspace.current_draft else 0}:{len(operations)}",
        ):
            return denied
        try:
            draft = _apply_draft_operations(workspace, operations)
        except ValueError as exc:
            return {"ok": False, "code": "invalid_draft_operation", "message": str(exc)}
        updated = workspace.with_draft(draft)
        ctx.deps.repository.save_workspace(updated, expected_version=workspace.version)
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.events.append(
            {
                "type": "draft_changed",
                "draft_id": draft.draft_id,
                "operation_count": len(operations),
            }
        )
        return _remember_effect(
            ctx,
            "apply_draft_operations",
            {"ok": True, "version": updated.version, "draft_id": draft.draft_id},
        )

    @toolset.tool
    async def simulate_candidate(ctx: RunContext[TripAgentDeps]) -> dict[str, object]:
        """Run deterministic structural simulation without mutating the draft."""

        workspace = _workspace(ctx)
        if denied := _authorize(ctx, "simulate_candidate", str(workspace.version)):
            return denied
        report = _simulation(ctx.deps.repository, workspace)
        ctx.deps.budget.observe_simulation(
            sum(1 for issue in report["issues"] if issue["blocking"])
        )
        ctx.deps.events.append({"type": "candidate_simulated", "ok": bool(report["ok"])})
        return report

    @toolset.tool(sequential=True, retries=2)
    async def request_clarification(
        ctx: RunContext[TripAgentDeps], questions: tuple[ClarificationQuestionInput, ...]
    ) -> dict[str, object] | None:
        """Pause this run for one to five material user decisions; do not ask preference trivia."""

        if denied := _authorize(
            ctx,
            "request_clarification",
            ":".join(question.question_id for question in questions),
        ):
            return denied
        if not 1 <= len(questions) <= 5:
            raise ValueError("request_clarification requires one to five questions")
        if not ctx.tool_call_id:
            raise RuntimeError("clarification requires a provider tool_call_id")
        interruption_id = f"interruption-{uuid4()}"
        batch = ClarificationBatch(
            interruption_id=interruption_id,
            run_id=ctx.deps.run_id,
            provider_run_id=ctx.deps.provider_run_id,
            tool_call_id=ctx.tool_call_id,
            questions=tuple(
                ClarificationQuestion(
                    question_id=question.question_id,
                    prompt=question.prompt,
                    reason=question.reason,
                    options=question.options,
                    allow_other=question.allow_other,
                )
                for question in questions
            ),
            created_at=utc_now(),
        )
        ctx.deps.repository.create_interruption_and_wait(batch)
        ctx.deps.events.append(
            {
                "type": "clarification_requested",
                "interruption_id": interruption_id,
                "question_count": len(questions),
            }
        )
        raise CallDeferred(
            metadata={"interruption_id": interruption_id, "run_id": ctx.deps.run_id}
        )

    @toolset.tool(sequential=True, retries=2)
    async def submit_candidate(
        ctx: RunContext[TripAgentDeps], completion_reason: str
    ) -> dict[str, object]:
        """Freeze and publish the current Agent-created candidate if structural blockers are absent."""

        if cached := _cached_effect(ctx, "submit_candidate"):
            return cached
        workspace = _workspace(ctx)
        if denied := _authorize(
            ctx,
            "submit_candidate",
            f"{workspace.current_draft.draft_version if workspace.current_draft else 0}:{workspace.fact_version}",
        ):
            return denied
        report = _simulation(ctx.deps.repository, workspace)
        if workspace.current_draft is None:
            return {
                "ok": False,
                "code": "candidate_rejected",
                "blockers": [
                    {
                        "code": "draft_missing",
                        "message": "No draft exists.",
                        "blocking": True,
                    }
                ],
            }
        now = utc_now()
        candidate = CandidateSnapshot(
            candidate_id=f"candidate-{uuid4()}",
            workspace_id=workspace.workspace_id,
            agent_run_id=ctx.deps.run_id,
            draft_id=workspace.current_draft.draft_id,
            workspace_version=workspace.version,
            fact_version=workspace.fact_version,
            draft_version=workspace.current_draft.draft_version,
            completion_reason=completion_reason,
            created_at=now,
        )
        if not report["ok"]:
            blockers = [issue for issue in report["issues"] if issue["blocking"]]
            prior_rejections = ctx.deps.repository.list_candidate_rejections(ctx.deps.run_id)
            if len(prior_rejections) >= 3:
                return {
                    "ok": False,
                    "code": "candidate_submission_limit_reached",
                    "blockers": blockers,
                    "attempts": len(prior_rejections),
                }
            rejection = CandidateRejected(
                rejection_id=f"rejection-{uuid4()}",
                candidate_id=candidate.candidate_id,
                issue_codes=tuple(sorted({str(issue["code"]) for issue in blockers})),
                created_at=now,
            )
            ctx.deps.repository.reject_candidate(candidate, rejection)
            ctx.deps.events.append(
                {
                    "type": "candidate_rejected",
                    "candidate_id": candidate.candidate_id,
                    "rejection_id": rejection.rejection_id,
                    "issue_codes": list(rejection.issue_codes),
                }
            )
            return _remember_effect(ctx, "submit_candidate", {
                "ok": False,
                "code": "candidate_rejected",
                "candidate_id": candidate.candidate_id,
                "rejection_id": rejection.rejection_id,
                "attempts": len(prior_rejections) + 1,
                "blockers": blockers,
            })
        fact_fingerprint_input = {
            "draft": workspace.current_draft.model_dump(mode="json"),
            "claims": [
                claim.model_dump(mode="json")
                for claim in ctx.deps.repository.list_claims(workspace.workspace_id)
            ],
        }
        fingerprint = sha256(
            json.dumps(fact_fingerprint_input, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        simulation = FeasibilityCompiler().compile(
            workspace, ctx.deps.repository.list_claims(workspace.workspace_id)
        )
        fact_status = ReleaseGate().decide_fact_status(workspace, simulation)
        experience_issues = [
            issue.code for issue in simulation.issues if not issue.blocking
        ]
        if any(day.outing_minutes > 10 * 60 for day in simulation.days):
            experience_issues.append("long_outing_day")
        if any(day.meal_strategy is None for day in workspace.current_draft.days):
            experience_issues.append("meal_strategy_missing")
        release = ReleaseRecord(
            release_id=f"release-{uuid4()}",
            release_key=f"{workspace.workspace_id}:{workspace.version}:{workspace.fact_version}",
            workspace_id=workspace.workspace_id,
            run_id=ctx.deps.run_id,
            candidate_id=candidate.candidate_id,
            workspace_version=workspace.version,
            fact_version=workspace.fact_version,
            fact_status=fact_status,
            experience_status=(
                ExperienceStatus.NEEDS_ADJUSTMENT
                if experience_issues
                else ExperienceStatus.GOOD
            ),
            issue_codes=tuple(sorted(set(simulation.fact_issue_codes) | set(experience_issues))),
            route_fact_fingerprint=fingerprint,
            created_at=now,
        )
        narrative: ReleaseNarrative
        try:
            if ctx.deps.narrative_generator is None:
                narrative = _template_narrative(release, workspace)
            else:
                narrative = await ctx.deps.narrative_generator.generate(
                    release, workspace, simulation
                )
                if narrative.release_id != release.release_id:
                    raise ValueError("narrative references another release")
                if narrative.route_fact_fingerprint != release.route_fact_fingerprint:
                    raise ValueError("narrative fact fingerprint drifted")
        except Exception as exc:
            narrative = _template_narrative(release, workspace)
            ctx.deps.events.append(
                {"type": "narrative_degraded", "error_type": type(exc).__name__}
            )
        ctx.deps.repository.publish_candidate(candidate, release, narrative)
        ctx.deps.events.append({"type": "candidate_published", "release_id": release.release_id})
        return _remember_effect(ctx, "submit_candidate", {
            "ok": True,
            "candidate_id": candidate.candidate_id,
            "release_id": release.release_id,
            "fact_status": release.fact_status.value,
        })

    return toolset


def _build_draft_day(day: DraftDayInput) -> DraftDay:
    visits = tuple(
        DraftVisit(
            visit_id=f"visit-{uuid4()}",
            place_candidate_id=visit.place_candidate_id,
            duration_min=visit.duration_min,
            earliest_start=visit.earliest_start,
            latest_end=visit.latest_end,
            fixed_start=visit.fixed_start,
            travel_mode_from_previous=visit.travel_mode_from_previous,
            optional=visit.optional,
        )
        for visit in day.visits
    )
    invalid_indexes = sorted(
        {
            meal.after_visit_index
            for meal in day.meals
            if meal.after_visit_index is not None
            and meal.after_visit_index > len(visits)
        }
    )
    if invalid_indexes:
        raise ValueError(f"meal after_visit_index is out of range: {invalid_indexes}")
    return DraftDay(
        day_index=day.day_index,
        date=day.date,
        visits=visits,
        meals=tuple(
            DraftMeal(
                meal_id=f"meal-{uuid4()}",
                kind=meal.kind,
                duration_min=meal.duration_min,
                after_visit_id=(
                    visits[meal.after_visit_index - 1].visit_id
                    if meal.after_visit_index is not None
                    else None
                ),
                earliest_start=meal.earliest_start,
                latest_end=meal.latest_end,
            )
            for meal in day.meals
        ),
        meal_strategy=day.meal_strategy,
        hotel_candidate_id=day.hotel_candidate_id,
        depart_hotel_mode=day.depart_hotel_mode,
        return_to_hotel=day.return_to_hotel,
        return_hotel_mode=day.return_hotel_mode,
    )


def _identity_text(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _apply_draft_operations(
    workspace: TripWorkspace, operations: tuple[DraftOperation, ...]
) -> DraftSnapshot:
    if workspace.current_draft is None:
        raise ValueError("semantic operations require an existing draft")
    if not operations:
        raise ValueError("at least one draft operation is required")
    candidate_ids = {candidate.candidate_id for candidate in workspace.place_candidates}
    days = list(workspace.current_draft.days)

    def day_position(day_index: int) -> int:
        for index, day in enumerate(days):
            if day.day_index == day_index:
                return index
        raise ValueError(f"unknown draft day: {day_index}")

    def locate(candidate_id: str) -> tuple[int, int, DraftVisit]:
        for day_index, day in enumerate(days):
            for visit_index, visit in enumerate(day.visits):
                if visit.place_candidate_id == candidate_id:
                    return day_index, visit_index, visit
        raise ValueError(f"candidate is not scheduled: {candidate_id}")

    for operation in operations:
        if isinstance(operation, AllocateDayOperation):
            if operation.candidate_id not in candidate_ids:
                raise ValueError(f"unknown candidate: {operation.candidate_id}")
            target = day_position(operation.day_index)
            existing_visit = None
            for index, day in enumerate(days):
                kept = []
                for visit in day.visits:
                    if visit.place_candidate_id == operation.candidate_id:
                        existing_visit = visit
                    else:
                        kept.append(visit)
                days[index] = day.model_copy(update={"visits": tuple(kept)})
            visit = (
                existing_visit.model_copy(
                    update={"duration_min": operation.duration_min, "optional": operation.optional}
                )
                if existing_visit
                else DraftVisit(
                    visit_id=f"visit-{uuid4()}",
                    place_candidate_id=operation.candidate_id,
                    duration_min=operation.duration_min,
                    optional=operation.optional,
                )
            )
            days[target] = days[target].model_copy(
                update={"visits": days[target].visits + (visit,)}
            )
        elif isinstance(operation, ReorderClusterOperation):
            target = day_position(operation.day_index)
            visits = {visit.place_candidate_id: visit for visit in days[target].visits}
            if set(operation.ordered_candidate_ids) != set(visits):
                raise ValueError("reorder_cluster must list every visit on the target day exactly once")
            if len(operation.ordered_candidate_ids) != len(set(operation.ordered_candidate_ids)):
                raise ValueError("reorder_cluster candidate IDs must be unique")
            days[target] = days[target].model_copy(
                update={"visits": tuple(visits[item] for item in operation.ordered_candidate_ids)}
            )
        elif isinstance(operation, ProtectAnchorOperation):
            day_index, visit_index, visit = locate(operation.candidate_id)
            visits = list(days[day_index].visits)
            visits[visit_index] = visit.model_copy(
                update={"fixed_start": operation.fixed_start, "optional": False}
            )
            days[day_index] = days[day_index].model_copy(update={"visits": tuple(visits)})
        elif isinstance(operation, SetVisitWindowOperation):
            if (
                operation.earliest_start
                and operation.latest_end
                and operation.latest_end <= operation.earliest_start
            ):
                raise ValueError("latest_end must be later than earliest_start")
            day_index, visit_index, visit = locate(operation.candidate_id)
            visits = list(days[day_index].visits)
            visits[visit_index] = visit.model_copy(
                update={
                    "earliest_start": operation.earliest_start,
                    "latest_end": operation.latest_end,
                }
            )
            days[day_index] = days[day_index].model_copy(update={"visits": tuple(visits)})
        elif isinstance(operation, ReduceIntensityOperation):
            target = day_position(operation.day_index)
            visits = list(days[target].visits)
            for index in range(len(visits) - 1, -1, -1):
                if len(visits) <= operation.max_visits:
                    break
                if visits[index].optional:
                    visits.pop(index)
            if len(visits) > operation.max_visits:
                raise ValueError("reduce_intensity cannot remove protected visits")
            days[target] = days[target].model_copy(update={"visits": tuple(visits)})
        elif isinstance(operation, ReplacePlaceOperation):
            if operation.new_candidate_id not in candidate_ids:
                raise ValueError(f"unknown candidate: {operation.new_candidate_id}")
            if any(
                visit.place_candidate_id == operation.new_candidate_id
                for day in days
                for visit in day.visits
            ):
                raise ValueError("replacement candidate is already scheduled")
            day_index, visit_index, visit = locate(operation.old_candidate_id)
            visits = list(days[day_index].visits)
            visits[visit_index] = visit.model_copy(
                update={"place_candidate_id": operation.new_candidate_id}
            )
            days[day_index] = days[day_index].model_copy(update={"visits": tuple(visits)})
        elif isinstance(operation, RemoveOptionalPlaceOperation):
            day_index, visit_index, visit = locate(operation.candidate_id)
            if not visit.optional:
                raise ValueError("remove_optional_place cannot remove a protected visit")
            visits = list(days[day_index].visits)
            visits.pop(visit_index)
            days[day_index] = days[day_index].model_copy(update={"visits": tuple(visits)})
        elif isinstance(operation, SetMealStrategyOperation):
            target = day_position(operation.day_index)
            days[target] = days[target].model_copy(update={"meal_strategy": operation.strategy})

    return DraftSnapshot(
        draft_id=f"draft-{uuid4()}",
        workspace_id=workspace.workspace_id,
        workspace_version=workspace.version,
        draft_version=workspace.current_draft.draft_version + 1,
        days=tuple(days),
    )


def _template_narrative(
    release: ReleaseRecord, workspace: TripWorkspace
) -> ReleaseNarrative:
    assert workspace.current_draft is not None
    return ReleaseNarrative(
        narrative_id=f"narrative-{uuid4()}",
        release_id=release.release_id,
        route_fact_fingerprint=release.route_fact_fingerprint,
        overview="行程已通过事实与时间线门禁，请按工作区中的确定性路线执行。",
        days=tuple(
            NarrativeDay(
                day_index=day.day_index,
                theme=f"第 {day.day_index} 天",
                summary="按已发布的地点顺序游览，并根据现场情况保留合理缓冲。",
            )
            for day in workspace.current_draft.days
        ),
        visits=tuple(
            NarrativeVisit(
                visit_id=visit.visit_id,
                note="该说明不修改已发布的地点、时长或交通事实。",
            )
            for day in workspace.current_draft.days
            for visit in day.visits
        ),
        risk_notes=tuple(release.issue_codes),
        generator="deterministic-template",
        created_at=utc_now(),
    )
