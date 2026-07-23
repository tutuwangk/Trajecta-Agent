from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.trip_agent.domain import (
    EstimateClaim,
    GeoPoint,
    PlaceCandidate,
    PlaceHypothesis,
    RunStatus,
    SourceRecord,
    TextSpan,
    utc_now,
)
from app.trip_agent.repositories import SqliteTripAgentRepository
from app.trip_agent.runtime import TripAgentService
from app.trip_agent.toolsets import CandidateSearch, FactAcquisition


@dataclass
class MultiMutationKnowledge:
    async def analyze_mentions(self, raw_request: str) -> tuple[PlaceHypothesis, ...]:
        return tuple(
            PlaceHypothesis(
                hypothesis_id=f"hypothesis-{name}",
                raw_name=name,
                context=raw_request,
                spans=(
                    TextSpan(
                        start=raw_request.index(name),
                        end=raw_request.index(name) + len(name),
                    ),
                ),
            )
            for name in ("甲地", "乙地")
        )

    async def search_candidates(self, hypothesis: PlaceHypothesis) -> CandidateSearch:
        name = hypothesis.raw_name
        source = SourceRecord(
            source_record_id=f"source-{name}",
            source_type="amap",
            provider="fixture",
            payload={"id": f"amap-{name}"},
            content_hash=f"hash-{name}",
            retrieved_at=utc_now(),
        )
        return CandidateSearch(
            sources=(source,),
            candidates=(
                PlaceCandidate(
                    candidate_id=f"candidate-{name}",
                    hypothesis_id=hypothesis.hypothesis_id,
                    provider="amap",
                    provider_place_id=f"amap-{name}",
                    name=name,
                    location=GeoPoint(lng=104.0, lat=30.6),
                    source_record_id=source.source_record_id,
                ),
            ),
        )

    async def estimate_visit_profile(self, candidate: PlaceCandidate) -> FactAcquisition:
        claim = EstimateClaim(
            claim_id=f"visit-profile-{candidate.candidate_id}",
            entity_id=candidate.candidate_id,
            field="recommended_duration_min",
            value=60,
            extractor="fixture",
            extractor_version="1",
            acquired_at=utc_now(),
            confidence=0.8,
            release_eligible=False,
            method="fixture_estimate",
        )
        return FactAcquisition(sources=(), claims=(claim,))


def _calls(*calls: tuple[str, dict[str, Any]], step: int) -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=name,
                args=args,
                tool_call_id=f"call-{step}-{index}-{name}",
            )
            for index, (name, args) in enumerate(calls, 1)
        ],
        finish_reason="tool_call",
    )


@pytest.mark.anyio
async def test_multiple_workspace_mutations_from_one_model_turn_use_runtime_owned_versions(
    tmp_path,
):
    step = 0
    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        available = {tool.name for tool in info.function_tools}
        if step == 1:
            return _calls(("analyze_place_mentions", {}), step=step)
        if step == 2:
            assert "search_place_candidates" not in available
            assert "search_candidate_sets" in available
            return _calls(
                (
                    "search_candidate_sets",
                    {"hypothesis_ids": ["hypothesis-甲地", "hypothesis-乙地"]},
                ),
                step=step,
            )
        if step == 3:
            return _calls(
                (
                    "resolve_place",
                    {
                        "hypothesis_id": "hypothesis-甲地",
                        "candidate_id": "candidate-甲地",
                        "rationale": "exact identity",
                    },
                ),
                (
                    "resolve_place",
                    {
                        "hypothesis_id": "hypothesis-乙地",
                        "candidate_id": "candidate-乙地",
                        "rationale": "exact identity",
                    },
                ),
                step=step,
            )
        if step == 4:
            return _calls(
                ("estimate_visit_profile", {"candidate_id": "candidate-甲地"}),
                ("estimate_visit_profile", {"candidate_id": "candidate-乙地"}),
                step=step,
            )
        if step == 5:
            return _calls(
                (
                    "apply_draft_change",
                        json.dumps({
                            "days": [
                            {
                                "day_index": 1,
                                "date": "2026-08-03",
                                "visits": [
                                    {
                                        "place_candidate_id": "candidate-甲地",
                                        "duration_min": 60,
                                    }
                                ],
                            }
                        ]
                        }),
                ),
                step=step,
            )
        if step == 6:
            return _calls(
                (
                    "submit_candidate",
                    {"completion_reason": "one-place draft is structurally viable"},
                ),
                step=step,
            )
        return ModelResponse(parts=[TextPart("published")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "multi-mutation.sqlite3")
    service = TripAgentService(repository, MultiMutationKnowledge())
    outcome = await service.start(
        raw_request="2026年8月3日考虑甲地和乙地，最终可由你取舍。",
        destination="成都",
        start_date=date(2026, 8, 3),
        days=1,
        model=FunctionModel(model_function),
    )

    run = repository.get_run(outcome.run_id)
    assert outcome.status is RunStatus.PUBLISHED, (
        run.error_code if run else None,
        run.error_message if run else None,
        outcome.events,
        outcome.output,
        step,
    )
    workspace = repository.get_workspace(outcome.workspace_id)
    assert workspace is not None
    # The filtered toolset now rejects premature single-place fact expansion before
    # an early draft exists, so only intent, candidates, resolutions, and the draft
    # advance the workspace in this scripted route.
    assert workspace.version == 6
    assert workspace.fact_version == 0
    assert len(repository.list_claims(outcome.workspace_id)) == 0
    repository.close()


@pytest.mark.anyio
async def test_batch_grounding_collapses_many_place_mutations_into_semantic_actions(tmp_path):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:
            return _calls(("analyze_place_mentions", {}), step=step)
        if step == 2:
            return _calls(
                (
                    "search_candidate_sets",
                    {"hypothesis_ids": ["hypothesis-甲地", "hypothesis-乙地"]},
                ),
                step=step,
            )
        if step == 3:
            return _calls(
                (
                    "apply_place_resolutions",
                    {
                        "decisions": [
                            {
                                "hypothesis_id": "hypothesis-甲地",
                                "candidate_id": "candidate-甲地",
                                "rationale": "exact identity",
                            },
                            {
                                "hypothesis_id": "hypothesis-乙地",
                                "candidate_id": "candidate-乙地",
                                "rationale": "exact identity",
                            },
                        ]
                    },
                ),
                step=step,
            )
        if step == 4:
            return _calls(
                (
                    "apply_draft_change",
                    json.dumps({
                        "days": [
                            {
                                "day_index": 1,
                                "date": "2026-08-03",
                                "visits": [
                                    {
                                        "place_candidate_id": "candidate-甲地",
                                        "duration_min": 60,
                                    }
                                ],
                            }
                        ]
                    }),
                ),
                step=step,
            )
        if step == 5:
            return _calls(
                ("submit_candidate", {"completion_reason": "complete one-day route"}),
                step=step,
            )
        return ModelResponse(parts=[TextPart("published")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "batch-grounding.sqlite3")
    service = TripAgentService(repository, MultiMutationKnowledge())
    outcome = await service.start(
        raw_request="2026年8月3日考虑甲地和乙地，最终可由你取舍。",
        destination="成都",
        start_date=date(2026, 8, 3),
        days=1,
        model=FunctionModel(model_function),
    )

    run = repository.get_run(outcome.run_id)
    assert outcome.status is RunStatus.PUBLISHED, (
        run.error_code if run else None,
        run.error_message if run else None,
        outcome.events,
        outcome.output,
        step,
    )
    assert step == 6
    event_types = [item["type"] for item in outcome.events]
    assert "place_candidate_sets_found" in event_types
    assert "place_resolutions_applied" in event_types
    workspace = repository.get_workspace(outcome.workspace_id)
    assert workspace is not None
    assert workspace.version == 5
    repository.close()
