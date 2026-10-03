from __future__ import annotations

from datetime import datetime, time
from app.trip_agent_v3.domain.delivery import (
    ExperienceIssue,
    ExperienceStatus,
)
from app.trip_agent_v3.domain.plan import CompiledTimeline, TimelineStop, StopKind
from app.trip_agent_v3.domain.requirements import (
    ConstraintStrength,
    DayAssignmentRequirement,
    PaceRequirement,
    RequirementLedger,
    TimeWindowRequirement,
    TransportPreferenceRequirement,
)


def _meal_window_advice(timeline: CompiledTimeline) -> tuple[ExperienceIssue, ...]:
    issues = []
    for day in timeline.days:
        for label, code, start, end in (
            ("午餐", "lunch_not_separately_planned", time(11, 30), time(14)),
            ("晚餐", "dinner_not_separately_planned", time(18), time(20)),
        ):
            window_start = datetime.combine(day.calendar_date, start, tzinfo=day.stops[0].arrival_at.tzinfo)
            window_end = datetime.combine(day.calendar_date, end, tzinfo=day.stops[0].arrival_at.tzinfo)
            if day.stops[0].arrival_at > window_start or day.stops[-1].departure_at < window_end:
                continue
            overlapping = tuple(stop for stop in day.stops if stop.arrival_at < window_end and stop.departure_at > window_start)
            if any(stop.kind is StopKind.MEAL for stop in overlapping):
                continue
            if any(
                stop.kind is StopKind.LODGING
                and (min(stop.departure_at, window_end) - max(stop.arrival_at, window_start)).total_seconds() >= 45 * 60
                for stop in overlapping
            ):
                continue
            names = tuple(dict.fromkeys(stop.name for stop in overlapping if stop.kind not in {StopKind.LODGING, StopKind.AIRPORT}))[:6]
            if not names:
                by_id = {stop.stop_id: stop.name for stop in day.stops}
                names = tuple(dict.fromkeys(
                    name for leg in day.legs
                    if leg.departure_at < window_end and leg.arrival_at > window_start
                    for name in (by_id[leg.from_stop_id], by_id[leg.to_stop_id])
                ))[:6]
            window = f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
            scope = f"（{'、'.join(names)}）" if names else ""
            issues.append(ExperienceIssue(
                code=code, severity="review", day_numbers=(day.day_number,), place_names=names,
                message=f"第 {day.day_number} 天行程覆盖 {window}{scope}，尚未单独安排{label}。",
                recommendation=f"在第 {day.day_number} 天 {window} 的行程中预留{label}时间，并说明自由用餐安排。",
            ))
    return tuple(issues)


def evaluate_timeline_constraints(
    *,
    ledger: RequirementLedger,
    timeline: CompiledTimeline,
) -> tuple[ExperienceStatus, tuple[ExperienceIssue, ...]]:
    obligations = {
        item.obligation_id: item for item in ledger.obligations
    }
    direct_locations: dict[str, list[tuple[int, TimelineStop]]] = {}
    all_locations = [
        (day.day_number, stop)
        for day in timeline.days
        for stop in day.stops
    ]
    for day_number, stop in all_locations:
        for obligation_id in stop.obligation_ids:
            direct_locations.setdefault(obligation_id, []).append(
                (day_number, stop)
            )
    stop_locations: dict[str, list[tuple[int, TimelineStop]]] = {}
    for obligation_id, locations in direct_locations.items():
        candidate_ids = {stop.candidate_id for _, stop in locations}
        stop_locations[obligation_id] = [
            (day_number, stop)
            for day_number, stop in all_locations
            if stop.candidate_id in candidate_ids
        ]
    issues: list[ExperienceIssue] = []
    for constraint in ledger.constraints:
        if isinstance(constraint, TransportPreferenceRequirement):
            preferred = {mode.value for mode in constraint.preferred_modes}
            for day in timeline.days:
                stop_by_id = {stop.stop_id: stop for stop in day.stops}
                for leg in day.legs:
                    if leg.mode == "stay":
                        continue
                    origin = stop_by_id[leg.from_stop_id]
                    destination = stop_by_id[leg.to_stop_id]
                    prefix = (
                        "required"
                        if constraint.strength is ConstraintStrength.REQUIRED
                        else "preferred"
                    )
                    if constraint.mode_policy == "only" and leg.mode not in preferred:
                        issues.append(
                            ExperienceIssue(
                                code=f"{prefix}_transport_mode_violated",
                                message=(
                                    f"第 {day.day_number} 天 {origin.name} 到 "
                                    f"{destination.name} 使用 {leg.mode}，"
                                    "未满足资料中的交通方式偏好 "
                                    f"{' / '.join(sorted(preferred))}。"
                                ),
                                recommendation=(
                                    "选择资料中的交通方式，或调整地点顺序缩短路程。"
                                ),
                                day_numbers=(day.day_number,),
                                place_names=(origin.name, destination.name),
                            )
                        )
                    if (
                        leg.mode == "walk"
                        and constraint.max_walk_minutes is not None
                        and leg.duration_min > constraint.max_walk_minutes
                    ):
                        issues.append(
                            ExperienceIssue(
                                code=f"{prefix}_walk_limit_violated",
                                message=(
                                    f"第 {day.day_number} 天 {origin.name} 到 "
                                    f"{destination.name} 需步行 {leg.duration_min} 分钟，"
                                    f"超过资料中的步行上限 "
                                    f"{constraint.max_walk_minutes} 分钟。"
                                ),
                                recommendation=(
                                    "改用资料允许的非步行方式，或调整停靠顺序以"
                                    "缩短步行距离。"
                                ),
                                day_numbers=(day.day_number,),
                                place_names=(origin.name, destination.name),
                            )
                        )
            continue
        if isinstance(constraint, PaceRequirement):
            for day in timeline.days:
                total_minutes = int(
                    (
                        day.stops[-1].departure_at
                        - day.stops[0].arrival_at
                    ).total_seconds()
                    // 60
                )
                if total_minutes <= constraint.max_day_minutes:
                    continue
                prefix = (
                    "required"
                    if constraint.strength is ConstraintStrength.REQUIRED
                    else "preferred"
                )
                overage = total_minutes - constraint.max_day_minutes
                issues.append(
                    ExperienceIssue(
                        code=f"{prefix}_pace_limit_violated",
                        message=(
                            f"第 {day.day_number} 天从 "
                            f"{day.stops[0].arrival_at.strftime('%H:%M')} "
                            f"持续到 {day.stops[-1].departure_at.strftime('%H:%M')}，"
                            f"共 {total_minutes} 分钟，超过资料中的节奏上限 "
                            f"{constraint.max_day_minutes} 分钟（超出 {overage} 分钟）。"
                        ),
                        recommendation=(
                            f"从第 {day.day_number} 天移出一个非必需停靠点，"
                            f"或拆分到其他日期，使总时长不超过 "
                            f"{constraint.max_day_minutes} 分钟。"
                        ),
                        day_numbers=(day.day_number,),
                    )
                )
            continue
        for obligation_id in constraint.subject_obligation_ids:
            locations = stop_locations.get(obligation_id)
            if not locations:
                if constraint.fixed_commitment:
                    mention = obligations[obligation_id].mention
                    issues.append(ExperienceIssue(
                        code="fixed_commitment_not_scheduled",
                        severity="blocking",
                        message=f"已确认且不可调整的预约地点 {mention} 尚未安排进当前行程。",
                        recommendation=f"在预约日期和时段安排 {mention}，或由用户明确变更预约。",
                        day_numbers=(constraint.day_number,) if constraint.day_number is not None else (),
                        obligation_ids=(obligation_id,),
                        place_names=(mention,),
                    ))
                continue
            mention = obligations[obligation_id].mention
            if isinstance(constraint, DayAssignmentRequirement):
                if any(
                    day_number == constraint.day_number
                    for day_number, _ in locations
                ):
                    continue
                day_number, _ = locations[0]
                prefix = (
                    "required"
                    if constraint.strength is ConstraintStrength.REQUIRED
                    else "preferred"
                )
                issues.append(
                    ExperienceIssue(
                        code=f"{prefix}_day_assignment_violated",
                        severity="blocking" if constraint.fixed_commitment else "review",
                        message=(
                            f"{mention} 实际安排在第 {day_number} 天，"
                            f"资料要求第 {constraint.day_number} 天。"
                        ),
                        recommendation=(
                            f"将 {mention} 调整到第 {constraint.day_number} 天，"
                            "或请用户确认变更。"
                        ),
                        day_numbers=(day_number, constraint.day_number),
                        obligation_ids=(obligation_id,),
                        place_names=(mention,),
                    )
                )
            elif isinstance(constraint, TimeWindowRequirement):
                if any(
                    (
                        constraint.day_number is None
                        or day_number == constraint.day_number
                    )
                    and constraint.earliest
                    <= stop.arrival_at.time()
                    <= constraint.latest
                    for day_number, stop in locations
                ):
                    continue
                day_number, stop = next(
                    (
                        location
                        for location in locations
                        if constraint.day_number is None
                        or location[0] == constraint.day_number
                    ),
                    locations[0],
                )
                arrival = stop.arrival_at.time()
                prefix = (
                    "required"
                    if constraint.strength is ConstraintStrength.REQUIRED
                    else "preferred"
                )
                evidence_text = " / ".join(
                    item.text for item in constraint.evidence
                )
                issues.append(
                    ExperienceIssue(
                        code=f"{prefix}_time_window_violated",
                        severity="blocking" if constraint.fixed_commitment else "review",
                        message=(
                            f"{mention} 实际于第 {day_number} 天 "
                            f"{arrival.strftime('%H:%M')} 到达，未满足资料中的"
                            f"“{evidence_text}”时段 "
                            f"{constraint.earliest.strftime('%H:%M')}–"
                            f"{constraint.latest.strftime('%H:%M')}。"
                        ),
                        recommendation=(
                            f"调整第 {day_number} 天顺序，使 {mention} 在"
                            f" {constraint.earliest.strftime('%H:%M')}–"
                            f"{constraint.latest.strftime('%H:%M')} 到达。"
                        ),
                        day_numbers=(day_number,),
                        obligation_ids=(obligation_id,),
                        place_names=(mention,),
                    )
                )
    explicit_pace = any(
        isinstance(item, PaceRequirement) for item in ledger.constraints
    )
    if not explicit_pace:
        for day in timeline.days:
            total_minutes = int(
                (
                    day.stops[-1].departure_at
                    - day.stops[0].arrival_at
                ).total_seconds()
                // 60
            )
            if total_minutes <= 720:
                continue
            issues.append(
                ExperienceIssue(
                    code="default_day_too_long",
                    message=(
                        f"第 {day.day_number} 天总时长为 {total_minutes} 分钟，"
                        "超过建议每日时长 720 分钟。"
                    ),
                    recommendation=(
                        f"拆分第 {day.day_number} 天，或移除一个低优先级停靠点。"
                    ),
                    day_numbers=(day.day_number,),
                )
            )
    issues.extend(_meal_window_advice(timeline))
    if not issues:
        return ExperienceStatus.GOOD, ()
    if any(item.severity == "blocking" for item in issues):
        return ExperienceStatus.CONFLICT, tuple(issues)
    return ExperienceStatus.NEEDS_ADJUSTMENT, tuple(issues)
