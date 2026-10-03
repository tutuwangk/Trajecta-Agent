from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.trip_agent_v3.domain import (
    EvidenceSpan,
    ObligationPriority,
    PlaceObligation,
    PlaceRole,
    QueryDecision,
    QueryPlan,
    QuerySkip,
    QueryTarget,
    RequirementLedger,
)


def _obligation(
    obligation_id: str,
    mention: str,
    *,
    decision: QueryDecision,
    role: PlaceRole = PlaceRole.VISIT,
    decision_reason: str | None = None,
    merged_into_obligation_id: str | None = None,
) -> PlaceObligation:
    return PlaceObligation(
        obligation_id=obligation_id,
        mention=mention,
        role=role,
        priority=ObligationPriority.PREFERRED,
        query_decision=decision,
        query_text=mention if decision is QueryDecision.QUERY else None,
        decision_reason=decision_reason,
        merged_into_obligation_id=merged_into_obligation_id,
        evidence=(
            EvidenceSpan(
                source_id="source-material",
                start=0,
                end=len(mention),
                text=mention,
            ),
        ),
    )


def test_query_plan_covers_every_query_decision_once() -> None:
    ledger = RequirementLedger(
        ledger_id="ledger-query",
        goal_revision_id="goal-revision-1",
        obligations=(
            _obligation("poi-wuhou", "武侯祠", decision=QueryDecision.QUERY),
            _obligation("poi-hotpot", "园里火锅", decision=QueryDecision.QUERY, role=PlaceRole.MEAL),
            _obligation(
                "word-afternoon",
                "下午",
                decision=QueryDecision.NOT_A_PLACE,
                role=PlaceRole.REFERENCE,
                decision_reason="时间词，不是可到访地点。",
            ),
        ),
    )

    plan = QueryPlan(
        plan_id="query-plan-1",
        ledger_id=ledger.ledger_id,
        ledger_revision=ledger.revision,
        targets=(
            QueryTarget(
                target_id="query-wuhou",
                obligation_ids=("poi-wuhou",),
                query_text="武侯祠",
                destination="成都",
                category_hints=("风景名胜",),
                rationale="用户明确希望到访。",
            ),
            QueryTarget(
                target_id="query-hotpot",
                obligation_ids=("poi-hotpot",),
                query_text="园里火锅",
                destination="成都",
                category_hints=("餐饮服务",),
                rationale="指定餐厅必须解析为真实停靠点。",
            ),
        ),
        skips=(
            QuerySkip(
                obligation_id="word-afternoon",
                decision=QueryDecision.NOT_A_PLACE,
                reason_code="temporal_phrase",
                rationale="时间词不进入地图查询。",
            ),
        ),
    )

    plan.validate_against(ledger)

    assert plan.query_obligation_ids == ("poi-wuhou", "poi-hotpot")
    assert plan.skipped_obligation_ids == ("word-afternoon",)
    assert all(target.max_candidates == 3 for target in plan.targets)


def test_query_plan_rejects_uncovered_query_obligation() -> None:
    ledger = RequirementLedger(
        ledger_id="ledger-query",
        goal_revision_id="goal-revision-1",
        obligations=(
            _obligation("poi-wuhou", "武侯祠", decision=QueryDecision.QUERY),
            _obligation("poi-hotpot", "园里火锅", decision=QueryDecision.QUERY),
        ),
    )
    plan = QueryPlan(
        plan_id="query-plan-incomplete",
        ledger_id=ledger.ledger_id,
        ledger_revision=ledger.revision,
        targets=(
            QueryTarget(
                target_id="query-wuhou",
                obligation_ids=("poi-wuhou",),
                query_text="武侯祠",
                destination="成都",
                rationale="用户明确希望到访。",
            ),
        ),
    )

    with pytest.raises(ValueError, match="query coverage mismatch"):
        plan.validate_against(ledger)


def test_query_plan_groups_merged_obligation_with_one_provider_query() -> None:
    root = _obligation(
        "poi-wuhou",
        "成都武侯祠博物馆",
        decision=QueryDecision.QUERY,
    )
    merged = _obligation(
        "poi-wuhou-alias",
        "武侯祠",
        decision=QueryDecision.MERGE,
        decision_reason="同一地点的简称。",
        merged_into_obligation_id=root.obligation_id,
    )
    ledger = RequirementLedger(
        ledger_id="ledger-merge",
        goal_revision_id="goal-revision-1",
        obligations=(root, merged),
    )

    from app.trip_agent_v3.requirements import build_query_plan

    plan = build_query_plan(
        plan_id="query-plan-merge",
        ledger=ledger,
        destination="成都",
    )

    assert len(plan.targets) == 1
    assert plan.targets[0].obligation_ids == (
        "poi-wuhou",
        "poi-wuhou-alias",
    )
    assert plan.skips == ()
    plan.validate_against(ledger)


def test_query_target_rejects_candidate_expansion_above_default_contract() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 3"):
        QueryTarget(
            target_id="query-unsafe",
            obligation_ids=("poi-ifs",),
            query_text="IFS",
            destination="成都",
            max_candidates=5,
            rationale="不应把 provider 原始结果全部写入工作区。",
        )
