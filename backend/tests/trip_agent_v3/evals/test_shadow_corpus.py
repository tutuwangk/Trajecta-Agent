from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.trip_agent_v3.shadow_eval import (
    aggregate_shadow_report,
    validate_shadow_scenarios,
)
from app.trip_agent_v3.domain.delivery import RunStatus
from scripts.run_trip_agent_v3_shadow import (
    _arguments,
    _execute_with_scenario_control,
    _has_nonretryable_provider_blocker,
    _is_balance_blocked_case,
    _run_bounded_cases,
)


CORPUS = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "agent_v3"
    / "shadow_scenarios.json"
)


def test_shadow_runner_accepts_explicit_flash_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_trip_agent_v3_shadow.py",
            "--output-dir",
            "/tmp/v3-shadow",
            "--planner-model",
            "flash",
        ],
    )

    assert _arguments().planner_model == "flash"


def test_shadow_corpus_covers_real_cutover_distribution() -> None:
    scenarios = json.loads(CORPUS.read_text(encoding="utf-8"))

    validation = validate_shadow_scenarios(scenarios)

    assert validation == {
        "valid": True,
        "scenario_count": 30,
        "city_count": 6,
        "cities": ["上海", "北京", "广州", "成都", "杭州", "西安"],
        "day_counts": [1, 2, 3, 4, 5],
        "cancellation_case_count": 2,
        "errors": [],
    }
    assert sum("clarification_response" in item for item in scenarios) >= 6
    assert sum(
        item.get("evaluation_mode") == "cancellation"
        for item in scenarios
    ) == 2
    assert all("酒店" in item["raw_request"] or "宾馆" in item["raw_request"] or "公寓" in item["raw_request"] or "饭店" in item["raw_request"] for item in scenarios)


def test_shadow_aggregate_never_promotes_unexercised_gates() -> None:
    validation = {
        "valid": True,
        "scenario_count": 30,
        "city_count": 6,
        "cities": ["A", "B", "C", "D", "E", "F"],
        "day_counts": [1, 2, 3, 4, 5],
        "cancellation_case_count": 2,
        "errors": [],
    }
    complete_checks = {
        "explicit_place_coverage": True,
        "named_meal_complete": True,
        "candidate_context_cap": True,
        "one_search_per_query_target": True,
        "scheduled_fact_scope": True,
        "timeline_complete": True,
        "fact_gaps_explained": True,
        "operational_fact_contract": True,
        "strict_publication": True,
        "release_lineage": True,
        "cancelled_artifact_safety": None,
        "blocker_specificity": True,
    }
    cases = [
        {
            "case_id": f"case-{index}",
            "run_status": "succeeded",
            "delivery_state": "publishable",
            "release_id": f"release-{index}",
            "metrics": {"wall_clock_ms": 1_000},
            "checks": complete_checks,
        }
        for index in range(30)
    ]

    report = aggregate_shadow_report(
        corpus_validation=validation,
        cases=cases,
    )

    assert report["corpus_distribution_gate"] is True
    assert len(report["hard_gates"]) == 13
    assert report["hard_gates"]["11_cancelled_run_artifact_safety"] is None
    assert (
        report["hard_gates"][
            "13_human_review_and_seven_day_no_p0_p1"
        ]
        is None
    )
    assert report["cutover_eligible"] is False


def test_shadow_distribution_rejects_provider_blocked_started_cases() -> None:
    validation = {
        "valid": True,
        "scenario_count": 30,
        "city_count": 6,
        "cities": ["A", "B", "C", "D", "E", "F"],
        "day_counts": [1, 2, 3, 4, 5],
        "cancellation_case_count": 2,
        "errors": [],
    }
    cases = [
        {
            "case_id": f"case-{index}",
            "evaluation_mode": "journey",
            "run_status": "needs_resume",
            "delivery_state": None,
            "release_id": None,
            "provider_blocker": {
                "reason_code": "provider_balance_insufficient"
            },
            "metrics": {"wall_clock_ms": 1},
            "checks": {},
        }
        for index in range(30)
    ]

    report = aggregate_shadow_report(
        corpus_validation=validation,
        cases=cases,
    )

    assert report["case_count"] == 30
    assert report["corpus_distribution_gate"] is False
    assert report["cutover_eligible"] is False


def test_shadow_aggregate_uses_cancel_cases_only_for_cancel_gate() -> None:
    validation = {
        "valid": True,
        "scenario_count": 30,
        "city_count": 6,
        "cities": ["A", "B", "C", "D", "E", "F"],
        "day_counts": [1, 2, 3, 4, 5],
        "cancellation_case_count": 2,
        "errors": [],
    }
    journey_checks = {
        "explicit_place_coverage": True,
        "named_meal_complete": True,
        "candidate_context_cap": True,
        "one_search_per_query_target": True,
        "scheduled_fact_scope": True,
        "timeline_complete": True,
        "fact_gaps_explained": True,
        "operational_fact_contract": True,
        "strict_publication": True,
        "release_lineage": True,
        "cancelled_artifact_safety": None,
        "blocker_specificity": True,
    }
    cases = [
        {
            "case_id": f"journey-{index}",
            "evaluation_mode": "journey",
            "run_status": "succeeded",
            "delivery_state": "publishable",
            "release_id": f"release-{index}",
            "metrics": {"wall_clock_ms": 1_000},
            "checks": journey_checks,
        }
        for index in range(28)
    ]
    cases.extend(
        {
            "case_id": f"cancel-{index}",
            "evaluation_mode": "cancellation",
            "run_status": "cancelled",
            "delivery_state": None,
            "release_id": None,
            "metrics": {"wall_clock_ms": 500},
            "checks": {
                **{key: None for key in journey_checks},
                "cancelled_artifact_safety": True,
            },
        }
        for index in range(2)
    )

    report = aggregate_shadow_report(
        corpus_validation=validation,
        cases=cases,
    )

    assert report["hard_gates"]["01_explicit_place_coverage_ratio_1"] is True
    assert report["hard_gates"]["11_cancelled_run_artifact_safety"] is True
    assert report["hard_gates"][
        "13_human_review_and_seven_day_no_p0_p1"
    ] is None
    assert report["cutover_eligible"] is False


@pytest.mark.anyio
async def test_shadow_control_cancels_after_declared_business_event() -> None:
    class Repository:
        def __init__(self) -> None:
            self.run = SimpleNamespace(status=RunStatus.ACTIVE)
            self.events: list[dict[str, object]] = []

        def list_events(self, run_id: str):
            return tuple(self.events)

        def get_run(self, run_id: str):
            return self.run

        def transition_run(self, run_id: str, status: RunStatus):
            self.run = SimpleNamespace(status=status)
            return self.run

        def append_event(self, run_id: str, event: dict[str, object]):
            self.events.append(event)

    repository = Repository()

    class Runtime:
        async def execute(self, run_id: str) -> None:
            repository.append_event(
                run_id, {"type": "candidate_group_ready"}
            )
            while repository.run.status is RunStatus.ACTIVE:
                await asyncio.sleep(0.01)

    await _execute_with_scenario_control(
        runtime=Runtime(),
        repository=repository,
        run_id="run-cancel",
        scenario={
            "cancel_after_event": "candidate_group_ready",
        },
    )

    assert repository.run.status is RunStatus.CANCELLED
    assert [event["type"] for event in repository.events] == [
        "candidate_group_ready",
        "run_cancelled",
    ]
    assert repository.events[-1]["trigger_event"] == "candidate_group_ready"


def test_shadow_runner_does_not_auto_retry_balance_blocker() -> None:
    assert _has_nonretryable_provider_blocker(
        (
            {"type": "run_started"},
            {
                "type": "provider_blocked",
                "reason_code": "provider_balance_insufficient",
                "retryable_now": False,
            },
        )
    )
    assert not _has_nonretryable_provider_blocker(
        (
            {
                "type": "provider_blocked",
                "reason_code": "provider_temporarily_unavailable",
                "retryable_now": True,
            },
        )
    )


@pytest.mark.anyio
async def test_shadow_runner_stops_starting_cases_after_balance_blocker() -> None:
    started: list[str] = []

    async def execute_case(
        scenario: dict[str, object],
    ) -> dict[str, object]:
        case_id = str(scenario["case_id"])
        started.append(case_id)
        return {
            "case_id": case_id,
            "provider_blocker": {
                "reason_code": "provider_balance_insufficient"
            },
        }

    cases, not_started = await _run_bounded_cases(
        selected=[
            {"case_id": "case-1"},
            {"case_id": "case-2"},
            {"case_id": "case-3"},
        ],
        concurrency=1,
        execute_case=execute_case,
    )

    assert started == ["case-1"]
    assert [item["case_id"] for item in cases] == ["case-1"]
    assert not_started == ["case-2", "case-3"]
    assert _is_balance_blocked_case(cases[0]) is True
