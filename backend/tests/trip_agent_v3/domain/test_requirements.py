from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.trip_agent_v3.domain import (
    CoverageDisposition,
    DispositionStatus,
    EvidenceSpan,
    ObligationPriority,
    PlaceObligation,
    PlaceRole,
    QueryDecision,
    RequirementLedger,
)


def _obligation(
    obligation_id: str,
    mention: str,
    *,
    role: PlaceRole = PlaceRole.VISIT,
    priority: ObligationPriority = ObligationPriority.PREFERRED,
    decision: QueryDecision = QueryDecision.QUERY,
    merged_into_obligation_id: str | None = None,
) -> PlaceObligation:
    return PlaceObligation(
        obligation_id=obligation_id,
        mention=mention,
        role=role,
        priority=priority,
        query_decision=decision,
        query_text=mention if decision is QueryDecision.QUERY else None,
        merged_into_obligation_id=merged_into_obligation_id,
        evidence=(
            EvidenceSpan(
                source_id="source-user",
                start=0,
                end=len(mention),
                text=mention,
            ),
        ),
    )


def test_explicit_place_coverage_cannot_silently_disappear() -> None:
    ledger = RequirementLedger(
        ledger_id="ledger-chengdu",
        goal_revision_id="goal-revision-1",
        obligations=(
            _obligation("poi-wuhou", "武侯祠", priority=ObligationPriority.REQUIRED),
            _obligation("poi-hotpot", "园里火锅", role=PlaceRole.MEAL),
            _obligation("poi-ifs", "IFS", role=PlaceRole.SHOPPING),
        ),
        dispositions=(
            CoverageDisposition(
                obligation_id="poi-wuhou",
                status=DispositionStatus.SCHEDULED,
                stop_id="stop-wuhou",
            ),
            CoverageDisposition(
                obligation_id="poi-hotpot",
                status=DispositionStatus.PENDING_CONFIRMATION,
                reason_code="branch_ambiguous",
                rationale="资料未说明具体分店，分店选择会改变路线。",
            ),
        ),
    )

    report = ledger.coverage_report()

    assert report.explicit_place_count == 3
    assert report.disposed_place_count == 2
    assert report.coverage_ratio == pytest.approx(2 / 3)
    assert report.open_obligation_ids == ("poi-ifs",)
    assert report.entries[0].status is DispositionStatus.SCHEDULED
    assert report.entries[1].status is DispositionStatus.PENDING_CONFIRMATION
    assert report.entries[2].status is DispositionStatus.UNHANDLED


def test_not_scheduled_requires_specific_reason() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        CoverageDisposition(
            obligation_id="poi-restaurant",
            status=DispositionStatus.NOT_SCHEDULED,
        )


def test_merge_must_reference_another_obligation() -> None:
    with pytest.raises(ValidationError, match="merged_into_obligation_id"):
        _obligation(
            "poi-duplicate",
            "成都大熊猫基地",
            decision=QueryDecision.MERGE,
        )


def test_query_plan_state_cannot_be_encoded_as_reference_without_reason() -> None:
    with pytest.raises(ValidationError, match="decision_reason"):
        PlaceObligation(
            obligation_id="not-a-destination",
            mention="下午",
            role=PlaceRole.REFERENCE,
            priority=ObligationPriority.OPTIONAL,
            query_decision=QueryDecision.NOT_A_PLACE,
            evidence=(
                EvidenceSpan(
                    source_id="source-user",
                    start=0,
                    end=2,
                    text="下午",
                ),
            ),
        )


def test_ledger_rejects_disposition_for_unknown_obligation() -> None:
    with pytest.raises(ValidationError, match="unknown obligation"):
        RequirementLedger(
            ledger_id="ledger-invalid",
            goal_revision_id="goal-revision-1",
            obligations=(_obligation("poi-wuhou", "武侯祠"),),
            dispositions=(
                CoverageDisposition(
                    obligation_id="poi-missing",
                    status=DispositionStatus.EXCLUDED,
                    reason_code="user_excluded",
                    rationale="用户明确排除。",
                ),
            ),
        )
