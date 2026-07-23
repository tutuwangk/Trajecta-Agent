"""Run auditable, resumable Agent V2 WP8 scenarios with persistent artifacts."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path
import statistics
import sys
from time import monotonic
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic_ai_harness.step_persistence import SqliteStepStore

from app.env import load_project_env
from app.trip_agent.adapters.narrative import DeepSeekNarrativeGenerator
from app.trip_agent.adapters.place_knowledge import DeepSeekAmapPlaceKnowledge
from app.trip_agent.adapters.provider_coordinator import ProviderRequestCoordinator
from app.trip_agent.adapters.provider import (
    DEEPSEEK_V4_FLASH,
    DEEPSEEK_V4_PRO,
    DeepSeekModelVariant,
    build_deepseek_v4_model,
)
from app.trip_agent.domain import expand_visit_candidate_coverage
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.runtime import TripAgentService
from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter
from app.trip_agent.validation import FeasibilityCompiler
from scripts.live_acceptance_six import SCENARIOS


CLARIFICATION_FIXTURES: dict[str, tuple[dict[str, str], ...]] = {
    "beijing_3d_high_culture": (
        {"prompt_contains": "便宜坊", "strategy": "first_option"},
        {
            "prompt_contains": "炸酱面",
            "answer": "无所谓，由你安排最方便的",
        },
    ),
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--legacy-summary", type=Path)
    parser.add_argument(
        "--planner-model",
        choices=("flash", "pro"),
        default="flash",
        help="WP8 root Agent model tier. Defaults to Flash for cost-bounded MVP acceptance.",
    )
    parser.add_argument(
        "--gate-profile",
        choices=("mvp", "cutover"),
        default="mvp",
        help="MVP proves six-scenario stability; cutover retains the 30-run production gate.",
    )
    return parser.parse_args()


def _request(scenario: dict) -> str:
    return "\n".join(
        (
            scenario["raw_input"],
            "可参考但需自行判断的资料：" + scenario["ugc"],
            "补充说明：" + scenario["notes"],
        )
    )


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * percentile + 0.999999) - 1)))
    return round(ordered[index], 2)


def _read_index(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_index(path: Path, records: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _fixture_answers(scenario: dict, interruption) -> dict[str, str] | None:
    fixtures = tuple(scenario.get("clarification_fixtures", ())) or CLARIFICATION_FIXTURES.get(
        scenario["id"], ()
    )
    answers: dict[str, str] = {}
    for question in interruption.questions:
        fixture = next(
            (
                item
                for item in fixtures
                if (
                    item.get("question_id")
                    and str(item["question_id"]) == question.question_id
                )
                or (
                    item.get("prompt_contains")
                    and str(item["prompt_contains"]) in question.prompt
                )
            ),
            None,
        )
        if fixture is None:
            return None
        if fixture.get("answer"):
            answers[question.question_id] = str(fixture["answer"])
        elif fixture.get("strategy") == "first_option" and question.options:
            answers[question.question_id] = question.options[0]
        else:
            return None
    return answers


def _normalized_place_label(value: str) -> str:
    return "".join(character for character in value.casefold().strip() if not character.isspace())


def _resolved_expected_ids(workspace, expected_names: list[str]) -> tuple[set[str], list[str]]:
    resolutions = {
        item.hypothesis_id: item.candidate_id
        for item in workspace.place_resolutions
        if item.candidate_id
    }
    expected_ids: set[str] = set()
    unresolved: list[str] = []
    for expected in expected_names:
        normalized_expected = _normalized_place_label(expected)
        committed_hypothesis_ids = {
            item.subject_hypothesis_id
            for item in workspace.goal_ledger.commitments
            if item.subject_hypothesis_id
            and (
                _normalized_place_label(item.value) == normalized_expected
                or _normalized_place_label(item.evidence_text) == normalized_expected
            )
        }
        if committed_hypothesis_ids:
            hypothesis_ids = committed_hypothesis_ids
        else:
            # Evaluation labels may describe a non-commitment time window. Match the
            # extracted entity exactly; substring matching confuses e.g. `外滩` with
            # `上海外滩英迪格酒店` and violates the stable-identity acceptance contract.
            hypothesis_ids = {
                item.hypothesis_id
                for item in workspace.place_hypotheses
                if _normalized_place_label(item.raw_name) == normalized_expected
            }
        candidate_ids = {
            resolutions[hypothesis_id]
            for hypothesis_id in hypothesis_ids
            if hypothesis_id in resolutions
        }
        if candidate_ids:
            expected_ids.update(candidate_ids)
        else:
            unresolved.append(expected)
    return expected_ids, unresolved


async def _run_case(
    scenario: dict,
    output_dir: Path,
    *,
    coordinator: ProviderRequestCoordinator,
    cache_repository: SqliteTripAgentRepository,
    previous_attempt_id: str | None,
    planner_model_variant: DeepSeekModelVariant,
) -> dict:
    case_id = scenario["id"]
    attempt_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    attempt_dir = output_dir / "attempts" / case_id / attempt_id
    attempt_dir.mkdir(parents=True, exist_ok=False)
    repository = SqliteTripAgentRepository(attempt_dir / "domain.sqlite3")
    persistence = HarnessPersistenceAdapter(
        SqliteStepStore(database=attempt_dir / "provider.sqlite3"),
        deferred_database=attempt_dir / "deferred.sqlite3",
    )
    profile = scenario["user_profile"]
    started = monotonic()
    started_at = datetime.now(timezone.utc)
    try:
        model = build_deepseek_v4_model("root", model_variant=planner_model_variant)
        service = TripAgentService(
            repository,
            DeepSeekAmapPlaceKnowledge(
                city=profile["destination"], coordinator=coordinator
            ),
            persistence,
            DeepSeekNarrativeGenerator(),
            place_cache_repository=cache_repository,
        )
        outcome = await service.start(
            raw_request=_request(scenario),
            destination=profile["destination"],
            start_date=date.fromisoformat(profile["start_date"]),
            days=int(profile["days"]),
            model=model,
        )
        clarification_rounds = 0
        waiting_user_unresolved = False
        while outcome.status.value == "waiting_user" and clarification_rounds < 2:
            active_run = repository.get_run(outcome.run_id)
            interruption = (
                repository.get_interruption(active_run.active_interruption_id)
                if active_run and active_run.active_interruption_id
                else None
            )
            answers = _fixture_answers(scenario, interruption) if interruption else None
            if not answers:
                waiting_user_unresolved = True
                break
            clarification_rounds += 1
            outcome = await service.resume(
                run_id=outcome.run_id,
                answers=answers,
                model=model,
            )
        elapsed = monotonic() - started
        workspace = repository.get_workspace(outcome.workspace_id)
        run = repository.get_run(outcome.run_id)
        budget_state = repository.get_run_budget(outcome.run_id) or {}
        releases = repository.list_releases(outcome.workspace_id)
        release = releases[-1] if releases else None
        events = repository.list_events(outcome.run_id)
        scheduled_visit_ids = {
            visit.place_candidate_id
            for day in (workspace.current_draft.days if workspace and workspace.current_draft else ())
            for visit in day.visits
        }
        scheduled_meal_ids = {
            meal.place_candidate_id
            for day in (workspace.current_draft.days if workspace and workspace.current_draft else ())
            for meal in day.meals
            if meal.place_candidate_id
        }
        scheduled_ids = scheduled_visit_ids | scheduled_meal_ids
        covered_scheduled_ids = (
            expand_visit_candidate_coverage(
                workspace.place_candidates, scheduled_visit_ids
            )
            if workspace
            else set()
        ) | scheduled_meal_ids
        candidate_names = {
            candidate.candidate_id: candidate.name
            for candidate in (workspace.place_candidates if workspace else ())
        }
        scheduled_names = sorted(candidate_names[item] for item in scheduled_ids if item in candidate_names)
        must_visit = profile.get("constraints", {}).get("must_visit", [])
        expected_must_visit_ids, unresolved_expected_names = (
            _resolved_expected_ids(workspace, must_visit) if workspace else (set(), list(must_visit))
        )
        missing_expected_ids = sorted(expected_must_visit_ids - covered_scheduled_ids)
        missing_must_visit = unresolved_expected_names + missing_expected_ids
        event_types = [str(event.get("type")) for event in events]
        provider_codes = sorted(
            {
                str(event["provider_code"])
                for event in events
                if event.get("provider_code")
            }
        )
        claims = repository.list_claims(outcome.workspace_id)
        simulation = (
            FeasibilityCompiler().compile(workspace, claims)
            if workspace is not None and workspace.current_draft is not None
            else None
        )
        expected_days = int(profile["days"])
        product_issue_codes: list[str] = []
        if not workspace or not workspace.current_draft or len(workspace.current_draft.days) != expected_days:
            product_issue_codes.append("expected_day_count_missing")
        if missing_must_visit:
            product_issue_codes.append("expected_must_visit_missing")
        compiled_starts: dict[str, list[int]] = {}
        if simulation is not None:
            for compiled_day in simulation.days:
                for visit in compiled_day.visits:
                    covered_ids = expand_visit_candidate_coverage(
                        workspace.place_candidates, {visit.candidate_id}
                    )
                    for covered_id in covered_ids:
                        compiled_starts.setdefault(covered_id, []).append(visit.start_minute)
                for meal in compiled_day.meals:
                    if meal.candidate_id:
                        compiled_starts.setdefault(meal.candidate_id, []).append(meal.start_minute)
        failed_time_windows: list[str] = []
        for place_name, expected_window in scenario.get("expected_time_windows", {}).items():
            matching_ids, unresolved_window_names = (
                _resolved_expected_ids(workspace, [place_name])
                if workspace
                else (set(), [place_name])
            )
            starts = [
                start
                for candidate_id in matching_ids
                for start in compiled_starts.get(candidate_id, [])
            ]
            if unresolved_window_names or (
                expected_window == "night" and not any(start >= 19 * 60 for start in starts)
            ):
                failed_time_windows.append(place_name)
        if failed_time_windows:
            product_issue_codes.append("expected_time_window_missing")

        def event_elapsed(event_type: str) -> float | None:
            event = next((item for item in events if item.get("type") == event_type), None)
            if event is None or not event.get("created_at"):
                return None
            return round(
                (datetime.fromisoformat(str(event["created_at"])) - started_at).total_seconds(),
                2,
            )

        record = {
            "case_id": case_id,
            "attempt_id": attempt_id,
            "previous_attempt_id": previous_attempt_id,
            "provider_cache_mode": "cold" if previous_attempt_id is None else "shared",
            "planner_model_variant": planner_model_variant,
            "planner_model": (
                DEEPSEEK_V4_PRO if planner_model_variant == "pro" else DEEPSEEK_V4_FLASH
            ),
            "started_at": started_at.isoformat(),
            "elapsed_seconds": round(elapsed, 2),
            "latency_hard_limit_seconds": (
                480 if int(profile["days"]) == 1 else 600 if int(profile["days"]) == 2 else 720
            ),
            "status": outcome.status.value,
            "unhandled_exception": False,
            "error_code": run.error_code if run else None,
            "error_message": run.error_message if run else None,
            "failure_class": run.failure_class.value if run and run.failure_class else None,
            "retryable": run.retryable if run else None,
            "provider_attempt_count": run.provider_attempt_count if run else 0,
            "model_request_count": int(budget_state.get("model_requests", 0)),
            "tool_call_count": int(budget_state.get("tool_calls", 0)),
            "input_tokens": int(budget_state.get("input_tokens", 0)),
            "output_tokens": int(budget_state.get("output_tokens", 0)),
            "provider_calls": budget_state.get("provider_calls", {}),
            "workspace_id": outcome.workspace_id,
            "run_id": outcome.run_id,
            "workspace_version": workspace.version if workspace else None,
            "fact_version": workspace.fact_version if workspace else None,
            "draft_day_count": len(workspace.current_draft.days) if workspace and workspace.current_draft else 0,
            "candidate_count": len(workspace.place_candidates) if workspace else 0,
            "claim_count": len(claims),
            "release_id": release.release_id if release else None,
            "fact_status": release.fact_status.value if release else None,
            "experience_status": release.experience_status.value if release else None,
            "release_issue_codes": list(release.issue_codes) if release else [],
            "event_count": len(events),
            "event_types": event_types,
            "deterministic_fallback_detected": any("fallback" in item for item in event_types),
            "scheduled_names": scheduled_names,
            "missing_expected_must_visit": missing_must_visit,
            "missing_expected_candidate_ids": missing_expected_ids,
            "failed_expected_time_windows": failed_time_windows,
            "product_issue_codes": product_issue_codes,
            "first_progress_seconds": event_elapsed("place_mentions_analyzed"),
            "first_draft_seconds": event_elapsed("draft_changed"),
            "first_checkpoint_seconds": event_elapsed("candidate_checkpoint_saved"),
            "product_checks_passed": bool(release) and not product_issue_codes,
            "clarification_rounds": clarification_rounds,
            "waiting_user_unresolved": waiting_user_unresolved,
            "provider_retry_count": event_types.count("provider_retry_scheduled"),
            "provider_recovery_count": event_types.count("provider_retry_recovered"),
            "provider_circuit_open_count": event_types.count("provider_circuit_open"),
            "provider_codes": provider_codes,
            "checkpoint_publish_count": event_types.count("candidate_checkpoint_published"),
            "cache_hit_count": sum("cache_hit" in item for item in event_types),
            "artifact_dir": str(attempt_dir),
        }
        (attempt_dir / "result.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if workspace is not None:
            (attempt_dir / "workspace.json").write_text(
                workspace.model_dump_json(indent=2), encoding="utf-8"
            )
        if release is not None:
            (attempt_dir / "release.json").write_text(
                release.model_dump_json(indent=2), encoding="utf-8"
            )
        (attempt_dir / "events.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return record
    except Exception as exc:
        record = {
            "case_id": case_id,
            "attempt_id": attempt_id,
            "previous_attempt_id": previous_attempt_id,
            "provider_cache_mode": "cold" if previous_attempt_id is None else "shared",
            "planner_model_variant": planner_model_variant,
            "planner_model": (
                DEEPSEEK_V4_PRO if planner_model_variant == "pro" else DEEPSEEK_V4_FLASH
            ),
            "started_at": started_at.isoformat(),
            "elapsed_seconds": round(monotonic() - started, 2),
            "status": "unhandled_exception",
            "unhandled_exception": True,
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:4_000],
            "artifact_dir": str(attempt_dir),
        }
        (attempt_dir / "result.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return record
    finally:
        repository.close()


def _summary(
    records: list[dict],
    legacy_summary: Path | None,
    *,
    gate_profile: str | None = None,
) -> dict:
    elapsed = [float(record["elapsed_seconds"]) for record in records]
    status_counts = Counter(str(record.get("status")) for record in records)
    failure_class_counts = Counter(
        str(record["failure_class"])
        for record in records
        if record.get("failure_class")
    )
    published = [record for record in records if record.get("status") == "published"]
    incomplete = [record for record in records if record.get("status") == "incomplete"]
    unhandled = [record for record in records if record.get("unhandled_exception")]
    latest_by_case: dict[str, dict] = {}
    for record in records:
        latest_by_case[str(record["case_id"])] = record
    latest = list(latest_by_case.values())
    latest_product_failures = [
        record for record in latest if not record.get("product_checks_passed")
    ]
    internal_failures = [
        record for record in records if record.get("failure_class") == "internal"
    ]
    latest_internal_failures = [
        record for record in latest if record.get("failure_class") == "internal"
    ]
    unresolved_waits = [
        record for record in latest if record.get("waiting_user_unresolved")
    ]
    provider_failures = [
        record
        for record in records
        if record.get("failure_class")
        in {"transient_external", "permanent_external", "provider_protocol"}
    ]
    case_quality_gate_passed = bool(latest) and not (
        latest_product_failures
        or latest_internal_failures
        or unresolved_waits
        or any(record.get("unhandled_exception") for record in latest)
    )
    budget_incomplete = [
        record
        for record in records
        if record.get("status") == "incomplete" and record.get("failure_class") == "budget"
    ]
    hard_limit_exceeded = [
        record
        for record in records
        if record.get("latency_hard_limit_seconds") is not None
        and float(record["elapsed_seconds"]) > float(record["latency_hard_limit_seconds"])
    ]
    early_draft_count = sum(
        record.get("first_draft_seconds") is not None
        and float(record["first_draft_seconds"])
        <= float(record.get("latency_hard_limit_seconds", 0)) * 0.5
        for record in records
    )
    early_checkpoint_count = sum(
        record.get("first_checkpoint_seconds") is not None
        and float(record["first_checkpoint_seconds"])
        <= float(record.get("latency_hard_limit_seconds", 0)) * 0.7
        for record in records
    )
    deterministic_fallback_count = sum(
        bool(record.get("deterministic_fallback_detected")) for record in records
    )
    cutover_gate_evaluated = len(records) >= 30
    cutover_distribution_gate_passed = (
        bool(records)
        and len(published) / len(records) >= 0.95
        and len(incomplete) / len(records) < 0.05
        and len(budget_incomplete) / len(records) < 0.02
        and not internal_failures
        and not unhandled
        and not hard_limit_exceeded
        and early_draft_count / len(records) >= 0.95
        and early_checkpoint_count / len(records) >= 0.95
        and (statistics.median(elapsed) if elapsed else float("inf")) <= 300
        and (_percentile(elapsed, 0.95) or float("inf")) <= 600
    )
    cutover_gate_passed = (
        cutover_gate_evaluated
        and case_quality_gate_passed
        and cutover_distribution_gate_passed
        and deterministic_fallback_count == 0
    )
    required_mvp_case_ids = {str(scenario["id"]) for scenario in SCENARIOS}
    latest_mvp = [
        record for record in latest if str(record["case_id"]) in required_mvp_case_ids
    ]
    mvp_elapsed = [float(record["elapsed_seconds"]) for record in latest_mvp]
    mvp_published = [
        record for record in latest_mvp if record.get("status") == "published"
    ]
    mvp_product_passed = [
        record for record in mvp_published if record.get("product_checks_passed")
    ]
    mvp_early_draft_count = sum(
        record.get("first_draft_seconds") is not None
        and float(record["first_draft_seconds"])
        <= float(record.get("latency_hard_limit_seconds", 0)) * 0.5
        for record in latest_mvp
    )
    mvp_early_checkpoint_count = sum(
        record.get("first_checkpoint_seconds") is not None
        and float(record["first_checkpoint_seconds"])
        <= float(record.get("latency_hard_limit_seconds", 0)) * 0.7
        for record in latest_mvp
    )
    mvp_gate_evaluated = {
        str(record["case_id"]) for record in latest_mvp
    } == required_mvp_case_ids
    mvp_gate_passed = (
        mvp_gate_evaluated
        and len(mvp_published) >= 5
        and len(mvp_product_passed) >= 5
        and all(
            record.get("status") in {"published", "incomplete"}
            for record in latest_mvp
        )
        and not any(
            record.get("failure_class") == "internal"
            or record.get("unhandled_exception")
            or record.get("waiting_user_unresolved")
            or record.get("deterministic_fallback_detected")
            for record in latest_mvp
        )
        and not [
            record
            for record in latest_mvp
            if record.get("latency_hard_limit_seconds") is not None
            and float(record["elapsed_seconds"])
            > float(record["latency_hard_limit_seconds"])
        ]
        and (statistics.median(mvp_elapsed) if mvp_elapsed else float("inf")) <= 420
        and (_percentile(mvp_elapsed, 0.95) or float("inf")) <= 720
    )
    gate_passed = (
        case_quality_gate_passed
        if gate_profile is None
        else mvp_gate_passed
        if gate_profile == "mvp"
        else cutover_gate_passed
    )
    summary: dict[str, object] = {
        "attempt_count": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "published_count": len(published),
        "incomplete_count": len(incomplete),
        "failed_count": status_counts["failed"],
        "waiting_user_count": status_counts["waiting_user"],
        "cancelled_count": status_counts["cancelled"],
        "unhandled_exception_count": len(unhandled),
        "publish_rate": len(published) / len(records) if records else 0,
        "incomplete_rate": len(incomplete) / len(records) if records else 0,
        "budget_incomplete_count": len(budget_incomplete),
        "budget_incomplete_rate": len(budget_incomplete) / len(records) if records else 0,
        "product_pass_count": sum(bool(record.get("product_checks_passed")) for record in records),
        "latest_case_product_pass_count": sum(
            bool(record.get("product_checks_passed")) for record in latest
        ),
        "latest_case_count": len(latest),
        "latest_published_count": sum(
            record.get("status") == "published" for record in latest
        ),
        "latest_publish_rate": (
            sum(record.get("status") == "published" for record in latest) / len(latest)
            if latest
            else 0
        ),
        "failure_class_counts": dict(sorted(failure_class_counts.items())),
        "internal_failure_count": len(internal_failures),
        "latest_internal_failure_count": len(latest_internal_failures),
        "first_provider_error_count": sum(
            bool(record.get("previous_attempt_id") is None) for record in provider_failures
        ),
        "provider_retry_count": sum(int(record.get("provider_retry_count", 0)) for record in records),
        "provider_recovery_count": sum(
            int(record.get("provider_recovery_count", 0)) for record in records
        ),
        "provider_circuit_open_count": sum(
            int(record.get("provider_circuit_open_count", 0)) for record in records
        ),
        "checkpoint_publish_count": sum(
            int(record.get("checkpoint_publish_count", 0)) for record in records
        ),
        "cache_hit_count": sum(int(record.get("cache_hit_count", 0)) for record in records),
        "waiting_user_unresolved_count": len(unresolved_waits),
        "p50_seconds": round(statistics.median(elapsed), 2) if elapsed else None,
        "p95_seconds": _percentile(elapsed, 0.95),
        "hard_limit_exceeded_count": len(hard_limit_exceeded),
        "early_draft_rate": early_draft_count / len(records) if records else 0,
        "early_checkpoint_rate": early_checkpoint_count / len(records) if records else 0,
        "deterministic_fallback_count": deterministic_fallback_count,
        "planner_model_counts": dict(
            sorted(
                Counter(
                    str(record.get("planner_model", "unknown")) for record in records
                ).items()
            )
        ),
        "gate_profile": gate_profile or "case",
        "gate_passed": gate_passed,
        "case_quality_gate_passed": case_quality_gate_passed,
        "mvp_gate_evaluated": mvp_gate_evaluated,
        "mvp_gate_passed": mvp_gate_passed if mvp_gate_evaluated else None,
        "mvp_published_count": len(mvp_published),
        "mvp_product_pass_count": len(mvp_product_passed),
        "mvp_p50_seconds": (
            round(statistics.median(mvp_elapsed), 2) if mvp_elapsed else None
        ),
        "mvp_p95_seconds": _percentile(mvp_elapsed, 0.95),
        "mvp_early_draft_rate": (
            mvp_early_draft_count / len(latest_mvp) if latest_mvp else 0
        ),
        "mvp_early_checkpoint_rate": (
            mvp_early_checkpoint_count / len(latest_mvp) if latest_mvp else 0
        ),
        "cutover_gate_evaluated": cutover_gate_evaluated,
        "cutover_gate_passed": cutover_gate_passed if cutover_gate_evaluated else None,
        "distribution_gate_evaluated": cutover_gate_evaluated,
        "distribution_gate_passed": (
            cutover_distribution_gate_passed if cutover_gate_evaluated else None
        ),
        "environment_blocked": any(
            record.get("failure_class") == "permanent_external" for record in latest
        )
        or any(
            record.get("provider_circuit_open_count", 0)
            or {
                "DAILY_QUERY_OVER_LIMIT",
                "INSUFFICIENT_BALANCE",
                "missing_configuration",
            }
            & set(record.get("provider_codes", []))
            for record in latest
        ),
        "results": records,
    }
    if legacy_summary and legacy_summary.exists():
        legacy = json.loads(legacy_summary.read_text(encoding="utf-8"))
        legacy_by_id = {item["id"]: item for item in legacy.get("results", [])}
        summary["shadow_pairs"] = [
            {
                "case_id": record["case_id"],
                "v2_published": record.get("status") == "published",
                "v2_product_passed": record.get("product_checks_passed"),
                "legacy_technical_ok": legacy_by_id.get(record["case_id"], {}).get("technical_ok"),
                "legacy_product_passed": legacy_by_id.get(record["case_id"], {}).get(
                    "product_checks_passed"
                ),
            }
            for record in records
        ]
    return summary


async def _main(args: argparse.Namespace) -> int:
    load_project_env()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    index_path = args.output_dir / "attempts.jsonl"
    records = _read_index(index_path)
    recorded_variants = {
        str(record["planner_model_variant"])
        for record in records
        if record.get("planner_model_variant")
    }
    if recorded_variants and recorded_variants != {args.planner_model}:
        raise ValueError(
            "output directory already contains a different planner model tier: "
            f"{sorted(recorded_variants)}"
        )
    latest_attempt_by_case: dict[str, str] = {}
    for record in records:
        latest_attempt_by_case[str(record["case_id"])] = str(record["attempt_id"])
    existing_counts = Counter(str(record["case_id"]) for record in records)
    eligible = [
        scenario
        for scenario in SCENARIOS
        if not args.case_id or scenario["id"] in args.case_id
    ]
    selected = []
    for scenario in eligible:
        remaining = (
            max(0, args.repetitions - existing_counts[scenario["id"]])
            if args.resume
            else args.repetitions
        )
        selected.extend([scenario] * remaining)
    if args.limit:
        selected = selected[: args.limit]
    coordinator = ProviderRequestCoordinator()
    cache_repository = SqliteTripAgentRepository(args.output_dir / "provider-cache.sqlite3")
    try:
        for index, scenario in enumerate(selected, 1):
            record = await _run_case(
                scenario,
                args.output_dir,
                coordinator=coordinator,
                cache_repository=cache_repository,
                previous_attempt_id=latest_attempt_by_case.get(scenario["id"]),
                planner_model_variant=args.planner_model,
            )
            records.append(record)
            latest_attempt_by_case[scenario["id"]] = record["attempt_id"]
            _write_index(index_path, records)
            print(
                json.dumps(
                    {
                        "completed": index,
                        "selected": len(selected),
                        "case_id": record["case_id"],
                        "status": record["status"],
                        "elapsed_seconds": record["elapsed_seconds"],
                        "planner_model": record["planner_model"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        cache_repository.close()
    summary = _summary(records, args.legacy_summary, gate_profile=args.gate_profile)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["environment_blocked"]:
        return 2
    return 0 if summary["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_arguments())))
