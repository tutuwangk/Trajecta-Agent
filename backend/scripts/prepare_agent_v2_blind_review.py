"""Create anonymized A/B itinerary packets from persisted V2 and Legacy artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-output-dir", type=Path, required=True)
    parser.add_argument("--legacy-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def _v2_view(record: dict) -> dict:
    workspace = json.loads(
        (Path(record["artifact_dir"]) / "workspace.json").read_text(encoding="utf-8")
    )
    candidates = {item["candidate_id"]: item["name"] for item in workspace["place_candidates"]}
    draft = workspace.get("current_draft") or {"days": []}
    return {
        "status": record["status"],
        "fact_status": record.get("fact_status"),
        "experience_status": record.get("experience_status"),
        "days": [
            {
                "day": day["day_index"],
                "date": day["date"],
                "hotel": candidates.get(day.get("hotel_candidate_id")),
                "visits": [
                    {
                        "name": candidates.get(visit["place_candidate_id"], "unknown"),
                        "duration_min": visit["duration_min"],
                        "earliest_start": visit.get("earliest_start"),
                        "latest_end": visit.get("latest_end"),
                    }
                    for visit in day["visits"]
                ],
                "meals": day.get("meals", []),
            }
            for day in draft["days"]
        ],
    }


def main() -> int:
    args = _arguments()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    attempts = [
        json.loads(line)
        for line in (args.v2_output_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    latest_v2: dict[str, dict] = {}
    for record in attempts:
        if record.get("status") == "published":
            latest_v2[record["case_id"]] = record
    legacy = json.loads(args.legacy_summary.read_text(encoding="utf-8"))
    legacy_by_id = {item["id"]: item for item in legacy.get("results", [])}
    packets = []
    answer_key = []
    for case_id in sorted(set(latest_v2) & set(legacy_by_id)):
        v2 = _v2_view(latest_v2[case_id])
        legacy_view = legacy_by_id[case_id]
        v2_first = bool(secrets.randbelow(2))
        packets.append(
            {
                "pair_id": f"pair-{len(packets) + 1:03d}",
                "case_id": case_id,
                "itinerary_a": v2 if v2_first else legacy_view,
                "itinerary_b": legacy_view if v2_first else v2,
                "rubric": [
                    "用户目标保真",
                    "路线与分天合理性",
                    "时间和用餐体验",
                    "信息透明度",
                    "总体偏好",
                ],
            }
        )
        answer_key.append(
            {
                "pair_id": packets[-1]["pair_id"],
                "a_system": "v2" if v2_first else "legacy",
                "b_system": "legacy" if v2_first else "v2",
            }
        )
    (args.output_dir / "blind_review_packets.json").write_text(
        json.dumps(packets, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "blind_review_answer_key.json").write_text(
        json.dumps(answer_key, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"pair_count": len(packets)}, ensure_ascii=False))
    return 0 if packets else 1


if __name__ == "__main__":
    raise SystemExit(main())
