from __future__ import annotations

import asyncio
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
    CandidateCheckpoint,
    CandidateSnapshot,
    ClarificationBatch,
    ClarificationQuestion,
    CommitmentStrength,
    DomainModel,
    DraftDay,
    DraftMeal,
    DraftSnapshot,
    DraftVisit,
    ExperienceStatus,
    FactStatus,
    GoalCommitment,
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
    expand_visit_candidate_coverage,
    utc_now,
)
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.budget import RuntimeBudget
from app.trip_agent.validation import (
    CompletionEvaluator,
    FeasibilityCompiler,
    ReleaseGate,
    SimulationReport,
)


TravelMode = Literal["walking", "driving", "taxi", "transit", "public_transport", "subway"]


class RouteFactRequest(DomainModel):
    origin_candidate_id: str = Field(min_length=1, max_length=200)
    destination_candidate_id: str = Field(min_length=1, max_length=200)
    mode: TravelMode


class PlaceFactRequest(DomainModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    applicable_date: date | None = None


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
    place_cache_repository: SqliteTripAgentRepository | None
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
    place_candidate_id: str | None = Field(default=None, max_length=200)
    travel_mode_from_previous: TravelMode = "walking"
    earliest_start: time | None = None
    latest_end: time | None = None


class DraftDayInput(DomainModel):
    day_index: int = Field(ge=1, le=60)
    date: date
    day_purpose: Literal["touring", "arrival", "departure", "rest"] = "touring"
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


class PlaceResolutionDecision(DomainModel):
    hypothesis_id: str = Field(min_length=1, max_length=200)
    candidate_id: str | None = Field(default=None, max_length=200)
    rationale: str = Field(min_length=1, max_length=4_000)
    action: Literal["resolve", "ambiguous", "exclude"] = "resolve"


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
    return ctx.deps.repository.get_tool_effect(
        ctx.tool_call_id, run_id=ctx.deps.run_id, tool_name=tool_name
    )


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


def _atomic_effect(
    ctx: RunContext[TripAgentDeps], tool_name: str, result: dict[str, object]
) -> tuple[str, str, str, dict[str, object]] | None:
    if not ctx.tool_call_id:
        return None
    return (ctx.tool_call_id, ctx.deps.run_id, tool_name, result)


def _simulation(
    repository: SqliteTripAgentRepository, workspace: TripWorkspace
) -> dict[str, object]:
    report = FeasibilityCompiler().compile(
        workspace, repository.list_claims(workspace.workspace_id)
    )
    return report.model_dump(mode="json")


def _candidate_merge_summary(
    workspace: TripWorkspace, candidates: tuple[PlaceCandidate, ...]
) -> tuple[list[str], list[str], list[str]]:
    existing = {item.candidate_id: item for item in workspace.place_candidates}
    added: list[str] = []
    reused: list[str] = []
    updated: list[str] = []
    for candidate in candidates:
        prior = existing.get(candidate.candidate_id)
        if prior is None:
            added.append(candidate.candidate_id)
        elif prior == candidate:
            reused.append(candidate.candidate_id)
        else:
            updated.append(candidate.candidate_id)
    return added, reused, updated


def _normalize_candidate_observations(
    workspace: TripWorkspace, candidates: tuple[PlaceCandidate, ...]
) -> tuple[PlaceCandidate, ...]:
    stable_ids = {
        (item.hypothesis_id, item.provider, item.provider_place_id): item.candidate_id
        for item in workspace.place_candidates
    }
    normalized: dict[str, PlaceCandidate] = {}
    for item in candidates:
        identity = (item.hypothesis_id, item.provider, item.provider_place_id)
        stable_id = stable_ids.setdefault(identity, item.candidate_id)
        normalized[stable_id] = (
            item.model_copy(update={"candidate_id": stable_id})
            if stable_id != item.candidate_id
            else item
        )
    return tuple(normalized.values())


def _provider_failure_detail(exc: BaseException, *, item_id: str) -> dict[str, object]:
    details = getattr(exc, "details", {}) or {}
    provider_code = details.get("provider_code") or getattr(exc, "reason", None)
    permanent_codes = {"DAILY_QUERY_OVER_LIMIT", "INSUFFICIENT_BALANCE"}
    retryable = provider_code not in permanent_codes and getattr(exc, "code", None) not in {
        "missing_configuration",
        "authentication_failed",
    }
    return {
        "item_id": item_id,
        "error_type": type(exc).__name__,
        "provider_code": provider_code,
        "retryable": retryable,
        "message": str(exc)[:500],
    }


def _record_provider_failure(
    ctx: RunContext[TripAgentDeps], failure: dict[str, object]
) -> None:
    event_type = (
        "provider_circuit_open"
        if failure["error_type"] == "ProviderCircuitOpen"
        else "provider_attempt_failed"
    )
    ctx.deps.events.append({"type": event_type, **failure})


def _commitment_strength(hypothesis: PlaceHypothesis) -> CommitmentStrength:
    immutable_markers = ("不可调整", "不能调整", "不能改", "已预约", "固定预约", "必须在")
    return (
        CommitmentStrength.HARD
        if any(marker in hypothesis.context for marker in immutable_markers)
        else CommitmentStrength.STRONG
    )


def _reconcile_fact_result(
    ctx: RunContext[TripAgentDeps],
    *,
    workspace: TripWorkspace,
    updated: TripWorkspace,
    result: dict[str, object],
) -> None:
    result["version"] = updated.version
    result["fact_version"] = updated.fact_version
    if updated.version == workspace.version:
        result["code"] = "claims_reused"
        ctx.deps.events.append(
            {
                "type": "claims_reused",
                "claim_count": len(result.get("claim_ids", [])),
            }
        )
    else:
        ctx.deps.budget.observe_workspace_version(updated.version)


async def _search_candidates_cached(
    ctx: RunContext[TripAgentDeps], hypothesis: PlaceHypothesis
) -> tuple[CandidateSearch, bool]:
    workspace = _workspace(ctx)
    destination = workspace.goal_ledger.goal.destination or ""
    cache_key = sha256(
        f"{_identity_text(destination)}:{_identity_text(hypothesis.raw_name)}".encode()
    ).hexdigest()
    cache_repository = ctx.deps.place_cache_repository or ctx.deps.repository
    cached = cache_repository.get_place_search_cache(cache_key)
    if cached is None:
        ctx.deps.budget.observe_provider_call("amap", "place_search")
        search = await ctx.deps.place_knowledge.search_candidates(hypothesis)
        if search.candidates:
            cache_repository.save_place_search_cache(
                cache_key, sources=search.sources, candidates=search.candidates
            )
        return search, False
    sources, cached_candidates = cached
    candidates = tuple(
        candidate
        if candidate.hypothesis_id == hypothesis.hypothesis_id
        else candidate.model_copy(
            update={
                "hypothesis_id": hypothesis.hypothesis_id,
                "candidate_id": (
                    "candidate-cache-"
                    + sha256(
                        f"{hypothesis.hypothesis_id}:{candidate.provider}:"
                        f"{candidate.provider_place_id}".encode()
                    ).hexdigest()[:24]
                ),
            }
        )
        for candidate in cached_candidates
    )
    return CandidateSearch(sources=sources, candidates=candidates), True


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
        ctx.deps.budget.observe_provider_call("deepseek", "mention_analysis")
        hypotheses = await ctx.deps.place_knowledge.analyze_mentions(workspace.goal_ledger.goal.raw_request)
        if not hypotheses:
            return {"ok": False, "code": "no_place_mentions", "version": workspace.version}
        commitments = tuple(
            GoalCommitment(
                commitment_id=f"commitment-{sha256(item.hypothesis_id.encode()).hexdigest()[:20]}",
                field="lodging" if item.role == "lodging" else "must_visit",
                value=item.raw_name,
                evidence_text=item.raw_name,
                subject_hypothesis_id=item.hypothesis_id,
                strength=_commitment_strength(item),
            )
            for item in hypotheses
            if item.priority == "strong" and item.polarity == "requested"
        )
        updated = workspace.with_intent_analysis(hypotheses, commitments)
        result = {
            "ok": True,
            "version": updated.version,
            "hypothesis_ids": [item.hypothesis_id for item in hypotheses],
            "strong_commitment_ids": [item.commitment_id for item in commitments],
        }
        if updated.version == workspace.version:
            result["code"] = "intent_analysis_reused"
            result["reused_hypothesis_ids"] = [item.hypothesis_id for item in hypotheses]
            ctx.deps.events.append(
                {"type": "intent_analysis_reused", "count": len(hypotheses)}
            )
        else:
            ctx.deps.repository.save_workspace(
                updated,
                expected_version=workspace.version,
                tool_effect=_atomic_effect(ctx, "analyze_place_mentions", result),
            )
            ctx.deps.budget.observe_workspace_version(updated.version)
            ctx.deps.events.append({"type": "place_mentions_analyzed", "count": len(hypotheses)})
        return _remember_effect(ctx, "analyze_place_mentions", result)

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
        try:
            search, cache_hit = await _search_candidates_cached(ctx, hypothesis)
        except Exception as exc:
            failure = _provider_failure_detail(exc, item_id=hypothesis_id)
            _record_provider_failure(ctx, failure)
            return {
                "ok": False,
                "code": "place_provider_unavailable",
                "failure": failure,
                "retryable": failure["retryable"],
                "version": workspace.version,
            }
        if not search.candidates:
            return {"ok": False, "code": "candidate_not_found", "version": workspace.version}
        observed_candidates = _normalize_candidate_observations(
            workspace, search.candidates
        )
        try:
            updated = workspace.with_candidates(observed_candidates)
        except ValueError as exc:
            return {
                "ok": False,
                "code": "candidate_identity_conflict",
                "message": str(exc),
                "retryable": False,
                "version": workspace.version,
            }
        added_ids, reused_ids, updated_ids = _candidate_merge_summary(
            workspace, observed_candidates
        )
        result = {
            "ok": True,
            "version": updated.version,
            "candidate_ids": [item.candidate_id for item in observed_candidates],
            "added_candidate_ids": added_ids,
            "reused_candidate_ids": reused_ids,
            "updated_candidate_ids": updated_ids,
            "cache_hit": cache_hit,
        }
        if cache_hit:
            ctx.deps.events.append(
                {"type": "provider_cache_hit", "hypothesis_id": hypothesis_id}
            )
        if updated.version == workspace.version:
            result["code"] = "candidate_observation_reused"
            ctx.deps.events.append(
                {"type": "candidate_observation_reused", "count": len(reused_ids)}
            )
        else:
            ctx.deps.repository.save_workspace_with_sources(
                updated,
                expected_version=workspace.version,
                sources=search.sources,
                tool_effect=_atomic_effect(ctx, "search_place_candidates", result),
            )
            ctx.deps.budget.observe_workspace_version(updated.version)
            ctx.deps.events.append(
                {"type": "place_candidates_found", "count": len(search.candidates)}
            )
        return _remember_effect(ctx, "search_place_candidates", result)

    @toolset.tool(sequential=True, retries=2)
    async def search_candidate_sets(
        ctx: RunContext[TripAgentDeps], hypothesis_ids: tuple[str, ...]
    ) -> dict[str, object]:
        """Batch-search bounded candidate sets; prefer this for initial grounding."""

        if cached := _cached_effect(ctx, "search_candidate_sets"):
            return cached
        if not hypothesis_ids or len(hypothesis_ids) > 20:
            return {"ok": False, "code": "invalid_hypothesis_batch", "maximum": 20}
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            return {"ok": False, "code": "duplicate_hypothesis_ids"}
        workspace = _workspace(ctx)
        if denied := _authorize(ctx, "search_candidate_sets", ";".join(hypothesis_ids)):
            return denied
        hypotheses = {item.hypothesis_id: item for item in workspace.place_hypotheses}
        unknown = [item for item in hypothesis_ids if item not in hypotheses]
        if unknown:
            return {"ok": False, "code": "hypothesis_not_found", "hypothesis_ids": unknown}
        search_results = await asyncio.gather(
            *(_search_candidates_cached(ctx, hypotheses[item]) for item in hypothesis_ids),
            return_exceptions=True,
        )
        fatal = next(
            (
                result
                for result in search_results
                if isinstance(result, BaseException) and not isinstance(result, Exception)
            ),
            None,
        )
        if fatal is not None:
            raise fatal
        failed_hypothesis_ids = [
            hypothesis_id
            for hypothesis_id, result in zip(hypothesis_ids, search_results, strict=True)
            if isinstance(result, BaseException)
        ]
        failures = [
            _provider_failure_detail(result, item_id=hypothesis_id)
            for hypothesis_id, result in zip(hypothesis_ids, search_results, strict=True)
            if isinstance(result, BaseException)
        ]
        for failure in failures:
            _record_provider_failure(ctx, failure)
        searches = [
            result[0] for result in search_results if not isinstance(result, BaseException)
        ]
        cache_hit_hypothesis_ids = [
            hypothesis_id
            for hypothesis_id, result in zip(hypothesis_ids, search_results, strict=True)
            if not isinstance(result, BaseException) and result[1]
        ]
        candidates = tuple(
            candidate
            for search in searches
            for candidate in search.candidates
        )
        candidates = _normalize_candidate_observations(workspace, candidates)
        source_by_id = {
            source.source_record_id: source for search in searches for source in search.sources
        }
        if not candidates:
            if failed_hypothesis_ids:
                return {
                    "ok": False,
                    "code": "place_provider_unavailable",
                    "hypothesis_ids": failed_hypothesis_ids,
                    "failures": failures,
                    "retryable": any(bool(item["retryable"]) for item in failures),
                    "version": workspace.version,
                }
            return {
                "ok": True,
                "code": "candidate_sets_already_available",
                "version": workspace.version,
            }
        try:
            updated = workspace.with_candidates(candidates)
        except ValueError as exc:
            return {
                "ok": False,
                "code": "candidate_identity_conflict",
                "message": str(exc),
                "retryable": False,
                "version": workspace.version,
            }
        added_ids, reused_ids, updated_ids = _candidate_merge_summary(workspace, candidates)
        candidate_sets = {
            hypothesis_id: [
                item.candidate_id for item in candidates if item.hypothesis_id == hypothesis_id
            ]
            for hypothesis_id in hypothesis_ids
        }
        result = {
            "ok": True,
            "version": updated.version,
            "candidate_sets": candidate_sets,
            "provider_unavailable_hypothesis_ids": failed_hypothesis_ids,
            "provider_failures": failures,
            "cache_hit_hypothesis_ids": cache_hit_hypothesis_ids,
            "added_candidate_ids": added_ids,
            "reused_candidate_ids": reused_ids,
            "updated_candidate_ids": updated_ids,
        }
        if cache_hit_hypothesis_ids:
            ctx.deps.events.append(
                {
                    "type": "provider_cache_hit",
                    "hypothesis_ids": cache_hit_hypothesis_ids,
                }
            )
        if updated.version == workspace.version:
            result["code"] = "candidate_sets_already_available"
            ctx.deps.events.append(
                {"type": "candidate_observation_reused", "count": len(reused_ids)}
            )
        else:
            ctx.deps.repository.save_workspace_with_sources(
                updated,
                expected_version=workspace.version,
                sources=tuple(source_by_id.values()),
                tool_effect=_atomic_effect(ctx, "search_candidate_sets", result),
            )
            ctx.deps.budget.observe_workspace_version(updated.version)
            ctx.deps.events.append(
                {
                    "type": "place_candidate_sets_found",
                    "hypothesis_count": len(hypothesis_ids),
                    "candidate_count": len(candidates),
                }
            )
        return _remember_effect(
            ctx,
            "search_candidate_sets",
            result,
        )

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
        ctx.deps.budget.observe_provider_call("deepseek", "candidate_comparison")
        advice = await ctx.deps.place_knowledge.compare_candidates(hypothesis, candidates)
        return {"ok": True, **advice.model_dump(mode="json")}

    @toolset.tool
    async def compare_candidate_sets(
        ctx: RunContext[TripAgentDeps], hypothesis_ids: tuple[str, ...]
    ) -> dict[str, object]:
        """Batch-compare only identity sets that the root Agent considers ambiguous."""

        if not hypothesis_ids or len(hypothesis_ids) > 20:
            return {"ok": False, "code": "invalid_hypothesis_batch", "maximum": 20}
        workspace = _workspace(ctx)
        if denied := _authorize(ctx, "compare_candidate_sets", ";".join(hypothesis_ids)):
            return denied
        hypotheses = {item.hypothesis_id: item for item in workspace.place_hypotheses}
        candidate_sets = {
            hypothesis_id: tuple(
                item for item in workspace.place_candidates if item.hypothesis_id == hypothesis_id
            )
            for hypothesis_id in hypothesis_ids
        }
        missing = [
            item for item in hypothesis_ids if item not in hypotheses or not candidate_sets[item]
        ]
        if missing:
            return {"ok": False, "code": "candidate_sets_missing", "hypothesis_ids": missing}
        for _ in hypothesis_ids:
            ctx.deps.budget.observe_provider_call("deepseek", "candidate_comparison")
        advice = await asyncio.gather(
            *(
                ctx.deps.place_knowledge.compare_candidates(
                    hypotheses[hypothesis_id], candidate_sets[hypothesis_id]
                )
                for hypothesis_id in hypothesis_ids
            )
        )
        return {
            "ok": True,
            "advice": {
                hypothesis_id: item.model_dump(mode="json")
                for hypothesis_id, item in zip(hypothesis_ids, advice, strict=True)
            },
        }

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
        result = {
            "ok": True,
            "version": updated.version,
            "status": status.value,
            "candidate_id": candidate_id,
        }
        ctx.deps.repository.save_workspace(
            updated,
            expected_version=workspace.version,
            tool_effect=_atomic_effect(ctx, "resolve_place", result),
        )
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
        return _remember_effect(ctx, "resolve_place", result)

    @toolset.tool(sequential=True, retries=2)
    async def apply_place_resolutions(
        ctx: RunContext[TripAgentDeps], decisions: tuple[PlaceResolutionDecision, ...]
    ) -> dict[str, object]:
        """Atomically resolve, preserve, or exclude several place hypotheses."""

        if cached := _cached_effect(ctx, "apply_place_resolutions"):
            return cached
        if not decisions or len(decisions) > 20:
            return {"ok": False, "code": "invalid_resolution_batch", "maximum": 20}
        if len({item.hypothesis_id for item in decisions}) != len(decisions):
            return {"ok": False, "code": "duplicate_hypothesis_ids"}
        if denied := _authorize(
            ctx,
            "apply_place_resolutions",
            ";".join(f"{item.hypothesis_id}:{item.action}:{item.candidate_id}" for item in decisions),
        ):
            return denied
        workspace = _workspace(ctx)
        hypotheses = {item.hypothesis_id: item for item in workspace.place_hypotheses}
        candidates = {item.candidate_id: item for item in workspace.place_candidates}
        ambiguous_strong_ids = [
            decision.hypothesis_id
            for decision in decisions
            if decision.action == "ambiguous"
            and decision.hypothesis_id in hypotheses
            and hypotheses[decision.hypothesis_id].priority == "strong"
        ]
        for _ in ambiguous_strong_ids:
            ctx.deps.budget.observe_provider_call("deepseek", "candidate_comparison")
        ambiguous_advice_items = await asyncio.gather(
            *(
                ctx.deps.place_knowledge.compare_candidates(
                    hypotheses[hypothesis_id],
                    tuple(
                        item
                        for item in workspace.place_candidates
                        if item.hypothesis_id == hypothesis_id
                    ),
                )
                for hypothesis_id in ambiguous_strong_ids
            )
        )
        ambiguous_advice = {
            hypothesis_id: advice.model_dump(mode="json")
            for hypothesis_id, advice in zip(
                ambiguous_strong_ids, ambiguous_advice_items, strict=True
            )
        }
        resolutions: list[PlaceResolution] = []
        for decision in decisions:
            hypothesis = hypotheses.get(decision.hypothesis_id)
            if hypothesis is None:
                return {
                    "ok": False,
                    "code": "hypothesis_not_found",
                    "hypothesis_id": decision.hypothesis_id,
                }
            status = {
                "resolve": ResolutionStatus.RESOLVED,
                "ambiguous": ResolutionStatus.AMBIGUOUS,
                "exclude": ResolutionStatus.EXCLUDED,
            }[decision.action]
            candidate_id = decision.candidate_id if status is ResolutionStatus.RESOLVED else None
            if status is ResolutionStatus.RESOLVED:
                candidate = candidates.get(candidate_id or "")
                if candidate is None or candidate.hypothesis_id != decision.hypothesis_id:
                    return {
                        "ok": False,
                        "code": "candidate_not_found",
                        "hypothesis_id": decision.hypothesis_id,
                    }
                if hypothesis.brand_only or hypothesis.branch_unspecified:
                    distinct = {
                        item.provider_place_id
                        for item in workspace.place_candidates
                        if item.hypothesis_id == decision.hypothesis_id
                    }
                    if len(distinct) > 1:
                        return {
                            "ok": False,
                            "code": "branch_ambiguous",
                            "hypothesis_id": decision.hypothesis_id,
                        }
            resolutions.append(
                PlaceResolution(
                    hypothesis_id=decision.hypothesis_id,
                    status=status,
                    candidate_id=candidate_id,
                    rationale=decision.rationale,
                    workspace_version=workspace.version,
                )
            )
        updated = workspace.with_resolutions(tuple(resolutions))
        result = {
            "ok": True,
            "version": updated.version,
            "resolutions": [item.model_dump(mode="json") for item in resolutions],
            "ambiguous_strong_advice": ambiguous_advice,
        }
        ctx.deps.repository.save_workspace(
            updated,
            expected_version=workspace.version,
            tool_effect=_atomic_effect(ctx, "apply_place_resolutions", result),
        )
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.events.append(
            {"type": "place_resolutions_applied", "count": len(resolutions)}
        )
        return _remember_effect(
            ctx,
            "apply_place_resolutions",
            result,
        )

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
        try:
            ctx.deps.budget.observe_provider_call("web", "place_facts")
            ctx.deps.budget.observe_provider_call("deepseek", "fact_extraction")
            acquisition = await ctx.deps.place_knowledge.acquire_place_facts(
                candidate, applicable_date
            )
        except Exception as exc:
            failure = _provider_failure_detail(exc, item_id=candidate_id)
            _record_provider_failure(ctx, failure)
            return {
                "ok": False,
                "code": "place_facts_unavailable",
                "failure": failure,
                "retryable": failure["retryable"],
                "version": workspace.version,
            }
        if not acquisition.claims:
            return {"ok": False, "code": "fact_not_found", "version": workspace.version}
        result = {
            "ok": True,
            "version": workspace.version + 1,
            "fact_version": workspace.fact_version + 1,
            "claim_ids": [claim.claim_id for claim in acquisition.claims],
        }
        updated = ctx.deps.repository.record_facts(
            workspace.workspace_id,
            expected_version=workspace.version,
            sources=acquisition.sources,
            claims=acquisition.claims,
            tool_effect=_atomic_effect(ctx, "acquire_place_facts", result),
        )
        _reconcile_fact_result(ctx, workspace=workspace, updated=updated, result=result)
        ctx.deps.events.append(
            {"type": "place_facts_acquired", "candidate_id": candidate_id, "count": len(acquisition.claims)}
        )
        return _remember_effect(ctx, "acquire_place_facts", result)

    @toolset.tool(sequential=True, retries=2)
    async def acquire_place_fact_batch(
        ctx: RunContext[TripAgentDeps], requests: tuple[PlaceFactRequest, ...]
    ) -> dict[str, object]:
        """Acquire operational facts concurrently and commit the available batch once."""

        if cached := _cached_effect(ctx, "acquire_place_fact_batch"):
            return cached
        if not requests or len(requests) > 20:
            return {"ok": False, "code": "invalid_place_fact_batch", "maximum": 20}
        signature = ";".join(
            f"{item.candidate_id}:{item.applicable_date}" for item in requests
        )
        if denied := _authorize(ctx, "acquire_place_fact_batch", signature):
            return denied
        workspace = _workspace(ctx)
        candidates = {item.candidate_id: item for item in workspace.place_candidates}
        unknown = [item.candidate_id for item in requests if item.candidate_id not in candidates]
        if unknown:
            return {"ok": False, "code": "candidate_not_found", "candidate_ids": unknown}
        semaphore = asyncio.Semaphore(3)

        async def acquire(item: PlaceFactRequest) -> FactAcquisition:
            async with semaphore:
                ctx.deps.budget.observe_provider_call("web", "place_facts")
                ctx.deps.budget.observe_provider_call("deepseek", "fact_extraction")
                return await ctx.deps.place_knowledge.acquire_place_facts(
                    candidates[item.candidate_id], item.applicable_date
                )

        acquisition_results = await asyncio.gather(
            *(acquire(item) for item in requests), return_exceptions=True
        )
        failures = [
            _provider_failure_detail(result, item_id=request.candidate_id)
            for request, result in zip(requests, acquisition_results, strict=True)
            if isinstance(result, BaseException)
        ]
        for failure in failures:
            _record_provider_failure(ctx, failure)
        acquisitions = [
            result for result in acquisition_results if not isinstance(result, BaseException)
        ]
        source_by_id = {
            source.source_record_id: source
            for acquisition in acquisitions
            for source in acquisition.sources
        }
        claims = tuple(
            claim for acquisition in acquisitions for claim in acquisition.claims
        )
        unavailable = [
            item.candidate_id
            for item, acquisition in zip(requests, acquisition_results, strict=True)
            if isinstance(acquisition, BaseException) or not acquisition.claims
        ]
        if not claims:
            return {
                "ok": False,
                "code": "place_facts_unavailable",
                "candidate_ids": unavailable,
                "failures": failures,
                "retryable": any(bool(item["retryable"]) for item in failures),
                "version": workspace.version,
            }
        result = {
            "ok": True,
            "version": workspace.version + 1,
            "fact_version": workspace.fact_version + 1,
            "claim_ids": [item.claim_id for item in claims],
            "unavailable_candidate_ids": unavailable,
            "provider_failures": failures,
        }
        updated = ctx.deps.repository.record_facts(
            workspace.workspace_id,
            expected_version=workspace.version,
            sources=tuple(source_by_id.values()),
            claims=claims,
            tool_effect=_atomic_effect(ctx, "acquire_place_fact_batch", result),
        )
        _reconcile_fact_result(ctx, workspace=workspace, updated=updated, result=result)
        ctx.deps.events.append(
            {
                "type": "place_fact_batch_acquired",
                "candidate_count": len(requests),
                "claim_count": len(claims),
                "unavailable_candidate_ids": unavailable,
            }
        )
        return _remember_effect(ctx, "acquire_place_fact_batch", result)

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
        for _ in candidate_ids:
            ctx.deps.budget.observe_provider_call("deepseek", "visit_profile")
        acquisition_results = await asyncio.gather(
            *(
                ctx.deps.place_knowledge.estimate_visit_profile(candidates[candidate_id])
                for candidate_id in candidate_ids
            ),
            return_exceptions=True,
        )
        failures = [
            _provider_failure_detail(result, item_id=candidate_id)
            for candidate_id, result in zip(candidate_ids, acquisition_results, strict=True)
            if isinstance(result, BaseException)
        ]
        for failure in failures:
            _record_provider_failure(ctx, failure)
        acquisitions = [
            result for result in acquisition_results if not isinstance(result, BaseException)
        ]
        sources = tuple(source for item in acquisitions for source in item.sources)
        claims = tuple(claim for item in acquisitions for claim in item.claims)
        unavailable = [
            candidate_id
            for candidate_id, acquisition in zip(candidate_ids, acquisition_results, strict=True)
            if isinstance(acquisition, BaseException) or not acquisition.claims
        ]
        if not claims:
            return {
                "ok": False,
                "code": "visit_profiles_unavailable",
                "candidate_ids": unavailable,
                "failures": failures,
                "retryable": any(bool(item["retryable"]) for item in failures),
                "version": workspace.version,
            }
        result = {
            "ok": True,
            "version": workspace.version + 1,
            "fact_version": workspace.fact_version + 1,
            "claim_ids": [claim.claim_id for claim in claims],
            "unavailable_candidate_ids": unavailable,
            "provider_failures": failures,
        }
        updated = ctx.deps.repository.record_facts(
            workspace.workspace_id,
            expected_version=workspace.version,
            sources=sources,
            claims=claims,
            tool_effect=_atomic_effect(ctx, "estimate_visit_profiles", result),
        )
        _reconcile_fact_result(ctx, workspace=workspace, updated=updated, result=result)
        ctx.deps.events.append(
            {
                "type": "visit_profiles_estimated",
                "candidate_ids": list(candidate_ids),
                "unavailable_candidate_ids": unavailable,
            }
        )
        return _remember_effect(ctx, "estimate_visit_profiles", result)

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

        for _ in routes:
            ctx.deps.budget.observe_provider_call("amap", "route")
        acquisition_results = await asyncio.gather(
            *(
                ctx.deps.place_knowledge.acquire_route_facts(
                    candidates[route.origin_candidate_id],
                    candidates[route.destination_candidate_id],
                    route.mode,
                )
                for route in routes
            ),
            return_exceptions=True,
        )
        failures = [
            _provider_failure_detail(
                result,
                item_id=(
                    f"{route.origin_candidate_id}>{route.destination_candidate_id}:{route.mode}"
                ),
            )
            for route, result in zip(routes, acquisition_results, strict=True)
            if isinstance(result, BaseException)
        ]
        for failure in failures:
            _record_provider_failure(ctx, failure)
        acquisitions = [
            result for result in acquisition_results if not isinstance(result, BaseException)
        ]
        sources = tuple(source for item in acquisitions for source in item.sources)
        claims = tuple(claim for item in acquisitions for claim in item.claims)
        if not claims:
            return {
                "ok": False,
                "code": "route_not_found",
                "provider_failures": failures,
                "retryable": any(bool(item["retryable"]) for item in failures),
                "version": workspace.version,
            }
        result = {
            "ok": True,
            "version": workspace.version + 1,
            "fact_version": workspace.fact_version + 1,
            "claim_ids": [claim.claim_id for claim in claims],
            "route_count": len(routes),
            "provider_failures": failures,
        }
        updated = ctx.deps.repository.record_facts(
            workspace.workspace_id,
            expected_version=workspace.version,
            sources=sources,
            claims=claims,
            tool_effect=_atomic_effect(ctx, "acquire_route_facts", result),
        )
        _reconcile_fact_result(ctx, workspace=workspace, updated=updated, result=result)
        ctx.deps.events.append(
            {
                "type": "route_facts_acquired",
                "routes": [route.model_dump(mode="json") for route in routes],
            }
        )
        return _remember_effect(ctx, "acquire_route_facts", result)

    @toolset.tool(sequential=True, retries=2)
    async def hydrate_draft_context(ctx: RunContext[TripAgentDeps]) -> dict[str, object]:
        """Acquire the facts, visit profiles, and directional routes required by the current draft."""

        if cached := _cached_effect(ctx, "hydrate_draft_context"):
            return cached
        workspace = _workspace(ctx)
        draft = workspace.current_draft
        if draft is None:
            return {"ok": False, "code": "draft_required", "version": workspace.version}
        if denied := _authorize(
            ctx, "hydrate_draft_context", f"{draft.draft_id}:{draft.draft_version}"
        ):
            return denied
        candidates = {item.candidate_id: item for item in workspace.place_candidates}
        hypotheses = {item.hypothesis_id: item for item in workspace.place_hypotheses}
        scheduled_dates: dict[str, date] = {}
        scheduled_ids: list[str] = []
        routes: list[RouteFactRequest] = []
        for day in draft.days:
            prior_candidate_id = day.hotel_candidate_id
            meals_by_after: dict[str | None, list[DraftMeal]] = {}
            for meal in day.meals:
                meals_by_after.setdefault(meal.after_visit_id, []).append(meal)

            def append_scheduled(candidate_id: str) -> None:
                if candidate_id not in scheduled_dates:
                    scheduled_ids.append(candidate_id)
                    scheduled_dates[candidate_id] = day.date

            def append_route(candidate_id: str, mode: TravelMode) -> None:
                nonlocal prior_candidate_id
                if prior_candidate_id and prior_candidate_id != candidate_id:
                    routes.append(
                        RouteFactRequest(
                            origin_candidate_id=prior_candidate_id,
                            destination_candidate_id=candidate_id,
                            mode=mode,
                        )
                    )
                prior_candidate_id = candidate_id

            for meal in meals_by_after.get(None, []):
                if meal.place_candidate_id:
                    append_scheduled(meal.place_candidate_id)
                    append_route(meal.place_candidate_id, meal.travel_mode_from_previous)
            for index, visit in enumerate(day.visits):
                append_scheduled(visit.place_candidate_id)
                append_route(
                    visit.place_candidate_id,
                    (
                        day.depart_hotel_mode
                        if index == 0 and prior_candidate_id == day.hotel_candidate_id
                        else visit.travel_mode_from_previous
                    ),
                )
                for meal in meals_by_after.get(visit.visit_id, []):
                    if meal.place_candidate_id:
                        append_scheduled(meal.place_candidate_id)
                        append_route(meal.place_candidate_id, meal.travel_mode_from_previous)
            if (
                day.return_to_hotel
                and day.hotel_candidate_id
                and prior_candidate_id
                and prior_candidate_id != day.hotel_candidate_id
            ):
                routes.append(
                    RouteFactRequest(
                        origin_candidate_id=prior_candidate_id,
                        destination_candidate_id=day.hotel_candidate_id,
                        mode=day.return_hotel_mode,
                    )
                )
        unknown = [candidate_id for candidate_id in scheduled_ids if candidate_id not in candidates]
        route_ids = {
            candidate_id
            for route in routes
            for candidate_id in (route.origin_candidate_id, route.destination_candidate_id)
        }
        unknown.extend(sorted(route_ids - candidates.keys()))
        if unknown:
            return {"ok": False, "code": "candidate_not_found", "candidate_ids": unknown}
        unique_routes = tuple(
            {
                (route.origin_candidate_id, route.destination_candidate_id, route.mode): route
                for route in routes
            }.values()
        )
        profile_ids = [
            candidate_id
            for candidate_id in scheduled_ids
            if hypotheses[candidates[candidate_id].hypothesis_id].role == "visit"
        ]
        existing_claims = ctx.deps.repository.list_claims(workspace.workspace_id)
        operational_fields = {"opening_hours", "closure", "last_entry", "reservation"}
        profile_fields = {
            "minimum_visit_minutes",
            "recommended_visit_minutes",
            "recommended_visit_min",
            "recommended_duration_min",
            "extended_visit_minutes",
            "preferred_period",
        }
        place_query_ids = [
            candidate_id
            for candidate_id in scheduled_ids
            if not any(
                claim.kind == "observed"
                and claim.entity_id == candidate_id
                and claim.field in operational_fields
                and (
                    claim.applicable_date is None
                    or claim.applicable_date == scheduled_dates[candidate_id]
                )
                for claim in existing_claims
            )
        ]
        profile_query_ids = [
            candidate_id
            for candidate_id in profile_ids
            if not any(
                claim.entity_id == candidate_id and claim.field in profile_fields
                for claim in existing_claims
            )
        ]
        route_query_items = [
            route
            for route in unique_routes
            if not any(
                claim.entity_id
                == (
                    f"route:{route.origin_candidate_id}:"
                    f"{route.destination_candidate_id}:{route.mode}"
                )
                and claim.field == "duration_min"
                for claim in existing_claims
            )
        ]
        if not place_query_ids and not profile_query_ids and not route_query_items:
            result = {
                "ok": True,
                "reused": True,
                "version": workspace.version,
                "fact_version": workspace.fact_version,
                "scheduled_candidate_count": len(scheduled_ids),
                "profile_candidate_count": len(profile_ids),
                "route_count": len(unique_routes),
                "claim_ids": [],
            }
            ctx.deps.events.append(
                {
                    "type": "draft_context_reused",
                    "scheduled_candidate_count": len(scheduled_ids),
                    "route_count": len(unique_routes),
                }
            )
            return _remember_effect(ctx, "hydrate_draft_context", result)
        semaphore = asyncio.Semaphore(3)

        async def bounded(coroutine):
            async with semaphore:
                return await coroutine

        place_tasks = [
            bounded(
                ctx.deps.place_knowledge.acquire_place_facts(
                    candidates[candidate_id], scheduled_dates[candidate_id]
                )
            )
            for candidate_id in place_query_ids
        ]
        profile_tasks = [
            bounded(ctx.deps.place_knowledge.estimate_visit_profile(candidates[candidate_id]))
            for candidate_id in profile_query_ids
        ]
        route_tasks = [
            bounded(
                ctx.deps.place_knowledge.acquire_route_facts(
                    candidates[route.origin_candidate_id],
                    candidates[route.destination_candidate_id],
                    route.mode,
                )
            )
            for route in route_query_items
        ]
        for _ in place_query_ids:
            ctx.deps.budget.observe_provider_call("web", "place_facts")
            ctx.deps.budget.observe_provider_call("deepseek", "fact_extraction")
        for _ in profile_query_ids:
            ctx.deps.budget.observe_provider_call("deepseek", "visit_profile")
        for _ in route_query_items:
            ctx.deps.budget.observe_provider_call("amap", "route")
        task_labels = (
            [f"place:{candidate_id}" for candidate_id in place_query_ids]
            + [f"profile:{candidate_id}" for candidate_id in profile_query_ids]
            + [
                f"route:{route.origin_candidate_id}>{route.destination_candidate_id}:{route.mode}"
                for route in route_query_items
            ]
        )
        acquisition_results = await asyncio.gather(
            *(place_tasks + profile_tasks + route_tasks), return_exceptions=True
        )
        failures = [
            _provider_failure_detail(result, item_id=label)
            for label, result in zip(task_labels, acquisition_results, strict=True)
            if isinstance(result, BaseException)
        ]
        for failure in failures:
            _record_provider_failure(ctx, failure)
        acquisitions = [
            result for result in acquisition_results if not isinstance(result, BaseException)
        ]
        source_by_id = {
            source.source_record_id: source
            for acquisition in acquisitions
            for source in acquisition.sources
        }
        claims = tuple(claim for acquisition in acquisitions for claim in acquisition.claims)
        if not claims:
            return {
                "ok": False,
                "code": "draft_context_unavailable",
                "provider_failures": failures,
                "retryable": any(bool(item["retryable"]) for item in failures),
                "version": workspace.version,
            }
        result = {
            "ok": True,
            "version": workspace.version + 1,
            "fact_version": workspace.fact_version + 1,
            "scheduled_candidate_count": len(scheduled_ids),
            "profile_candidate_count": len(profile_ids),
            "route_count": len(unique_routes),
            "queried_place_count": len(place_query_ids),
            "queried_profile_count": len(profile_query_ids),
            "queried_route_count": len(route_query_items),
            "claim_ids": [claim.claim_id for claim in claims],
            "provider_failures": failures,
        }
        updated = ctx.deps.repository.record_facts(
            workspace.workspace_id,
            expected_version=workspace.version,
            sources=tuple(source_by_id.values()),
            claims=claims,
            tool_effect=_atomic_effect(ctx, "hydrate_draft_context", result),
        )
        _reconcile_fact_result(ctx, workspace=workspace, updated=updated, result=result)
        ctx.deps.events.append(
            {
                "type": "draft_context_hydrated",
                "scheduled_candidate_count": len(scheduled_ids),
                "profile_candidate_count": len(profile_ids),
                "route_count": len(unique_routes),
                "queried_place_count": len(place_query_ids),
                "queried_profile_count": len(profile_query_ids),
                "queried_route_count": len(route_query_items),
                "claim_count": len(claims),
            }
        )
        return _remember_effect(ctx, "hydrate_draft_context", result)

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
        ctx.deps.budget.observe_provider_call("deepseek", "visit_profile")
        acquisition = await ctx.deps.place_knowledge.estimate_visit_profile(candidate)
        if not acquisition.claims:
            return {"ok": False, "code": "visit_profile_unavailable", "version": workspace.version}
        result = {
            "ok": True,
            "version": workspace.version + 1,
            "fact_version": workspace.fact_version + 1,
            "claim_ids": [claim.claim_id for claim in acquisition.claims],
        }
        updated = ctx.deps.repository.record_facts(
            workspace.workspace_id,
            expected_version=workspace.version,
            sources=acquisition.sources,
            claims=acquisition.claims,
            tool_effect=_atomic_effect(ctx, "estimate_visit_profile", result),
        )
        _reconcile_fact_result(ctx, workspace=workspace, updated=updated, result=result)
        ctx.deps.events.append({"type": "visit_profile_estimated", "candidate_id": candidate_id})
        return _remember_effect(ctx, "estimate_visit_profile", result)

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
        result = {"ok": True, "version": updated.version, "draft_id": draft.draft_id}
        ctx.deps.repository.save_workspace(
            updated,
            expected_version=workspace.version,
            tool_effect=_atomic_effect(ctx, "apply_draft_change", result),
        )
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.budget.observe_draft()
        ctx.deps.events.append({"type": "draft_changed", "draft_id": draft.draft_id})
        return _remember_effect(
            ctx,
            "apply_draft_change",
            result,
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
        result = {"ok": True, "version": updated.version, "draft_id": draft.draft_id}
        ctx.deps.repository.save_workspace(
            updated,
            expected_version=workspace.version,
            tool_effect=_atomic_effect(ctx, "apply_draft_operations", result),
        )
        ctx.deps.budget.observe_workspace_version(updated.version)
        ctx.deps.budget.observe_draft()
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
            result,
        )

    @toolset.tool
    async def simulate_candidate(ctx: RunContext[TripAgentDeps]) -> dict[str, object]:
        """Run deterministic structural simulation without mutating the draft."""

        workspace = _workspace(ctx)
        if denied := _authorize(ctx, "simulate_candidate", str(workspace.version)):
            return denied
        report = _simulation(ctx.deps.repository, workspace)
        completion = CompletionEvaluator().evaluate(workspace)
        ctx.deps.budget.observe_simulation(
            sum(1 for issue in report["issues"] if issue["blocking"])
            + len(completion.issue_codes)
        )
        checkpoint_id: str | None = None
        if report["ok"] and completion.complete and workspace.current_draft is not None:
            checkpoint_fingerprint = sha256(
                json.dumps(
                    {
                        "run_id": ctx.deps.run_id,
                        "draft": workspace.current_draft.model_dump(mode="json"),
                        "workspace_version": workspace.version,
                        "fact_version": workspace.fact_version,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest()[:24]
            checkpoint = CandidateCheckpoint(
                checkpoint_id=f"checkpoint-{checkpoint_fingerprint}",
                workspace_id=workspace.workspace_id,
                agent_run_id=ctx.deps.run_id,
                workspace_version=workspace.version,
                fact_version=workspace.fact_version,
                draft=workspace.current_draft,
                claim_ids=tuple(
                    claim.claim_id
                    for claim in ctx.deps.repository.list_claims(workspace.workspace_id)
                ),
                created_at=utc_now(),
            )
            previous = ctx.deps.repository.latest_candidate_checkpoint(ctx.deps.run_id)
            persisted = ctx.deps.repository.create_candidate_checkpoint(checkpoint)
            checkpoint_id = checkpoint.checkpoint_id
            if previous is None or previous.checkpoint_id != persisted.checkpoint_id:
                ctx.deps.budget.observe_complete_checkpoint()
                ctx.deps.events.append(
                    {"type": "candidate_checkpoint_saved", "checkpoint_id": checkpoint_id}
                )
            else:
                ctx.deps.events.append(
                    {"type": "candidate_checkpoint_reused", "checkpoint_id": checkpoint_id}
                )
        ctx.deps.events.append(
            {
                "type": "candidate_simulated",
                "ok": bool(report["ok"] and completion.complete),
            }
        )
        report["completion"] = completion.model_dump(mode="json")
        report["checkpoint_id"] = checkpoint_id
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
        completion = CompletionEvaluator().evaluate(workspace)
        if not completion.complete:
            report["ok"] = False
            report["issues"] = list(report["issues"]) + [
                {
                    "code": code,
                    "message": "; ".join(completion.details) or code,
                    "blocking": True,
                }
                for code in completion.issue_codes
            ]
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
        experience_issues = _experience_issue_codes(workspace, simulation)
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
        result = {
            "ok": True,
            "candidate_id": candidate.candidate_id,
            "release_id": release.release_id,
            "fact_status": release.fact_status.value,
        }
        ctx.deps.repository.publish_candidate(
            candidate,
            release,
            narrative,
            tool_effect=_atomic_effect(ctx, "submit_candidate", result),
        )
        ctx.deps.events.append({"type": "candidate_published", "release_id": release.release_id})
        return _remember_effect(ctx, "submit_candidate", result)

    return toolset


async def publish_latest_complete_checkpoint(
    deps: TripAgentDeps,
) -> ReleaseRecord | None:
    """Publish only a previously simulated, complete Agent-authored checkpoint."""

    checkpoint = deps.repository.latest_candidate_checkpoint(deps.run_id)
    workspace = deps.repository.get_workspace(deps.workspace_id)
    run = deps.repository.get_run(deps.run_id)
    if checkpoint is None or workspace is None or run is None or run.status is not RunStatus.RUNNING:
        return None
    checkpoint_workspace = workspace.model_copy(update={"current_draft": checkpoint.draft})
    claims = deps.repository.list_claims(workspace.workspace_id)
    simulation = FeasibilityCompiler().compile(checkpoint_workspace, claims)
    completion = CompletionEvaluator().evaluate(checkpoint_workspace)
    if not simulation.ok or not completion.complete:
        return None
    deps.events.append(
        {
            "type": "checkpoint_revalidated",
            "checkpoint_id": checkpoint.checkpoint_id,
            "workspace_version": workspace.version,
            "fact_version": workspace.fact_version,
        }
    )
    now = utc_now()
    candidate = CandidateSnapshot(
        candidate_id=f"candidate-{uuid4()}",
        workspace_id=workspace.workspace_id,
        agent_run_id=deps.run_id,
        draft_id=checkpoint.draft.draft_id,
        workspace_version=workspace.version,
        fact_version=workspace.fact_version,
        draft_version=checkpoint.draft.draft_version,
        completion_reason="Published the latest complete Agent-authored checkpoint after convergence ended.",
        created_at=now,
    )
    fingerprint = sha256(
        json.dumps(
            {
                "draft": checkpoint.draft.model_dump(mode="json"),
                "claims": [item.model_dump(mode="json") for item in claims],
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    experience_issues = _experience_issue_codes(checkpoint_workspace, simulation)
    release = ReleaseRecord(
        release_id=f"release-{uuid4()}",
        release_key=f"{workspace.workspace_id}:checkpoint:{checkpoint.checkpoint_id}",
        workspace_id=workspace.workspace_id,
        run_id=deps.run_id,
        candidate_id=candidate.candidate_id,
        workspace_version=workspace.version,
        fact_version=workspace.fact_version,
        fact_status=ReleaseGate().decide_fact_status(checkpoint_workspace, simulation),
        experience_status=(
            ExperienceStatus.NEEDS_ADJUSTMENT
            if experience_issues
            else ExperienceStatus.GOOD
        ),
        issue_codes=tuple(
            sorted(set(simulation.fact_issue_codes) | set(experience_issues))
        ),
        route_fact_fingerprint=fingerprint,
        created_at=now,
    )
    try:
        if deps.narrative_generator is None:
            narrative = _template_narrative(release, checkpoint_workspace)
        else:
            narrative = await deps.narrative_generator.generate(
                release, checkpoint_workspace, simulation
            )
            if narrative.release_id != release.release_id:
                raise ValueError("narrative references another release")
            if narrative.route_fact_fingerprint != release.route_fact_fingerprint:
                raise ValueError("narrative fact fingerprint drifted")
    except Exception as exc:
        narrative = _template_narrative(release, checkpoint_workspace)
        deps.events.append(
            {"type": "narrative_degraded", "error_type": type(exc).__name__}
        )
    deps.repository.publish_candidate(candidate, release, narrative)
    deps.events.append(
        {
            "type": "candidate_checkpoint_published",
            "checkpoint_id": checkpoint.checkpoint_id,
            "release_id": release.release_id,
        }
    )
    return release


def _experience_issue_codes(
    workspace: TripWorkspace, simulation: SimulationReport
) -> list[str]:
    issues = [item.code for item in simulation.issues if not item.blocking]
    if any(day.outing_minutes > 10 * 60 for day in simulation.days):
        issues.append("long_outing_day")
    draft = workspace.current_draft
    if draft is None:
        return issues
    if any(day.meal_strategy is None and not day.meals for day in draft.days):
        issues.append("meal_strategy_missing")
    represented_visits = {
        visit.place_candidate_id for day in draft.days for visit in day.visits
    }
    represented_non_lodging = expand_visit_candidate_coverage(
        workspace.place_candidates, represented_visits
    ) | {
        meal.place_candidate_id
        for day in draft.days
        for meal in day.meals
        if meal.place_candidate_id
    }
    represented_lodging = {
        day.hotel_candidate_id for day in draft.days if day.hotel_candidate_id
    }
    represented = represented_non_lodging | represented_lodging
    requested_meal_hypotheses = {
        hypothesis.hypothesis_id
        for hypothesis in workspace.place_hypotheses
        if hypothesis.role == "meal" and hypothesis.polarity == "requested"
    }
    requested_meal_candidates = {
        resolution.candidate_id
        for resolution in workspace.place_resolutions
        if resolution.hypothesis_id in requested_meal_hypotheses
        and resolution.status is ResolutionStatus.RESOLVED
        and resolution.candidate_id
    }
    if requested_meal_candidates - represented:
        issues.append("requested_meal_place_omitted")
    resolved_by_hypothesis = {
        resolution.hypothesis_id: resolution.candidate_id
        for resolution in workspace.place_resolutions
        if resolution.status is ResolutionStatus.RESOLVED and resolution.candidate_id
    }
    omitted_strong = [
        commitment.commitment_id
        for commitment in workspace.goal_ledger.commitments
        if commitment.strength is CommitmentStrength.STRONG
        and commitment.subject_hypothesis_id
        and resolved_by_hypothesis.get(commitment.subject_hypothesis_id)
        not in (
            represented_lodging
            if commitment.field == "lodging"
            else represented_non_lodging
        )
    ]
    if omitted_strong:
        issues.append("strong_commitment_omitted")
    return issues


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
        day_purpose=day.day_purpose,
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
                place_candidate_id=meal.place_candidate_id,
                travel_mode_from_previous=meal.travel_mode_from_previous,
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
