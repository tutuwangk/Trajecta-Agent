"""Run one real DeepSeek V4 Pro Agent V2 vertical slice with fixture facts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic_ai_harness.step_persistence import SqliteStepStore

from app.env import load_project_env
from app.trip_agent.adapters.provider import build_deepseek_v4_model
from app.trip_agent.domain import GeoPoint, PlaceCandidate, PlaceHypothesis, SourceRecord, TextSpan, utc_now
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.runtime import TripAgentService
from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter
from app.trip_agent.toolsets import CandidateSearch


@dataclass(slots=True)
class FixturePlaceKnowledge:
    async def analyze_mentions(self, raw_request: str) -> tuple[PlaceHypothesis, ...]:
        raw_name = "武侯祠"
        start = raw_request.index(raw_name)
        return (
            PlaceHypothesis(
                hypothesis_id="hypothesis-wuhou",
                raw_name=raw_name,
                context=raw_request,
                spans=(TextSpan(start=start, end=start + len(raw_name)),),
                possible_category="attraction",
            ),
        )

    async def search_candidates(self, hypothesis: PlaceHypothesis) -> CandidateSearch:
        return CandidateSearch(
            sources=(
                SourceRecord(
                    source_record_id="source-wuhou",
                    source_type="amap",
                    provider="fixture",
                    payload={"id": "amap-wuhou", "name": "成都武侯祠博物馆"},
                    content_hash="source-wuhou-hash",
                    retrieved_at=utc_now(),
                ),
            ),
            candidates=(PlaceCandidate(
                candidate_id="candidate-wuhou",
                hypothesis_id=hypothesis.hypothesis_id,
                provider="fixture",
                provider_place_id="amap-wuhou",
                name="成都武侯祠博物馆",
                address="武侯祠大街231号",
                city="成都",
                category="attraction",
                location=GeoPoint(lng=104.049, lat=30.646),
                source_record_id="source-wuhou",
            ),),
        )


async def async_main() -> int:
    load_project_env()
    started = monotonic()
    with TemporaryDirectory(prefix="trip-agent-v2-") as directory:
        root = Path(directory)
        repository = SqliteTripAgentRepository(root / "domain.sqlite3")
        persistence = HarnessPersistenceAdapter(
            SqliteStepStore(database=root / "provider-steps.sqlite3"),
            deferred_database=root / "deferred-transcripts.sqlite3",
        )
        service = TripAgentService(repository, FixturePlaceKnowledge(), persistence)
        try:
            outcome = await service.start(
                raw_request=(
                    "请规划2026年8月3日成都一日游。武侯祠必须安排，全天只安排这个地点也可以，"
                    "不要询问我，直接建立草案、模拟并提交。"
                ),
                destination="成都",
                days=1,
                model=build_deepseek_v4_model("root"),
            )
            summary = {
                "ok": outcome.status.value == "published",
                "status": outcome.status.value,
                "event_types": [event["type"] for event in outcome.events],
                "elapsed_seconds": round(monotonic() - started, 2),
                "deterministic_fallback": False,
            }
            print(json.dumps(summary, ensure_ascii=False))
            return 0 if summary["ok"] else 1
        finally:
            repository.close()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
