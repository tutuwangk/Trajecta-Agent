"""Run the current Agent V3 revision against the auditable real-provider corpus."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.env import load_project_env

load_project_env()

from app.trip_agent_v3.api.routes import AgentV3Runtime
from app.trip_agent_v3.domain.delivery import RunStatus
from app.trip_agent_v3.domain.execution import (
    JourneyGoal,
    TripWorkspaceRecord,
)
from app.trip_agent_v3.requirements import source_document
from app.trip_agent_v3.repository import SqliteTripAgentV3Repository
from app.trip_agent_v3.shadow_eval import (
    aggregate_shadow_report,
    build_shadow_case_report,
    validate_shadow_scenarios,
)


def _load_corpus(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("shadow corpus root must be a JSON array")
    return [dict(item) for item in payload]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _assert_provider_configuration() -> None:
    missing = [
        name for name in ("LLM_API_KEY", "AMAP_API_KEY") if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(
            "missing required provider configuration: "
            + ", ".join(missing)
        )


async def _run_case(
    scenario: dict[str, object],
    *,
    output_dir: Path,
    max_episodes: int,
    attempt_id: str,
    planner_model: str,
) -> dict[str, object]:
    case_id = str(scenario["case_id"])
    lock_path = output_dir / "locks" / f"{case_id}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        raise RuntimeError(
            f"another shadow executor already owns case {case_id}"
        )
    runtime = AgentV3Runtime(
        output_dir / "databases" / f"{case_id}.sqlite3",
        root_model_variant=planner_model,
    )
    repository = runtime.repository
    try:
        workspace_id = f"shadow-{case_id}-{attempt_id}"
        workspace = repository.get_workspace(workspace_id)
        if workspace is None:
            workspace = TripWorkspaceRecord(
                workspace_id=workspace_id,
                goal=JourneyGoal(
                    goal_revision_id=f"goal-{case_id}",
                    destination=str(scenario["city"]),
                    start_date=date.fromisoformat(
                        str(scenario["start_date"])
                    ),
                    days=int(scenario["days"]),
                ),
                sources=(
                    source_document(
                        source_id=f"source-{case_id}-request",
                        kind="user_request",
                        content=str(scenario["raw_request"]),
                    ),
                ),
            )
            repository.save_workspace(workspace)
        run = repository.get_run_by_idempotency_key(
            workspace_id=workspace_id,
            idempotency_key=f"shadow-{attempt_id}",
        )
        if run is None:
            run = repository.create_run(
                workspace_id=workspace_id,
                idempotency_key=f"shadow-{attempt_id}",
            )
        if run.status is RunStatus.ACTIVE:
            repository.transition_run(run.run_id, RunStatus.NEEDS_RESUME)
            repository.append_event(
                run.run_id,
                {
                    "type": "interrupted_shadow_executor_recovered",
                    "reason": (
                        "exclusive case lock proved the prior runner exited"
                    ),
                },
            )
            run = repository.get_run(run.run_id) or run
        clarification_rounds = sum(
            event.get("type") == "clarification_answered"
            for event in repository.list_events(run.run_id)
        )
        for _ in range(max_episodes):
            run = repository.get_run(run.run_id) or run
            if run.status in {RunStatus.CREATED, RunStatus.NEEDS_RESUME}:
                await _execute_with_scenario_control(
                    runtime=runtime,
                    repository=repository,
                    run_id=run.run_id,
                    scenario=scenario,
                )
                if _has_nonretryable_provider_blocker(
                    repository.list_events(run.run_id)
                ):
                    break
                continue
            if (
                run.status is RunStatus.WAITING_USER
                and scenario.get("clarification_response")
                and clarification_rounds == 0
            ):
                workspace = repository.get_workspace(workspace_id)
                if workspace is None:
                    raise RuntimeError(
                        "workspace disappeared during clarification"
                    )
                response = str(scenario["clarification_response"])
                revised = TripWorkspaceRecord(
                    workspace_id=workspace.workspace_id,
                    version=workspace.version + 1,
                    goal=workspace.goal,
                    sources=workspace.sources
                    + (
                        source_document(
                            source_id=f"source-{case_id}-clarification",
                            kind="user_revision",
                            content=f"用户地点澄清：{response}",
                        ),
                    ),
                )
                repository.update_workspace(
                    revised, expected_version=workspace.version
                )
                repository.transition_run(
                    run.run_id, RunStatus.NEEDS_RESUME
                )
                repository.append_event(
                    run.run_id,
                    {
                        "type": "clarification_answered",
                        "source_id": f"source-{case_id}-clarification",
                    },
                )
                clarification_rounds += 1
                continue
            break
        run = repository.get_run(run.run_id) or run
        candidate = repository.latest_candidate_for_run(run.run_id)
        assessment = (
            repository.assessment_for_candidate(
                candidate.candidate_snapshot_id
            )
            if candidate is not None
            else None
        )
        return build_shadow_case_report(
            scenario=scenario,
            run=run,
            candidate=candidate,
            assessment=assessment,
            release=repository.release_for_run(run.run_id),
            metrics=repository.get_run_metrics(run.run_id),
            events=repository.list_events(run.run_id),
            clarification_rounds=clarification_rounds,
        )
    finally:
        repository.close()
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


async def _execute_with_scenario_control(
    *,
    runtime: AgentV3Runtime,
    repository,
    run_id: str,
    scenario: dict[str, object],
) -> None:
    cancel_after_event = scenario.get("cancel_after_event")
    if not cancel_after_event:
        await runtime.execute(run_id)
        return
    execution = asyncio.create_task(runtime.execute(run_id))
    while not execution.done():
        events = repository.list_events(run_id)
        if any(
            event.get("type") == cancel_after_event
            for event in events
        ):
            current = repository.get_run(run_id)
            if current is not None and current.status in {
                RunStatus.CREATED,
                RunStatus.ACTIVE,
                RunStatus.NEEDS_RESUME,
            }:
                repository.transition_run(run_id, RunStatus.CANCELLED)
                repository.append_event(
                    run_id,
                    {
                        "type": "run_cancelled",
                        "trigger_event": cancel_after_event,
                        "source": "shadow_scenario_control",
                    },
                )
            break
        await asyncio.sleep(0.05)
    await execution


def _has_nonretryable_provider_blocker(
    events: tuple[dict[str, object], ...],
) -> bool:
    return any(
        event.get("type") == "provider_blocked"
        and event.get("retryable_now") is False
        for event in reversed(events)
    )


def _is_balance_blocked_case(result: dict[str, object]) -> bool:
    blocker = result.get("provider_blocker")
    return bool(
        isinstance(blocker, dict)
        and blocker.get("reason_code")
        == "provider_balance_insufficient"
    )


async def _run_bounded_cases(
    *,
    selected: list[dict[str, object]],
    concurrency: int,
    execute_case,
) -> tuple[list[dict[str, object]], list[str]]:
    semaphore = asyncio.Semaphore(concurrency)
    stop_event = asyncio.Event()
    not_started: list[str] = []

    async def bounded(
        item: dict[str, object],
    ) -> dict[str, object] | None:
        async with semaphore:
            if stop_event.is_set():
                not_started.append(str(item["case_id"]))
                return None
            result = await execute_case(item)
            if _is_balance_blocked_case(result):
                stop_event.set()
            return result

    raw_results = await asyncio.gather(
        *(bounded(item) for item in selected)
    )
    return (
        [item for item in raw_results if item is not None],
        sorted(not_started),
    )


def _existing_case_report(
    scenario: dict[str, object],
    *,
    output_dir: Path,
    attempt_id: str,
) -> dict[str, object] | None:
    case_id = str(scenario["case_id"])
    database = output_dir / "databases" / f"{case_id}.sqlite3"
    if not database.exists():
        return None
    repository = SqliteTripAgentV3Repository(database)
    try:
        workspace_id = f"shadow-{case_id}-{attempt_id}"
        run = repository.get_run_by_idempotency_key(
            workspace_id=workspace_id,
            idempotency_key=f"shadow-{attempt_id}",
        )
        if run is None:
            return None
        candidate = repository.latest_candidate_for_run(run.run_id)
        assessment = (
            repository.assessment_for_candidate(
                candidate.candidate_snapshot_id
            )
            if candidate is not None
            else None
        )
        events = repository.list_events(run.run_id)
        return build_shadow_case_report(
            scenario=scenario,
            run=run,
            candidate=candidate,
            assessment=assessment,
            release=repository.release_for_run(run.run_id),
            metrics=repository.get_run_metrics(run.run_id),
            events=events,
            clarification_rounds=sum(
                event.get("type") == "clarification_answered"
                for event in events
            ),
        )
    finally:
        repository.close()


def _write_report_artifacts(
    *,
    args: argparse.Namespace,
    validation: dict[str, object],
    selected: list[dict[str, object]],
    cases: list[dict[str, object]],
    not_started_case_ids: list[str],
) -> dict[str, object]:
    cases.sort(key=lambda item: str(item["case_id"]))
    report = aggregate_shadow_report(
        corpus_validation=validation,
        cases=cases,
    )
    balance_blocked_count = sum(
        _is_balance_blocked_case(item) for item in cases
    )
    terminal_statuses = {
        RunStatus.SUCCEEDED.value,
        RunStatus.WAITING_USER.value,
        RunStatus.CANCELLED.value,
        RunStatus.FAILED.value,
    }
    evaluable_cases = [
        item
        for item in cases
        if item.get("run_status") in terminal_statuses
        and item.get("provider_blocker") is None
    ]
    provider_blocked_case_ids = sorted(
        str(item["case_id"])
        for item in cases
        if item.get("provider_blocker") is not None
    )
    interrupted_case_ids = sorted(
        str(item["case_id"])
        for item in cases
        if item.get("run_status") == RunStatus.NEEDS_RESUME.value
        and item.get("provider_blocker") is None
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["corpus_path"] = str(args.corpus.resolve())
    report["current_revision_only"] = True
    report["attempt_id"] = args.attempt_id
    report["execution"] = {
        "requested_case_count": len(selected),
        "started_case_count": len(cases),
        "not_started_case_count": len(not_started_case_ids),
        "not_started_case_ids": not_started_case_ids,
        "evaluable_case_count": len(evaluable_cases),
        "provider_blocked_case_ids": provider_blocked_case_ids,
        "interrupted_case_ids": interrupted_case_ids,
        "stop_reason": (
            "provider_balance_insufficient"
            if balance_blocked_count
            else None
        ),
        "balance_blocked_count": balance_blocked_count,
        "partial": len(evaluable_cases) < len(selected),
    }
    review_path = args.output_dir / "human_review_queue.json"
    existing_reviews: dict[str, dict[str, object]] = {}
    if review_path.exists():
        try:
            existing_reviews = {
                str(item["release_id"]): dict(item)
                for item in json.loads(
                    review_path.read_text(encoding="utf-8")
                )
                if item.get("release_id") is not None
            }
        except (json.JSONDecodeError, TypeError, KeyError):
            existing_reviews = {}
    review_queue = []
    for item in cases:
        release_id = item.get("release_id")
        if release_id is None:
            continue
        base = {
            "case_id": item["case_id"],
            "run_id": item["run_id"],
            "release_id": release_id,
            "review_status": "pending",
            "day_by_day_executable": None,
            "named_places_correct": None,
            "meal_stops_correct": None,
            "transport_readable": None,
            "notes": "",
        }
        previous = existing_reviews.get(str(release_id))
        if previous is not None:
            base.update(
                {
                    key: previous.get(key)
                    for key in (
                        "review_status",
                        "day_by_day_executable",
                        "named_places_correct",
                        "meal_stops_correct",
                        "transport_readable",
                        "notes",
                    )
                }
            )
        review_queue.append(base)
    reviewed = [
        item
        for item in review_queue
        if item["review_status"] in {"passed", "failed"}
    ]
    passed = sum(item["review_status"] == "passed" for item in reviewed)
    report["human_review"] = {
        "release_count": len(review_queue),
        "reviewed_count": len(reviewed),
        "passed_count": passed,
        "failed_count": len(reviewed) - passed,
        "pass_rate": passed / len(reviewed) if reviewed else None,
    }
    _write_json(args.output_dir / "shadow_report.json", report)
    _write_json(review_path, review_queue)
    return report


async def _run_all(args: argparse.Namespace) -> int:
    scenarios = _load_corpus(args.corpus)
    validation = validate_shadow_scenarios(scenarios)
    if not validation["valid"]:
        _write_json(args.output_dir / "corpus_validation.json", validation)
        print(json.dumps(validation, ensure_ascii=False))
        return 2
    if args.validate_corpus_only:
        print(json.dumps(validation, ensure_ascii=False))
        return 0
    selected = [
        item
        for item in scenarios
        if args.case_id is None or item["case_id"] == args.case_id
    ]
    if args.limit is not None:
        selected = selected[: args.limit]
    if args.report_existing:
        cases = [
            report
            for item in selected
            if (
                report := _existing_case_report(
                    item,
                    output_dir=args.output_dir,
                    attempt_id=args.attempt_id,
                )
            )
            is not None
        ]
        existing_ids = {str(item["case_id"]) for item in cases}
        not_started_case_ids = sorted(
            str(item["case_id"])
            for item in selected
            if str(item["case_id"]) not in existing_ids
        )
        report = _write_report_artifacts(
            args=args,
            validation=validation,
            selected=selected,
            cases=cases,
            not_started_case_ids=not_started_case_ids,
        )
        print(
            json.dumps(
                {
                    "output": str(
                        args.output_dir / "shadow_report.json"
                    ),
                    "case_count": len(cases),
                    "cutover_eligible": report["cutover_eligible"],
                    "report_existing": True,
                },
                ensure_ascii=False,
            )
        )
        return 0
    _assert_provider_configuration()

    async def execute_case(
        item: dict[str, object],
    ) -> dict[str, object]:
        result = await _run_case(
            item,
            output_dir=args.output_dir,
            max_episodes=args.max_episodes,
            attempt_id=args.attempt_id,
            planner_model=args.planner_model,
        )
        print(
            json.dumps(
                {
                    "case_id": result["case_id"],
                    "run_id": result["run_id"],
                    "run_status": result["run_status"],
                    "release_id": result["release_id"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return result

    cases, not_started_case_ids = await _run_bounded_cases(
        selected=selected,
        concurrency=args.concurrency,
        execute_case=execute_case,
    )
    report = _write_report_artifacts(
        args=args,
        validation=validation,
        selected=selected,
        cases=cases,
        not_started_case_ids=not_started_case_ids,
    )
    print(
        json.dumps(
            {
                "output": str(args.output_dir / "shadow_report.json"),
                "case_count": len(cases),
                "cutover_eligible": report["cutover_eligible"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        type=Path,
        default=(
            BACKEND_ROOT
            / "evals"
            / "agent_v3"
            / "shadow_scenarios.json"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=1, choices=(1, 2, 3))
    parser.add_argument("--max-episodes", type=int, default=3)
    parser.add_argument(
        "--planner-model",
        choices=("flash", "pro"),
        default="pro",
    )
    parser.add_argument("--attempt-id", default="current")
    parser.add_argument("--validate-corpus-only", action="store_true")
    parser.add_argument("--report-existing", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_all(_arguments())))
