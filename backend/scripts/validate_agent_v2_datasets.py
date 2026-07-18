from __future__ import annotations

import argparse
import json
from pathlib import Path


DATASETS = {
    "mention": ("mention_cases*.jsonl", 100),
    "grounding": ("grounding_cases*.jsonl", 60),
    "fact_claim": ("fact_claim_cases*.jsonl", 50),
    "trip": ("trip_cases*.jsonl", 30),
    "runtime": ("runtime_cases*.jsonl", 20),
}


def load_cases(root: Path, pattern: str) -> list[dict]:
    cases: list[dict] = []
    for path in sorted(root.glob(pattern)):
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            item["_source_file"] = path.name
            cases.append(item)
    return cases


def validate(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    all_ids: dict[str, str] = {}
    failures: list[str] = []
    for name, (pattern, minimum) in DATASETS.items():
        cases = load_cases(root, pattern)
        counts[name] = len(cases)
        if len(cases) < minimum:
            failures.append(f"{name}: {len(cases)} < {minimum}")
        for item in cases:
            missing = {"case_id", "label_version", "source", "input", "expected"} - item.keys()
            if missing:
                failures.append(f"{item.get('_source_file')} missing {sorted(missing)}")
                continue
            case_id = str(item["case_id"])
            if case_id in all_ids:
                failures.append(
                    f"duplicate case_id {case_id}: {all_ids[case_id]} and {item['_source_file']}"
                )
            all_ids[case_id] = item["_source_file"]
            if not isinstance(item["label_version"], int) or item["label_version"] < 1:
                failures.append(f"{case_id}: label_version must be a positive integer")
    if failures:
        raise ValueError("dataset validation failed:\n- " + "\n- ".join(failures))
    return counts


def _latest_predictions(attempts: dict[str, list[dict]]) -> dict[str, dict]:
    return {
        case_id: records[-1].get("prediction", {})
        for case_id, records in attempts.items()
        if records and "error_type" not in records[-1]
    }


def _reliability(cases: list[dict], attempts: dict[str, list[dict]]) -> dict[str, float]:
    case_ids = {case["case_id"] for case in cases}
    attempted = {case_id for case_id in attempts if case_id in case_ids}
    latest_success = {
        case_id
        for case_id, records in attempts.items()
        if case_id in case_ids and records and "error_type" not in records[-1]
    }
    first_pass_errors = sum(
        bool(records and "error_type" in records[0])
        for case_id, records in attempts.items()
        if case_id in case_ids
    )
    recovered = sum(
        bool(
            records
            and "error_type" in records[0]
            and "error_type" not in records[-1]
        )
        for case_id, records in attempts.items()
        if case_id in case_ids
    )
    return {
        "attempted_coverage": len(attempted) / len(cases) if cases else 0.0,
        "successful_coverage": len(latest_success) / len(cases) if cases else 0.0,
        "first_pass_error_rate": first_pass_errors / len(attempted) if attempted else 0.0,
        "recovered_case_count": float(recovered),
    }


def score_mentions(cases: list[dict], attempts: dict[str, list[dict]]) -> dict[str, float]:
    predictions = _latest_predictions(attempts)
    expected_total = 0
    matched = 0
    predicted_total = 0
    for case in cases:
        expected = {_normalize(item) for item in case["expected"].get("mentions", [])}
        predicted = {
            _normalize(item) for item in predictions.get(case["case_id"], {}).get("mentions", [])
        }
        expected_total += len(expected)
        predicted_total += len(predicted)
        matched += len(expected & predicted)
    return {
        "recall": matched / expected_total if expected_total else 0.0,
        "precision": matched / predicted_total if predicted_total else 0.0,
        "expected_mentions": float(expected_total),
        **_reliability(cases, attempts),
    }


def score_grounding(cases: list[dict], attempts: dict[str, list[dict]]) -> dict[str, float]:
    predictions = _latest_predictions(attempts)
    automatic = 0
    correct = 0
    ambiguous_cases = 0
    ambiguous_wrong_confirmation = 0
    for case in cases:
        expected = case["expected"].get("resolution")
        predicted = predictions.get(case["case_id"], {}).get("resolution")
        if expected == "ambiguous":
            ambiguous_cases += 1
            if predicted not in {None, "ambiguous"}:
                ambiguous_wrong_confirmation += 1
        if predicted not in {None, "ambiguous", "unresolved", "unresolved_category_preference"}:
            automatic += 1
            if predicted == expected:
                correct += 1
    return {
        "automatic_confirmation_precision": correct / automatic if automatic else 0.0,
        "ambiguous_wrong_confirmation_rate": (
            ambiguous_wrong_confirmation / ambiguous_cases if ambiguous_cases else 0.0
        ),
        "automatic_confirmations": float(automatic),
        **_reliability(cases, attempts),
    }


def load_predictions(paths: list[Path]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for path in paths:
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            item = json.loads(raw)
            case_id = item.get("case_id")
            if not case_id:
                raise ValueError(f"{path}:{line_number}: missing case_id")
            result.setdefault(case_id, []).append(item)
    return result


def _normalize(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "evals" / "agent_v2" / "datasets",
    )
    parser.add_argument("--mention-predictions", type=Path, nargs="+")
    parser.add_argument("--grounding-predictions", type=Path, nargs="+")
    args = parser.parse_args()
    counts = validate(args.root)
    report: dict[str, object] = {"dataset_counts": counts}
    if args.mention_predictions:
        report["mention"] = score_mentions(
            load_cases(args.root, DATASETS["mention"][0]),
            load_predictions(args.mention_predictions),
        )
    if args.grounding_predictions:
        report["grounding"] = score_grounding(
            load_cases(args.root, DATASETS["grounding"][0]),
            load_predictions(args.grounding_predictions),
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
