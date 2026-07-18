"""Run a concise real-provider Agent V2 chain and print only acceptance metadata."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic_ai_harness.step_persistence import SqliteStepStore

from app.env import load_project_env
from app.trip_agent.adapters.place_knowledge import DeepSeekAmapPlaceKnowledge
from app.trip_agent.adapters.narrative import DeepSeekNarrativeGenerator
from app.trip_agent.adapters.provider import build_deepseek_v4_model
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.runtime import TripAgentService
from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=("single", "multiday-hotel"),
        default="single",
    )
    return parser.parse_args()


async def async_main(*, scenario: str) -> int:
    load_project_env()
    started = monotonic()
    if scenario == "multiday-hotel":
        raw_request = (
            "请规划2026年8月3日至4日成都两日游，住成都首座万豪酒店。"
            "第一天只参观成都武侯祠博物馆，第二天只参观杜甫草堂博物馆；"
            "每天必须从酒店出发并返回酒店，午餐各安排一个明确的60分钟用餐块。"
            "必须使用搜索得到的真实地点和酒店，建立草案并模拟；"
            "非关键营业事实不足可 degraded 发布，不要询问用户。"
        )
        destination = "成都"
        start_date = date(2026, 8, 3)
        days = 2
    else:
        raw_request = (
            "请规划2026年8月3日成都一日游，只安排成都武侯祠博物馆。"
            "必须使用搜索得到的真实地点，建立草案并模拟；非关键营业事实不足可 degraded 发布，"
            "不要询问用户。"
        )
        destination = "成都"
        start_date = date(2026, 8, 3)
        days = 1
    with TemporaryDirectory(prefix="trip-agent-v2-real-") as directory:
        root = Path(directory)
        repository = SqliteTripAgentRepository(root / "domain.sqlite3")
        service = TripAgentService(
            repository,
            DeepSeekAmapPlaceKnowledge(city="成都"),
            HarnessPersistenceAdapter(
                SqliteStepStore(database=root / "provider.sqlite3"),
                deferred_database=root / "deferred.sqlite3",
            ),
            DeepSeekNarrativeGenerator(),
        )
        try:
            outcome = await service.start(
                raw_request=raw_request,
                destination=destination,
                start_date=start_date,
                days=days,
                model=build_deepseek_v4_model("root"),
            )
            workspace = repository.get_workspace(outcome.workspace_id)
            run = repository.get_run(outcome.run_id)
            sources = repository.list_sources(outcome.workspace_id)
            claims = repository.list_claims(outcome.workspace_id)
            releases = repository.list_releases(outcome.workspace_id)
            narrative = (
                repository.get_release_narrative(releases[-1].release_id) if releases else None
            )
            event_types = [event["type"] for event in outcome.events]
            summary = {
                "ok": outcome.status.value == "published",
                "status": outcome.status.value,
                "error_code": run.error_code if run else None,
                "error_message": run.error_message if run else None,
                "output_preview": outcome.output[:200],
                "event_types": event_types,
                "candidate_count": len(workspace.place_candidates) if workspace else 0,
                "source_count": len(sources),
                "claim_count": len(claims),
                "draft_date": (
                    workspace.current_draft.days[0].date.isoformat()
                    if workspace and workspace.current_draft and workspace.current_draft.days
                    else None
                ),
                "draft_day_count": (
                    len(workspace.current_draft.days)
                    if workspace and workspace.current_draft
                    else 0
                ),
                "hotel_day_count": (
                    sum(bool(day.hotel_candidate_id) for day in workspace.current_draft.days)
                    if workspace and workspace.current_draft
                    else 0
                ),
                "return_hotel_day_count": (
                    sum(day.return_to_hotel for day in workspace.current_draft.days)
                    if workspace and workspace.current_draft
                    else 0
                ),
                "meal_count": (
                    sum(len(day.meals) for day in workspace.current_draft.days)
                    if workspace and workspace.current_draft
                    else 0
                ),
                "narrative_generator": narrative.generator if narrative else None,
                "all_candidates_have_provider_ids": bool(workspace)
                and all(candidate.provider_place_id for candidate in workspace.place_candidates),
                "elapsed_seconds": round(monotonic() - started, 2),
                "deterministic_fallback": any(
                    "fallback" in event_type.lower() for event_type in event_types
                ),
            }
            print(json.dumps(summary, ensure_ascii=False))
            return 0 if summary["ok"] else 1
        finally:
            repository.close()


if __name__ == "__main__":
    arguments = _arguments()
    raise SystemExit(asyncio.run(async_main(scenario=arguments.scenario)))
