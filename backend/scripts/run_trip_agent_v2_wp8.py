"""Run auditable, resumable Agent V2 WP8 scenarios with persistent artifacts."""

from __future__ import annotations

import argparse
import asyncio
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
from app.trip_agent.adapters.provider import build_deepseek_v4_model
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.runtime import TripAgentService
from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter
from scripts.live_acceptance_six import SCENARIOS


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--legacy-summary", type=Path)
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


async def _run_case(scenario: dict, output_dir: Path) -> dict:
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
        service = TripAgentService(
            repository,
            DeepSeekAmapPlaceKnowledge(city=profile["destination"]),
            persistence,
            DeepSeekNarrativeGenerator(),
        )
        outcome = await service.start(
            raw_request=_request(scenario),
            destination=profile["destination"],
            start_date=date.fromisoformat(profile["start_date"]),
            days=int(profile["days"]),
            model=build_deepseek_v4_model("root"),
        )
        elapsed = monotonic() - started
        workspace = repository.get_workspace(outcome.workspace_id)
        run = repository.get_run(outcome.run_id)
        releases = repository.list_releases(outcome.workspace_id)
        release = releases[-1] if releases else None
        events = repository.list_events(outcome.run_id)
        scheduled_ids = {
            visit.place_candidate_id
            for day in (workspace.current_draft.days if workspace and workspace.current_draft else ())
            for visit in day.visits
        }
        candidate_names = {
            candidate.candidate_id: candidate.name
            for candidate in (workspace.place_candidates if workspace else ())
        }
        scheduled_names = [candidate_names[item] for item in scheduled_ids if item in candidate_names]
        must_visit = profile.get("constraints", {}).get("must_visit", [])
        missing_must_visit = [
            name
            for name in must_visit
            if not any(name in scheduled_name or scheduled_name in name for scheduled_name in scheduled_names)
        ]
        event_types = [str(event.get("type")) for event in events]
        record = {
            "case_id": case_id,
            "attempt_id": attempt_id,
            "started_at": started_at.isoformat(),
            "elapsed_seconds": round(elapsed, 2),
            "status": outcome.status.value,
            "unhandled_exception": False,
            "error_code": run.error_code if run else None,
            "error_message": run.error_message if run else None,
            "workspace_id": outcome.workspace_id,
            "run_id": outcome.run_id,
            "workspace_version": workspace.version if workspace else None,
            "fact_version": workspace.fact_version if workspace else None,
            "draft_day_count": len(workspace.current_draft.days) if workspace and workspace.current_draft else 0,
            "candidate_count": len(workspace.place_candidates) if workspace else 0,
            "claim_count": len(repository.list_claims(outcome.workspace_id)),
            "release_id": release.release_id if release else None,
            "fact_status": release.fact_status.value if release else None,
            "experience_status": release.experience_status.value if release else None,
            "release_issue_codes": list(release.issue_codes) if release else [],
            "event_count": len(events),
            "event_types": event_types,
            "deterministic_fallback_detected": any("fallback" in item for item in event_types),
            "scheduled_names": scheduled_names,
            "missing_expected_must_visit": missing_must_visit,
            "product_checks_passed": bool(release) and not missing_must_visit,
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


def _summary(records: list[dict], legacy_summary: Path | None) -> dict:
    elapsed = [float(record["elapsed_seconds"]) for record in records]
    published = [record for record in records if record.get("status") == "published"]
    incomplete = [record for record in records if record.get("status") == "incomplete"]
    unhandled = [record for record in records if record.get("unhandled_exception")]
    summary: dict[str, object] = {
        "attempt_count": len(records),
        "published_count": len(published),
        "incomplete_count": len(incomplete),
        "unhandled_exception_count": len(unhandled),
        "publish_rate": len(published) / len(records) if records else 0,
        "incomplete_rate": len(incomplete) / len(records) if records else 0,
        "product_pass_count": sum(bool(record.get("product_checks_passed")) for record in records),
        "p50_seconds": round(statistics.median(elapsed), 2) if elapsed else None,
        "p95_seconds": _percentile(elapsed, 0.95),
        "deterministic_fallback_count": sum(
            bool(record.get("deterministic_fallback_detected")) for record in records
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
    completed = {
        record["case_id"]
        for record in records
        if args.resume and record.get("status") == "published"
    }
    selected = [
        scenario
        for scenario in SCENARIOS
        if (not args.case_id or scenario["id"] in args.case_id)
        and scenario["id"] not in completed
    ]
    if args.limit:
        selected = selected[: args.limit]
    for index, scenario in enumerate(selected, 1):
        record = await _run_case(scenario, args.output_dir)
        records.append(record)
        _write_index(index_path, records)
        print(
            json.dumps(
                {
                    "completed": index,
                    "selected": len(selected),
                    "case_id": record["case_id"],
                    "status": record["status"],
                    "elapsed_seconds": record["elapsed_seconds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    summary = _summary(records, args.legacy_summary)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["unhandled_exception_count"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_arguments())))
