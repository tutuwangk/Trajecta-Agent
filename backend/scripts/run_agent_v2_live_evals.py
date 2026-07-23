from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.env import load_project_env

load_project_env()

from app.trip_agent.adapters.place_knowledge import DeepSeekAmapPlaceKnowledge
from app.trip_agent.domain import GeoPoint, PlaceCandidate, PlaceHypothesis, TextSpan


def load_cases(root: Path, pattern: str) -> list[dict]:
    cases: list[dict] = []
    for path in sorted(root.glob(pattern)):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                cases.append(json.loads(raw))
    return cases


def _load_existing(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _checkpoint(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def run_bounded(
    cases: list[dict], worker, concurrency: int, *, checkpoint: Path, resume: bool
) -> list[dict]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: dict) -> dict:
        async with semaphore:
            try:
                return {
                    "case_id": case["case_id"],
                    "prediction": await worker(case),
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                return {
                    "case_id": case["case_id"],
                    "prediction": {},
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1_000],
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }

    records = _load_existing(checkpoint) if resume else []
    completed_ids = {record["case_id"] for record in records}
    pending = [case for case in cases if case["case_id"] not in completed_ids]
    tasks = [asyncio.create_task(one(case)) for case in pending]
    for completed, task in enumerate(asyncio.as_completed(tasks), 1):
        record = await task
        records.append(record)
        records.sort(key=lambda item: item["case_id"])
        _checkpoint(checkpoint, records)
        print(
            json.dumps(
                {
                    "task_progress": checkpoint.stem,
                    "completed": len(completed_ids) + completed,
                    "total": len(cases),
                    "case_id": record["case_id"],
                    "ok": "error_type" not in record,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return records


async def mention_prediction(case: dict) -> dict:
    adapter = DeepSeekAmapPlaceKnowledge(city=None)
    hypotheses = await adapter.analyze_mentions(case["input"]["text"])
    route_entities = tuple(
        item
        for item in hypotheses
        if item.route_relevant
        and item.polarity == "requested"
        and item.role not in {"destination_context", "reference"}
    )
    return {
        "mentions": [item.raw_name for item in route_entities],
        "spans": [
            [span.model_dump(mode="json") for span in item.spans] for item in hypotheses
        ],
        "hypotheses": [
            {
                "raw_name": item.raw_name,
                "spans": [span.model_dump(mode="json") for span in item.spans],
                "role": item.role,
                "polarity": item.polarity,
                "route_relevant": item.route_relevant,
                "priority": item.priority,
            }
            for item in hypotheses
        ],
    }


async def grounding_prediction(case: dict) -> dict:
    raw_name = case["input"]["mention"]
    hypothesis = PlaceHypothesis(
        hypothesis_id=f"eval-{case['case_id']}",
        raw_name=raw_name,
        context=raw_name,
        spans=(TextSpan(start=0, end=len(raw_name)),),
        brand_only=bool(case["input"].get("brand_only", False)),
        branch_unspecified=bool(case["input"].get("branch_unspecified", False)),
    )
    candidates = tuple(
        PlaceCandidate(
            candidate_id=str(item["poi_id"]),
            hypothesis_id=hypothesis.hypothesis_id,
            provider="eval-fixture",
            provider_place_id=str(item["poi_id"]),
            name=str(item["name"]),
            address=item.get("address"),
            category=item.get("category"),
            parent_provider_place_id=item.get("parent_id"),
            location=(
                GeoPoint(lng=104, lat=30)
                if item.get("coordinate_status") == "verified"
                else None
            ),
            source_record_id=f"source-{case['case_id']}-{index}",
        )
        for index, item in enumerate(case["input"].get("candidates", []))
    )
    if not candidates:
        return {"resolution": "unresolved_category_preference"}
    adapter = DeepSeekAmapPlaceKnowledge(city=case["input"].get("city"))
    advice = await adapter.compare_candidates(hypothesis, candidates)
    return {
        "resolution": (
            advice.recommended_candidate_id if advice.status == "resolve" else advice.status
        ),
        "status": advice.status,
        "confidence": advice.confidence,
    }


async def main_async(args) -> None:
    root = args.root
    outputs: dict[str, list[dict]] = {}
    if args.task in {"mention", "all"}:
        cases = load_cases(root, "mention_cases*.jsonl")
        if args.case_id:
            cases = [case for case in cases if case["case_id"] == args.case_id]
        cases = cases[: args.limit or None]
        outputs["mention"] = await run_bounded(
            cases,
            mention_prediction,
            args.concurrency,
            checkpoint=args.output_dir / "mention_predictions.jsonl",
            resume=args.resume,
        )
    if args.task in {"grounding", "all"}:
        cases = load_cases(root, "grounding_cases*.jsonl")
        if args.case_id:
            cases = [case for case in cases if case["case_id"] == args.case_id]
        cases = cases[: args.limit or None]
        outputs["grounding"] = await run_bounded(
            cases,
            grounding_prediction,
            args.concurrency,
            checkpoint=args.output_dir / "grounding_predictions.jsonl",
            resume=args.resume,
        )
    for task, records in outputs.items():
        target = args.output_dir / f"{task}_predictions.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
            encoding="utf-8",
        )
        errors = sum(1 for item in records if "error_type" in item)
        print(json.dumps({"task": task, "cases": len(records), "errors": errors, "output": str(target)}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=("mention", "grounding", "all"), default="all")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "evals" / "agent_v2" / "datasets",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
