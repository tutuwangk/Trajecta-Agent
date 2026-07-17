from __future__ import annotations

import hashlib
import json
import re

from app.core import AppError


def route_fact_fingerprint(itinerary: dict) -> str:
    payload = {
        "destination": itinerary.get("destination"),
        "days": [
            {
                "day": day.get("day"),
                "strategy": day.get("day_strategy"),
                "hotel_departure_transport_min": day.get("hotel_departure_transport_min"),
                "hotel_return_transport_min": day.get("hotel_return_transport_min"),
                "items": [
                    {
                        "poi_id": item.get("poi_id"),
                        "arrival_time": item.get("arrival_time"),
                        "duration_min": item.get("duration_min"),
                        "transport_to_next": item.get("transport_to_next"),
                    }
                    for item in day.get("items") or []
                ],
                "meal_breaks": day.get("meal_breaks") or [],
                "segments": day.get("segments") or [],
                "hotel_rest_breaks": day.get("hotel_rest_breaks") or [],
            }
            for day in itinerary.get("days") or []
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_copy_preserved_route_facts(before_fingerprint: str, itinerary: dict) -> None:
    if route_fact_fingerprint(itinerary) != before_fingerprint:
        raise AppError(
            "文案生成试图修改已确认路线事实，系统已阻止发布。",
            code="copy_fact_drift",
            step="copywriting",
        )


def enforce_copy_day_count(itinerary: dict, user_profile: dict) -> None:
    expected_days = int(user_profile.get("days") or len(itinerary.get("days") or []) or 1)
    summary = itinerary.get("route_summary")
    if not isinstance(summary, dict):
        return
    message = str(summary.get("main_message") or "")
    mentioned = _mentioned_day_counts(message)
    if mentioned and any(value != expected_days for value in mentioned):
        summary["main_message"] = _deterministic_main_message(expected_days, user_profile)


def _mentioned_day_counts(text: str) -> list[int]:
    values = [int(value) for value in re.findall(r"(\d{1,2})\s*天", text)]
    chinese = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    values.extend(chinese[value] for value in re.findall(r"([一二三四五六七八九十])天", text) if value in chinese)
    return values


def _deterministic_main_message(days: int, user_profile: dict) -> str:
    goal = {
        "food_first": "美食体验",
        "photo_first": "拍照体验",
        "citywalk": "城市漫步",
        "relaxed": "轻松节奏",
    }.get(str(user_profile.get("route_goal") or ""), "路线连贯和游玩节奏")
    return f"已为你整理出 {days} 天路线，优先兼顾{goal}。"
