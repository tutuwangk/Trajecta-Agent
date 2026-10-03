from __future__ import annotations

from datetime import date, datetime, time, timezone

from app.trip_agent_v3.assembly import assemble_candidate_snapshot
from app.trip_agent_v3.candidates import intake_provider_candidates
from app.trip_agent_v3.commitments import commit_plan_dispositions
from app.trip_agent_v3.delivery import assess_delivery, publish_release
from app.trip_agent_v3.domain.delivery import (
    DeliveryState,
    ExperienceStatus,
    FactStatus,
    RunRecord,
    RunStatus,
)
from app.trip_agent_v3.domain.facts import (
    FactGapReport,
    FactSourceRecord,
    FactNeedKind,
    FactResolutionStatus,
    OperationalClaim,
    OperationalFact,
    RouteFact,
    RouteFactSource,
)
from app.trip_agent_v3.domain.grounding import ResolutionStatus
from app.trip_agent_v3.domain.plan import (
    DraftDay,
    DraftStop,
    StopKind,
    WorkingDraft,
)
from app.trip_agent_v3.domain.requirements import (
    ConstraintStrength,
    CoverageDisposition,
    DispositionStatus,
    ObligationPriority,
    PlaceRole,
    QueryDecision,
)
from app.trip_agent_v3.domain.sources import (
    EvidenceSpanProposal,
    PlaceMentionProposal,
    RequirementProposal,
    TimeWindowProposal,
)
from app.trip_agent_v3.fact_needs import build_fact_need_plan
from app.trip_agent_v3.grounding import (
    GroundingDecisionProposal,
    compile_grounding_registry,
)
from app.trip_agent_v3.requirements import (
    build_query_plan,
    compile_requirement_ledger,
    source_document,
)
from app.trip_agent_v3.timeline import compile_timeline


RAW = "住成都太古里亚朵S酒店。上午去武侯祠，中午吃陈麻婆豆腐，下午返回酒店。"


def _span(text: str) -> EvidenceSpanProposal:
    start = RAW.index(text)
    return EvidenceSpanProposal(
        source_id="source-user",
        start=start,
        end=start + len(text),
    )


def _mention(
    key: str,
    text: str,
    role: PlaceRole,
    priority: ObligationPriority,
) -> PlaceMentionProposal:
    return PlaceMentionProposal(
        mention_key=key,
        mention=text,
        role=role,
        priority=priority,
        evidence=(_span(text),),
        query_decision=QueryDecision.QUERY,
        query_text=text,
        category_hints=(role.value,),
    )


def test_chengdu_named_meal_journey_reaches_strict_release() -> None:
    source = source_document(
        source_id="source-user", kind="user_request", content=RAW
    )
    proposal = RequirementProposal(
        proposal_id="proposal-1",
        goal_revision_id="goal-1",
        destination="成都",
        mentions=(
            _mention(
                "hotel",
                "成都太古里亚朵S酒店",
                PlaceRole.LODGING,
                ObligationPriority.REQUIRED,
            ),
            _mention(
                "wuhou",
                "武侯祠",
                PlaceRole.VISIT,
                ObligationPriority.REQUIRED,
            ),
            _mention(
                "meal",
                "陈麻婆豆腐",
                PlaceRole.MEAL,
                ObligationPriority.REQUIRED,
            ),
        ),
        constraints=(
            TimeWindowProposal(
                proposal_key="morning",
                subject_mention_keys=("wuhou",),
                evidence=(_span("上午"),),
                strength=ConstraintStrength.REQUIRED,
                day_number=1,
                earliest=time(8, 0),
                latest=time(12, 0),
            ),
            TimeWindowProposal(
                proposal_key="lunch",
                subject_mention_keys=("meal",),
                evidence=(_span("中午"),),
                strength=ConstraintStrength.REQUIRED,
                day_number=1,
                earliest=time(11, 0),
                latest=time(13, 30),
            ),
        ),
    )
    ledger = compile_requirement_ledger(
        ledger_id="ledger-1",
        revision=1,
        sources=(source,),
        proposal=proposal,
    )
    query_plan = build_query_plan(
        plan_id="query-plan-1",
        ledger=ledger,
        destination="成都",
    )
    raw_results = {
        "成都太古里亚朵S酒店": (
            {
                "id": "B-hotel",
                "name": "成都太古里亚朵S酒店",
                "address": "锦江区",
                "type": "住宿服务;宾馆酒店",
                "location": "104.08,30.65",
            },
            {
                "id": "B-hotel-subway",
                "name": "春熙路地铁站",
                "address": "地铁2号线",
                "type": "交通设施;地铁站",
                "location": "104.08,30.65",
            },
        ),
        "武侯祠": (
            {
                "id": "B-wuhou",
                "name": "成都武侯祠博物馆",
                "address": "武侯祠大街231号",
                "type": "风景名胜;博物馆",
                "location": "104.04,30.64",
            },
            {
                "id": "B-wuhou-gate",
                "name": "武侯祠东门",
                "address": "武侯祠大街",
                "type": "通行设施;门",
                "location": "104.04,30.64",
            },
        ),
        "陈麻婆豆腐": (
            {
                "id": "B-meal",
                "name": "陈麻婆豆腐(骡马市店)",
                "address": "青羊区",
                "type": "餐饮服务;中餐厅",
                "location": "104.07,30.67",
            },
        ),
    }
    candidate_sets = tuple(
        intake_provider_candidates(
            target=target,
            provider="amap",
            results=raw_results[target.query_text],
        )
        for target in query_plan.targets
    )
    selected_provider_ids = {
        "成都太古里亚朵S酒店": "B-hotel",
        "武侯祠": "B-wuhou",
        "陈麻婆豆腐": "B-meal",
    }
    grounding = compile_grounding_registry(
        registry_id="grounding-1",
        query_plan=query_plan,
        candidate_sets=candidate_sets,
        decisions=tuple(
            GroundingDecisionProposal(
                target_id=candidate_set.target_id,
                status=ResolutionStatus.SELECTED,
                selected_provider_place_id=selected_provider_ids[
                    candidate_set.query_text
                ],
                rationale="名称、城市和类别与原始地点一致。",
                confidence=0.96,
                selection_factors=("exact_name", "city", "category"),
            )
            for candidate_set in candidate_sets
        ),
    )
    selected_by_obligation = {
        resolution.obligation_ids[0]: resolution.selected_candidate_id
        for resolution in grounding.resolutions
    }
    obligations_by_mention = {
        item.mention: item.obligation_id for item in ledger.obligations
    }
    hotel_obligation = obligations_by_mention["成都太古里亚朵S酒店"]
    wuhou_obligation = obligations_by_mention["武侯祠"]
    meal_obligation = obligations_by_mention["陈麻婆豆腐"]
    hotel_candidate = selected_by_obligation[hotel_obligation] or ""
    wuhou_candidate = selected_by_obligation[wuhou_obligation] or ""
    meal_candidate = selected_by_obligation[meal_obligation] or ""
    draft = WorkingDraft(
        draft_id="draft-1",
        goal_revision_id="goal-1",
        revision=1,
        days=(
            DraftDay(
                day_number=1,
                calendar_date=date(2026, 8, 1),
                title="武侯祠与陈麻婆豆腐",
                start_time=time(9, 0),
                stops=(
                    DraftStop(
                        stop_id="hotel-start",
                        candidate_id=hotel_candidate,
                        obligation_ids=(hotel_obligation,),
                        name="成都太古里亚朵S酒店",
                        kind=StopKind.LODGING,
                        stay_duration_min=0,
                        rationale="每日出发锚点。",
                    ),
                    DraftStop(
                        stop_id="wuhou",
                        candidate_id=wuhou_candidate,
                        obligation_ids=(wuhou_obligation,),
                        name="成都武侯祠博物馆",
                        kind=StopKind.VISIT,
                        stay_duration_min=90,
                        rationale="用户明确必去。",
                    ),
                    DraftStop(
                        stop_id="meal",
                        candidate_id=meal_candidate,
                        obligation_ids=(meal_obligation,),
                        name="陈麻婆豆腐(骡马市店)",
                        kind=StopKind.MEAL,
                        stay_duration_min=60,
                        rationale="用户明确指定餐厅。",
                    ),
                    DraftStop(
                        stop_id="hotel-return",
                        candidate_id=hotel_candidate,
                        name="成都太古里亚朵S酒店",
                        kind=StopKind.LODGING,
                        stay_duration_min=0,
                        rationale="每日返店锚点。",
                    ),
                ),
            ),
        ),
    )
    committed_ledger = commit_plan_dispositions(
        ledger=ledger,
        grounding=grounding,
        draft=draft,
        dispositions=(
            CoverageDisposition(
                obligation_id=hotel_obligation,
                status=DispositionStatus.SCHEDULED,
                stop_id="hotel-start",
            ),
            CoverageDisposition(
                obligation_id=wuhou_obligation,
                status=DispositionStatus.SCHEDULED,
                stop_id="wuhou",
            ),
            CoverageDisposition(
                obligation_id=meal_obligation,
                status=DispositionStatus.SCHEDULED,
                stop_id="meal",
            ),
        ),
    )
    fact_plan = build_fact_need_plan(plan_id="facts-1", draft=draft)
    route_facts = (
        RouteFact(
            fact_id="route-hotel-wuhou",
            origin_candidate_id=hotel_candidate,
            destination_candidate_id=wuhou_candidate,
            duration_min=20,
            mode="taxi",
            source=RouteFactSource.AMAP,
            status=FactResolutionStatus.VERIFIED,
        ),
        RouteFact(
            fact_id="route-wuhou-meal",
            origin_candidate_id=wuhou_candidate,
            destination_candidate_id=meal_candidate,
            duration_min=15,
            mode="taxi",
            source=RouteFactSource.AMAP,
            status=FactResolutionStatus.VERIFIED,
        ),
        RouteFact(
            fact_id="route-meal-hotel",
            origin_candidate_id=meal_candidate,
            destination_candidate_id=hotel_candidate,
            duration_min=25,
            mode="taxi",
            source=RouteFactSource.AMAP,
            status=FactResolutionStatus.VERIFIED,
        ),
    )
    timeline = compile_timeline(
        snapshot_id="timeline-1",
        draft=draft,
        route_facts=route_facts,
    )
    operational_facts = tuple(
        OperationalFact(
            fact_id=f"operation-{stop.stop_id}",
            candidate_id=stop.candidate_id,
            stop_id=stop.stop_id,
            visit_at=stop.arrival_at,
            claims=(
                OperationalClaim(
                    field="opening_hours",
                    value="计划到访时段开放",
                    source_ids=(f"source-{stop.stop_id}",),
                    confidence=0.99,
                ),
            ),
            sources=(
                FactSourceRecord(
                    source_id=f"source-{stop.stop_id}",
                    provider="test_source",
                    uri=f"https://example.test/{stop.stop_id}",
                    title=f"{stop.name} 官方运营信息",
                    excerpt="计划到访时段开放。",
                    content_hash=("a" if stop.stop_id == "wuhou" else "b")
                    * 64,
                    retrieved_at=datetime.now(timezone.utc),
                ),
            ),
        )
        for day in timeline.days
        for stop in day.stops
        if stop.kind in {StopKind.VISIT, StopKind.MEAL}
    )
    candidate = assemble_candidate_snapshot(
        candidate_snapshot_id="candidate-1",
        workspace_id="workspace-1",
        producing_run_id="run-1",
        ledger=committed_ledger,
        grounding=grounding,
        timeline=timeline,
        fact_gap_report=FactGapReport(fact_need_plan_id=fact_plan.plan_id),
        operational_facts=operational_facts,
    )
    run = RunRecord(
        run_id="run-1",
        workspace_id="workspace-1",
        goal_revision_id="goal-1",
        status=RunStatus.SUCCEEDED,
    )
    assessment = assess_delivery(run=run, candidate=candidate)
    release = publish_release(
        release_id="release-1",
        run=run,
        candidate=candidate,
        assessment=assessment,
        published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert len(ledger.obligations) == 3
    assert len(query_plan.targets) == 3
    assert sum(len(item.candidates) for item in candidate_sets) == 3
    assert all(len(item.candidates) <= 3 for item in candidate_sets)
    assert committed_ledger.coverage_report().coverage_ratio == 1
    assert candidate.grounding_decisions[1].selected_name == "成都武侯祠博物馆"
    assert candidate.grounding_decisions[1].rationale.startswith("名称")
    assert candidate.timeline.days[0].stops[2].kind is StopKind.MEAL
    assert candidate.timeline.days[0].stops[2].name == "陈麻婆豆腐(骡马市店)"
    assert candidate.timeline.days[0].stops[0].kind is StopKind.LODGING
    assert candidate.timeline.days[0].stops[-1].kind is StopKind.LODGING
    assert len(candidate.timeline.days[0].legs) == 3
    assert {
        candidate_id
        for need in fact_plan.needs
        for candidate_id in need.candidate_ids
    } == {hotel_candidate, wuhou_candidate, meal_candidate}
    assert len(
        [need for need in fact_plan.needs if need.kind is FactNeedKind.ROUTE]
    ) == 3
    assert candidate.fact_status is FactStatus.VERIFIED
    assert candidate.experience_status is ExperienceStatus.GOOD
    assert assessment.state is DeliveryState.PUBLISHABLE
    assert release.producing_run_id == "run-1"
    assert release.candidate_snapshot_id == "candidate-1"
    assert "陈麻婆豆腐" in release.narrative.overview
    assert "09:20 到达 成都武侯祠博物馆" in (
        release.narrative.days[0].summary
    )
    assert "打车 20 分钟" in release.narrative.days[0].summary
