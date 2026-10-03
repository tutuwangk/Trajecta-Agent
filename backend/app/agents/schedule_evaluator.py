from __future__ import annotations

from app.agents.intensity import daily_time_minutes


_FACTUAL_PENALTY = 1_000_000
_PREFERENCE_WEIGHTS = {
    "must_visit_missing": 20_000,
    "avoid_visit_scheduled": 20_000,
    "empty_day_with_available_places": 4_000,
    "time_constraint_violated": 1_200,
    "order_constraint_violated": 900,
    "meal_slot_missing": 700,
    "meal_time_invalid": 500,
    "daily_time_over_intensity_limit": 300,
}


def evaluate_schedule_candidate(
    itinerary: dict,
    factual_issues: list[dict],
    preference_issues: list[dict],
    *,
    attempt: int,
) -> dict:
    """Score a compiled candidate without changing it.

    Lower is better. Factual invalidity is deliberately separated from quality
    so a merely imperfect itinerary can still be published.
    """
    analysis = analyze_schedule(itinerary)
    penalty = len(factual_issues) * _FACTUAL_PENALTY
    penalty += sum(_PREFERENCE_WEIGHTS.get(str(issue.get("type") or ""), 200) for issue in preference_issues)
    penalty += sum(max(0, gap["duration_min"] - 90) for gap in analysis["idle_gaps"])
    penalty += sum(max(0, rest["hotel_detour_min"] - rest["rest_duration_min"]) for rest in analysis["hotel_rests"])
    return {
        "attempt": attempt,
        "publishable": not factual_issues,
        "score": -penalty,
        "penalty": penalty,
        "factual_issues": list(factual_issues),
        "quality_issues": list(preference_issues),
        "analysis": analysis,
    }


def analyze_schedule(itinerary: dict) -> dict:
    idle_gaps: list[dict] = []
    hotel_rests: list[dict] = []
    day_summaries: list[dict] = []
    for day in itinerary.get("days") or []:
        items = day.get("items") or []
        hotel_rest_pairs = {
            (str(rest.get("after_poi_id") or ""), str(rest.get("before_poi_id") or ""))
            for rest in day.get("hotel_rest_breaks") or []
        }
        for previous, current in zip(items, items[1:]):
            if (str(previous.get("poi_id") or ""), str(current.get("poi_id") or "")) in hotel_rest_pairs:
                continue
            previous_start = _parse_time(previous.get("arrival_time"))
            current_start = _parse_time(current.get("arrival_time"))
            if previous_start is None or current_start is None:
                continue
            expected = previous_start + _int(previous.get("duration_min")) + _int(
                (previous.get("transport_to_next") or {}).get("duration_min")
            )
            gap = current_start - expected
            if gap > 30:
                idle_gaps.append(
                    {
                        "day": day.get("day"),
                        "after_poi_id": previous.get("poi_id"),
                        "before_poi_id": current.get("poi_id"),
                        "duration_min": gap,
                    }
                )
        for rest in day.get("hotel_rest_breaks") or []:
            hotel_rests.append(
                {
                    "day": day.get("day"),
                    "after_poi_id": rest.get("after_poi_id"),
                    "before_poi_id": rest.get("before_poi_id"),
                    "rest_duration_min": _int(rest.get("duration_min")),
                    "hotel_detour_min": _int(rest.get("return_to_hotel_transport_min"))
                    + _int(rest.get("depart_from_hotel_transport_min")),
                }
            )
        day_summaries.append(
            {
                "day": day.get("day"),
                "first_arrival": (items[0] if items else {}).get("arrival_time"),
                "total_outing_min": daily_time_minutes(day),
                "has_hotel_rest": bool(day.get("hotel_rest_breaks")),
            }
        )
    return {"idle_gaps": idle_gaps, "hotel_rests": hotel_rests, "days": day_summaries}


def build_replan_feedback(candidate: dict, previous_candidate: dict | None = None) -> dict:
    feedback = {
        "issues": list(candidate.get("factual_issues") or []) + list(candidate.get("quality_issues") or []),
        "schedule_analysis": dict(candidate.get("analysis") or {}),
        "strategy_options": [
            "没有真正必须上午完成的地点时，优先晚些或中午再从酒店出发",
            "调整下午地点，让晚餐后自然衔接19:00后的夜间活动",
            "只有大熊猫活跃时段、预约或开放时间等真正早间锚点存在时，才考虑回酒店休整后傍晚再次出发",
            "比较换天是否比等待或酒店往返更顺路",
        ],
    }
    if previous_candidate is not None:
        feedback["previous_attempt"] = {
            "score": previous_candidate.get("score"),
            "analysis": previous_candidate.get("analysis"),
        }
        feedback["instruction"] = "这是第二次重排。不要机械重复上一版策略；比较剩余方案并选择整体体验更好的路线。"
    return feedback


def quality_deviation_messages(issues: list[dict]) -> list[str]:
    messages: list[str] = []
    for issue in issues:
        message = str(issue.get("message") or "").strip()
        if message and message not in messages:
            messages.append(message)
    return messages


def _parse_time(value) -> int | None:
    text = str(value or "")
    if ":" not in text:
        return None
    try:
        hour, minute = text.split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
