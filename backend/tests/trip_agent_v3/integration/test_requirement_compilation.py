from __future__ import annotations

from datetime import time
import pytest

from app.trip_agent_v3.domain.requirements import (
    ConstraintStrength,
    ObligationPriority,
    PlaceRole,
    QueryDecision,
    TravelMode,
)
from app.trip_agent_v3.domain.sources import (
    DayAssignmentProposal,
    EvidenceSpanProposal,
    PlaceMentionProposal,
    RequirementProposal,
    TimeWindowProposal,
    TransportPreferenceProposal,
)
from app.trip_agent_v3.requirements import (
    RequirementCompilationError,
    build_query_plan,
    compile_requirement_ledger,
    source_document,
)


@pytest.mark.parametrize("strength", [ConstraintStrength.REQUIRED, ConstraintStrength.PREFERRED])
@pytest.mark.parametrize("policy", ["prefer", "only"])
@pytest.mark.parametrize("preferred", [TravelMode.TAXI, TravelMode.WALK])
@pytest.mark.parametrize("mode, minutes", [("walk", 15), ("walk", 30), ("taxi", 30), ("driving", 30), ("transit", 30)])
def test_transport_policy_and_walking_limit_have_independent_semantics(strength, policy, preferred, mode, minutes):
    from datetime import date, datetime, timedelta
    from app.trip_agent_v3.domain.plan import CompiledTimeline, DayTimeline, TimelineStop, TimelineLeg, StopKind
    from app.trip_agent_v3.domain.facts import FactResolutionStatus, RouteFactSource
    from app.trip_agent_v3.experience import evaluate_timeline_constraints

    raw = "交通要求"
    source = source_document(source_id="transport-source", kind="user_request", content=raw)
    proposal = RequirementProposal(
        proposal_id="transport-proposal", goal_revision_id="goal-transport", destination="测试市",
        constraints=(TransportPreferenceProposal(
            proposal_key="transport", evidence=(EvidenceSpanProposal(source_id=source.source_id, start=0, end=len(raw)),),
            strength=strength, mode_policy=policy, preferred_modes=(preferred,), max_walk_minutes=20,
        ),),
    )
    ledger = compile_requirement_ledger(ledger_id="transport-ledger", revision=1, sources=(source,), proposal=proposal)
    assert ledger.constraints[0].mode_policy == policy
    assert ledger.constraints[0].preferred_modes == (preferred,)
    depart = datetime(2026, 8, 1, 9, 0)
    arrive = depart + timedelta(minutes=minutes)
    timeline = CompiledTimeline(snapshot_id="transport-timeline", draft_id="transport-draft", draft_revision=1, days=(DayTimeline(
        day_number=1, calendar_date=date(2026, 8, 1), title="路线",
        stops=(TimelineStop(stop_id="start", candidate_id="hotel-a", name="酒店A", kind=StopKind.LODGING, arrival_at=depart, departure_at=depart, stay_duration_min=0),
               TimelineStop(stop_id="end", candidate_id="hotel-b", name="酒店B", kind=StopKind.LODGING, arrival_at=arrive, departure_at=arrive, stay_duration_min=0)),
        legs=(TimelineLeg(leg_id="leg", from_stop_id="start", to_stop_id="end", departure_at=depart, arrival_at=arrive, duration_min=minutes, mode=mode, fact_id="route", fact_source=RouteFactSource.AMAP, fact_status=FactResolutionStatus.VERIFIED),),
    ),))
    status, issues = evaluate_timeline_constraints(ledger=ledger, timeline=timeline)
    mode_issue = policy == "only" and mode != preferred.value
    walk_issue = mode == "walk" and minutes > 20
    assert status.value == ("needs_adjustment" if mode_issue or walk_issue else "good")
    assert all(issue.severity == "review" for issue in issues)
    assert any(issue.code.endswith("transport_mode_violated") for issue in issues) is mode_issue
    assert any(issue.code.endswith("walk_limit_violated") for issue in issues) is walk_issue


@pytest.mark.parametrize("raw, expected", [
    ("第一天上午去武侯祠", False), ("已预约第一天上午去武侯祠", True), ("尚未预约成功，第一天上午去武侯祠", False),
    ("第一天上午去武侯祠，预约需要确认", False),
])
def test_fixed_commitment_requires_explicit_confirmed_source_evidence(raw, expected):
    source = source_document(source_id="fixed-source", kind="user_request", content=raw)
    start = raw.index("武侯祠")
    evidence = (EvidenceSpanProposal(source_id=source.source_id, start=0, end=len(raw)),)
    proposal = RequirementProposal(
        proposal_id="fixed-proposal", goal_revision_id="goal-fixed", destination="成都",
        mentions=(PlaceMentionProposal(mention_key="museum", mention="武侯祠", role=PlaceRole.VISIT, priority=ObligationPriority.REQUIRED,
            evidence=(EvidenceSpanProposal(source_id=source.source_id, start=start, end=start+3),), query_decision=QueryDecision.QUERY, query_text="武侯祠"),),
        constraints=(TimeWindowProposal(proposal_key="window", subject_mention_keys=("museum",), evidence=evidence, strength=ConstraintStrength.REQUIRED,
            fixed_commitment=True, day_number=1, earliest=time(9), latest=time(12)),
            DayAssignmentProposal(proposal_key="day", subject_mention_keys=("museum",), evidence=evidence, strength=ConstraintStrength.REQUIRED, fixed_commitment=True, day_number=1)),
    )
    ledger = compile_requirement_ledger(ledger_id="fixed-ledger", revision=1, sources=(source,), proposal=proposal)
    assert all(constraint.fixed_commitment is expected for constraint in ledger.constraints)
    from app.trip_agent_v3.context import _planning_constraints
    assert all(constraint.fixed_commitment is expected for constraint in _planning_constraints(ledger))


RAW = (
    "住成都太古里亚朵S酒店。第一天上午去武侯祠和锦里，"
    "中午吃陈麻婆豆腐，下午人民公园喝茶。"
    "第二天去成都博物馆、IFS 和太古里，晚上九眼桥。"
)


def _span(text: str, occurrence: int = 0) -> EvidenceSpanProposal:
    start = -1
    cursor = 0
    for _ in range(occurrence + 1):
        start = RAW.index(text, cursor)
        cursor = start + len(text)
    return EvidenceSpanProposal(
        source_id="source-raw",
        start=start,
        end=start + len(text),
    )


def _mention(
    key: str,
    text: str,
    role: PlaceRole,
    *,
    priority: ObligationPriority = ObligationPriority.PREFERRED,
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


def _proposal() -> RequirementProposal:
    return RequirementProposal(
        proposal_id="proposal-1",
        goal_revision_id="goal-1",
        destination="成都",
        mentions=(
            _mention(
                "hotel",
                "成都太古里亚朵S酒店",
                PlaceRole.LODGING,
                priority=ObligationPriority.REQUIRED,
            ),
            _mention(
                "wuhou",
                "武侯祠",
                PlaceRole.VISIT,
                priority=ObligationPriority.REQUIRED,
            ),
            _mention("jinli", "锦里", PlaceRole.VISIT),
            _mention(
                "chenmapo",
                "陈麻婆豆腐",
                PlaceRole.MEAL,
                priority=ObligationPriority.REQUIRED,
            ),
            _mention("park", "人民公园", PlaceRole.VISIT),
            _mention("museum", "成都博物馆", PlaceRole.VISIT),
            _mention("ifs", "IFS", PlaceRole.SHOPPING),
            _mention("taikoo", "太古里", PlaceRole.SHOPPING),
            _mention("jiuyan", "九眼桥", PlaceRole.PHOTO),
        ),
        constraints=(
            DayAssignmentProposal(
                proposal_key="day-1",
                subject_mention_keys=("wuhou", "jinli", "chenmapo", "park"),
                evidence=(_span("第一天"),),
                strength=ConstraintStrength.REQUIRED,
                day_number=1,
            ),
            DayAssignmentProposal(
                proposal_key="day-2",
                subject_mention_keys=("museum", "ifs", "taikoo", "jiuyan"),
                evidence=(_span("第二天"),),
                strength=ConstraintStrength.REQUIRED,
                day_number=2,
            ),
            TimeWindowProposal(
                proposal_key="morning",
                subject_mention_keys=("wuhou", "jinli"),
                evidence=(_span("上午"),),
                strength=ConstraintStrength.REQUIRED,
                day_number=1,
                earliest=time(8, 0),
                latest=time(12, 0),
            ),
            TimeWindowProposal(
                proposal_key="noon",
                subject_mention_keys=("chenmapo",),
                evidence=(_span("中午"),),
                strength=ConstraintStrength.REQUIRED,
                day_number=1,
                earliest=time(11, 30),
                latest=time(13, 30),
            ),
            TimeWindowProposal(
                proposal_key="afternoon",
                subject_mention_keys=("park",),
                evidence=(_span("下午"),),
                strength=ConstraintStrength.REQUIRED,
                day_number=1,
                earliest=time(13, 0),
                latest=time(18, 0),
            ),
            TimeWindowProposal(
                proposal_key="evening",
                subject_mention_keys=("jiuyan",),
                evidence=(_span("晚上"),),
                strength=ConstraintStrength.REQUIRED,
                day_number=2,
                earliest=time(18, 0),
                latest=time(23, 59),
            ),
        ),
    )


def test_chengdu_material_produces_bounded_place_queries_without_noise() -> None:
    source = source_document(
        source_id="source-raw", kind="raw_material", content=RAW
    )
    proposal = _proposal()
    ledger = compile_requirement_ledger(
        ledger_id="ledger-1",
        revision=1,
        sources=(source,),
        proposal=proposal,
    )
    query_plan = build_query_plan(
        plan_id="query-plan-1",
        ledger=ledger,
        destination=proposal.destination,
    )

    assert len(ledger.obligations) == 9
    assert len(ledger.constraints) == 6
    assert len(query_plan.targets) == 9
    assert sum(item.max_candidates for item in query_plan.targets) == 27
    assert {item.query_text for item in query_plan.targets} == {
        "成都太古里亚朵S酒店",
        "武侯祠",
        "锦里",
        "陈麻婆豆腐",
        "人民公园",
        "成都博物馆",
        "IFS",
        "太古里",
        "九眼桥",
    }
    assert {item.query_text for item in query_plan.targets}.isdisjoint(
        {"上午", "中午", "下午", "晚上", "去", "吃", "喝茶"}
    )
    assert len(query_plan.skips) == 0
    assert {
        evidence.text
        for constraint in ledger.constraints
        for evidence in constraint.evidence
    } == {"第一天", "第二天", "上午", "中午", "下午", "晚上"}


def test_proposal_cannot_fabricate_a_place_without_exact_source_evidence() -> None:
    source = source_document(
        source_id="source-raw", kind="raw_material", content=RAW
    )
    proposal = _proposal().model_copy(
        update={
            "mentions": (
                _proposal().mentions[0].model_copy(
                    update={"mention": "不存在的地点"}
                ),
            )
        }
    )

    with pytest.raises(RequirementCompilationError, match="does not equal mention"):
        compile_requirement_ledger(
            ledger_id="ledger-1",
            revision=1,
            sources=(source,),
            proposal=proposal,
        )


def test_action_prefix_is_deterministically_trimmed_from_place_evidence() -> None:
    source = source_document(
        source_id="source-raw", kind="raw_material", content=RAW
    )
    proposal = _proposal()
    hotel = proposal.mentions[0].model_copy(
        update={"evidence": (_span("住成都太古里亚朵S酒店"),)}
    )

    ledger = compile_requirement_ledger(
        ledger_id="ledger-trimmed-evidence",
        revision=1,
        sources=(source,),
        proposal=proposal.model_copy(
            update={"mentions": (hotel, *proposal.mentions[1:])}
        ),
    )

    evidence = ledger.obligations[0].evidence[0]
    assert evidence.text == "成都太古里亚朵S酒店"
    assert source.content[evidence.start : evidence.end] == evidence.text


def test_contextual_hotel_references_collapse_into_named_anchor() -> None:
    raw = "住成都博舍酒店，从酒店出发，晚上回酒店。"
    source = source_document(
        source_id="source-anchor", kind="user_request", content=raw
    )
    named_start = raw.index("成都博舍酒店")
    first_reference = raw.index("酒店", named_start + len("成都博舍酒店"))
    second_reference = raw.index("酒店", first_reference + len("酒店"))
    proposal = RequirementProposal(
        proposal_id="proposal-anchor",
        goal_revision_id="goal-anchor",
        destination="成都",
        mentions=(
            PlaceMentionProposal(
                mention_key="named-hotel",
                mention="成都博舍酒店",
                role=PlaceRole.LODGING,
                priority=ObligationPriority.REQUIRED,
                evidence=(
                    EvidenceSpanProposal(
                        source_id="source-anchor",
                        start=named_start,
                        end=named_start + len("成都博舍酒店"),
                    ),
                ),
                query_decision=QueryDecision.QUERY,
                query_text="成都博舍酒店",
            ),
            *(
                PlaceMentionProposal(
                    mention_key=f"hotel-reference-{index}",
                    mention="酒店",
                    role=PlaceRole.LODGING,
                    priority=ObligationPriority.REQUIRED,
                    evidence=(
                        EvidenceSpanProposal(
                            source_id="source-anchor",
                            start=start,
                            end=start + len("酒店"),
                        ),
                    ),
                    query_decision=QueryDecision.MERGE,
                    merge_into_mention_key="named-hotel",
                    decision_reason="上下文中的酒店指向已命名住宿。",
                )
                for index, start in enumerate(
                    (first_reference, second_reference), start=1
                )
            ),
        ),
    )

    ledger = compile_requirement_ledger(
        ledger_id="ledger-anchor",
        revision=1,
        sources=(source,),
        proposal=proposal,
    )
    query_plan = build_query_plan(
        plan_id="query-plan-anchor",
        ledger=ledger,
        destination="成都",
    )

    assert [item.mention for item in ledger.obligations] == ["成都博舍酒店"]
    assert query_plan.targets[0].obligation_ids == (
        ledger.obligations[0].obligation_id,
    )


def test_generic_span_reanchors_to_unique_named_place_in_same_source() -> None:
    raw = "入住成都尼依格罗酒店，第二天从酒店出发。"
    source = source_document(
        source_id="source-reanchor",
        kind="user_request",
        content=raw,
    )
    generic_start = raw.rindex("酒店")
    proposal = RequirementProposal(
        proposal_id="proposal-reanchor",
        goal_revision_id="goal-reanchor",
        destination="成都",
        mentions=(
            PlaceMentionProposal(
                mention_key="hotel",
                mention="成都尼依格罗酒店",
                role=PlaceRole.LODGING,
                priority=ObligationPriority.REQUIRED,
                evidence=(
                    EvidenceSpanProposal(
                        source_id="source-reanchor",
                        start=generic_start,
                        end=generic_start + len("酒店"),
                    ),
                ),
                query_decision=QueryDecision.QUERY,
                query_text="成都尼依格罗酒店",
            ),
        ),
    )

    ledger = compile_requirement_ledger(
        ledger_id="ledger-reanchor",
        revision=1,
        sources=(source,),
        proposal=proposal,
    )

    evidence = ledger.obligations[0].evidence[0]
    assert evidence.text == "成都尼依格罗酒店"
    assert raw[evidence.start : evidence.end] == evidence.text


def test_obvious_time_word_cannot_enter_provider_query() -> None:
    source = source_document(
        source_id="source-raw", kind="raw_material", content=RAW
    )
    proposal = RequirementProposal(
        proposal_id="proposal-noise",
        goal_revision_id="goal-1",
        destination="成都",
        mentions=(
            PlaceMentionProposal(
                mention_key="morning-noise",
                mention="上午",
                role=PlaceRole.VISIT,
                priority=ObligationPriority.OPTIONAL,
                evidence=(_span("上午"),),
                query_decision=QueryDecision.QUERY,
                query_text="上午",
            ),
        ),
    )

    with pytest.raises(
        RequirementCompilationError, match="obvious non-place term"
    ):
        compile_requirement_ledger(
            ledger_id="ledger-noise",
            revision=1,
            sources=(source,),
            proposal=proposal,
        )


def test_source_mutation_invalidates_proposal_before_any_provider_query() -> None:
    source = source_document(
        source_id="source-raw", kind="raw_material", content=RAW
    ).model_copy(update={"content": f"{RAW}。"})

    with pytest.raises(RequirementCompilationError, match="hash mismatch"):
        compile_requirement_ledger(
            ledger_id="ledger-1",
            revision=1,
            sources=(source,),
            proposal=_proposal(),
        )


def test_transport_language_becomes_constraint_not_place_query() -> None:
    raw = "步行优先，单段最多走二十分钟，远途打车。"
    source = source_document(
        source_id="source-transport",
        kind="raw_material",
        content=raw,
    )
    evidence_text = "步行优先，单段最多走二十分钟，远途打车"
    proposal = RequirementProposal(
        proposal_id="proposal-transport",
        goal_revision_id="goal-transport",
        destination="成都",
        constraints=(
            TransportPreferenceProposal(
                proposal_key="transport",
                evidence=(
                    EvidenceSpanProposal(
                        source_id=source.source_id,
                        start=0,
                        end=len(evidence_text),
                    ),
                ),
                strength=ConstraintStrength.REQUIRED,
                preferred_modes=(TravelMode.WALK, TravelMode.TAXI),
                max_walk_minutes=20,
            ),
        ),
    )

    ledger = compile_requirement_ledger(
        ledger_id="ledger-transport",
        revision=1,
        sources=(source,),
        proposal=proposal,
    )
    query_plan = build_query_plan(
        plan_id="query-transport",
        ledger=ledger,
        destination="成都",
    )

    assert len(ledger.obligations) == 0
    assert ledger.constraints[0].kind == "transport_preference"
    assert ledger.constraints[0].max_walk_minutes == 20
    assert query_plan.targets == ()


def test_explicit_meal_slot_is_inferred_when_interpreter_omits_constraint() -> None:
    raw = "指定在钟水饺（人民公园店）吃午餐、在马旺子（太古里店）吃晚餐。"
    source = source_document(
        source_id="source-meal-slots",
        kind="user_request",
        content=raw,
    )

    def mention(
        key: str,
        text: str,
    ) -> PlaceMentionProposal:
        start = raw.index(text)
        return PlaceMentionProposal(
            mention_key=key,
            mention=text,
            role=PlaceRole.MEAL,
            priority=ObligationPriority.REQUIRED,
            evidence=(
                EvidenceSpanProposal(
                    source_id=source.source_id,
                    start=start,
                    end=start + len(text),
                ),
            ),
            query_decision=QueryDecision.QUERY,
            query_text=text,
        )

    ledger = compile_requirement_ledger(
        ledger_id="ledger-meal-slots",
        revision=1,
        sources=(source,),
        proposal=RequirementProposal(
            proposal_id="proposal-meal-slots",
            goal_revision_id="goal-meal-slots",
            destination="成都",
            mentions=(
                mention("lunch", "钟水饺（人民公园店）"),
                mention("dinner", "马旺子（太古里店）"),
            ),
        ),
    )

    obligation_by_mention = {
        item.mention: item.obligation_id for item in ledger.obligations
    }
    windows = {
        item.subject_obligation_ids[0]: (item.earliest, item.latest)
        for item in ledger.constraints
        if item.kind == "time_window"
    }
    assert windows[obligation_by_mention["钟水饺（人民公园店）"]] == (
        time(11, 0),
        time(14, 30),
    )
    assert windows[obligation_by_mention["马旺子（太古里店）"]] == (
        time(17, 0),
        time(21, 30),
    )
