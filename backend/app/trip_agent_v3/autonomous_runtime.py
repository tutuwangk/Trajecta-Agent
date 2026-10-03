from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json

from app.trip_agent_v3.assembly import assemble_candidate_snapshot
from app.trip_agent_v3.candidates import intake_provider_candidates
from app.trip_agent_v3.commitments import commit_plan_dispositions
from app.trip_agent_v3.context import (
    build_grounding_context,
    build_planning_context,
)
from app.trip_agent_v3.delivery import assess_delivery, publish_release
from app.trip_agent_v3.domain.delivery import (
    CandidateSnapshot,
    DeliveryAssessment,
    ReleaseRecord,
    RunRecord,
    RunStatus,
)
from app.trip_agent_v3.domain.execution import (
    JourneyGoal,
    PlanningSubmission,
)
from app.trip_agent_v3.domain.facts import (
    FactGapReport,
    FactNeed,
    FactNeedKind,
    FactNeedPlan,
    FactNeedStatus,
    FactResolution,
    OperationalFact,
    RouteFact,
)
from app.trip_agent_v3.domain.grounding import (
    CandidateSet,
    GroundingCandidate,
    GroundingRegistry,
    ResolutionStatus,
)
from app.trip_agent_v3.domain.plan import CompiledTimeline, WorkingDraft
from app.trip_agent_v3.domain.query import QueryPlan
from app.trip_agent_v3.domain.requirements import (
    DayAssignmentRequirement,
    PlaceRole,
    QueryDecision,
    RequirementLedger,
    TimeWindowRequirement,
)
from app.trip_agent_v3.domain.runtime import LocalAgentContext
from app.trip_agent_v3.domain.sources import SourceDocument
from app.trip_agent_v3.fact_needs import (
    build_fact_gap_report,
    build_fact_need_plan,
    build_operational_fact_need_plan,
)
from app.trip_agent_v3.grounding import (
    GroundingDecisionProposal,
    compile_grounding_registry,
)
from app.trip_agent_v3.ports import (
    FactProviderPort,
    PlaceSearchProviderPort,
    RequirementInterpreterPort,
    RootTripPlannerAgentPort,
)
from app.trip_agent_v3.repository import SqliteTripAgentV3Repository
from app.trip_agent_v3.requirements import (
    build_query_plan,
    compile_requirement_ledger,
)
from app.trip_agent_v3.telemetry import RunTelemetry
from app.trip_agent_v3.timeline import compile_timeline


class AutonomousRuntimeError(RuntimeError):
    pass


class AutonomousRuntimeCancelled(AutonomousRuntimeError):
    pass


def _input_gap_questions(
    goal: JourneyGoal,
    ledger: RequirementLedger,
) -> tuple[str, ...]:
    schedulable_roles = {
        PlaceRole.VISIT,
        PlaceRole.MEAL,
        PlaceRole.LODGING,
        PlaceRole.SHOPPING,
        PlaceRole.PHOTO,
        PlaceRole.AIRPORT,
    }
    questions = [
        (
            f"请补充“{obligation.mention}”的具体地址、门店或链接，"
            "或明确允许不安排。"
        )
        for obligation in ledger.obligations
        if obligation.explicit
        and obligation.role in schedulable_roles
        and obligation.query_decision is QueryDecision.CLARIFY
    ]
    has_lodging = any(
        obligation.role is PlaceRole.LODGING
        and obligation.query_decision
        in {QueryDecision.QUERY, QueryDecision.MERGE}
        for obligation in ledger.obligations
    )
    has_airport = any(
        obligation.role is PlaceRole.AIRPORT
        and obligation.query_decision
        in {QueryDecision.QUERY, QueryDecision.MERGE}
        for obligation in ledger.obligations
    )
    if not (has_lodging or (goal.days == 1 and has_airport)):
        questions.append(
            "请提供每天出发和返回的具体酒店／住宿地点；"
            "如果是当日机场往返，请提供具体机场。"
        )
    for airport in (
        obligation
        for obligation in ledger.obligations
        if obligation.role is PlaceRole.AIRPORT
        and obligation.query_decision
        in {QueryDecision.QUERY, QueryDecision.MERGE}
    ):
        assigned_days = {
            constraint.day_number
            for constraint in ledger.constraints
            if isinstance(constraint, DayAssignmentRequirement)
            and airport.obligation_id
            in constraint.subject_obligation_ids
        }
        timed_days = {
            constraint.day_number
            for constraint in ledger.constraints
            if isinstance(constraint, TimeWindowRequirement)
            and airport.obligation_id
            in constraint.subject_obligation_ids
        }
        missing_days = assigned_days - timed_days
        if missing_days:
            questions.extend(
                (
                    f"请补充第{day_number}天在“{airport.mention}”的"
                    "航班落地或起飞时间，避免系统虚构机场出发／抵达时刻。"
                )
                for day_number in sorted(missing_days)
            )
        elif not assigned_days and not timed_days:
            questions.append(
                f"请补充“{airport.mention}”的航班日期及落地／起飞时间，"
                "避免系统虚构机场出发或抵达时刻。"
            )
    return tuple(dict.fromkeys(questions))


_CACHEABLE_FACT_FAILURE_CODES = frozenset(
    {
        "operational_evidence_insufficient",
        "planned_visit_operationally_incompatible",
        "operational_sources_missing",
        "scheduled_candidate_missing",
    }
)


def _artifact_id(prefix: str, *parts: str) -> str:
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _input_fingerprint(
    goal: JourneyGoal, sources: tuple[SourceDocument, ...]
) -> str:
    identity = "|".join(
        (
            goal.model_dump_json(),
            *(source.content_hash for source in sources),
        )
    )
    return sha256(identity.encode("utf-8")).hexdigest()


def _restore_model(model_type, payload):
    return model_type.model_validate_json(
        json.dumps(payload, ensure_ascii=False)
    )


def _resolved_plan(
    plan: FactNeedPlan,
    resolutions: tuple[FactResolution, ...],
) -> FactNeedPlan:
    return FactNeedPlan(
        plan_id=plan.plan_id,
        draft_id=plan.draft_id,
        draft_revision=plan.draft_revision,
        needs=tuple(resolution.need for resolution in resolutions),
    )


def _same_fact_ownership(
    *, original: object, resolved: object
) -> bool:
    if not isinstance(original, type(resolved)):
        return False
    assert isinstance(original, FactNeed)
    assert isinstance(resolved, FactNeed)
    return (
        resolved.kind is original.kind
        and resolved.day_number == original.day_number
        and resolved.candidate_ids == original.candidate_ids
        and resolved.stop_ids == original.stop_ids
        and resolved.requested_mode is original.requested_mode
        and resolved.visit_at == original.visit_at
    )


def _reusable_candidate_checkpoint(
    payload: dict[str, object],
    query_plan: QueryPlan,
) -> dict[str, object] | None:
    raw_plan = payload.get("query_plan")
    if raw_plan is None:
        return None
    try:
        old_plan = _restore_model(QueryPlan, raw_plan)
        old_sets = tuple(
            _restore_model(CandidateSet, item)
            for item in (payload.get("candidate_sets") or [])
        )
    except (TypeError, ValueError):
        return None
    old_targets = {target.target_id: target for target in old_plan.targets}
    current_targets = {
        target.target_id: target for target in query_plan.targets
    }
    reusable_ids = {
        target_id
        for target_id, current in current_targets.items()
        if old_targets.get(target_id) == current
    }
    reusable_sets = [
        candidate_set.model_dump(mode="json")
        for candidate_set in old_sets
        if candidate_set.target_id in reusable_ids
    ]
    return (
        {"candidate_sets": reusable_sets}
        if reusable_sets
        else None
    )


@dataclass(frozen=True, slots=True)
class RuntimeExecutionResult:
    run: RunRecord
    requirement_ledger: RequirementLedger
    query_plan: QueryPlan
    grounding: GroundingRegistry | None
    draft: WorkingDraft | None
    fact_need_plan: FactNeedPlan | None
    fact_gap_report: FactGapReport | None
    timeline: CompiledTimeline | None
    candidate: CandidateSnapshot | None
    assessment: DeliveryAssessment | None
    release: ReleaseRecord | None
    clarification_questions: tuple[str, ...]
    events: tuple[dict[str, object], ...]


class AutonomousTripRuntime:
    """Controlled state and tools used by the single root TripPlannerAgent."""

    def __init__(
        self,
        *,
        run: RunRecord,
        goal: JourneyGoal,
        ledger: RequirementLedger,
        query_plan: QueryPlan,
        place_provider: PlaceSearchProviderPort,
        fact_provider: FactProviderPort,
        repository: SqliteTripAgentV3Repository | None = None,
        input_fingerprint: str,
        checkpoint_payload: dict[str, object] | None = None,
        telemetry: RunTelemetry | None = None,
    ) -> None:
        self.run_record = run
        self.goal = goal
        self.ledger = ledger
        self.query_plan = query_plan
        self.place_provider = place_provider
        self.fact_provider = fact_provider
        self.repository = repository
        self.input_fingerprint = input_fingerprint
        self.telemetry = telemetry or RunTelemetry()
        self.input_gap_questions = _input_gap_questions(goal, ledger)
        stored_transcript = (
            repository.get_provider_transcript(run.run_id)
            if repository is not None
            else None
        )
        self.provider_transcript_json = (
            stored_transcript[1]
            if stored_transcript is not None
            and stored_transcript[0] == input_fingerprint
            else None
        )
        self.candidate_sets: dict[str, CandidateSet] = {}
        self._grounding_read_locks: dict[str, asyncio.Lock] = {}
        self.grounding_decisions: dict[str, GroundingDecisionProposal] = {}
        self.grounding: GroundingRegistry | None = None
        self.draft: WorkingDraft | None = None
        self.fact_need_plan: FactNeedPlan | None = None
        self.fact_resolutions: dict[str, FactResolution] = {}
        self.fact_gap_report: FactGapReport | None = None
        self.timeline: CompiledTimeline | None = None
        self.candidate: CandidateSnapshot | None = None
        self.assessment: DeliveryAssessment | None = None
        self.release: ReleaseRecord | None = None
        self.clarification_questions: tuple[str, ...] = ()
        self.last_tool_repair_feedback: str | None = None
        self.events: list[dict[str, object]] = []
        if checkpoint_payload is not None:
            self._restore_checkpoint(checkpoint_payload)

    def _restore_checkpoint(self, payload: dict[str, object]) -> None:
        candidate_sets = payload.get("candidate_sets") or []
        decisions = payload.get("grounding_decisions") or []
        fact_resolutions = payload.get("fact_resolutions") or []
        self.candidate_sets = {
            item.target_id: item
            for item in (
                _restore_model(CandidateSet, raw)
                for raw in candidate_sets
            )
        }
        self.grounding_decisions = {
            item.target_id: item
            for item in (
                _restore_model(GroundingDecisionProposal, raw)
                for raw in decisions
            )
        }
        self.fact_resolutions = {
            item.need.need_id: item
            for item in (
                _restore_model(FactResolution, raw)
                for raw in fact_resolutions
            )
        }
        grounding = payload.get("grounding")
        draft = payload.get("draft")
        restored_ledger = payload.get("ledger")
        if restored_ledger is not None:
            self.ledger = _restore_model(
                RequirementLedger, restored_ledger
            )
        if grounding is not None:
            self.grounding = _restore_model(
                GroundingRegistry, grounding
            )
        if draft is not None:
            self.draft = _restore_model(WorkingDraft, draft)
        repair_feedback = payload.get("last_tool_repair_feedback")
        if isinstance(repair_feedback, str):
            self.last_tool_repair_feedback = repair_feedback[:2_000]

    def _persist_checkpoint(self) -> None:
        if self.repository is None:
            return
        self.repository.save_checkpoint(
            run_id=self.run_record.run_id,
            input_fingerprint=self.input_fingerprint,
            payload={
                "ledger": self.ledger.model_dump(mode="json"),
                "query_plan": self.query_plan.model_dump(mode="json"),
                "candidate_sets": [
                    self.candidate_sets[target_id].model_dump(mode="json")
                    for target_id in self.grounding_target_ids
                    if target_id in self.candidate_sets
                ],
                "grounding_decisions": [
                    self.grounding_decisions[target_id].model_dump(
                        mode="json"
                    )
                    for target_id in self.grounding_target_ids
                    if target_id in self.grounding_decisions
                ],
                "grounding": (
                    self.grounding.model_dump(mode="json")
                    if self.grounding is not None
                    else None
                ),
                "draft": (
                    self.draft.model_dump(mode="json")
                    if self.draft is not None
                    else None
                ),
                "fact_resolutions": [
                    self.fact_resolutions[need_id].model_dump(mode="json")
                    for need_id in sorted(self.fact_resolutions)
                ],
                "last_tool_repair_feedback": self.last_tool_repair_feedback,
            },
        )

    def persist_provider_transcript(self, payload: bytes) -> None:
        self.provider_transcript_json = payload
        if self.repository is not None:
            self.repository.save_provider_transcript(
                run_id=self.run_record.run_id,
                input_fingerprint=self.input_fingerprint,
                payload=payload,
            )

    def persist_tool_repair_feedback(self, feedback: str) -> None:
        self.last_tool_repair_feedback = feedback[:2_000]
        self._persist_checkpoint()

    def _record_event(self, event: dict[str, object]) -> None:
        self.events.append(event)
        if self.repository is not None:
            self.repository.append_event(self.run_record.run_id, event)

    def _ensure_not_cancelled(self) -> None:
        if self.repository is not None:
            current = self.repository.get_run(self.run_record.run_id)
            if current is not None:
                self.run_record = current
        if self.run_record.status is RunStatus.CANCELLED:
            raise AutonomousRuntimeCancelled(self.run_record.run_id)

    @property
    def grounding_target_ids(self) -> tuple[str, ...]:
        return tuple(target.target_id for target in self.query_plan.targets)

    @property
    def open_grounding_target_ids(self) -> tuple[str, ...]:
        return tuple(
            target_id
            for target_id in self.grounding_target_ids
            if target_id not in self.grounding_decisions
        )

    async def read_grounding_context(
        self, target_id: str
    ) -> LocalAgentContext:
        # The root provider may issue parallel tool calls for the same target.
        # Serialize its cache fill so a bounded target consumes one map query.
        lock = self._grounding_read_locks.setdefault(target_id, asyncio.Lock())
        async with lock:
            return await self._read_grounding_context(target_id)

    async def _read_grounding_context(
        self, target_id: str
    ) -> LocalAgentContext:
        self._ensure_not_cancelled()
        if self.grounding is not None:
            raise AutonomousRuntimeError(
                "grounding is already finalized; continue with planning"
            )
        target = next(
            (
                item
                for item in self.query_plan.targets
                if item.target_id == target_id
            ),
            None,
        )
        if target is None:
            raise AutonomousRuntimeError(
                f"unknown grounding target: {target_id}"
            )
        candidate_set = self.candidate_sets.get(target_id)
        if candidate_set is None:
            self.telemetry.record_provider_call("place_search")
            self.telemetry.place_search_by_target[target_id] = (
                self.telemetry.place_search_by_target.get(target_id, 0) + 1
            )
            raw_results = await self.place_provider.search(target)
            candidate_set = intake_provider_candidates(
                target=target,
                provider="amap",
                results=raw_results,
            )
            self.candidate_sets[target_id] = candidate_set
            self.telemetry.provider_result_count += (
                candidate_set.provider_result_count
            )
            self.telemetry.retained_candidate_count += len(
                candidate_set.candidates
            )
            self.telemetry.excluded_candidate_count += (
                candidate_set.excluded_result_count
            )
            self.telemetry.truncated_candidate_count += (
                candidate_set.truncated_result_count
            )
            self._record_event(
                {
                    "type": "candidate_group_ready",
                    "target_id": target_id,
                    "provider_result_count": candidate_set.provider_result_count,
                    "retained_candidate_count": len(
                        candidate_set.candidates
                    ),
                    "excluded_result_count": (
                        candidate_set.excluded_result_count
                    ),
                }
            )
            self._persist_checkpoint()
        context = build_grounding_context(
            run_id=self.run_record.run_id,
            ledger=self.ledger,
            active_candidate_set=candidate_set,
        )
        self.telemetry.record_context(context.model_dump_json())
        return context

    def submit_grounding_decision(
        self, decision: GroundingDecisionProposal
    ) -> None:
        self._ensure_not_cancelled()
        if self.grounding is not None:
            raise AutonomousRuntimeError(
                "grounding is already finalized; continue with planning"
            )
        candidate_set = self.candidate_sets.get(decision.target_id)
        target = next(
            (
                item
                for item in self.query_plan.targets
                if item.target_id == decision.target_id
            ),
            None,
        )
        if candidate_set is None or target is None:
            raise AutonomousRuntimeError(
                "read a grounding target before deciding it"
            )
        one_target_plan = QueryPlan(
            plan_id=self.query_plan.plan_id,
            ledger_id=self.query_plan.ledger_id,
            ledger_revision=self.query_plan.ledger_revision,
            targets=(target,),
        )
        compile_grounding_registry(
            registry_id="grounding-decision-check",
            query_plan=one_target_plan,
            candidate_sets=(candidate_set,),
            decisions=(decision,),
        )
        self.grounding_decisions[decision.target_id] = decision
        self.grounding = None
        self._record_event(
            {
                "type": "grounding_decision_submitted",
                "target_id": decision.target_id,
                "status": decision.status.value,
            }
        )
        self._persist_checkpoint()

    def submit_grounding_decisions(
        self, decisions: tuple[GroundingDecisionProposal, ...]
    ) -> None:
        """Validate the complete batch before applying existing bounded writes."""
        self._ensure_not_cancelled()
        if self.grounding is not None:
            raise AutonomousRuntimeError("grounding is already finalized; continue with planning")
        if not 1 <= len(decisions) <= 20:
            raise ValueError("grounding batch must contain between 1 and 20 decisions")
        target_ids = tuple(decision.target_id for decision in decisions)
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("grounding batch target ids must be unique")
        targets = {target.target_id: target for target in self.query_plan.targets}
        if any(target_id not in targets or target_id not in self.candidate_sets for target_id in target_ids):
            raise ValueError("read every grounding target before submitting the batch")
        compile_grounding_registry(
            registry_id="grounding-batch-check",
            query_plan=QueryPlan(
                plan_id=self.query_plan.plan_id,
                ledger_id=self.query_plan.ledger_id,
                ledger_revision=self.query_plan.ledger_revision,
                targets=tuple(targets[target_id] for target_id in target_ids),
            ),
            candidate_sets=tuple(self.candidate_sets[target_id] for target_id in target_ids),
            decisions=decisions,
        )
        for decision in decisions:
            self.submit_grounding_decision(decision)

    def finalize_grounding(self) -> GroundingRegistry:
        self._ensure_not_cancelled()
        if self.grounding is not None:
            raise AutonomousRuntimeError(
                "grounding is already finalized; continue with planning"
            )
        missing_sets = set(self.grounding_target_ids) - self.candidate_sets.keys()
        missing_decisions = (
            set(self.grounding_target_ids)
            - self.grounding_decisions.keys()
        )
        if missing_sets or missing_decisions:
            raise AutonomousRuntimeError(
                "grounding is incomplete: "
                f"unread={sorted(missing_sets)}, "
                f"undecided={sorted(missing_decisions)}"
            )
        self.grounding = compile_grounding_registry(
            registry_id=_artifact_id(
                "grounding", self.run_record.run_id, self.ledger.ledger_id
            ),
            query_plan=self.query_plan,
            candidate_sets=tuple(
                self.candidate_sets[target_id]
                for target_id in self.grounding_target_ids
            ),
            decisions=tuple(
                self.grounding_decisions[target_id]
                for target_id in self.grounding_target_ids
            ),
        )
        self.telemetry.selected_grounding_count = sum(
            item.status is ResolutionStatus.SELECTED
            for item in self.grounding.resolutions
        )
        self.telemetry.unresolved_grounding_count = (
            len(self.grounding.resolutions)
            - self.telemetry.selected_grounding_count
        )
        self._record_event(
            {
                "type": "grounding_registry_compiled",
                "selected_count": sum(
                    item.status is ResolutionStatus.SELECTED
                    for item in self.grounding.resolutions
                ),
                "unresolved_count": sum(
                    item.status is not ResolutionStatus.SELECTED
                    for item in self.grounding.resolutions
                ),
            }
        )
        self._persist_checkpoint()
        return self.grounding

    def read_planning_context(
        self, *, day_number: int, obligation_offset: int = 0
    ) -> LocalAgentContext:
        self._ensure_not_cancelled()
        if self.grounding is None:
            raise AutonomousRuntimeError(
                "finalize grounding before reading planning context"
            )
        if day_number < 1 or day_number > self.goal.days:
            raise AutonomousRuntimeError(
                f"day number is outside trip goal: {day_number}"
            )
        feedback: list[str] = []
        if self.last_tool_repair_feedback is not None:
            feedback.append(self.last_tool_repair_feedback)
        if self.fact_gap_report is not None:
            feedback.extend(
                (
                    f"{gap.failure_code}: "
                    f"{' → '.join(gap.place_names)}: "
                    f"{gap.failure_message}"
                )[:1_000]
                for gap in self.fact_gap_report.gaps
                if gap.day_number == day_number
            )
        if self.assessment is not None:
            feedback.extend(
                (
                    f"{issue.code}: {issue.message} "
                    f"建议：{issue.recommendation}"
                )[:1_000]
                for issue in self.assessment.issues
                if not issue.day_numbers
                or day_number in issue.day_numbers
            )
        context = build_planning_context(
            run_id=self.run_record.run_id,
            ledger=self.ledger,
            grounding=self.grounding,
            active_day_number=day_number,
            obligation_offset=obligation_offset,
            timeline=self.timeline,
            feedback=tuple(dict.fromkeys(feedback)),
        )
        self.telemetry.record_context(context.model_dump_json())
        return context

    def submit_plan(self, submission: PlanningSubmission) -> None:
        if self.release is not None:
            return
        self._ensure_not_cancelled()
        if self.grounding is None:
            raise AutonomousRuntimeError(
                "finalize grounding before submitting a plan"
            )
        submission = self._normalize_submission(submission)
        if len(submission.draft.days) != self.goal.days:
            raise AutonomousRuntimeError(
                "draft day count does not match trip goal"
            )
        committed = commit_plan_dispositions(
            ledger=self.ledger,
            grounding=self.grounding,
            draft=submission.draft,
            dispositions=submission.dispositions,
        )
        self.ledger = committed
        self.draft = submission.draft
        self.last_tool_repair_feedback = None
        self.telemetry.disposition_counts = {}
        for disposition in committed.dispositions:
            status = disposition.status.value
            self.telemetry.disposition_counts[status] = (
                self.telemetry.disposition_counts.get(status, 0) + 1
            )
        self.telemetry.stop_count = sum(
            len(day.stops) for day in self.draft.days
        )
        self.telemetry.leg_count = sum(
            max(len(day.stops) - 1, 0) for day in self.draft.days
        )
        self.fact_need_plan = None
        self.fact_gap_report = None
        self.timeline = None
        self.candidate = None
        self.assessment = None
        self.release = None
        self._record_event(
            {
                "type": "working_draft_committed",
                "draft_id": self.draft.draft_id,
                "draft_revision": self.draft.revision,
                "coverage_ratio": self.ledger.coverage_report().coverage_ratio,
            }
        )
        self._persist_checkpoint()

    def _normalize_submission(
        self, submission: PlanningSubmission
    ) -> PlanningSubmission:
        """Own dates, versions, and persistent IDs outside the model."""
        revision = self.draft.revision + 1 if self.draft is not None else 1
        obligations_by_id = {
            item.obligation_id: item for item in self.ledger.obligations
        }
        preferred_name_by_candidate: dict[str, str] = {}
        if self.grounding is not None:
            for resolution in self.grounding.resolutions:
                if (
                    resolution.status is not ResolutionStatus.SELECTED
                    or resolution.selected_candidate_id is None
                ):
                    continue
                obligation = next(
                    (
                        obligations_by_id[obligation_id]
                        for obligation_id in resolution.obligation_ids
                        if obligation_id in obligations_by_id
                        and obligations_by_id[obligation_id].explicit
                    ),
                    None,
                )
                if obligation is not None:
                    preferred_name_by_candidate[
                        resolution.selected_candidate_id
                    ] = obligation.mention
        stop_id_by_model_id: dict[str, str] = {}
        normalized_days = []
        for day_index, day in enumerate(submission.draft.days, start=1):
            normalized_stops = []
            for stop_index, stop in enumerate(day.stops, start=1):
                stop_id = _artifact_id(
                    "stop",
                    self.run_record.run_id,
                    str(day_index),
                    str(stop_index),
                    stop.candidate_id,
                )
                stop_id_by_model_id[stop.stop_id] = stop_id
                normalized_stops.append(
                    stop.model_copy(
                        update={
                            "stop_id": stop_id,
                            "name": preferred_name_by_candidate.get(
                                stop.candidate_id,
                                stop.name,
                            ),
                        }
                    )
                )
            normalized_days.append(
                day.model_copy(
                    update={
                        "day_number": day_index,
                        "calendar_date": self.goal.start_date
                        + timedelta(days=day_index - 1),
                        "stops": tuple(normalized_stops),
                    }
                )
            )
        normalized_draft = submission.draft.model_copy(
            update={
                "draft_id": _artifact_id(
                    "draft",
                    self.run_record.run_id,
                    self.goal.goal_revision_id,
                ),
                "goal_revision_id": self.goal.goal_revision_id,
                "revision": revision,
                "days": tuple(normalized_days),
            }
        )
        normalized_dispositions = tuple(
            disposition.model_copy(
                update={
                    "stop_id": (
                        stop_id_by_model_id.get(disposition.stop_id or "")
                        if disposition.stop_id is not None
                        else None
                    )
                }
            )
            for disposition in submission.dispositions
        )
        return PlanningSubmission.model_validate(
            {
                "draft": normalized_draft.model_dump(mode="python"),
                "dispositions": tuple(
                    item.model_dump(mode="python")
                    for item in normalized_dispositions
                ),
            }
        )

    async def assess_candidate(self) -> DeliveryAssessment | None:
        if self.assessment is not None:
            return self.assessment
        self._ensure_not_cancelled()
        if self.grounding is None or self.draft is None:
            raise AutonomousRuntimeError(
                "grounding and a committed draft are required"
            )
        route_plan = build_fact_need_plan(
            plan_id=_artifact_id(
                "route_facts",
                self.run_record.run_id,
                self.draft.draft_id,
                str(self.draft.revision),
            ),
            draft=self.draft,
        )
        self.telemetry.route_fact_need_count = len(route_plan.needs)
        candidate_by_id: dict[str, GroundingCandidate] = {
            candidate.candidate_id: candidate
            for candidate_set in self.grounding.candidate_sets
            for candidate in candidate_set.candidates
        }
        route_resolutions = await self._resolve_fact_plan(
            plan=route_plan,
            candidates=candidate_by_id,
        )
        self._ensure_not_cancelled()
        self.fact_need_plan = _resolved_plan(route_plan, route_resolutions)
        self.fact_gap_report = build_fact_gap_report(
            draft=self.draft,
            fact_plan=self.fact_need_plan,
        )
        self.telemetry.fact_need_count = len(self.fact_need_plan.needs)
        self.telemetry.fact_gap_count = len(self.fact_gap_report.gaps)
        if self.fact_gap_report.gaps:
            self._record_fact_gap_event()
            return None
        route_facts: tuple[RouteFact, ...] = tuple(
            resolution.route_fact
            for resolution in route_resolutions
            if resolution.route_fact is not None
        )
        if any(
            need.status is not FactNeedStatus.SUCCEEDED
            for need in self.fact_need_plan.needs
        ):
            raise AutonomousRuntimeError(
                "fact plan contains a non-success status without a gap"
            )
        self.timeline = compile_timeline(
            snapshot_id=_artifact_id(
                "timeline",
                self.run_record.run_id,
                self.draft.draft_id,
                str(self.draft.revision),
            ),
            draft=self.draft,
            route_facts=route_facts,
        )
        operational_plan = build_operational_fact_need_plan(
            plan_id=_artifact_id(
                "operational_facts",
                self.run_record.run_id,
                self.draft.draft_id,
                str(self.draft.revision),
            ),
            draft=self.draft,
            timeline=self.timeline,
        )
        self.telemetry.operational_fact_need_count = len(
            operational_plan.needs
        )
        operational_resolutions = await self._resolve_fact_plan(
            plan=operational_plan,
            candidates=candidate_by_id,
        )
        self._ensure_not_cancelled()
        all_resolutions = route_resolutions + operational_resolutions
        combined_plan = FactNeedPlan(
            plan_id=_artifact_id(
                "facts",
                self.run_record.run_id,
                self.draft.draft_id,
                str(self.draft.revision),
            ),
            draft_id=self.draft.draft_id,
            draft_revision=self.draft.revision,
            needs=tuple(
                resolution.need for resolution in all_resolutions
            ),
        )
        self.fact_need_plan = combined_plan
        self.fact_gap_report = build_fact_gap_report(
            draft=self.draft,
            fact_plan=combined_plan,
        )
        self.telemetry.fact_need_count = len(combined_plan.needs)
        self.telemetry.fact_gap_count = len(self.fact_gap_report.gaps)
        operational_facts: tuple[OperationalFact, ...] = tuple(
            resolution.operational_fact
            for resolution in operational_resolutions
            if resolution.operational_fact is not None
        )
        self.candidate = assemble_candidate_snapshot(
            candidate_snapshot_id=_artifact_id(
                "candidate",
                self.run_record.run_id,
                self.draft.draft_id,
                str(self.draft.revision),
            ),
            workspace_id=self.run_record.workspace_id,
            producing_run_id=self.run_record.run_id,
            ledger=self.ledger,
            grounding=self.grounding,
            timeline=self.timeline,
            fact_gap_report=self.fact_gap_report,
            operational_facts=operational_facts,
        )
        succeeded_run = RunRecord(
            run_id=self.run_record.run_id,
            workspace_id=self.run_record.workspace_id,
            goal_revision_id=self.run_record.goal_revision_id,
            status=RunStatus.SUCCEEDED,
        )
        self.assessment = assess_delivery(
            run=succeeded_run,
            candidate=self.candidate,
        )
        if self.repository is not None:
            self.repository.save_candidate(
                self.candidate, self.assessment
            )
        self._persist_checkpoint()
        if self.fact_gap_report.gaps:
            self._record_fact_gap_event()
        self._record_event(
            {
                "type": "candidate_assessed",
                "candidate_snapshot_id": (
                    self.candidate.candidate_snapshot_id
                ),
                "delivery_state": self.assessment.state.value,
                "issue_codes": [
                    issue.code for issue in self.assessment.issues
                ],
            }
        )
        return self.assessment

    async def finalize_candidate(self) -> DeliveryAssessment | None:
        if self.release is not None:
            return self.assessment
        assessment = await self.assess_candidate()
        if assessment is not None and assessment.may_publish:
            self._ensure_not_cancelled()
            succeeded_run = self.run_record.model_copy(update={"status": RunStatus.SUCCEEDED})
            release = publish_release(
                release_id=_artifact_id(
                    "release", self.run_record.run_id, self.candidate.candidate_snapshot_id,
                ),
                run=succeeded_run, candidate=self.candidate, assessment=self.assessment,
            )
            if self.repository is not None:
                release = self.repository.commit_release(release)
            self.release = release
            self.run_record = succeeded_run
            self._record_event(
                {
                    "type": "release_published",
                    "release_id": self.release.release_id,
                    "producing_run_id": self.release.producing_run_id,
                }
            )
        return self.assessment

    async def _resolve_fact_plan(
        self,
        *,
        plan: FactNeedPlan,
        candidates: dict[str, GroundingCandidate],
    ) -> tuple[FactResolution, ...]:
        resolved_by_need_id: dict[str, FactResolution] = {}
        missing_needs = []
        for need in plan.needs:
            cached = self.fact_resolutions.get(need.need_id)
            if cached is not None and _same_fact_ownership(
                original=need, resolved=cached.need
            ):
                resolved_by_need_id[need.need_id] = cached
                self.telemetry.fact_cache_hit_count += 1
                continue
            missing_needs.append(need)
            self.telemetry.record_provider_call(f"fact_{need.kind.value}")
        fetched = tuple(
            await asyncio.gather(
                *(
                    self.fact_provider.resolve(
                        need=need,
                        candidates=candidates,
                    )
                    for need in missing_needs
                )
            )
        )
        for resolution in fetched:
            resolved_by_need_id[resolution.need.need_id] = resolution
            if (
                resolution.need.status is FactNeedStatus.SUCCEEDED
                or resolution.need.failure_code
                in _CACHEABLE_FACT_FAILURE_CODES
            ):
                self.fact_resolutions[
                    resolution.need.need_id
                ] = resolution
        if fetched:
            self._persist_checkpoint()
        if set(resolved_by_need_id) != {
            need.need_id for need in plan.needs
        }:
            raise AutonomousRuntimeError(
                "fact provider did not resolve every scheduled fact need"
            )
        ordered: list[FactResolution] = []
        for original_need in plan.needs:
            resolved = resolved_by_need_id[original_need.need_id]
            if (
                resolved.need.kind is not original_need.kind
                or resolved.need.candidate_ids
                != original_need.candidate_ids
                or resolved.need.stop_ids != original_need.stop_ids
                or resolved.need.requested_mode
                is not original_need.requested_mode
                or resolved.need.visit_at != original_need.visit_at
            ):
                raise AutonomousRuntimeError(
                    f"fact resolution changed ownership for "
                    f"{original_need.need_id}"
                )
            if (
                original_need.kind is FactNeedKind.ROUTE
                and resolved.need.status is FactNeedStatus.SUCCEEDED
            ):
                route_fact = resolved.route_fact
                if (
                    route_fact is None
                    or route_fact.origin_stop_id
                    != original_need.stop_ids[0]
                    or route_fact.destination_stop_id
                    != original_need.stop_ids[1]
                    or route_fact.day_number
                    != original_need.day_number
                ):
                    raise AutonomousRuntimeError(
                        f"route fact lost stop-level ownership for "
                        f"{original_need.need_id}"
                    )
            ordered.append(resolved)
        return tuple(ordered)

    def _record_fact_gap_event(self) -> None:
        if self.fact_gap_report is None or not self.fact_gap_report.gaps:
            return
        self._record_event(
            {
                "type": "fact_resolution_blocked",
                "gap_count": len(self.fact_gap_report.gaps),
                "affected_days": sorted(
                    {
                        gap.day_number
                        for gap in self.fact_gap_report.gaps
                    }
                ),
                "affected_places": sorted(
                    {
                        name
                        for gap in self.fact_gap_report.gaps
                        for name in gap.place_names
                    }
                ),
                "gaps": [
                    {
                        "need_id": gap.need_id,
                        "kind": gap.kind.value,
                        "day_number": gap.day_number,
                        "place_names": list(gap.place_names),
                        "failure_code": gap.failure_code,
                        "failure_message": gap.failure_message,
                        "still_scheduled": gap.still_scheduled,
                        "impact": gap.impact,
                    }
                    for gap in self.fact_gap_report.gaps
                ],
            }
        )

    def request_clarification(self) -> tuple[str, ...]:
        self._ensure_not_cancelled()
        if self.grounding is None and not self.input_gap_questions:
            self.finalize_grounding()
        obligations = {
            item.obligation_id: item for item in self.ledger.obligations
        }
        grounding_questions = tuple(
            (
                (
                    resolution.clarification_question
                    or f"请确认 {resolution.target_id} 的具体地点。"
                )
                if resolution.status is ResolutionStatus.NEEDS_CONFIRMATION
                else (
                    "地图服务没有找到与“"
                    + " / ".join(
                        obligations[obligation_id].mention
                        for obligation_id in resolution.obligation_ids
                    )
                    + "”匹配的地点。请补充地址、门店或链接，"
                    "或明确允许不安排。"
                )
            )
            for resolution in (
                self.grounding.resolutions
                if self.grounding is not None
                else ()
            )
            if resolution.status is not ResolutionStatus.SELECTED
        )
        fact_questions = tuple(
            (
                f"第 {gap.day_number} 天 "
                f"{' → '.join(gap.place_names)} 的"
                + (
                    "路线方式或时长"
                    if gap.kind is FactNeedKind.ROUTE
                    else "营业、闭馆、停止入场或预约信息"
                )
                + f"无法核验（{gap.failure_message}）。"
                + (
                    "请补充官方来源或确认改换到访时段／地点，"
                    "也可以明确允许不安排。"
                )
            )
            for gap in (
                self.fact_gap_report.gaps
                if self.fact_gap_report is not None
                else ()
            )
        )
        issue_questions = tuple(
            (
                f"{issue.message} {issue.recommendation}"
            )
            for issue in (
                self.assessment.issues
                if self.assessment is not None
                and not fact_questions
                else ()
            )
            if issue.severity.value == "blocking"
            and (
                issue.day_numbers
                or issue.obligation_ids
                or issue.place_names
            )
        )
        questions = tuple(
            dict.fromkeys(
                self.input_gap_questions
                + grounding_questions
                + fact_questions
                + issue_questions
            )
        )
        if not questions:
            raise AutonomousRuntimeError(
                "no run-bound ambiguity or delivery blocker requires clarification"
            )
        self.clarification_questions = questions
        self.run_record = RunRecord(
            run_id=self.run_record.run_id,
            workspace_id=self.run_record.workspace_id,
            goal_revision_id=self.run_record.goal_revision_id,
            status=RunStatus.WAITING_USER,
        )
        if self.repository is not None:
            self.run_record = self.repository.transition_run(
                self.run_record.run_id, RunStatus.WAITING_USER
            )
        self._record_event(
            {
                "type": "clarification_requested",
                "questions": list(questions),
            }
        )
        return questions

    def result(self) -> RuntimeExecutionResult:
        return RuntimeExecutionResult(
            run=self.run_record,
            requirement_ledger=self.ledger,
            query_plan=self.query_plan,
            grounding=self.grounding,
            draft=self.draft,
            fact_need_plan=self.fact_need_plan,
            fact_gap_report=self.fact_gap_report,
            timeline=self.timeline,
            candidate=self.candidate,
            assessment=self.assessment,
            release=self.release,
            clarification_questions=self.clarification_questions,
            events=tuple(self.events),
        )


async def execute_autonomous_journey(
    *,
    run: RunRecord,
    goal: JourneyGoal,
    sources: tuple[SourceDocument, ...],
    requirement_interpreter: RequirementInterpreterPort,
    root_agent: RootTripPlannerAgentPort,
    place_provider: PlaceSearchProviderPort,
    fact_provider: FactProviderPort,
    repository: SqliteTripAgentV3Repository | None = None,
    telemetry: RunTelemetry | None = None,
) -> RuntimeExecutionResult:
    telemetry = telemetry or RunTelemetry()
    telemetry.source_count = len(sources)
    telemetry.source_char_count = sum(len(source.content) for source in sources)
    if run.goal_revision_id != goal.goal_revision_id:
        raise AutonomousRuntimeError(
            "run and journey goal revisions do not match"
        )
    active_run = RunRecord(
        run_id=run.run_id,
        workspace_id=run.workspace_id,
        goal_revision_id=run.goal_revision_id,
        status=RunStatus.ACTIVE,
    )
    if repository is not None:
        existing = repository.get_run(active_run.run_id)
        if existing is None:
            repository.save_run(active_run)
        elif existing.status is not RunStatus.ACTIVE:
            active_run = repository.transition_run(
                active_run.run_id, RunStatus.ACTIVE
            )
    input_fingerprint = _input_fingerprint(goal, sources)
    checkpoint_payload: dict[str, object] | None = None
    reused_candidates_after_revision = False
    stored_checkpoint = (
        repository.get_checkpoint(run.run_id)
        if repository is not None
        else None
    )
    if (
        stored_checkpoint is not None
        and stored_checkpoint[0] == input_fingerprint
    ):
        checkpoint_payload = stored_checkpoint[1]
    if (
        checkpoint_payload is not None
        and checkpoint_payload.get("ledger") is not None
        and checkpoint_payload.get("query_plan") is not None
    ):
        ledger = _restore_model(
            RequirementLedger, checkpoint_payload["ledger"]
        )
        query_plan = _restore_model(
            QueryPlan, checkpoint_payload["query_plan"]
        )
    else:
        if repository is not None:
            repository.append_event(active_run.run_id, {"type": "requirements_started"})
        proposal = await requirement_interpreter.interpret(
            goal=goal, sources=sources
        )
        if (
            proposal.goal_revision_id != goal.goal_revision_id
            or proposal.destination != goal.destination
        ):
            raise AutonomousRuntimeError(
                "requirement proposal changed the journey goal"
            )
        ledger = compile_requirement_ledger(
            ledger_id=_artifact_id(
                "ledger", run.run_id, goal.goal_revision_id
            ),
            revision=1,
            sources=sources,
            proposal=proposal,
        )
        query_plan = build_query_plan(
            plan_id=_artifact_id(
                "query_plan", run.run_id, ledger.ledger_id
            ),
            ledger=ledger,
            destination=goal.destination,
        )
        if (
            stored_checkpoint is not None
            and stored_checkpoint[0] != input_fingerprint
        ):
            checkpoint_payload = _reusable_candidate_checkpoint(
                stored_checkpoint[1], query_plan
            )
            reused_candidates_after_revision = (
                checkpoint_payload is not None
            )
    telemetry.obligation_count = len(ledger.obligations)
    telemetry.constraint_count = len(ledger.constraints)
    for target in query_plan.targets:
        telemetry.query_targets_seen.setdefault(target.target_id, 1)
    telemetry.query_target_count = len(telemetry.query_targets_seen)
    runtime = AutonomousTripRuntime(
        run=active_run,
        goal=goal,
        ledger=ledger,
        query_plan=query_plan,
        place_provider=place_provider,
        fact_provider=fact_provider,
        repository=repository,
        input_fingerprint=input_fingerprint,
        checkpoint_payload=checkpoint_payload,
        telemetry=telemetry,
    )
    runtime._persist_checkpoint()
    runtime._record_event({
        "type": "requirements_ready",
        "place_count": len(ledger.obligations),
        "constraint_count": len(ledger.constraints),
    })
    if checkpoint_payload is not None:
        runtime._record_event(
            {
                "type": (
                    "checkpoint_candidates_reused_after_revision"
                    if reused_candidates_after_revision
                    else "checkpoint_restored"
                ),
                "candidate_group_count": len(runtime.candidate_sets),
                "grounding_decision_count": len(
                    runtime.grounding_decisions
                ),
                "draft_restored": runtime.draft is not None,
            }
        )
    if runtime.input_gap_questions:
        runtime.request_clarification()
        return runtime.result()
    await root_agent.run(runtime)
    if (
        runtime.release is None
        and runtime.run_record.status is RunStatus.ACTIVE
    ):
        runtime.run_record = RunRecord(
            run_id=active_run.run_id,
            workspace_id=active_run.workspace_id,
            goal_revision_id=active_run.goal_revision_id,
            status=RunStatus.NEEDS_RESUME,
        )
        if repository is not None:
            runtime.run_record = repository.transition_run(
                active_run.run_id, RunStatus.NEEDS_RESUME
            )
        runtime._record_event(
            {
                "type": "run_needs_resume",
                "reason": (
                    "root agent returned before strict delivery completion"
                ),
            }
        )
    return runtime.result()
