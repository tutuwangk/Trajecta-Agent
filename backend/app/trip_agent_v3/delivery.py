from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from app.trip_agent_v3.domain.delivery import (
    CandidateSnapshot,
    DeliveryAssessment,
    DeliveryIssue,
    DeliveryIssueSeverity,
    DeliveryState,
    FactStatus,
    ReleaseDayNarrative,
    ReleaseNarrative,
    ReleaseRecord,
    RunRecord,
    RunStatus,
)
from app.trip_agent_v3.domain.facts import FactResolutionStatus
from app.trip_agent_v3.domain.plan import StopKind
from app.trip_agent_v3.domain.requirements import (
    DispositionStatus,
    ObligationPriority,
    PlaceRole,
)


class ReleasePublicationError(ValueError):
    """Raised when a non-publishable or mismatched artifact is published."""


_MODE_LABELS = {
    "walk": "步行",
    "walking": "步行",
    "taxi": "打车",
    "driving": "驾车",
    "transit": "公共交通",
    "public_transport": "公共交通",
    "stay": "原地",
}


def _issue(
    *,
    code: str,
    severity: DeliveryIssueSeverity,
    message: str,
    recommendation: str,
    day_numbers: tuple[int, ...] = (),
    obligation_ids: tuple[str, ...] = (),
    place_names: tuple[str, ...] = (),
) -> DeliveryIssue:
    return DeliveryIssue(
        code=code,
        severity=severity,
        message=message,
        recommendation=recommendation,
        day_numbers=day_numbers,
        obligation_ids=obligation_ids,
        place_names=place_names,
    )


def assess_delivery(
    *, run: RunRecord, candidate: CandidateSnapshot
) -> DeliveryAssessment:
    issues: list[DeliveryIssue] = []
    if (
        run.run_id != candidate.producing_run_id
        or run.workspace_id != candidate.workspace_id
        or run.goal_revision_id != candidate.goal_revision_id
    ):
        issues.append(
            _issue(
                code="run_candidate_lineage_mismatch",
                severity=DeliveryIssueSeverity.BLOCKING,
                message="候选方案不属于当前运行、工作区或目标修订。",
                recommendation="只加载并评估当前 run_id 产生的候选方案。",
            )
        )
    if run.status is not RunStatus.SUCCEEDED:
        issues.append(
            _issue(
                code=f"run_status_{run.status.value}",
                severity=DeliveryIssueSeverity.BLOCKING,
                message=f"当前运行状态为 {run.status.value}，没有发布资格。",
                recommendation="成功完成当前运行后重新生成候选方案。",
            )
        )

    stops_by_day_and_id = {
        (day.day_number, stop.stop_id): stop
        for day in candidate.timeline.days
        for stop in day.stops
    }
    for day in candidate.timeline.days:
        for leg in day.legs:
            if leg.fact_status is not FactResolutionStatus.ESTIMATED:
                continue
            origin = stops_by_day_and_id[(day.day_number, leg.from_stop_id)]
            destination = stops_by_day_and_id[
                (day.day_number, leg.to_stop_id)
            ]
            issues.append(
                _issue(
                    code="estimated_route_leg",
                    severity=DeliveryIssueSeverity.REVIEW,
                    message=(
                        f"第 {day.day_number} 天 {origin.name} → "
                        f"{destination.name} 的交通时长仅为空间估算。"
                    ),
                    recommendation="查询该段真实路线方式与时长后重新编译。",
                    day_numbers=(day.day_number,),
                    place_names=(origin.name, destination.name),
                )
            )

    for gap in candidate.fact_gap_report.gaps:
        issues.append(
            _issue(
                code=f"fact_gap_{gap.failure_code}",
                severity=DeliveryIssueSeverity.BLOCKING,
                message=(
                    f"第 {gap.day_number} 天 "
                    f"{' → '.join(gap.place_names)}：{gap.failure_message} "
                    f"{gap.impact}"
                ),
                recommendation="重试该事实查询或从路线中明确移除受影响地点。",
                day_numbers=(gap.day_number,),
                place_names=gap.place_names,
            )
        )

    operational_stop_ids = {
        fact.stop_id for fact in candidate.operational_facts
    }
    for day in candidate.timeline.days:
        for stop in day.stops:
            if (
                stop.kind
                not in {
                    StopKind.VISIT,
                    StopKind.MEAL,
                    StopKind.SHOPPING,
                    StopKind.PHOTO,
                }
                or stop.stop_id in operational_stop_ids
            ):
                continue
            issues.append(
                _issue(
                    code="operational_fact_missing",
                    severity=DeliveryIssueSeverity.BLOCKING,
                    message=(
                        f"第 {day.day_number} 天 {stop.name} 在 "
                        f"{stop.arrival_at.strftime('%H:%M')} 到访，但没有"
                        "可追溯的营业、闭馆、停止入场或预约核验。"
                    ),
                    recommendation=(
                        "仅对这个已排入行程的停靠点补充带来源的运营事实，"
                        "再重新进入发布门禁。"
                    ),
                    day_numbers=(day.day_number,),
                    place_names=(stop.name,),
                )
            )

    has_specific_fact_issue = any(
        issue.code == "estimated_route_leg"
        or issue.code == "operational_fact_missing"
        or issue.code.startswith("fact_gap_")
        for issue in issues
    )
    if (
        candidate.fact_status in {FactStatus.DEGRADED, FactStatus.FAILED}
        and not has_specific_fact_issue
    ):
        affected_days = tuple(
            day.day_number for day in candidate.timeline.days
        )
        affected_places = tuple(
            dict.fromkeys(
                stop.name
                for day in candidate.timeline.days
                for stop in day.stops
                if stop.kind
                not in {StopKind.LODGING, StopKind.AIRPORT}
            )
        )
        issues.append(
            _issue(
                code="fact_status_inconsistent",
                severity=DeliveryIssueSeverity.BLOCKING,
                message=(
                    "候选方案的事实状态不是 verified，但没有与具体路线段或"
                    "停靠点对应的事实缺口。"
                ),
                recommendation=(
                    "定位下列日期和地点的事实来源，补充具体缺口后重新编译；"
                    "不得用汇总状态代替可执行风险。"
                ),
                day_numbers=affected_days,
                place_names=affected_places,
            )
        )

    for unresolved in candidate.unresolved_places:
        issues.append(
            _issue(
                code="place_unresolved",
                severity=DeliveryIssueSeverity.BLOCKING,
                message=f"{unresolved.mention} 尚未完成地点消歧：{unresolved.reason}",
                recommendation=unresolved.clarification_question
                or "确认具体地点后重新规划。",
                obligation_ids=unresolved.obligation_ids,
                place_names=(unresolved.mention,),
            )
        )

    for entry in candidate.coverage.entries:
        if entry.status is DispositionStatus.UNHANDLED:
            issues.append(
                _issue(
                    code="explicit_place_unhandled",
                    severity=DeliveryIssueSeverity.BLOCKING,
                    message=f"显式地点 {entry.mention} 尚未被处置。",
                    recommendation="安排、请求确认，或给出不安排的具体原因。",
                    obligation_ids=(entry.obligation_id,),
                    place_names=(entry.mention,),
                )
            )
        elif (
            entry.priority is ObligationPriority.REQUIRED
            and entry.status is not DispositionStatus.SCHEDULED
        ):
            issues.append(
                _issue(
                    code="required_place_not_scheduled",
                    severity=DeliveryIssueSeverity.REVIEW,
                    message=(
                        f"用户要求的地点 {entry.mention} 未进入行程："
                        f"{entry.rationale or '未提供原因'}"
                    ),
                    recommendation="可调整其他停留以加入该地点，或查看本次取舍原因。",
                    obligation_ids=(entry.obligation_id,),
                    place_names=(entry.mention,),
                )
            )
        elif (
            entry.role is PlaceRole.MEAL
            and entry.status is not DispositionStatus.SCHEDULED
        ):
            issues.append(
                _issue(
                    code="named_meal_not_scheduled",
                    severity=DeliveryIssueSeverity.REVIEW,
                    message=(
                        f"用户明确餐饮地点 {entry.mention} 未成为真实 meal stop："
                        f"{entry.rationale or '未提供原因'}"
                    ),
                    recommendation=(
                        "把该餐厅作为带已选 candidate_id 的餐饮停靠点，"
                        "或保留当前取舍原因供用户调整。"
                    ),
                    obligation_ids=(entry.obligation_id,),
                    place_names=(entry.mention,),
                )
            )

    issues.extend(
        _issue(
            code=item.code,
            severity=DeliveryIssueSeverity(item.severity),
            message=item.message,
            recommendation=item.recommendation,
            day_numbers=item.day_numbers,
            obligation_ids=item.obligation_ids,
            place_names=item.place_names,
        )
        for item in candidate.experience_issues
    )

    has_blocker = any(
        item.severity is DeliveryIssueSeverity.BLOCKING for item in issues
    )
    has_estimated_routes = any(
        leg.fact_status is FactResolutionStatus.ESTIMATED
        for day in candidate.timeline.days
        for leg in day.legs
    )
    state = (
        DeliveryState.BLOCKED
        if has_blocker
        else DeliveryState.REVIEW_REQUIRED
        if candidate.fact_status is not FactStatus.VERIFIED or has_estimated_routes
        else DeliveryState.PUBLISHABLE
    )
    return DeliveryAssessment(
        candidate_snapshot_id=candidate.candidate_snapshot_id,
        producing_run_id=candidate.producing_run_id,
        state=state,
        may_publish=state is DeliveryState.PUBLISHABLE,
        issues=tuple(issues),
    )


def publish_release(
    *,
    release_id: str,
    run: RunRecord,
    candidate: CandidateSnapshot,
    assessment: DeliveryAssessment,
    published_at: datetime | None = None,
) -> ReleaseRecord:
    if assessment.state is not DeliveryState.PUBLISHABLE:
        raise ReleasePublicationError("delivery assessment is not publishable")
    if not assessment.may_publish:
        raise ReleasePublicationError("delivery assessment denied publication")
    if run.status is not RunStatus.SUCCEEDED:
        raise ReleasePublicationError("run did not succeed")
    if (
        assessment.candidate_snapshot_id != candidate.candidate_snapshot_id
        or assessment.producing_run_id != run.run_id
        or candidate.producing_run_id != run.run_id
        or candidate.workspace_id != run.workspace_id
        or candidate.goal_revision_id != run.goal_revision_id
    ):
        raise ReleasePublicationError(
            "run, candidate, and assessment lineage do not match"
        )
    return ReleaseRecord(
        release_id=release_id,
        workspace_id=run.workspace_id,
        producing_run_id=run.run_id,
        candidate_snapshot_id=candidate.candidate_snapshot_id,
        goal_revision_id=run.goal_revision_id,
        narrative=_build_release_narrative(candidate),
        published_at=published_at or datetime.now(timezone.utc),
    )


def _build_release_narrative(
    candidate: CandidateSnapshot,
) -> ReleaseNarrative:
    day_summaries: list[ReleaseDayNarrative] = []
    for day in candidate.timeline.days:
        stop_parts = [
            (
                f"{stop.arrival_at.strftime('%H:%M')} 到达 {stop.name}"
                f"（停留 {stop.stay_duration_min} 分钟）"
            )
            for stop in day.stops
        ]
        leg_parts = [
            (
                f"{_MODE_LABELS.get(leg.mode, leg.mode)} "
                f"{leg.duration_min} 分钟"
                f"（{leg.departure_at.strftime('%H:%M')}–"
                f"{leg.arrival_at.strftime('%H:%M')}）"
            )
            for leg in day.legs
        ]
        day_summaries.append(
            ReleaseDayNarrative(
                day_number=day.day_number,
                title=day.title,
                summary=(
                    "停靠：" + "；".join(stop_parts)
                    + "。交通：" + "；".join(leg_parts) + "。"
                ),
            )
        )
    scheduled_names = [
        entry.mention
        for entry in candidate.coverage.entries
        if entry.status is DispositionStatus.SCHEDULED
    ]
    return ReleaseNarrative(
        overview=(
            f"共 {len(candidate.timeline.days)} 天；"
            f"显式地点已闭环 {candidate.coverage.disposed_place_count}/"
            f"{candidate.coverage.explicit_place_count}。"
            + (
                "已安排：" + "、".join(scheduled_names) + "。"
                if scheduled_names
                else "本次没有需要安排的显式地点。"
            )
            + "以下时间、交通与运营事实均来自当前运行通过门禁的候选。"
        ),
        days=tuple(day_summaries),
    )


def release_for_run(
    releases: Iterable[ReleaseRecord], *, run_id: str
) -> ReleaseRecord | None:
    matches = [
        release for release in releases if release.producing_run_id == run_id
    ]
    if not matches:
        return None
    return max(matches, key=lambda release: release.published_at)
