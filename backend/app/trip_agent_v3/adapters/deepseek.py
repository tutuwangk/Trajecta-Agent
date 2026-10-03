from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import time, timedelta
from hashlib import sha256
from time import perf_counter
from typing import Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from pydantic_ai import (
    Agent,
    ModelRetry,
    RunContext,
    UsageLimitExceeded,
    UsageLimits,
    UnexpectedModelBehavior,
)
from pydantic_ai.models import Model
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.toolsets import FunctionToolset, WrapperToolset

from app.trip_agent_v3.adapters.provider import (
    DeepSeekV4ChatModel,
    deepseek_v4_settings,
)
from app.trip_agent_v3.autonomous_runtime import AutonomousTripRuntime
from app.trip_agent_v3.commitments import PlanCommitmentError
from app.trip_agent_v3.domain.execution import JourneyGoal, PlanningSubmission
from app.trip_agent_v3.domain.sources import RequirementProposal, SourceDocument
from app.trip_agent_v3.grounding import GroundingCompilationError, GroundingDecisionProposal
from app.trip_agent_v3.telemetry import RunTelemetry


class ModelDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ProviderContextCheckpoint(BaseException):
    """Stop one provider episode after durable business state is saved."""


class EvidenceSpanDTO(ModelDTO):
    source_id: str
    start: int
    end: int


class PlaceMentionDTO(ModelDTO):
    mention_key: str
    mention: str
    role: Literal[
        "visit",
        "meal",
        "lodging",
        "shopping",
        "photo",
        "airport",
        "transport",
        "reference",
    ]
    priority: Literal["required", "preferred", "optional"]
    evidence: list[EvidenceSpanDTO]
    explicit: bool = True
    query_decision: Literal[
        "query", "merge", "reference", "not_a_place", "clarify"
    ]
    decision_reason: str | None = None
    merge_into_mention_key: str | None = None
    query_text: str | None = None
    aliases: list[str] = Field(default_factory=list)
    category_hints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_decision_contract(self) -> "PlaceMentionDTO":
        if (
            self.explicit
            and self.role
            in {
                "visit",
                "meal",
                "lodging",
                "shopping",
                "photo",
                "airport",
            }
            and self.query_decision in {"reference", "not_a_place"}
        ):
            raise ValueError(
                "an explicit schedulable place must be queried, merged, or "
                "clarified"
            )
        if self.query_decision == "query":
            if not self.query_text:
                raise ValueError("query decisions require query_text")
        elif self.query_text is not None:
            raise ValueError("query_text is only valid for query decisions")
        if self.query_decision == "merge":
            if not self.merge_into_mention_key:
                raise ValueError(
                    "merge decisions require merge_into_mention_key"
                )
            if self.merge_into_mention_key == self.mention_key:
                raise ValueError("a mention cannot merge into itself")
        elif self.merge_into_mention_key is not None:
            raise ValueError(
                "merge_into_mention_key is only valid for merge decisions"
            )
        if self.query_decision in {
            "merge",
            "reference",
            "not_a_place",
            "clarify",
        } and not self.decision_reason:
            raise ValueError(
                f"{self.query_decision} decisions require decision_reason"
            )
        return self


class TimeWindowDTO(ModelDTO):
    kind: Literal["time_window"] = "time_window"
    proposal_key: str
    subject_mention_keys: list[str]
    evidence: list[EvidenceSpanDTO]
    strength: Literal["required", "preferred"]
    fixed_commitment: bool = False
    day_number: int | None = None
    earliest: time
    latest: time

    @model_validator(mode="after")
    def validate_window(self) -> "TimeWindowDTO":
        if self.latest <= self.earliest:
            raise ValueError("time-window latest must be after earliest")
        return self


class DayAssignmentDTO(ModelDTO):
    kind: Literal["day_assignment"] = "day_assignment"
    proposal_key: str
    subject_mention_keys: list[str]
    evidence: list[EvidenceSpanDTO]
    strength: Literal["required", "preferred"]
    fixed_commitment: bool = False
    day_number: int


class PaceDTO(ModelDTO):
    kind: Literal["pace"] = "pace"
    proposal_key: str
    evidence: list[EvidenceSpanDTO]
    strength: Literal["required", "preferred"]
    max_day_minutes: int


class TransportPreferenceDTO(ModelDTO):
    kind: Literal["transport_preference"] = "transport_preference"
    proposal_key: str
    evidence: list[EvidenceSpanDTO]
    strength: Literal["required", "preferred"]
    mode_policy: Literal["prefer", "only"] = "prefer"
    preferred_modes: list[
        Literal["walk", "taxi", "transit", "driving"]
    ] = Field(default_factory=list, max_length=4)
    max_walk_minutes: int | None = None


class InterpretedRequirements(ModelDTO):
    mentions: list[PlaceMentionDTO] = Field(default_factory=list)
    constraints: list[
        TimeWindowDTO
        | DayAssignmentDTO
        | PaceDTO
        | TransportPreferenceDTO
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cross_references(self) -> "InterpretedRequirements":
        keys = [item.mention_key for item in self.mentions]
        if len(keys) != len(set(keys)):
            raise ValueError("mention keys must be unique")
        by_key = {item.mention_key: item for item in self.mentions}
        proposal_keys = [item.proposal_key for item in self.constraints]
        if len(proposal_keys) != len(set(proposal_keys)):
            raise ValueError("constraint proposal keys must be unique")
        for constraint in self.constraints:
            if isinstance(
                constraint, (PaceDTO, TransportPreferenceDTO)
            ):
                continue
            unknown = set(constraint.subject_mention_keys) - by_key.keys()
            if unknown:
                raise ValueError(
                    "constraint references unknown mention keys: "
                    f"{sorted(unknown)}"
                )
        for mention in self.mentions:
            if mention.query_decision != "merge":
                continue
            target = mention.merge_into_mention_key or ""
            if target not in by_key:
                raise ValueError(
                    f"merge target references unknown mention key: {target}"
                )
            if by_key[target].query_decision != "query":
                raise ValueError("merge target must be a query mention")
        return self


class GroundingDecisionDTO(ModelDTO):
    target_id: str
    status: Literal["selected", "needs_confirmation", "no_match"]
    selected_provider_place_id: str | None = None
    rationale: str
    confidence: float
    selection_factors: list[str] = Field(default_factory=list)
    provider_place_ids_requiring_confirmation: list[str] = Field(
        default_factory=list
    )
    clarification_question: str | None = None


class DraftStopDTO(ModelDTO):
    stop_key: str
    candidate_id: str
    obligation_ids: list[str] = Field(default_factory=list)
    name: str
    kind: Literal[
        "lodging", "airport", "visit", "meal", "shopping", "photo"
    ]
    stay_duration_min: int
    travel_mode_from_previous: Literal[
        "walk", "taxi", "transit", "driving"
    ] | None = None
    rationale: str


class DraftDayDTO(ModelDTO):
    day_number: int
    title: str
    start_time: time
    stops: list[DraftStopDTO]


class WorkingDraftDTO(ModelDTO):
    days: list[DraftDayDTO]

    @model_validator(mode="after")
    def validate_local_keys(self) -> "WorkingDraftDTO":
        if [day.day_number for day in self.days] != list(
            range(1, len(self.days) + 1)
        ):
            raise ValueError(
                "draft day numbers must be contiguous and start at one"
            )
        keys = [
            stop.stop_key for day in self.days for stop in day.stops
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("stop_key values must be globally unique")
        return self


class CoverageDispositionDTO(ModelDTO):
    obligation_id: str
    status: Literal[
        "unhandled",
        "scheduled",
        "pending_confirmation",
        "not_scheduled",
        "excluded",
    ]
    stop_key: str | None = None
    reason_code: str | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def validate_disposition(self) -> "CoverageDispositionDTO":
        if self.status == "scheduled":
            if not self.stop_key:
                raise ValueError(
                    "scheduled disposition requires stop_key"
                )
            if self.reason_code is not None or self.rationale is not None:
                raise ValueError(
                    "scheduled disposition cannot carry omission reason"
                )
            return self
        if self.stop_key is not None:
            raise ValueError(
                "only a scheduled disposition may reference stop_key"
            )
        if self.status != "unhandled" and (
            not self.reason_code or not self.rationale
        ):
            raise ValueError(
                f"{self.status} disposition requires reason and rationale"
            )
        return self


class PlanningSubmissionDTO(ModelDTO):
    draft: WorkingDraftDTO
    dispositions: list[CoverageDispositionDTO]


def _to_domain_planning_submission(
    submission: PlanningSubmissionDTO,
    *,
    goal: JourneyGoal,
) -> PlanningSubmission:
    raw_submission = json.loads(submission.model_dump_json())
    draft_days: list[dict[str, object]] = []
    for raw_day in raw_submission["draft"]["days"]:
        day_number = int(raw_day["day_number"])
        raw_day["calendar_date"] = (
            goal.start_date + timedelta(days=day_number - 1)
        ).isoformat()
        for stop in raw_day["stops"]:
            stop["stop_id"] = stop.pop("stop_key")
        draft_days.append(raw_day)
    dispositions = raw_submission["dispositions"]
    for item in dispositions:
        item["stop_id"] = item.pop("stop_key")
    return PlanningSubmission.model_validate_json(
        json.dumps(
            {
                "draft": {
                    "draft_id": "runtime-owned",
                    "goal_revision_id": goal.goal_revision_id,
                    "revision": 1,
                    "days": draft_days,
                },
                "dispositions": dispositions,
            },
            ensure_ascii=False,
        )
    )


def _submit_plan_or_retry(
    runtime: AutonomousTripRuntime,
    submission: PlanningSubmissionDTO,
) -> None:
    try:
        domain_submission = _to_domain_planning_submission(
            submission,
            goal=runtime.goal,
        )
        runtime.submit_plan(domain_submission)
    except (PlanCommitmentError, ValidationError) as exc:
        raise ModelRetry(
            "The proposed plan violates the current Requirement Ledger or "
            f"Grounding Registry: {exc}. Re-read the affected planning "
            "context and submit a corrected complete plan."
        ) from exc


def _submit_grounding_or_retry(
    runtime: AutonomousTripRuntime, decision: GroundingDecisionDTO
) -> None:
    if decision.target_id not in runtime.candidate_sets:
        raise ModelRetry("Read this grounding target before submitting its decision.")
    try:
        proposal = GroundingDecisionProposal.model_validate_json(decision.model_dump_json())
        runtime.submit_grounding_decision(proposal)
    except (GroundingCompilationError, ValidationError) as exc:
        raise ModelRetry(
            f"Grounding decision does not match the retained candidate group: {exc}. "
            "Choose a candidate from the read group or request clarification."
        ) from exc


def _submit_grounding_batch_or_retry(
    runtime: AutonomousTripRuntime, decisions: list[GroundingDecisionDTO]
) -> None:
    try:
        proposals = tuple(
            GroundingDecisionProposal.model_validate_json(decision.model_dump_json())
            for decision in decisions
        )
        runtime.submit_grounding_decisions(proposals)
    except (ValueError, ValidationError) as exc:
        raise ModelRetry(
            f"Grounding batch was rejected before any decision was written: {exc}. "
            "Read every group first, use unique target IDs and retained candidate IDs, "
            "then resubmit the corrected complete batch."
        ) from exc


class DeepSeekRequirementInterpreter:
    """A constrained source-understanding call, not an additional domain Agent."""

    def __init__(
        self, model: Model, *, telemetry: RunTelemetry | None = None
    ) -> None:
        self.telemetry = telemetry
        self.agent = Agent(
            model,
            output_type=InterpretedRequirements,
            instructions=(
                "Extract a requirement ledger proposal from supplied travel sources. "
                "Place mentions must be exact source substrings with exact source_id, "
                "start, and end offsets. Include only real destinations, hotels, "
                "restaurants, shops, airports, and photo stops in mentions. Never put "
                "action words, time words, day words, generic meal words, or prose "
                "fragments into place mentions. Encode day and time language as "
                "day_assignment or time_window constraints linked to mention_key values. "
                "Encode explicit relaxed, low-intensity, compact, or maximum daily "
                "duration language as a pace constraint with a conservative "
                "max_day_minutes value. "
                "Encode explicit walking, taxi, transit, driving, or maximum "
                "walking-duration language as transport_preference; it is never "
                "a place mention. "
                "A maximum walking duration (e.g. 步行单段不要超过20分钟) only sets "
                "max_walk_minutes=20 and preferred_modes=[]; it does not request walking-only routes. "
                "Transport preferences use mode_policy='prefer': modes are optimization targets "
                "and allow other reasonable travel modes. Set mode_policy='only' solely for "
                "an explicit exclusive-mode instruction; strength alone never implies exclusivity. "
                "Walking duration is an independent numeric limit. "
                "For time_window and day_assignment, fixed_commitment defaults to false. "
                "Set it true only when its cited exact evidence explicitly states an already "
                "confirmed reservation or a fixed appointment/date that cannot be changed. "
                "普通上午/午餐/晚餐/分天要求 keep fixed_commitment=false even when strength=required. "
                "Use query only for a place that needs provider identity lookup; merge "
                "true duplicate mentions; use reference/not_a_place only as defensive "
                "classification when the input explicitly proposed a false place. "
                "Do not invent any name, source span, or user priority."
                " When sources include user_revision, treat later revisions and "
                "clarification answers as authoritative for conflicts while keeping "
                "the original evidence lineage."
            ),
            model_settings=(
                deepseek_v4_settings("lightweight")
                if isinstance(model, DeepSeekV4ChatModel)
                else None
            ),
            retries={"output": 3},
        )

    async def interpret(
        self,
        *,
        goal: JourneyGoal,
        sources: tuple[SourceDocument, ...],
    ) -> RequirementProposal:
        payload = {
            "goal": goal.model_dump(mode="json"),
            "sources": [
                {
                    "source_id": source.source_id,
                    "kind": source.kind.value,
                    "content": source.content,
                }
                for source in sources
            ],
        }
        result = await self.agent.run(
            json.dumps(payload, ensure_ascii=False),
            usage_limits=UsageLimits(request_limit=4),
        )
        if self.telemetry is not None:
            self.telemetry.record_model_usage(
                result.usage, role="requirement_interpreter"
            )
        proposal_id = "proposal_" + sha256(
            (
                goal.goal_revision_id
                + "|"
                + "|".join(source.content_hash for source in sources)
            ).encode("utf-8")
        ).hexdigest()[:20]
        return RequirementProposal.model_validate_json(
            json.dumps(
                {
                    "proposal_id": proposal_id,
                    "goal_revision_id": goal.goal_revision_id,
                    "destination": goal.destination,
                    "mentions": [
                        item.model_dump(mode="json")
                        for item in result.output.mentions
                    ],
                    "constraints": [
                        item.model_dump(mode="json")
                        for item in result.output.constraints
                    ],
                },
                ensure_ascii=False,
            )
        )


ROOT_AGENT_INSTRUCTIONS = """
You are Trajecta's only root TripPlannerAgent.
You decide place identity, route inclusion, day allocation, ordering, real meal stops,
stay durations, and repairs. The Runtime owns provider calls, IDs, fact ownership,
time arithmetic, coverage validation, versions, budgets, and release eligibility.

Use list_grounding_targets; each read returns exactly one grounding group. Submit a
decision with concrete name/city/category reasoning. Never choose an entrance, transit
station, auxiliary facility, arbitrary branch, or child merchant for a parent place.
Request clarification when the bounded group remains materially ambiguous.
Once several groups have been read, submit their clear decisions together through
submit_grounding_decisions. Batch independent reads in one model response; each read
still returns exactly one bounded group. Keep ambiguous groups for specific clarification.

After grounding, read planning context one day/page at a time. Every explicit place must
receive a disposition. A named restaurant must be a meal stop with its selected
candidate_id, not a duration placeholder. Use a unique local stop_key to connect each
scheduled disposition to its stop; Runtime replaces it with a persistent stop_id.
stop_key values must be globally unique across all days, including repeated hotel
anchors; use keys like d1_hotel_start, d1_hotel_lunch, d1_hotel_end, d2_hotel_start.
Every day must include a visible lodging or
airport start and end anchor and a non-empty title. Honor the structured day, time,
pace, and transport constraints in planning context. Prioritize fixed_commitment
reservations; ordinary preferences can retain specific review advice. A walking time
cap applies to walking legs and permits other transport modes. Choose sensible visit
durations and leave meal breaks using selected meal places or lodging when feasible;
do not stretch a visit solely to fill time. Set travel_mode_from_previous
on each affected stop so Runtime queries the intended provider route. For nearby places,
compare walking within the user's single-leg cap before choosing a car. When driving
would consume substantially more time than the visit or meal itself, query an alternative
transport route before finalizing and use the actual returned duration to repair the draft.
Merge adjacent stops at the same hotel when a departure anchor can also contain the
planned meal/rest period. mode_policy='prefer' is an optimization target; choose
reasonable alternatives using real route facts. mode_policy='only' limits route modes.
Submit a complete WorkingDraft, call assess_candidate, use its feedback to improve the
plan, then call finalize_candidate when ready to deliver. Review issues allow delivery;
use available budget to repair material experience problems before publishing.
Keep requests for assessment and publication. Reuse already
read context, batch independent tool calls, and make the smallest repair supported by
feedback instead of reopening completed work.
You may finish only after Runtime has published a strict release or entered waiting_user.
End the current response immediately after finalize_grounding succeeds, and also after a
non-publishable assess_candidate or finalize_candidate result. Runtime will start a fresh compact episode
with the same business state and only the relevant feedback; do not carry candidate
groups or an old draft forward in prose.
""".strip()


@dataclass(slots=True)
class RootAgentDeps:
    runtime: AutonomousTripRuntime


class RunLifecycleToolset(WrapperToolset[RootAgentDeps]):
    async def get_tools(self, ctx):
        if _runtime_is_terminal(ctx.deps.runtime):
            return {}
        return await super().get_tools(ctx)

    async def call_tool(self, name, tool_args, ctx, tool):
        runtime = ctx.deps.runtime
        if _runtime_is_terminal(runtime):
            return {
                "ok": True,
                "state": runtime.run_record.status.value,
                "release_id": runtime.release.release_id if runtime.release is not None else None,
            }
        call_id = uuid4().hex
        started_at = perf_counter()
        target = next((item for item in getattr(getattr(runtime, "query_plan", None), "targets", ())
                       if item.target_id == tool_args.get("target_id")), None)
        runtime._record_event({
            "type": "tool_started", "tool_name": name, "call_id": call_id,
            "object_name": target.query_text if target is not None else None,
            "context": {key: value for key, value in tool_args.items()
                        if key in {"target_id", "day_number", "offset", "limit"}},
        })
        outcome = "succeeded"
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        except _ProviderContextCheckpoint:
            raise
        except ModelRetry:
            outcome = "retry"
            raise
        except BaseException:
            outcome = "interrupted"
            raise
        finally:
            runtime._record_event({
                "type": "tool_finished", "tool_name": name, "call_id": call_id,
                "outcome": outcome,
                "duration_ms": round((perf_counter() - started_at) * 1_000),
            })


async def _finalize_candidate_or_retry(
    runtime: AutonomousTripRuntime,
):
    if runtime.grounding is None:
        raise ModelRetry(
            "Grounding is not finalized. Finish every grounding decision, "
            "then call finalize_grounding before planning."
        )
    if runtime.draft is None:
        raise ModelRetry(
            "No WorkingDraft has been committed. Read the planning context, "
            "submit a complete plan with dispositions for every explicit "
            "place, then call finalize_candidate."
        )
    if runtime.assessment is None:
        raise ModelRetry("Call assess_candidate and review its feedback before publication.")
    return await runtime.finalize_candidate()


class PydanticAIRootTripPlannerAgent:
    def __init__(
        self, model: Model, *, request_limit: int = 24, tool_calls_limit: int = 80
    ) -> None:
        if request_limit < 1 or tool_calls_limit < 1:
            raise ValueError("root agent usage limits must be positive")
        self.request_limit = request_limit
        self.tool_calls_limit = tool_calls_limit
        toolset = FunctionToolset()
        agent = Agent(
            model,
            deps_type=RootAgentDeps,
            output_type=str,
            instructions=ROOT_AGENT_INSTRUCTIONS,
            model_settings=(
                deepseek_v4_settings("root")
                if isinstance(model, DeepSeekV4ChatModel)
                else None
            ),
            retries={"tools": 2, "output": 3},
            toolsets=[RunLifecycleToolset(toolset)],
        )

        @toolset.tool
        async def list_grounding_targets(
            ctx: RunContext[RootAgentDeps],
        ) -> dict[str, object]:
            runtime = ctx.deps.runtime
            if runtime.grounding is not None:
                raise ModelRetry(
                    "Grounding is already finalized. Do not reopen candidate "
                    "selection; read planning context and revise the plan."
                )
            return {
                "all_target_ids": runtime.grounding_target_ids,
                "open_target_ids": runtime.open_grounding_target_ids,
            }

        @toolset.tool
        async def read_grounding_target(
            ctx: RunContext[RootAgentDeps], target_id: str
        ) -> dict[str, object]:
            if ctx.deps.runtime.grounding is not None:
                raise ModelRetry(
                    "Grounding is already finalized. Read planning context "
                    "instead."
                )
            if target_id not in ctx.deps.runtime.grounding_target_ids:
                raise ModelRetry("Unknown target_id. Use an ID returned by list_grounding_targets.")
            context = await ctx.deps.runtime.read_grounding_context(target_id)
            return context.model_dump(mode="json")

        @toolset.tool(sequential=True)
        async def submit_grounding_decision(
            ctx: RunContext[RootAgentDeps],
            decision: GroundingDecisionDTO,
        ) -> dict[str, object]:
            if ctx.deps.runtime.grounding is not None:
                raise ModelRetry(
                    "Grounding is already finalized. Revise only the plan."
                )
            _submit_grounding_or_retry(ctx.deps.runtime, decision)
            return {
                "ok": True,
                "remaining_target_ids": (
                    ctx.deps.runtime.open_grounding_target_ids
                ),
            }

        @toolset.tool(sequential=True)
        async def submit_grounding_decisions(
            ctx: RunContext[RootAgentDeps], decisions: list[GroundingDecisionDTO]
        ) -> dict[str, object]:
            runtime = ctx.deps.runtime
            if runtime.grounding is not None:
                raise ModelRetry("Grounding is already finalized. Revise only the plan.")
            _submit_grounding_batch_or_retry(runtime, decisions)
            return {"ok": True, "remaining_target_ids": runtime.open_grounding_target_ids}

        @toolset.tool(sequential=True)
        async def finalize_grounding(
            ctx: RunContext[RootAgentDeps],
        ) -> dict[str, object]:
            if ctx.deps.runtime.grounding is not None:
                raise ModelRetry(
                    "Grounding is already finalized. Continue with planning."
                )
            if ctx.deps.runtime.open_grounding_target_ids:
                raise ModelRetry(
                    "Grounding is incomplete. Read and decide the remaining targets: "
                    f"{ctx.deps.runtime.open_grounding_target_ids}"
                )
            ctx.deps.runtime.finalize_grounding()
            raise _ProviderContextCheckpoint("grounding_complete")

        @toolset.tool
        async def read_planning_context(
            ctx: RunContext[RootAgentDeps],
            day_number: int,
            obligation_offset: int = 0,
        ) -> dict[str, object]:
            if ctx.deps.runtime.grounding is None:
                raise ModelRetry("Finalize grounding before reading planning context.")
            if not 1 <= day_number <= ctx.deps.runtime.goal.days:
                raise ModelRetry(f"day_number must be between 1 and {ctx.deps.runtime.goal.days}.")
            if obligation_offset < 0:
                raise ModelRetry("obligation_offset must be zero or positive.")
            context = ctx.deps.runtime.read_planning_context(
                day_number=day_number,
                obligation_offset=obligation_offset,
            )
            return context.model_dump(mode="json")

        @toolset.tool(sequential=True)
        async def submit_plan(
            ctx: RunContext[RootAgentDeps],
            submission: PlanningSubmissionDTO,
        ) -> dict[str, object]:
            _submit_plan_or_retry(ctx.deps.runtime, submission)
            report = ctx.deps.runtime.ledger.coverage_report()
            return {
                "ok": True,
                "coverage_ratio": report.coverage_ratio,
                "open_obligation_ids": report.open_obligation_ids,
            }

        @toolset.tool(sequential=True)
        async def assess_candidate(ctx: RunContext[RootAgentDeps]) -> dict[str, object]:
            runtime = ctx.deps.runtime
            if runtime.grounding is None or runtime.draft is None:
                raise ModelRetry("Submit a grounded WorkingDraft before assessing it.")
            assessment = await runtime.assess_candidate()
            if assessment is None or not assessment.may_publish:
                raise _ProviderContextCheckpoint("candidate_not_publishable")
            return {
                "may_publish": assessment.may_publish,
                "state": assessment.state.value,
                "issues": [issue.model_dump(mode="json") for issue in assessment.issues],
            }

        @toolset.tool(sequential=True)
        async def finalize_candidate(
            ctx: RunContext[RootAgentDeps],
        ) -> dict[str, object]:
            runtime = ctx.deps.runtime
            assessment = await _finalize_candidate_or_retry(runtime)
            if assessment is None:
                raise _ProviderContextCheckpoint("fact_gap")
            if not assessment.may_publish:
                raise _ProviderContextCheckpoint(
                    "candidate_not_publishable"
                )
            return {
                "ok": assessment.may_publish,
                "state": assessment.state.value,
                "issues": [
                    issue.model_dump(mode="json")
                    for issue in assessment.issues
                ],
                "release_id": (
                    runtime.release.release_id
                    if runtime.release is not None
                    else None
                ),
            }

        @toolset.tool(sequential=True)
        async def request_clarification(
            ctx: RunContext[RootAgentDeps],
        ) -> dict[str, object]:
            questions = ctx.deps.runtime.request_clarification()
            return {"ok": True, "questions": questions}

        @agent.output_validator
        async def require_runtime_terminal(
            ctx: RunContext[RootAgentDeps], output: str
        ) -> str:
            runtime = ctx.deps.runtime
            if runtime.release is not None:
                return output
            if runtime.run_record.status.value == "waiting_user":
                return output
            if runtime.grounding is not None and runtime.draft is None:
                return output
            if (
                runtime.assessment is not None
                and not runtime.assessment.may_publish
            ):
                return output
            if (
                runtime.fact_gap_report is not None
                and runtime.fact_gap_report.gaps
            ):
                return output
            if runtime.assessment is not None:
                feedback = [
                    {
                        "code": issue.code,
                        "message": issue.message,
                        "recommendation": issue.recommendation,
                    }
                    for issue in runtime.assessment.issues
                ]
            elif runtime.fact_gap_report is not None:
                feedback = runtime.fact_gap_report.model_dump(
                    mode="json"
                )["gaps"]
            else:
                feedback = [
                    {
                        "code": "goal_not_closed",
                        "message": (
                            "No strict delivery assessment or clarification exists."
                        ),
                        "recommendation": (
                            "Continue with the next local-context tool."
                        ),
                    }
                ]
            raise ModelRetry(
                "Runtime has not reached a terminal delivery state. "
                f"Use this feedback: {json.dumps(feedback, ensure_ascii=False)}"
            )

        self.agent = agent

    async def run(self, runtime: AutonomousTripRuntime) -> None:
        # Compact episodes share one execution budget. A business checkpoint
        # must not reset the provider allowance and multiply API spending.
        requests_used = 0
        tools_used = 0
        request_limit = getattr(self, "request_limit", 24)
        tool_calls_limit = getattr(self, "tool_calls_limit", 80)
        message_history = None
        if runtime.provider_transcript_json is not None:
            try:
                message_history = ModelMessagesTypeAdapter.validate_json(
                    runtime.provider_transcript_json
                )
            except ValueError:
                message_history = None
        for _episode in range(6):
            if _should_request_grounding_clarification(runtime):
                runtime.request_clarification()
                return
            if _should_request_fact_clarification(runtime):
                runtime.request_clarification()
                return
            if requests_used >= request_limit or tools_used >= tool_calls_limit:
                _record_budget_pause(runtime, requests_used, tools_used)
                return
            user_prompt = (
                None
                if message_history
                else _root_episode_prompt(runtime)
            )
            if user_prompt is not None:
                user_prompt += (
                    f" This execution has {request_limit - requests_used} model requests "
                    f"and {tool_calls_limit - tools_used} tool calls remaining. "
                    "Reserve requests for candidate assessment and final publication."
                )
            checkpoint_requested = False
            try:
                try:
                    async with self.agent.iter(
                        user_prompt,
                        deps=RootAgentDeps(runtime=runtime),
                        message_history=message_history,
                        usage_limits=UsageLimits(
                            request_limit=request_limit - requests_used,
                            tool_calls_limit=tool_calls_limit - tools_used,
                        ),
                    ) as agent_run:
                        terminal_request = None
                        try:
                            async for node in agent_run:
                                if _runtime_is_terminal(runtime):
                                    if Agent.is_model_request_node(node):
                                        terminal_request = node.request
                                    break
                                if Agent.is_model_request_node(node):
                                    runtime._record_event({"type": "model_request_started"})
                                elif Agent.is_call_tools_node(node):
                                    runtime._record_event({"type": "model_request_finished"})
                        finally:
                            telemetry = getattr(
                                runtime, "telemetry", None
                            )
                            usage = getattr(agent_run, "usage", None)
                            requests_used += int(getattr(usage, "requests", 0))
                            tools_used += int(getattr(usage, "tool_calls", 0))
                            if (
                                telemetry is not None
                                and usage is not None
                            ):
                                telemetry.record_model_usage(
                                    usage, role="trip_planner_root"
                                )
                            transcript = agent_run.all_messages_json()
                            if terminal_request is not None:
                                messages = ModelMessagesTypeAdapter.validate_json(transcript)
                                messages.append(terminal_request)
                                transcript = ModelMessagesTypeAdapter.dump_json(messages)
                            runtime.persist_provider_transcript(transcript)
                except _ProviderContextCheckpoint:
                    checkpoint_requested = True
            except UsageLimitExceeded:
                _record_budget_pause(runtime, requests_used, tools_used)
                return
            except UnexpectedModelBehavior as exc:
                match = re.fullmatch(r"Tool '([a-z_]+)' exceeded max retries count of \d+", str(exc))
                if match is None or not isinstance(exc.__cause__, (ModelRetry, ValidationError)):
                    raise
                # The SDK exhausted corrections for a tool input. Resume from
                # durable domain state with a valid fresh provider transcript.
                runtime.persist_provider_transcript(ModelMessagesTypeAdapter.dump_json([]))
                cause = exc.__cause__
                if isinstance(cause, ValidationError):
                    repair_feedback = json.dumps([
                        {"location": ".".join(str(part) for part in issue["loc"]), "type": issue["type"], "message": issue["msg"]}
                        for issue in cause.errors(include_input=False, include_url=False)[:10]
                    ], ensure_ascii=False)[:2_000]
                else:
                    repair_feedback = str(cause)[:2_000]
                runtime.persist_tool_repair_feedback(repair_feedback)
                runtime._record_event({
                    "type": "planning_repair_paused",
                    "reason_code": "tool_input_retries_exhausted",
                    "tool_name": match.group(1),
                    "repair_feedback": repair_feedback,
                    "message": "本轮工具输入修正次数已用完，进度已保存，可继续当前运行。",
                })
                return
            if _runtime_is_terminal(runtime):
                return
            if (
                not checkpoint_requested
                and not _runtime_has_compaction_checkpoint(runtime)
            ):
                return
            message_history = None
            runtime.persist_provider_transcript(
                ModelMessagesTypeAdapter.dump_json([])
            )


def _record_budget_pause(
    runtime: AutonomousTripRuntime, requests_used: int, tools_used: int
) -> None:
    record = getattr(runtime, "_record_event", None)
    if record is not None:
        record({
            "type": "planning_budget_paused",
            "reason_code": "root_execution_budget_exhausted",
            "model_requests": requests_used,
            "tool_calls": tools_used,
            "message": "本轮规划调用预算已用完，已保存进度，可继续当前运行。",
        })


def _runtime_is_terminal(runtime: AutonomousTripRuntime) -> bool:
    return (
        getattr(runtime, "release", None) is not None
        or getattr(
            getattr(runtime, "run_record", None), "status", None
        )
        is not None
        and runtime.run_record.status.value in {"waiting_user", "succeeded", "failed", "cancelled"}
    )


def _should_request_grounding_clarification(
    runtime: AutonomousTripRuntime,
) -> bool:
    grounding = getattr(runtime, "grounding", None)
    return bool(
        grounding is not None
        and any(
            resolution.status.value != "selected"
            for resolution in getattr(grounding, "resolutions", ())
        )
    )


def _runtime_has_compaction_checkpoint(
    runtime: AutonomousTripRuntime,
) -> bool:
    grounding = getattr(runtime, "grounding", None)
    draft = getattr(runtime, "draft", None)
    assessment = getattr(runtime, "assessment", None)
    fact_gap_report = getattr(runtime, "fact_gap_report", None)
    return (
        (grounding is not None and draft is None)
        or (
            assessment is not None
            and not assessment.may_publish
        )
        or bool(
            fact_gap_report is not None
            and fact_gap_report.gaps
        )
    )


def _should_request_fact_clarification(
    runtime: AutonomousTripRuntime,
) -> bool:
    assessment = getattr(runtime, "assessment", None)
    fact_gap_report = getattr(runtime, "fact_gap_report", None)
    if (
        assessment is None
        or assessment.may_publish
        or fact_gap_report is None
        or not fact_gap_report.gaps
    ):
        return False
    if any(
        getattr(gap, "failure_code", None)
        == "planned_visit_operationally_incompatible"
        for gap in fact_gap_report.gaps
    ):
        return False
    blocking_issues = tuple(
        issue
        for issue in assessment.issues
        if issue.severity.value == "blocking"
    )
    return bool(blocking_issues) and all(
        issue.code == "operational_fact_missing"
        or issue.code == "fact_status_inconsistent"
        or issue.code.startswith("fact_gap_")
        for issue in blocking_issues
    )


def _root_episode_prompt(runtime: AutonomousTripRuntime) -> str:
    base = (
        f"Plan {runtime.goal.destination} from "
        f"{runtime.goal.start_date.isoformat()} for "
        f"{runtime.goal.days} day(s). Work only through Runtime tools."
    )
    repair_feedback = getattr(runtime, "last_tool_repair_feedback", None)
    if repair_feedback:
        base += " Correct the last rejected tool input using this saved feedback: " + repair_feedback
    if runtime.grounding is None:
        return base + " Complete the bounded grounding ledger first."
    if runtime.draft is None:
        return (
            base
            + " Grounding is complete. Read only the required day/page planning "
            "contexts, submit a complete plan, assess it, then publish when ready."
        )
    if runtime.assessment is None and runtime.fact_gap_report is None:
        return base + " A WorkingDraft is already committed. Call assess_candidate before deciding on repair or publication."
    feedback: list[dict[str, object]] = []
    if runtime.assessment is not None:
        feedback.extend(
            {
                "code": issue.code,
                "message": issue.message,
                "recommendation": issue.recommendation,
                "days": issue.day_numbers,
                "places": issue.place_names,
            }
            for issue in runtime.assessment.issues[:8]
        )
    elif runtime.fact_gap_report is not None:
        feedback.extend(
            {
                "code": gap.failure_code,
                "message": gap.failure_message,
                "day": gap.day_number,
                "places": gap.place_names,
                "impact": gap.impact,
            }
            for gap in runtime.fact_gap_report.gaps[:8]
        )
    if runtime.assessment is not None and runtime.assessment.may_publish:
        return (
            base + " The candidate is assessed and publishable. Improve useful review feedback "
            "within the remaining budget, or call finalize_candidate to deliver it: "
            + json.dumps(feedback, ensure_ascii=False, default=str)
        )
    return (
        base
        + " The previous candidate was not publishable. Revise only what the "
        "following run-bound feedback requires, or request specific user "
        "clarification when external evidence cannot be resolved: "
        + json.dumps(feedback, ensure_ascii=False, default=str)
    )
