from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from typing import Mapping, Sequence

import pytest

from app.trip_agent_v3.autonomous_runtime import (
    AutonomousRuntimeError,
    AutonomousTripRuntime,
    execute_autonomous_journey,
)
from app.trip_agent_v3.domain.delivery import RunRecord, RunStatus
from app.trip_agent_v3.domain.delivery import ExperienceStatus
from app.trip_agent_v3.domain.execution import (
    JourneyGoal,
    PlanningSubmission,
)
from app.trip_agent_v3.domain.facts import (
    FactNeed,
    FactNeedKind,
    FactNeedStatus,
    FactResolution,
    FactResolutionStatus,
    FactSourceRecord,
    OperationalClaim,
    OperationalFact,
    RouteFact,
    RouteFactSource,
)
from app.trip_agent_v3.domain.grounding import (
    GroundingCandidate,
    ResolutionStatus,
)
from app.trip_agent_v3.domain.plan import (
    DraftDay,
    DraftStop,
    StopKind,
    WorkingDraft,
)
from app.trip_agent_v3.domain.query import QueryTarget
from app.trip_agent_v3.domain.requirements import (
    ConstraintStrength,
    CoverageDisposition,
    DispositionStatus,
    ObligationPriority,
    PlaceRole,
    QueryDecision,
)
from app.trip_agent_v3.domain.sources import (
    DayAssignmentProposal,
    EvidenceSpanProposal,
    PlaceMentionProposal,
    PaceProposal,
    RequirementProposal,
    SourceDocument,
)
from app.trip_agent_v3.grounding import GroundingDecisionProposal
from app.trip_agent_v3.repository import SqliteTripAgentV3Repository
from app.trip_agent_v3.requirements import source_document
from app.trip_agent_v3.shadow_eval import build_shadow_case_report
from app.trip_agent_v3.telemetry import RunTelemetry


RAW = "住测试酒店，去测试博物馆，中午吃测试餐厅。"


def _span(text: str) -> EvidenceSpanProposal:
    start = RAW.index(text)
    return EvidenceSpanProposal(
        source_id="source-1",
        start=start,
        end=start + len(text),
    )


class Interpreter:
    async def interpret(
        self,
        *,
        goal: JourneyGoal,
        sources: tuple[SourceDocument, ...],
    ) -> RequirementProposal:
        return RequirementProposal(
            proposal_id="proposal-1",
            goal_revision_id=goal.goal_revision_id,
            destination=goal.destination,
            mentions=(
                PlaceMentionProposal(
                    mention_key="hotel",
                    mention="测试酒店",
                    role=PlaceRole.LODGING,
                    priority=ObligationPriority.REQUIRED,
                    evidence=(_span("测试酒店"),),
                    query_decision=QueryDecision.QUERY,
                    query_text="测试酒店",
                ),
                PlaceMentionProposal(
                    mention_key="museum",
                    mention="测试博物馆",
                    role=PlaceRole.VISIT,
                    priority=ObligationPriority.REQUIRED,
                    evidence=(_span("测试博物馆"),),
                    query_decision=QueryDecision.QUERY,
                    query_text="测试博物馆",
                ),
                PlaceMentionProposal(
                    mention_key="meal",
                    mention="测试餐厅",
                    role=PlaceRole.MEAL,
                    priority=ObligationPriority.REQUIRED,
                    evidence=(_span("测试餐厅"),),
                    query_decision=QueryDecision.QUERY,
                    query_text="测试餐厅",
                ),
            ),
        )


class CountingInterpreter(Interpreter):
    def __init__(self) -> None:
        self.calls = 0

    async def interpret(
        self,
        *,
        goal: JourneyGoal,
        sources: tuple[SourceDocument, ...],
    ) -> RequirementProposal:
        self.calls += 1
        return await super().interpret(goal=goal, sources=sources)


class NoAnchorInterpreter:
    async def interpret(
        self,
        *,
        goal: JourneyGoal,
        sources: tuple[SourceDocument, ...],
    ) -> RequirementProposal:
        content = sources[0].content

        def span(text: str) -> EvidenceSpanProposal:
            start = content.index(text)
            return EvidenceSpanProposal(
                source_id=sources[0].source_id,
                start=start,
                end=start + len(text),
            )

        return RequirementProposal(
            proposal_id="proposal-no-anchor",
            goal_revision_id=goal.goal_revision_id,
            destination=goal.destination,
            mentions=(
                PlaceMentionProposal(
                    mention_key="museum",
                    mention="测试博物馆",
                    role=PlaceRole.VISIT,
                    priority=ObligationPriority.REQUIRED,
                    evidence=(span("测试博物馆"),),
                    query_decision=QueryDecision.QUERY,
                    query_text="测试博物馆",
                ),
                PlaceMentionProposal(
                    mention_key="meal",
                    mention="测试餐厅",
                    role=PlaceRole.MEAL,
                    priority=ObligationPriority.REQUIRED,
                    evidence=(span("测试餐厅"),),
                    query_decision=QueryDecision.QUERY,
                    query_text="测试餐厅",
                ),
            ),
        )


class ClarifyPlaceInterpreter:
    async def interpret(
        self,
        *,
        goal: JourneyGoal,
        sources: tuple[SourceDocument, ...],
    ) -> RequirementProposal:
        return RequirementProposal(
            proposal_id="proposal-clarify-place",
            goal_revision_id=goal.goal_revision_id,
            destination=goal.destination,
            mentions=(
                PlaceMentionProposal(
                    mention_key="hotel",
                    mention="测试酒店",
                    role=PlaceRole.LODGING,
                    priority=ObligationPriority.REQUIRED,
                    evidence=(_span("测试酒店"),),
                    query_decision=QueryDecision.QUERY,
                    query_text="测试酒店",
                ),
                PlaceMentionProposal(
                    mention_key="meal",
                    mention="测试餐厅",
                    role=PlaceRole.MEAL,
                    priority=ObligationPriority.REQUIRED,
                    evidence=(_span("测试餐厅"),),
                    query_decision=QueryDecision.CLARIFY,
                    decision_reason="资料没有给出具体门店。",
                ),
            ),
        )


class AirportWithoutTimesInterpreter:
    async def interpret(
        self,
        *,
        goal: JourneyGoal,
        sources: tuple[SourceDocument, ...],
    ) -> RequirementProposal:
        content = sources[0].content

        def span(text: str) -> EvidenceSpanProposal:
            start = content.index(text)
            return EvidenceSpanProposal(
                source_id=sources[0].source_id,
                start=start,
                end=start + len(text),
            )

        return RequirementProposal(
            proposal_id="proposal-airport-without-times",
            goal_revision_id=goal.goal_revision_id,
            destination=goal.destination,
            mentions=(
                PlaceMentionProposal(
                    mention_key="airport",
                    mention="测试机场",
                    role=PlaceRole.AIRPORT,
                    priority=ObligationPriority.REQUIRED,
                    evidence=(span("测试机场"),),
                    query_decision=QueryDecision.QUERY,
                    query_text="测试机场",
                ),
                PlaceMentionProposal(
                    mention_key="hotel",
                    mention="测试酒店",
                    role=PlaceRole.LODGING,
                    priority=ObligationPriority.REQUIRED,
                    evidence=(span("测试酒店"),),
                    query_decision=QueryDecision.QUERY,
                    query_text="测试酒店",
                ),
            ),
            constraints=(
                DayAssignmentProposal(
                    proposal_key="airport-day-1",
                    subject_mention_keys=("airport",),
                    evidence=(span("第一天"),),
                    strength=ConstraintStrength.REQUIRED,
                    day_number=1,
                ),
                DayAssignmentProposal(
                    proposal_key="airport-day-4",
                    subject_mention_keys=("airport",),
                    evidence=(span("最后一天"),),
                    strength=ConstraintStrength.REQUIRED,
                    day_number=4,
                ),
            ),
        )


class PlaceProvider:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(
        self, target: QueryTarget
    ) -> Sequence[Mapping[str, object]]:
        self.queries.append(target.query_text)
        return (
            {
                "id": f"B-{target.query_text}",
                "name": target.query_text,
                "address": "测试市",
                "type": "地点",
                "location": "104.08,30.65",
            },
        )


class AliasPlaceProvider(PlaceProvider):
    async def search(
        self, target: QueryTarget
    ) -> Sequence[Mapping[str, object]]:
        rows = await super().search(target)
        return tuple(
            {**row, "name": f"地图别名-{target.query_text}"}
            for row in rows
        )


class FactProvider:
    def __init__(self) -> None:
        self.resolved_needs: list[FactNeed] = []

    async def resolve(
        self,
        *,
        need: FactNeed,
        candidates: Mapping[str, GroundingCandidate],
    ) -> FactResolution:
        self.resolved_needs.append(need)
        succeeded = need.model_copy(
            update={"status": FactNeedStatus.SUCCEEDED}
        )
        if need.kind is FactNeedKind.PLACE_OPERATION:
            assert need.visit_at is not None
            source = FactSourceRecord(
                source_id=f"source-{need.need_id}",
                provider="test",
                uri="https://example.test/fact",
                title="测试运营事实",
                excerpt="计划时段正常开放。",
                content_hash="a" * 64,
                retrieved_at=datetime.now(timezone.utc),
            )
            return FactResolution(
                need=succeeded,
                operational_fact=OperationalFact(
                    fact_id=f"operation-{need.need_id}",
                    candidate_id=need.candidate_ids[0],
                    stop_id=need.stop_ids[0],
                    visit_at=need.visit_at,
                    claims=(
                        OperationalClaim(
                            field="opening_hours",
                            value="计划时段正常开放",
                            source_ids=(source.source_id,),
                            confidence=1,
                        ),
                    ),
                    sources=(source,),
                ),
            )
        return FactResolution(
            need=succeeded,
            route_fact=RouteFact(
                fact_id=f"route-{need.need_id}",
                origin_candidate_id=need.candidate_ids[0],
                destination_candidate_id=need.candidate_ids[1],
                origin_stop_id=need.stop_ids[0],
                destination_stop_id=need.stop_ids[1],
                day_number=need.day_number,
                duration_min=10,
                mode="taxi",
                source=RouteFactSource.AMAP,
                status=FactResolutionStatus.VERIFIED,
            ),
        )


class RootAgent:
    def __init__(
        self, *, submissions: int = 1, finalize_each: bool = False, assess_each: bool = False
    ) -> None:
        self.submissions = submissions
        self.finalize_each = finalize_each
        self.assess_each = assess_each
        self.observed_revisions: list[int] = []

    async def run(self, runtime: AutonomousTripRuntime) -> None:
        for target_id in runtime.grounding_target_ids:
            context = await runtime.read_grounding_context(target_id)
            candidate_set = context.active_candidate_set
            assert candidate_set is not None
            candidate = candidate_set.candidates[0]
            runtime.submit_grounding_decision(
                GroundingDecisionProposal(
                    target_id=target_id,
                    status=ResolutionStatus.SELECTED,
                    selected_provider_place_id=candidate.provider_place_id,
                    rationale="当前有界候选组中名称、城市和类别均一致。",
                    confidence=0.98,
                    selection_factors=("name", "city", "category"),
                )
            )
        runtime.finalize_grounding()
        with pytest.raises(
            AutonomousRuntimeError, match="already finalized"
        ):
            await runtime.read_grounding_context(
                runtime.grounding_target_ids[0]
            )
        context = runtime.read_planning_context(day_number=1)
        by_role = {
            obligation.role: obligation
            for obligation in context.open_obligations
        }
        selected = {
            obligation_id: place
            for place in context.selected_places
            for obligation_id in place.obligation_ids
        }
        hotel_obligation = by_role[PlaceRole.LODGING]
        visit_obligation = by_role[PlaceRole.VISIT]
        meal_obligation = by_role[PlaceRole.MEAL]
        hotel = selected[hotel_obligation.obligation_id]
        visit = selected[visit_obligation.obligation_id]
        meal = selected[meal_obligation.obligation_id]
        draft = WorkingDraft(
            draft_id="draft-runtime",
            goal_revision_id=runtime.goal.goal_revision_id,
            revision=1,
            days=(
                DraftDay(
                    day_number=1,
                    calendar_date=runtime.goal.start_date,
                    title="博物馆与指定午餐",
                    start_time=time(9, 15),
                    stops=(
                        DraftStop(
                            stop_id="hotel-start",
                            candidate_id=hotel.candidate_id,
                            obligation_ids=(hotel_obligation.obligation_id,),
                            name=hotel.name,
                            kind=StopKind.LODGING,
                            stay_duration_min=0,
                            rationale="酒店出发锚点。",
                        ),
                        DraftStop(
                            stop_id="visit",
                            candidate_id=visit.candidate_id,
                            obligation_ids=(visit_obligation.obligation_id,),
                            name=visit.name,
                            kind=StopKind.VISIT,
                            stay_duration_min=90,
                            rationale="用户明确地点。",
                        ),
                        DraftStop(
                            stop_id="meal",
                            candidate_id=meal.candidate_id,
                            obligation_ids=(meal_obligation.obligation_id,),
                            name=meal.name,
                            kind=StopKind.MEAL,
                            stay_duration_min=60,
                            rationale="用户明确餐厅。",
                        ),
                        DraftStop(
                            stop_id="hotel-end",
                            candidate_id=hotel.candidate_id,
                            name=hotel.name,
                            kind=StopKind.LODGING,
                            stay_duration_min=0,
                            rationale="酒店返程锚点。",
                        ),
                    ),
                ),
            ),
        )
        submission = PlanningSubmission(
            draft=draft,
            dispositions=(
                    CoverageDisposition(
                        obligation_id=hotel_obligation.obligation_id,
                        status=DispositionStatus.SCHEDULED,
                        stop_id="hotel-start",
                    ),
                    CoverageDisposition(
                        obligation_id=visit_obligation.obligation_id,
                        status=DispositionStatus.SCHEDULED,
                        stop_id="visit",
                    ),
                    CoverageDisposition(
                        obligation_id=meal_obligation.obligation_id,
                        status=DispositionStatus.SCHEDULED,
                        stop_id="meal",
                    ),
                ),
        )
        for _ in range(self.submissions):
            runtime.submit_plan(submission)
            assert runtime.draft is not None
            self.observed_revisions.append(runtime.draft.revision)
            if self.assess_each:
                assessment = await runtime.assess_candidate()
                assert assessment.may_publish
                assert runtime.release is None
                assert runtime.run_record.status is RunStatus.ACTIVE
            if self.finalize_each:
                await runtime.finalize_candidate()
        if not self.finalize_each:
            await runtime.finalize_candidate()


class EarlyReturningAgent:
    async def run(self, runtime: AutonomousTripRuntime) -> None:
        await runtime.read_grounding_context(runtime.grounding_target_ids[0])


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", [None, "candidate", "unread", "duplicate"])
async def test_grounding_batch_prevalidates_all_decisions_before_checkpoint_writes(tmp_path, invalid):
    class BatchAgent:
        async def run(self, runtime):
            decisions = []
            for index, target_id in enumerate(runtime.grounding_target_ids):
                if invalid == "unread" and index == 1:
                    place_id = "unread"
                else:
                    context = await runtime.read_grounding_context(target_id)
                    place_id = context.active_candidate_set.candidates[0].provider_place_id
                if invalid == "candidate" and index == 1:
                    place_id = "not-retained"
                decisions.append(GroundingDecisionProposal(
                    target_id=target_id, status=ResolutionStatus.SELECTED,
                    selected_provider_place_id=place_id, rationale="名称城市类别一致。",
                    confidence=0.98, selection_factors=("name", "city", "category"),
                ))
            if invalid == "duplicate":
                decisions.append(decisions[0])
            before = len(runtime.events)
            if invalid:
                with pytest.raises(ValueError):
                    runtime.submit_grounding_decisions(tuple(decisions))
                assert runtime.grounding_decisions == {}
                assert len(runtime.events) == before
                payload = runtime.repository.get_checkpoint(runtime.run_record.run_id)[1]
                assert payload["grounding_decisions"] == []
            else:
                runtime.submit_grounding_decisions(tuple(decisions))
                assert set(runtime.grounding_decisions) == set(runtime.grounding_target_ids)
                assert [event["target_id"] for event in runtime.events[before:]] == list(runtime.grounding_target_ids)
                payload = runtime.repository.get_checkpoint(runtime.run_record.run_id)[1]
                assert len(payload["grounding_decisions"]) == 3
                runtime.finalize_grounding()

    result = await execute_autonomous_journey(
        run=_run(), goal=_goal(),
        sources=(source_document(source_id="source-1", kind="user_request", content=RAW),),
        requirement_interpreter=Interpreter(), root_agent=BatchAgent(),
        place_provider=PlaceProvider(), fact_provider=FactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
    )
    assert result.run.status is RunStatus.NEEDS_RESUME


@pytest.mark.anyio
async def test_preferred_pace_overage_publishes_verified_plan_with_review_advice(tmp_path):
    pace_text = "每天最多3小时。"

    class PaceInterpreter(Interpreter):
        async def interpret(self, *, goal, sources):
            proposal = await super().interpret(goal=goal, sources=sources)
            return proposal.model_copy(update={"constraints": (
                PaceProposal(
                    proposal_key="pace", strength=ConstraintStrength.PREFERRED,
                    max_day_minutes=180,
                    evidence=(EvidenceSpanProposal(source_id="source-1", start=len(RAW), end=len(RAW) + len(pace_text)),),
                ),
            )})

    class SlowRouteProvider(FactProvider):
        async def resolve(self, *, need, candidates):
            resolution = await super().resolve(need=need, candidates=candidates)
            if resolution.route_fact is not None:
                return resolution.model_copy(update={"route_fact": resolution.route_fact.model_copy(update={"duration_min": 30})})
            return resolution

    result = await execute_autonomous_journey(
        run=_run(), goal=_goal(),
        sources=(source_document(source_id="source-1", kind="user_request", content=RAW + pace_text),),
        requirement_interpreter=PaceInterpreter(), root_agent=RootAgent(),
        place_provider=PlaceProvider(), fact_provider=SlowRouteProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
    )
    assert result.run.status is RunStatus.SUCCEEDED
    assert result.release is not None
    assert result.candidate.experience_status is ExperienceStatus.NEEDS_ADJUSTMENT
    assert result.assessment.may_publish is True
    assert any(issue.code == "preferred_pace_limit_violated" and issue.severity.value == "review" for issue in result.assessment.issues)


@pytest.mark.anyio
async def test_parallel_reads_of_same_grounding_target_use_one_provider_query(tmp_path):
    class SlowPlaceProvider(PlaceProvider):
        async def search(self, target):
            await asyncio.sleep(0)
            return await super().search(target)

    class ParallelReadingAgent:
        async def run(self, runtime):
            target_id = runtime.grounding_target_ids[0]
            contexts = await asyncio.gather(
                runtime.read_grounding_context(target_id),
                runtime.read_grounding_context(target_id),
            )
            assert contexts[0].active_candidate_set == contexts[1].active_candidate_set

    provider = SlowPlaceProvider()
    telemetry = RunTelemetry()
    result = await execute_autonomous_journey(
        run=_run(), goal=_goal(),
        sources=(source_document(source_id="source-1", kind="user_request", content=RAW),),
        requirement_interpreter=Interpreter(), root_agent=ParallelReadingAgent(),
        place_provider=provider, fact_provider=FactProvider(), telemetry=telemetry,
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
    )
    assert len(provider.queries) == 1
    assert telemetry.provider_calls["place_search"] == 1
    assert result.run.status is RunStatus.NEEDS_RESUME


class ClarifyingAgent:
    async def run(self, runtime: AutonomousTripRuntime) -> None:
        for index, target_id in enumerate(runtime.grounding_target_ids):
            context = await runtime.read_grounding_context(target_id)
            candidate_set = context.active_candidate_set
            assert candidate_set is not None
            candidate = candidate_set.candidates[0]
            if index == 0:
                decision = GroundingDecisionProposal(
                    target_id=target_id,
                    status=ResolutionStatus.NEEDS_CONFIRMATION,
                    rationale="同名门店会改变当天路线，需要用户确认。",
                    confidence=0.55,
                    provider_place_ids_requiring_confirmation=(
                        candidate.provider_place_id,
                    ),
                    clarification_question="请确认具体门店。",
                )
            else:
                decision = GroundingDecisionProposal(
                    target_id=target_id,
                    status=ResolutionStatus.SELECTED,
                    selected_provider_place_id=candidate.provider_place_id,
                    rationale="名称、城市和类别一致。",
                    confidence=0.98,
                    selection_factors=("name", "city", "category"),
                )
            runtime.submit_grounding_decision(decision)
        runtime.finalize_grounding()
        runtime.request_clarification()


class NoMatchPlaceProvider(PlaceProvider):
    async def search(
        self, target: QueryTarget
    ) -> Sequence[Mapping[str, object]]:
        if target.query_text == "测试博物馆":
            self.queries.append(target.query_text)
            return ()
        return await super().search(target)


class NoMatchAgent:
    async def run(self, runtime: AutonomousTripRuntime) -> None:
        for target_id in runtime.grounding_target_ids:
            context = await runtime.read_grounding_context(target_id)
            candidate_set = context.active_candidate_set
            assert candidate_set is not None
            if not candidate_set.candidates:
                decision = GroundingDecisionProposal(
                    target_id=target_id,
                    status=ResolutionStatus.NO_MATCH,
                    rationale="地图候选为空，不能编造地点。",
                    confidence=1,
                )
            else:
                candidate = candidate_set.candidates[0]
                decision = GroundingDecisionProposal(
                    target_id=target_id,
                    status=ResolutionStatus.SELECTED,
                    selected_provider_place_id=candidate.provider_place_id,
                    rationale="名称、城市和类别一致。",
                    confidence=0.98,
                    selection_factors=("name", "city", "category"),
                )
            runtime.submit_grounding_decision(decision)
        runtime.finalize_grounding()
        runtime.request_clarification()


class FailingFactProvider(FactProvider):
    async def resolve(
        self,
        *,
        need: FactNeed,
        candidates: Mapping[str, GroundingCandidate],
    ) -> FactResolution:
        if need.kind is FactNeedKind.ROUTE:
            return FactResolution(
                need=need.model_copy(
                    update={
                        "status": FactNeedStatus.FAILED,
                        "failure_code": "provider_timeout",
                        "failure_message": "测试酒店到测试博物馆的高德路线查询超时。",
                    }
                )
            )
        return await super().resolve(need=need, candidates=candidates)


class OperationalFailingFactProvider(FactProvider):
    failure_code = "operational_evidence_insufficient"
    async def resolve(
        self,
        *,
        need: FactNeed,
        candidates: Mapping[str, GroundingCandidate],
    ) -> FactResolution:
        if need.kind is FactNeedKind.PLACE_OPERATION:
            candidate = candidates[need.candidate_ids[0]]
            return FactResolution(
                need=need.model_copy(
                    update={
                        "status": FactNeedStatus.FAILED,
                        "failure_code": self.failure_code,
                        "failure_message": (
                            f"{candidate.name} 缺少计划到访时段的可追溯运营事实。"
                        ),
                    }
                )
            )
        return await super().resolve(need=need, candidates=candidates)


class FactClarifyingAgent(RootAgent):
    async def run(self, runtime: AutonomousTripRuntime) -> None:
        await super().run(runtime)
        with pytest.raises(AutonomousRuntimeError, match="user-owned information"):
            runtime.request_clarification()


class ClosedVisitFactProvider(OperationalFailingFactProvider):
    async def resolve(self, *, need, candidates):
        resolution = await super().resolve(need=need, candidates=candidates)
        if need.kind is FactNeedKind.PLACE_OPERATION:
            return resolution.model_copy(update={"need": resolution.need.model_copy(update={
                "failure_code": "planned_visit_operationally_incompatible",
                "failure_message": "检索到当日闭馆公告，需要调整到访日期。",
            })})
        return resolution


def _run() -> RunRecord:
    return RunRecord(
        run_id="run-autonomous",
        workspace_id="workspace-1",
        goal_revision_id="goal-1",
        status=RunStatus.CREATED,
    )


def _goal() -> JourneyGoal:
    return JourneyGoal(
        goal_revision_id="goal-1",
        destination="测试市",
        start_date=date(2026, 8, 1),
        days=1,
    )


@pytest.mark.anyio
async def test_missing_daily_anchor_waits_before_any_provider_query(
    tmp_path,
) -> None:
    place_provider = PlaceProvider()

    class RootMustNotRun:
        async def run(self, runtime: AutonomousTripRuntime) -> None:
            raise AssertionError("root agent must wait for an anchor")

    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content="去测试博物馆，中午吃测试餐厅。",
            ),
        ),
        requirement_interpreter=NoAnchorInterpreter(),
        root_agent=RootMustNotRun(),
        place_provider=place_provider,
        fact_provider=FactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
        telemetry=RunTelemetry(),
    )

    assert result.run.status is RunStatus.WAITING_USER
    assert place_provider.queries == []
    assert result.candidate is None
    assert result.release is None
    assert result.clarification_questions == (
        "请提供每天出发和返回的具体酒店／住宿地点；"
        "如果是当日机场往返，请提供具体机场。",
    )
    assert result.events[-1]["type"] == "clarification_requested"


@pytest.mark.anyio
async def test_airport_anchor_without_flight_times_waits_before_search(
    tmp_path,
) -> None:
    place_provider = PlaceProvider()

    class RootMustNotRun:
        async def run(self, runtime: AutonomousTripRuntime) -> None:
            raise AssertionError("root agent must wait for flight times")

    result = await execute_autonomous_journey(
        run=_run(),
        goal=JourneyGoal(
            goal_revision_id="goal-1",
            destination="测试市",
            start_date=date(2026, 8, 1),
            days=4,
        ),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=(
                    "第一天从测试机场到测试酒店，"
                    "最后一天回测试机场。"
                ),
            ),
        ),
        requirement_interpreter=AirportWithoutTimesInterpreter(),
        root_agent=RootMustNotRun(),
        place_provider=place_provider,
        fact_provider=FactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
        telemetry=RunTelemetry(),
    )

    assert result.run.status is RunStatus.WAITING_USER
    assert place_provider.queries == []
    assert result.candidate is None
    assert result.release is None
    assert result.clarification_questions == (
        "请补充第1天在“测试机场”的航班落地或起飞时间，"
        "避免系统虚构机场出发／抵达时刻。",
        "请补充第4天在“测试机场”的航班落地或起飞时间，"
        "避免系统虚构机场出发／抵达时刻。",
    )


@pytest.mark.anyio
async def test_source_level_place_clarification_precedes_provider_search(
    tmp_path,
) -> None:
    place_provider = PlaceProvider()

    class RootMustNotRun:
        async def run(self, runtime: AutonomousTripRuntime) -> None:
            raise AssertionError("root agent must wait for source clarification")

    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=RAW,
            ),
        ),
        requirement_interpreter=ClarifyPlaceInterpreter(),
        root_agent=RootMustNotRun(),
        place_provider=place_provider,
        fact_provider=FactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
        telemetry=RunTelemetry(),
    )

    assert result.run.status is RunStatus.WAITING_USER
    assert place_provider.queries == []
    assert result.clarification_questions == (
        "请补充“测试餐厅”的具体地址、门店或链接，或明确允许不安排。",
    )
    assert result.release is None


def test_telemetry_attributes_model_usage_to_runtime_role() -> None:
    telemetry = RunTelemetry()
    usage = SimpleNamespace(
        requests=2,
        tool_calls=3,
        input_tokens=120,
        output_tokens=40,
    )

    telemetry.record_model_usage(usage, role="trip_planner_root")
    snapshot = telemetry.snapshot(
        run_status="active",
        wall_clock_ms=1,
        delivery_state=None,
        fact_status=None,
        experience_status=None,
        candidate_snapshot_id=None,
        release_id=None,
    )

    assert snapshot["model_requests_by_role"] == {
        "trip_planner_root": 2
    }
    assert snapshot["tool_calls_by_role"] == {
        "trip_planner_root": 3
    }
    assert snapshot["input_tokens_by_role"] == {
        "trip_planner_root": 120
    }
    assert snapshot["output_tokens_by_role"] == {
        "trip_planner_root": 40
    }


@pytest.mark.anyio
async def test_root_agent_controls_actions_while_runtime_owns_facts_and_release(
    tmp_path,
) -> None:
    place_provider = PlaceProvider()
    fact_provider = FactProvider()
    repository = SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3")
    telemetry = RunTelemetry()

    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=RAW,
            ),
        ),
        requirement_interpreter=Interpreter(),
        root_agent=RootAgent(),
        place_provider=place_provider,
        fact_provider=fact_provider,
        repository=repository,
        telemetry=telemetry,
    )

    assert result.run.status is RunStatus.SUCCEEDED
    assert result.release is not None
    assert result.release.producing_run_id == _run().run_id
    assert result.candidate is not None
    for day in result.candidate.timeline.days:
        for stop in day.stops:
            assert stop.location is not None
            assert stop.location.longitude == 104.08
            assert stop.location.latitude == 30.65
            assert stop.address == "测试市"
            assert stop.coordinate_system == "gcj02"
    assert all(
        "location" not in type(stop).model_fields
        for day in result.draft.days for stop in day.stops
    )
    assert result.draft.draft_id != "draft-runtime"
    assert result.draft.revision == 1
    assert all(
        stop.stop_id.startswith("stop_")
        for day in result.draft.days
        for stop in day.stops
    )
    assert place_provider.queries == ["测试酒店", "测试博物馆", "测试餐厅"]
    assert len(fact_provider.resolved_needs) == 5
    assert sum(
        need.kind is FactNeedKind.ROUTE
        for need in fact_provider.resolved_needs
    ) == 3
    assert sum(
        need.kind is FactNeedKind.PLACE_OPERATION
        for need in fact_provider.resolved_needs
    ) == 2
    assert {
        candidate_id
        for need in fact_provider.resolved_needs
        for candidate_id in need.candidate_ids
    } == {
        stop.candidate_id
        for day in result.draft.days
        for stop in day.stops
    }
    assert repository.release_for_run(_run().run_id) is not None
    assert [event["type"] for event in result.events][-1] == "release_published"
    assert telemetry.source_count == 1
    assert telemetry.obligation_count == 3
    assert telemetry.query_target_count == 3
    assert telemetry.provider_calls == {
        "fact_place_operation": 2,
        "fact_route": 3,
        "place_search": 3,
    }
    assert telemetry.provider_result_count == 3
    assert telemetry.retained_candidate_count == 3
    assert telemetry.selected_grounding_count == 3
    assert telemetry.unresolved_grounding_count == 0
    assert telemetry.disposition_counts == {"scheduled": 3}
    assert telemetry.stop_count == 4
    assert telemetry.leg_count == 3
    assert telemetry.fact_need_count == 5
    assert telemetry.route_fact_need_count == 3
    assert telemetry.operational_fact_need_count == 2
    assert telemetry.fact_gap_count == 0
    assert telemetry.local_context_read_count == 4
    assert telemetry.local_context_max_chars > 0
    shadow_case = build_shadow_case_report(
        scenario={"case_id": "test", "city": "测试市", "days": 1},
        run=result.run,
        candidate=result.candidate,
        assessment=result.assessment,
        release=result.release,
        metrics=telemetry.snapshot(
            run_status=result.run.status.value,
            wall_clock_ms=1,
            delivery_state=result.assessment.state.value,
            fact_status=result.candidate.fact_status.value,
            experience_status=result.candidate.experience_status.value,
            candidate_snapshot_id=result.candidate.candidate_snapshot_id,
            release_id=result.release.release_id,
        ),
        events=result.events,
        clarification_rounds=0,
    )
    assert shadow_case["checks"] == {
        "explicit_place_coverage": True,
        "named_meal_complete": True,
        "candidate_context_cap": True,
        "one_search_per_query_target": True,
        "scheduled_fact_scope": True,
        "timeline_complete": True,
        "fact_gaps_explained": True,
        "operational_fact_contract": True,
        "strict_publication": True,
        "release_lineage": True,
        "cancelled_artifact_safety": None,
        "blocker_specificity": True,
    }


@pytest.mark.anyio
async def test_timeline_keeps_user_place_names_when_provider_uses_aliases(
    tmp_path,
) -> None:
    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=RAW,
            ),
        ),
        requirement_interpreter=Interpreter(),
        root_agent=RootAgent(),
        place_provider=AliasPlaceProvider(),
        fact_provider=FactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
    )

    assert result.run.status is RunStatus.SUCCEEDED
    assert result.timeline is not None
    assert [stop.name for stop in result.timeline.days[0].stops] == [
        "测试酒店",
        "测试博物馆",
        "测试餐厅",
        "测试酒店",
    ]
    assert result.candidate is not None
    assert all(
        decision.selected_name.startswith("地图别名-")
        for decision in result.candidate.grounding_decisions
    )


@pytest.mark.anyio
async def test_runtime_owns_draft_revision_and_avoids_candidate_id_collision(
    tmp_path,
) -> None:
    agent = RootAgent(submissions=2, finalize_each=True)
    telemetry = RunTelemetry()

    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=RAW,
            ),
        ),
        requirement_interpreter=Interpreter(),
        root_agent=agent,
        place_provider=PlaceProvider(),
        fact_provider=ClosedVisitFactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
        telemetry=telemetry,
    )

    assert agent.observed_revisions == [1, 2]
    assert result.draft is not None
    assert result.draft.revision == 2
    assert result.candidate is not None
    assert result.candidate.timeline.draft_revision == 2
    assert result.release is None
    assert result.run.status is RunStatus.NEEDS_RESUME
    assert telemetry.fact_cache_hit_count == 5
    assert telemetry.provider_calls == {
        "fact_place_operation": 2,
        "fact_route": 3,
        "place_search": 3,
    }


@pytest.mark.anyio
async def test_repeat_submission_after_publication_reuses_one_release(tmp_path):
    agent = RootAgent(submissions=2, finalize_each=True)
    repository = SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3")
    facts = FactProvider()
    result = await execute_autonomous_journey(
        run=_run(), goal=_goal(),
        sources=(source_document(source_id="source-1", kind="user_request", content=RAW),),
        requirement_interpreter=Interpreter(), root_agent=agent,
        place_provider=PlaceProvider(), fact_provider=facts, repository=repository,
    )
    assert result.run.status is RunStatus.SUCCEEDED
    assert agent.observed_revisions == [1, 1]
    assert result.release.candidate_snapshot_id == result.candidate.candidate_snapshot_id
    assert repository.latest_candidate_for_run(_run().run_id) == result.candidate
    assert len(facts.resolved_needs) == 5
    assert [event["type"] for event in result.events].count("release_published") == 1


@pytest.mark.anyio
async def test_assessment_allows_revision_before_explicit_publication(tmp_path):
    agent = RootAgent(submissions=2, assess_each=True)
    repo = SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3")
    result = await execute_autonomous_journey(
        run=_run(), goal=_goal(),
        sources=(source_document(source_id="source-1", kind="user_request", content=RAW),),
        requirement_interpreter=Interpreter(), root_agent=agent,
        place_provider=PlaceProvider(), fact_provider=FactProvider(), repository=repo,
    )
    assert agent.observed_revisions == [1, 2]
    assert result.release.candidate_snapshot_id == result.candidate.candidate_snapshot_id
    assert result.candidate.timeline.draft_revision == 2
    events = [event["type"] for event in result.events]
    assert events.count("candidate_assessed") == 2
    assert events.count("release_published") == 1
    assert events[-1] == "release_published"


@pytest.mark.anyio
async def test_agent_return_without_goal_closure_becomes_resumable_not_release(
    tmp_path,
) -> None:
    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=RAW,
            ),
        ),
        requirement_interpreter=Interpreter(),
        root_agent=EarlyReturningAgent(),
        place_provider=PlaceProvider(),
        fact_provider=FactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
    )

    assert result.run.status is RunStatus.NEEDS_RESUME
    assert result.release is None
    assert result.events[-1]["type"] == "run_needs_resume"


@pytest.mark.anyio
async def test_same_input_resume_restores_business_checkpoint_without_requery(
    tmp_path,
) -> None:
    class SavingRepairAgent(EarlyReturningAgent):
        async def run(self, runtime):
            await super().run(runtime)
            runtime.persist_tool_repair_feedback("Use globally unique day-qualified stop keys.")

    class RestoringRepairAgent(RootAgent):
        async def run(self, runtime):
            assert runtime.last_tool_repair_feedback == "Use globally unique day-qualified stop keys."
            from app.trip_agent_v3.adapters.deepseek import _root_episode_prompt
            assert runtime.last_tool_repair_feedback in _root_episode_prompt(runtime)
            await super().run(runtime)
    repository = SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3")
    interpreter = CountingInterpreter()
    place_provider = PlaceProvider()
    sources = (
        source_document(
            source_id="source-1",
            kind="user_request",
            content=RAW,
        ),
    )
    first = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=sources,
        requirement_interpreter=interpreter,
        root_agent=SavingRepairAgent(),
        place_provider=place_provider,
        fact_provider=FactProvider(),
        repository=repository,
    )
    assert first.run.status is RunStatus.NEEDS_RESUME
    assert place_provider.queries == ["测试酒店"]

    resumed_run = repository.get_run(_run().run_id)
    assert resumed_run is not None
    second = await execute_autonomous_journey(
        run=resumed_run,
        goal=_goal(),
        sources=sources,
        requirement_interpreter=interpreter,
        root_agent=RestoringRepairAgent(),
        place_provider=place_provider,
        fact_provider=FactProvider(),
        repository=repository,
    )

    assert second.run.status is RunStatus.SUCCEEDED
    assert second.release is not None
    assert repository.get_checkpoint(_run().run_id)[1]["last_tool_repair_feedback"] is None
    assert interpreter.calls == 1
    assert place_provider.queries == [
        "测试酒店",
        "测试博物馆",
        "测试餐厅",
    ]
    assert any(
        event["type"] == "checkpoint_restored"
        for event in second.events
    )


@pytest.mark.anyio
async def test_source_revision_reuses_only_unchanged_candidate_queries(
    tmp_path,
) -> None:
    repository = SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3")
    interpreter = CountingInterpreter()
    place_provider = PlaceProvider()
    original_sources = (
        source_document(
            source_id="source-1",
            kind="user_request",
            content=RAW,
        ),
    )
    first = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=original_sources,
        requirement_interpreter=interpreter,
        root_agent=EarlyReturningAgent(),
        place_provider=place_provider,
        fact_provider=FactProvider(),
        repository=repository,
    )
    assert first.run.status is RunStatus.NEEDS_RESUME
    assert place_provider.queries == ["测试酒店"]

    revised_sources = original_sources + (
        source_document(
            source_id="source-2",
            kind="user_revision",
            content="用户补充：测试餐厅请按原名称继续查询。",
        ),
    )
    resumed_run = repository.get_run(_run().run_id)
    assert resumed_run is not None
    second = await execute_autonomous_journey(
        run=resumed_run,
        goal=_goal(),
        sources=revised_sources,
        requirement_interpreter=interpreter,
        root_agent=RootAgent(),
        place_provider=place_provider,
        fact_provider=FactProvider(),
        repository=repository,
    )

    assert second.run.status is RunStatus.SUCCEEDED
    assert interpreter.calls == 2
    assert place_provider.queries == [
        "测试酒店",
        "测试博物馆",
        "测试餐厅",
    ]
    assert any(
        event["type"]
        == "checkpoint_candidates_reused_after_revision"
        for event in second.events
    )


@pytest.mark.anyio
async def test_material_ambiguity_waits_on_same_run_without_candidate_release(
    tmp_path,
) -> None:
    repository = SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3")
    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=RAW,
            ),
        ),
        requirement_interpreter=Interpreter(),
        root_agent=ClarifyingAgent(),
        place_provider=PlaceProvider(),
        fact_provider=FactProvider(),
        repository=repository,
    )

    assert result.run.status is RunStatus.WAITING_USER
    assert len(result.clarification_questions) == 1
    assert "测试酒店" in result.clarification_questions[0]
    assert "哪个地点或门店" in result.clarification_questions[0]
    assert result.release is None
    assert repository.get_run(_run().run_id).status is RunStatus.WAITING_USER


@pytest.mark.anyio
async def test_no_match_requests_address_or_permission_instead_of_stalling(
    tmp_path,
) -> None:
    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=RAW,
            ),
        ),
        requirement_interpreter=Interpreter(),
        root_agent=NoMatchAgent(),
        place_provider=NoMatchPlaceProvider(),
        fact_provider=FactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
    )

    assert result.run.status is RunStatus.WAITING_USER
    assert "测试博物馆" in result.clarification_questions[0]
    assert "地址" in result.clarification_questions[0]
    assert result.release is None


@pytest.mark.anyio
async def test_grounding_question_is_scoped_to_identity_even_with_wrong_model_prose(tmp_path):
    class PublicQuestionAgent(ClarifyingAgent):
        async def run(self, runtime):
            submit = runtime.submit_grounding_decision
            def submit_question(decision):
                if decision.status is ResolutionStatus.NEEDS_CONFIRMATION:
                    decision = decision.model_copy(update={"clarification_question": "请你查询酒店营业时间并提供官方来源。"})
                submit(decision)
            runtime.submit_grounding_decision = submit_question
            await super().run(runtime)

    result = await execute_autonomous_journey(
        run=_run(), goal=_goal(), sources=(source_document(source_id="source-1", kind="user_request", content=RAW),),
        requirement_interpreter=Interpreter(), root_agent=PublicQuestionAgent(),
        place_provider=PlaceProvider(), fact_provider=FactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "identity.sqlite3"),
    )
    assert result.run.status is RunStatus.WAITING_USER
    assert "哪个地点或门店" in result.clarification_questions[0]
    assert "营业时间" not in result.clarification_questions[0]
    assert "官方来源" not in result.clarification_questions[0]


@pytest.mark.anyio
async def test_scheduled_route_failure_names_places_and_prevents_release(
    tmp_path,
) -> None:
    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=RAW,
            ),
        ),
        requirement_interpreter=Interpreter(),
        root_agent=RootAgent(),
        place_provider=PlaceProvider(),
        fact_provider=FailingFactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
    )

    assert result.run.status is RunStatus.NEEDS_RESUME
    assert result.release is None
    assert result.fact_gap_report is not None
    assert result.fact_gap_report.gaps[0].place_names == ("测试酒店", "测试博物馆")
    blocked_event = next(
        event
        for event in result.events
        if event["type"] == "fact_resolution_blocked"
    )
    assert blocked_event["affected_places"] == ["测试博物馆", "测试酒店", "测试餐厅"]


@pytest.mark.anyio
@pytest.mark.parametrize("failure_code", ["operational_sources_missing", "operational_evidence_insufficient", "operational_extraction_failed"])
async def test_operational_fact_gap_publishes_trip_with_available_route_facts(
    tmp_path, failure_code,
) -> None:
    repository = SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3")
    provider = OperationalFailingFactProvider()
    provider.failure_code = failure_code
    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=RAW,
            ),
        ),
        requirement_interpreter=Interpreter(),
        root_agent=RootAgent(),
        place_provider=PlaceProvider(),
        fact_provider=provider,
        repository=repository,
    )

    assert result.run.status is RunStatus.SUCCEEDED
    assert result.release is not None
    assert result.candidate is not None
    assert result.assessment is not None
    assert result.assessment.state.value == "publishable"
    assert result.fact_gap_report is not None
    assert {
        gap.place_names for gap in result.fact_gap_report.gaps
    } == {("测试博物馆",), ("测试餐厅",)}
    assert all(
        gap.still_scheduled for gap in result.fact_gap_report.gaps
    )
    persisted = repository.latest_candidate_for_run(_run().run_id)
    assert persisted is not None
    assert persisted.fact_status.value == "degraded"
    assert result.release.candidate_snapshot_id == persisted.candidate_snapshot_id
    assert all(event["type"] != "fact_resolution_blocked" for event in result.events)
    assert all(leg.fact_status is FactResolutionStatus.VERIFIED
               for day in persisted.timeline.days for leg in day.legs)
    assert all(stop.departure_at <= next_stop.arrival_at
               for day in persisted.timeline.days for stop, next_stop in zip(day.stops, day.stops[1:]))


@pytest.mark.anyio
async def test_public_fact_gaps_remain_owned_by_runtime_tools(
    tmp_path,
) -> None:
    result = await execute_autonomous_journey(
        run=_run(),
        goal=_goal(),
        sources=(
            source_document(
                source_id="source-1",
                kind="user_request",
                content=RAW,
            ),
        ),
        requirement_interpreter=Interpreter(),
        root_agent=FactClarifyingAgent(),
        place_provider=PlaceProvider(),
        fact_provider=OperationalFailingFactProvider(),
        repository=SqliteTripAgentV3Repository(tmp_path / "v3.sqlite3"),
    )

    assert result.run.status is RunStatus.SUCCEEDED
    assert result.release is not None
    assert result.clarification_questions == ()
    assert all(event["type"] != "clarification_requested" for event in result.events)


@pytest.mark.anyio
@pytest.mark.parametrize("answer_changes_input", [False, True])
async def test_recovered_facts_replace_blocked_snapshot_without_identity_conflict(
    tmp_path, answer_changes_input,
) -> None:
    repository = SqliteTripAgentV3Repository(tmp_path / "recovery.sqlite3")
    sources = (source_document(source_id="source-1", kind="user_request", content=RAW),)
    first = await execute_autonomous_journey(
        run=_run(), goal=_goal(), sources=sources,
        requirement_interpreter=Interpreter(), root_agent=RootAgent(),
        place_provider=PlaceProvider(), fact_provider=ClosedVisitFactProvider(),
        repository=repository,
    )
    assert first.candidate is not None
    blocked_id = first.candidate.candidate_snapshot_id

    class ResumeAssessmentAgent:
        async def run(self, runtime):
            assert runtime.draft is not None
            await runtime.finalize_candidate()

    class RecoveryProvider(FactProvider):
        def __init__(self):
            super().__init__()
            self.kinds = []

        async def resolve(self, *, need, candidates):
            self.kinds.append(need.kind)
            return await super().resolve(need=need, candidates=candidates)

    provider = RecoveryProvider()
    if answer_changes_input:
        sources += (source_document(
            source_id="answer-1", kind="user_revision", content="用户补充：营业时间为9点至17点。",
        ),)
    second = await execute_autonomous_journey(
        run=repository.get_run(_run().run_id), goal=_goal(), sources=sources,
        requirement_interpreter=Interpreter(),
        root_agent=RootAgent() if answer_changes_input else ResumeAssessmentAgent(),
        place_provider=PlaceProvider(), fact_provider=provider, repository=repository,
    )
    assert second.run.status is RunStatus.SUCCEEDED
    assert second.release is not None
    assert second.candidate.candidate_snapshot_id != blocked_id
    assert second.candidate.fact_gap_report.gaps == ()
    assert repository.get_candidate(blocked_id) == first.candidate
    assert repository.latest_candidate_for_run(_run().run_id) == second.candidate
    assert second.release.candidate_snapshot_id == second.candidate.candidate_snapshot_id
    if answer_changes_input:
        assert second.draft.draft_id != first.draft.draft_id
    else:
        assert second.draft == first.draft
        assert provider.kinds == [FactNeedKind.PLACE_OPERATION, FactNeedKind.PLACE_OPERATION]
