from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.env import load_project_env


SCENARIOS = [
    {
        "id": "chengdu_one_day",
        "raw_input": "成都一日游，武侯祠必去，希望路线顺路、不要太赶。",
        "notes": "想去武侯祠、锦里、人民公园，午餐想吃陈麻婆豆腐。",
        "user_profile": {
            "destination": "成都",
            "start_date": "2026-07-20",
            "days": 1,
            "route_goal": "balanced",
            "transport_preference": ["walking", "taxi"],
            "constraints": {"physical_intensity": "medium", "must_visit": ["武侯祠"]},
        },
    },
    {
        "id": "chengdu_two_day_hotel_night",
        "raw_input": "成都两日游，住春熙路附近，成都博物馆必去，九眼桥安排在晚上。",
        "notes": "成都博物馆、人民公园、宽窄巷子、九眼桥、马旺子川小馆。",
        "user_profile": {
            "destination": "成都",
            "start_date": "2026-07-20",
            "days": 2,
            "hotel_name": "春熙路附近酒店",
            "route_goal": "balanced",
            "transport_preference": ["taxi", "public_transport"],
            "constraints": {"physical_intensity": "medium", "must_visit": ["成都博物馆"]},
        },
        "expected_time_windows": {"九眼桥": "night"},
    },
    {
        "id": "shanghai_one_day_night_anchor",
        "raw_input": "上海一日游，晚上去外滩，白天想看博物馆和老城。",
        "notes": "上海博物馆、豫园、南京东路步行街、外滩，想安排一次正常午餐。",
        "user_profile": {
            "destination": "上海",
            "start_date": "2026-07-21",
            "days": 1,
            "route_goal": "citywalk",
            "transport_preference": ["walking", "public_transport", "taxi"],
            "constraints": {"physical_intensity": "medium", "must_visit": ["外滩"]},
        },
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live DeepSeek + Amap acceptance scenarios in an isolated database.")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=len(SCENARIOS))
    args = parser.parse_args()

    load_project_env()
    missing = [name for name in ("LLM_API_KEY", "AMAP_API_KEY") if not os.getenv(name)]
    if missing:
        print(json.dumps({"ok": False, "error": "missing_configuration", "missing": missing}, ensure_ascii=False))
        return 2

    with tempfile.TemporaryDirectory(prefix="trajecta-live-") as temp_dir:
        os.environ["DATABASE_PATH"] = os.path.join(temp_dir, "acceptance.sqlite3")
        from fastapi.testclient import TestClient
        from main import create_app

        results = []
        with TestClient(create_app()) as client:
            start = max(0, args.start)
            for scenario in SCENARIOS[start : start + max(0, args.limit)]:
                results.append(run_scenario(client, scenario))
        summary = {
            "ok": all(result["technical_ok"] and result["product_checks_passed"] for result in results),
            "scenario_count": len(results),
            "technical_success_count": sum(1 for result in results if result["technical_ok"]),
            "product_pass_count": sum(1 for result in results if result["product_checks_passed"]),
            "results": results,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["ok"] else 1


def run_scenario(client, scenario: dict[str, Any]) -> dict[str, Any]:
    started_at = time.perf_counter()
    created = client.post(
        "/sessions",
        json={
            "raw_input": scenario["raw_input"],
            "notes": scenario["notes"],
            "user_profile": scenario["user_profile"],
        },
    ).json()
    if not created.get("ok"):
        return failure_result(scenario, "create_session", created, started_at)
    session_id = created["data"]["session_id"]
    recognized = client.post(f"/sessions/{session_id}/recognize-places").json()
    if not recognized.get("ok"):
        return failure_result(scenario, "recognize_places", recognized, started_at)
    planned = client.post(
        f"/sessions/{session_id}/plan",
        headers={"Idempotency-Key": f"live-{scenario['id']}"},
    ).json()
    if not planned.get("ok"):
        return failure_result(scenario, "plan", planned, started_at)
    session = client.get(f"/sessions/{session_id}").json()
    if not session.get("ok"):
        return failure_result(scenario, "read_session", session, started_at)

    data = session["data"]
    run = data.get("latest_planning_run") or {}
    state = data.get("itinerary_state") or {}
    itinerary = state.get("itinerary") or {}
    verification = state.get("verification") or {}
    checks = product_checks(scenario, itinerary, verification)
    return {
        "id": scenario["id"],
        "technical_ok": run.get("status") == "completed" and itinerary.get("result_status") in {"verified", "degraded"},
        "terminal_status": run.get("status"),
        "result_status": itinerary.get("result_status") or run.get("result_status"),
        "error_code": run.get("error_code") or "",
        "release_decision": run.get("release_decision") or verification.get("release_decision") or {},
        "degradation_reasons": verification.get("degradation_reasons") or [],
        "elapsed_seconds": round(time.perf_counter() - started_at, 2),
        "recognized_places": [
            row.get("grounded_poi", {}).get("standard_name")
            for row in data.get("pois") or []
            if row.get("grounded_poi", {}).get("standard_name")
        ],
        "days": [
            {
                "day": day.get("day"),
                "total_outing_min": day.get("total_outing_min"),
                "places": [
                    {
                        "name": item.get("name"),
                        "arrival_time": item.get("arrival_time"),
                        "duration_min": item.get("duration_min"),
                    }
                    for item in day.get("items") or []
                ],
                "meals": [meal.get("label") or meal.get("slot") for meal in day.get("meal_breaks") or []],
                "meal_slots": day.get("meal_slots") or [],
                "segments": day.get("segments") or [],
            }
            for day in itinerary.get("days") or []
        ],
        "verification_issues": [
            {
                "type": issue.get("type"),
                "severity": issue.get("severity"),
                "day": issue.get("day"),
                "message": issue.get("message"),
            }
            for issue in verification.get("issues") or []
        ],
        "metrics": _acceptance_metrics(run.get("metrics") or {}),
        "fact_requests": (run.get("debug") or {}).get("fact_requests") or [],
        "product_checks_passed": not checks,
        "product_check_failures": checks,
    }


def product_checks(scenario: dict[str, Any], itinerary: dict, verification: dict) -> list[str]:
    failures: list[str] = []
    days = itinerary.get("days") or []
    expected_days = int(scenario["user_profile"]["days"])
    if len(days) != expected_days:
        failures.append(f"day_count:{len(days)}!={expected_days}")
    ceiling = {"low": 540, "medium": 660, "high": 780}.get(
        scenario["user_profile"].get("constraints", {}).get("physical_intensity", "medium"),
        660,
    )
    names: list[str] = []
    arrivals: dict[str, int] = {}
    for day in days:
        total = day.get("total_outing_min")
        if isinstance(total, int) and total > ceiling:
            failures.append(f"day_{day.get('day')}_over_ceiling:{total}>{ceiling}")
        if not day.get("items"):
            failures.append(f"day_{day.get('day')}_empty")
        if isinstance(total, int) and total >= 240:
            required_slots = {
                str(slot.get("slot") or "")
                for slot in day.get("meal_slots") or []
                if str(slot.get("requirement") or "required") == "required"
            }
            if not required_slots.intersection({"lunch", "dinner"}):
                failures.append(f"day_{day.get('day')}_formal_meal_missing")
        for item in day.get("items") or []:
            item_name = str(item.get("name") or "").strip()
            names.append(item_name.lower())
            parsed_arrival = _parse_time(item.get("arrival_time"))
            if item_name and parsed_arrival is not None:
                arrivals[item_name] = parsed_arrival
    names = [name for name in names if name]
    if len(names) != len(set(names)):
        failures.append("duplicate_place_name")
    if verification.get("blocking_issues"):
        failures.append("blocking_issues_present")
    if verification.get("publishable") is False:
        failures.append("not_publishable")
    issue_types = {str(issue.get("type") or "") for issue in verification.get("issues") or []}
    unacceptable_time_issues = {
        "fixed_time_constraint_violated",
        "time_constraint_violated",
        "segment_time_violated",
    }
    for issue_type in sorted(issue_types.intersection(unacceptable_time_issues)):
        failures.append(f"unresolved_time_issue:{issue_type}")
    if "不要太赶" in scenario.get("raw_input", "") and "daily_time_over_intensity_limit" in issue_types:
        failures.append("pace_preference_violated")
    for expected_name, window in (scenario.get("expected_time_windows") or {}).items():
        matching = [value for name, value in arrivals.items() if name == expected_name]
        if not matching:
            matching = [
                value
                for name, value in arrivals.items()
                if expected_name in name and not any(token in name for token in ("店", "馆", "火锅", "餐", "面"))
            ]
        if not matching:
            failures.append(f"expected_timed_place_missing:{expected_name}")
            continue
        earliest = {"evening": 17 * 60 + 30, "night": 19 * 60}.get(window, 0)
        if matching[0] < earliest:
            failures.append(f"time_window_violated:{expected_name}:{window}")
    visible = "\n".join(_visible_texts(itinerary))
    if any(token in visible for token in ("must_include", "final_decision", "user_override", "<think>")):
        failures.append("technical_copy_leak")
    return failures


def _visible_texts(itinerary: dict) -> list[str]:
    values = [
        str((itinerary.get("route_summary") or {}).get("main_message") or ""),
        *[str(item) for item in itinerary.get("global_risks") or []],
        *[str(item) for item in itinerary.get("revision_notes") or []],
    ]
    for day in itinerary.get("days") or []:
        values.extend([str(day.get("theme") or ""), str(day.get("summary") or "")])
        values.extend(str(item) for item in day.get("risk_notes") or [])
        for item in day.get("items") or []:
            values.append(str(item.get("reason") or ""))
            values.extend(str(note) for note in item.get("risk_notes") or [])
        for item in day.get("removed_pois") or []:
            values.append(str(item.get("reason") or ""))
    return [value for value in values if value]


def _acceptance_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "planning_attempts",
        "planning_duration_ms",
        "fact_completeness_rate",
        "amap_degradation_rate",
        "fact_request_count",
        "result_status",
        "release_reason",
        "copy_fingerprint_match",
    )
    return {key: metrics.get(key) for key in keys if key in metrics}


def _parse_time(value: Any) -> int | None:
    try:
        hour, minute = str(value or "").split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def failure_result(scenario: dict[str, Any], stage: str, response: dict, started_at: float) -> dict[str, Any]:
    error = response.get("error") or {}
    return {
        "id": scenario["id"],
        "technical_ok": False,
        "terminal_status": "failed",
        "result_status": "failed",
        "error_stage": stage,
        "error_code": error.get("code") or "unknown_error",
        "error_message": error.get("message") or "",
        "elapsed_seconds": round(time.perf_counter() - started_at, 2),
        "product_checks_passed": False,
        "product_check_failures": [f"{stage}_failed"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
