from __future__ import annotations

PREFERENCE_DOMAINS = {
    "must_places",
    "time_preferences",
    "order_preferences",
    "pace",
    "meal_arrangement",
}


def build_planning_preferences(planning_decisions: list[dict] | None = None) -> dict:
    preferences: dict[str, str] = {}
    for decision in planning_decisions or []:
        choice_id = str(decision.get("choice_id") or "").strip()
        domain = str(decision.get("domain") or _infer_domain_from_choice(choice_id)).strip()
        if not choice_id or domain not in PREFERENCE_DOMAINS:
            continue
        preferences[domain] = choice_id
    return preferences


def _infer_domain_from_choice(choice_id: str) -> str:
    if choice_id in {"keep_must_places", "keep_time_preferences"}:
        return "must_places"
    if choice_id == "keep_order_preferences":
        return "order_preferences"
    if choice_id == "relax_pace":
        return "pace"
    if choice_id in {"use_nearby_meal", "drop_optional_for_meal"}:
        return "meal_arrangement"
    return ""
