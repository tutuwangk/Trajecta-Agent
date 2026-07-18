from __future__ import annotations

from datetime import date
import json
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.trip_agent.domain import (
    GeoPoint,
    ObservedClaim,
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


class TwoPlaceKnowledge:
    async def analyze_mentions(self, raw_request: str) -> tuple[PlaceHypothesis, ...]:
        return tuple(
            PlaceHypothesis(
                hypothesis_id=f"hypothesis-{name}",
                raw_name=name,
                context=raw_request,
                spans=(TextSpan(start=raw_request.index(name), end=raw_request.index(name) + len(name)),),
            )
            for name in ("甲地", "乙地")
        )

    async def search_candidates(self, hypothesis: PlaceHypothesis) -> CandidateSearch:
        suffix = hypothesis.raw_name
        source = SourceRecord(
            source_record_id=f"source-{suffix}",
            source_type="amap",
            provider="fixture",
            payload={"id": f"amap-{suffix}"},
            content_hash=f"hash-{suffix}",
            retrieved_at=utc_now(),
        )
        return CandidateSearch(
            sources=(source,),
            candidates=(
                PlaceCandidate(
                    candidate_id=f"candidate-{suffix}",
                    hypothesis_id=hypothesis.hypothesis_id,
                    provider="amap",
                    provider_place_id=f"amap-{suffix}",
                    name=suffix,
                    location=GeoPoint(
                        lng=104.0 if suffix == "甲地" else 104.01,
                        lat=30.6,
                    ),
                    source_record_id=source.source_record_id,
                ),
            ),
        )

    async def acquire_route_facts(
        self, origin: PlaceCandidate, destination: PlaceCandidate, mode: str
    ) -> FactAcquisition:
        source = SourceRecord(
            source_record_id=f"source-route-{origin.candidate_id}-{destination.candidate_id}",
            source_type="amap",
            provider=f"fixture-{mode}",
            payload={"duration_min": 15, "distance_m": 1000},
            content_hash=f"hash-{origin.candidate_id}-{destination.candidate_id}",
            retrieved_at=utc_now(),
        )
        entity_id = f"route:{origin.candidate_id}:{destination.candidate_id}:{mode}"
        return FactAcquisition(
            sources=(source,),
            claims=tuple(
                ObservedClaim(
                    claim_id=f"claim-{field}-{origin.candidate_id}-{destination.candidate_id}",
                    entity_id=entity_id,
                    field=field,
                    value=value,
                    source_record_ids=(source.source_record_id,),
                    extractor="fixture",
                    extractor_version="1",
                    acquired_at=utc_now(),
                    confidence=1,
                    release_eligible=True,
                )
                for field, value in (("duration_min", 15), ("distance_m", 1000))
            ),
        )


def _tool(name: str, args: dict[str, Any] | str, index: int) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=f"call-{index}-{name}")],
        finish_reason="tool_call",
    )


@pytest.mark.anyio
async def test_route_batch_commits_multiple_route_queries_as_one_workspace_revision(tmp_path):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        scripted: dict[int, tuple[str, dict[str, Any] | str]] = {
            1: ("analyze_place_mentions", {}),
            2: (
                "search_place_candidates",
                {"hypothesis_id": "hypothesis-甲地"},
            ),
            3: (
                "search_place_candidates",
                {"hypothesis_id": "hypothesis-乙地"},
            ),
            4: (
                "resolve_place",
                {
                    "hypothesis_id": "hypothesis-甲地",
                    "candidate_id": "candidate-甲地",
                    "rationale": "exact",
                },
            ),
            5: (
                "resolve_place",
                {
                    "hypothesis_id": "hypothesis-乙地",
                    "candidate_id": "candidate-乙地",
                    "rationale": "exact",
                },
            ),
            6: (
                "acquire_route_facts",
                {
                    "routes": [
                        {
                            "origin_candidate_id": "candidate-甲地",
                            "destination_candidate_id": "candidate-乙地",
                            "mode": "walking",
                        },
                        {
                            "origin_candidate_id": "candidate-乙地",
                            "destination_candidate_id": "candidate-甲地",
                            "mode": "walking",
                        },
                    ],
                },
            ),
            7: (
                "apply_draft_change",
                json.dumps(
                    {
                        "days": [
                            {
                                "day_index": 1,
                                "date": "2026-08-03",
                                "visits": [
                                    {"place_candidate_id": "candidate-甲地", "duration_min": 60},
                                    {
                                        "place_candidate_id": "candidate-乙地",
                                        "duration_min": 60,
                                        "travel_mode_from_previous": "walking",
                                    },
                                ],
                            }
                        ],
                    }
                ),
            ),
            8: ("simulate_candidate", {}),
            9: (
                "submit_candidate",
                {"completion_reason": "batch route facts available"},
            ),
        }
        if step in scripted:
            name, args = scripted[step]
            assert name in {tool.name for tool in info.function_tools}
            return _tool(name, args, step)
        return ModelResponse(parts=[TextPart("published")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "route-batch.sqlite3")
    service = TripAgentService(repository, TwoPlaceKnowledge())
    outcome = await service.start(
        raw_request="2026年8月3日依次游览甲地和乙地。",
        destination="成都",
        start_date=date(2026, 8, 3),
        days=1,
        model=FunctionModel(model_function),
    )

    assert outcome.status is RunStatus.PUBLISHED
    route_events = [event for event in outcome.events if event["type"] == "route_facts_acquired"]
    assert len(route_events) == 1
    assert len(route_events[0]["routes"]) == 2
    workspace = repository.get_workspace(outcome.workspace_id)
    assert workspace is not None
    assert workspace.version == 8
    assert workspace.fact_version == 1
    route_claims = [
        claim for claim in repository.list_claims(outcome.workspace_id) if claim.entity_id.startswith("route:")
    ]
    assert len(route_claims) == 4
    repository.close()
