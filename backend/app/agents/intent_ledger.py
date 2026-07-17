from __future__ import annotations

import re
from typing import Any

from app.agents.intensity import daily_time_ceiling_minutes, daily_time_limit_minutes
from app.domain.planning import IntentLedger, PlanningCommitment, PlanningEnvelope


_DAY_MARKER = re.compile(r"(?:第\s*([一二三四五六七八九十\d]+)\s*天|day\s*(\d+))", re.IGNORECASE)
_CHINESE_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def build_intent_ledger(
    *,
    user_profile: dict,
    runtime_pois: list[dict],
    time_constraints: list[dict] | None,
    order_constraints: list[dict] | None,
    user_request: str,
) -> IntentLedger:
    commitments: list[PlanningCommitment] = []
    seen: set[tuple[Any, ...]] = set()
    allowed_ids = {str(poi.get("poi_id") or "") for poi in runtime_pois if poi.get("poi_id")}

    def add(**payload: Any) -> None:
        key = (
            payload.get("kind"),
            payload.get("poi_id", ""),
            tuple(payload.get("related_poi_ids") or []),
            payload.get("preferred_day"),
            payload.get("preferred_window", ""),
            payload.get("fixed_time", ""),
            payload.get("meal_slot", ""),
        )
        if key in seen:
            return
        seen.add(key)
        payload["commitment_id"] = f"intent_{len(commitments) + 1:03d}"
        commitments.append(PlanningCommitment.model_validate(payload))

    must_names = [str(name).strip() for name in user_profile.get("constraints", {}).get("must_visit", []) if str(name).strip()]
    for poi in runtime_pois:
        poi_id = str(poi.get("poi_id") or "")
        name = _poi_name(poi)
        if not poi_id or not name:
            continue
        if poi.get("user_override") == "must_include" or any(raw in name or raw in _raw_names(poi) for raw in must_names):
            add(kind="visit", strength="strong_preference", poi_id=poi_id, source_text=f"必去：{name}")

    for constraint in time_constraints or []:
        poi_id = str(constraint.get("poi_id") or "")
        if poi_id not in allowed_ids:
            continue
        fixed_time = str(
            constraint.get("fixed_time")
            or constraint.get("appointment_time")
            or constraint.get("objective_deadline")
            or ""
        )
        add(
            kind="time",
            strength="hard_anchor" if constraint.get("strength") == "hard" and fixed_time else "strong_preference",
            poi_id=poi_id,
            preferred_window=str(constraint.get("preferred_window") or ""),
            fixed_time=fixed_time,
            source_text=str(constraint.get("source_text") or "")[:240],
        )

    for constraint in order_constraints or []:
        before_id = str(constraint.get("before_poi_id") or "")
        after_id = str(constraint.get("after_poi_id") or "")
        if before_id in allowed_ids and after_id in allowed_ids:
            add(
                kind="order",
                strength="strong_preference",
                related_poi_ids=[before_id, after_id],
                source_text=f"先去{constraint.get('before') or before_id}，再去{constraint.get('after') or after_id}"[:240],
            )

    for preferred_day, section in _day_sections(user_request, int(user_profile.get("days") or 1)):
        for poi in runtime_pois:
            poi_id = str(poi.get("poi_id") or "")
            if poi_id not in allowed_ids or not _poi_mentioned_in_text(poi, section):
                continue
            add(
                kind="day",
                strength="strong_preference",
                poi_id=poi_id,
                preferred_day=preferred_day,
                source_text=section[:240],
            )

    # A place explicitly named by the user still represents intent even when it
    # is not introduced with a rigid marker such as "must visit" or "Day 2".
    # Keep it as a soft preference so the planner can trade it off when the day
    # would otherwise become unrealistic, while making silent omission visible
    # to candidate scoring and the final experience status.
    for poi in runtime_pois:
        poi_id = str(poi.get("poi_id") or "")
        if poi_id not in allowed_ids or not _poi_mentioned_in_text(poi, user_request):
            continue
        add(
            kind="visit",
            strength="soft_preference",
            poi_id=poi_id,
            source_text=_mention_evidence(poi, user_request)[:240],
        )

    for poi in runtime_pois:
        poi_id = str(poi.get("poi_id") or "")
        if poi_id not in allowed_ids or not _is_meal_candidate(poi):
            continue
        evidence = " ".join([user_request, *[str(item) for item in poi.get("contexts") or []]])
        if not _explicit_meal_signal(poi, evidence):
            continue
        add(
            kind="meal",
            strength="strong_preference",
            poi_id=poi_id,
            meal_slot=_meal_slot(evidence),
            source_text=_meal_evidence(poi, evidence)[:240],
        )

    return IntentLedger(commitments=commitments)


def build_planning_envelope(
    *,
    user_profile: dict,
    intent_ledger: IntentLedger,
    district_summary: list[dict],
    must_poi_ids: list[str],
) -> PlanningEnvelope:
    fixed_anchors = [item for item in intent_ledger.commitments if item.strength == "hard_anchor"]
    preferred_days: dict[int, list[str]] = {}
    explicit_meals: list[str] = []
    for commitment in intent_ledger.commitments:
        if commitment.kind == "day" and commitment.preferred_day and commitment.poi_id:
            preferred_days.setdefault(commitment.preferred_day, []).append(commitment.poi_id)
        if commitment.kind == "meal" and commitment.poi_id:
            explicit_meals.append(commitment.poi_id)
    protected = list(
        dict.fromkeys(
            [*must_poi_ids, *[item.poi_id for item in fixed_anchors if item.poi_id]]
        )
    )
    return PlanningEnvelope(
        comfort_target_min=daily_time_limit_minutes(user_profile),
        release_ceiling_min=daily_time_ceiling_minutes(user_profile),
        protected_poi_ids=protected,
        fixed_anchors=fixed_anchors,
        preferred_day_poi_ids={day: list(dict.fromkeys(poi_ids)) for day, poi_ids in preferred_days.items()},
        explicit_meal_poi_ids=list(dict.fromkeys(explicit_meals)),
        district_clusters={
            str(item.get("district") or "未分区"): [str(poi_id) for poi_id in item.get("poi_ids") or []]
            for item in district_summary
        },
    )


def _day_sections(text: str, max_days: int) -> list[tuple[int, str]]:
    matches = list(_DAY_MARKER.finditer(text or ""))
    sections: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        raw_day = match.group(1) or match.group(2) or ""
        day = int(raw_day) if raw_day.isdigit() else _CHINESE_NUMBERS.get(raw_day.replace(" ", ""), 0)
        if day < 1 or day > max_days:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((day, text[match.start():end].strip()))
    return sections


def _poi_mentioned_in_text(poi: dict, text: str) -> bool:
    return any(name and name in text for name in _poi_names(poi))


def _poi_names(poi: dict) -> list[str]:
    values = [poi.get("raw_name"), poi.get("standard_name"), poi.get("name"), *(poi.get("raw_names") or [])]
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


def _raw_names(poi: dict) -> str:
    return " ".join(_poi_names(poi))


def _poi_name(poi: dict) -> str:
    return str(poi.get("standard_name") or poi.get("name") or poi.get("raw_name") or "").strip()


def _is_meal_candidate(poi: dict) -> bool:
    semantics = poi.get("planning_semantics") or {}
    capability = str(semantics.get("meal_capability") or "")
    if capability in {"breakfast", "lunch", "dinner", "lunch_dinner"}:
        return True
    category = str(poi.get("category") or poi.get("category_normalized") or "").lower()
    return category == "restaurant" or "餐饮" in str(poi.get("category_raw") or "")


def _explicit_meal_signal(poi: dict, evidence: str) -> bool:
    names = _poi_names(poi)
    if not any(name in evidence for name in names):
        return False
    return any(token in evidence for token in ["想吃", "吃一次", "午餐", "晚餐", "正餐", "火锅", "烤鸭", "小面", "餐饮", "吃饭"])


def _meal_slot(evidence: str) -> str:
    if "早餐" in evidence or "早饭" in evidence:
        return "breakfast"
    if "午餐" in evidence or "中午" in evidence:
        return "lunch"
    if "晚餐" in evidence or "晚上" in evidence or "夜宵" in evidence:
        return "dinner"
    return ""


def _meal_evidence(poi: dict, evidence: str) -> str:
    name = _poi_name(poi)
    for sentence in re.split(r"[。；;\n]", evidence):
        if any(raw_name in sentence for raw_name in _poi_names(poi)):
            return sentence.strip()
    return f"用户指定餐饮：{name}"


def _mention_evidence(poi: dict, evidence: str) -> str:
    for sentence in re.split(r"[。；;，,\n]", evidence):
        if any(raw_name in sentence for raw_name in _poi_names(poi)):
            return sentence.strip()
    return f"用户提及：{_poi_name(poi)}"
