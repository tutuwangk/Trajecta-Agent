from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai import ModelHTTPError, UsageLimitExceeded
from pydantic_ai_harness.step_persistence import SqliteStepStore
import pytest

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
from app.trip_agent.runtime.harness_persistence import HarnessPersistenceAdapter
from app.trip_agent.runtime.service import EpisodeDeadlineExceeded
from app.trip_agent.toolsets import CandidateSearch, FactAcquisition


@dataclass
class FixturePlaceKnowledge:
    async def analyze_mentions(self, raw_request: str) -> tuple[PlaceHypothesis, ...]:
        assert "武侯祠" in raw_request
        start = raw_request.index("武侯祠")
        return (
            PlaceHypothesis(
                hypothesis_id="hypothesis-wuhou",
                raw_name="武侯祠",
                context=raw_request,
                spans=(TextSpan(start=start, end=start + 3),),
                possible_category="attraction",
            ),
        )

    async def search_candidates(self, hypothesis: PlaceHypothesis) -> CandidateSearch:
        assert hypothesis.hypothesis_id == "hypothesis-wuhou"
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


@dataclass
class ParentChildPlaceKnowledge(FixturePlaceKnowledge):
    async def search_candidates(self, hypothesis: PlaceHypothesis) -> CandidateSearch:
        source = SourceRecord(
            source_record_id="source-parent-child",
            source_type="amap",
            provider="fixture",
            payload={"case": "parent-child"},
            content_hash="parent-child-hash",
            retrieved_at=utc_now(),
        )
        return CandidateSearch(
            sources=(source,),
            candidates=(
                PlaceCandidate(
                    candidate_id="candidate-parent",
                    hypothesis_id=hypothesis.hypothesis_id,
                    provider="fixture",
                    provider_place_id="amap-parent",
                    name="成都武侯祠博物馆",
                    location=GeoPoint(lng=104.049, lat=30.646),
                    source_record_id=source.source_record_id,
                ),
                PlaceCandidate(
                    candidate_id="candidate-child",
                    hypothesis_id=hypothesis.hypothesis_id,
                    provider="fixture",
                    provider_place_id="amap-child",
                    parent_provider_place_id="amap-parent",
                    name="成都武侯祠博物馆文物区大门售票处",
                    location=GeoPoint(lng=104.049, lat=30.646),
                    source_record_id=source.source_record_id,
                ),
            ),
        )


@dataclass
class ClosingPlaceKnowledge(FixturePlaceKnowledge):
    async def acquire_place_facts(
        self, candidate: PlaceCandidate, applicable_date
    ) -> FactAcquisition:
        source = SourceRecord(
            source_record_id="source-current-closure",
            source_type="web",
            provider="fixture",
            payload={"closure": "闭馆"},
            content_hash="current-closure-hash",
            retrieved_at=utc_now(),
        )
        return FactAcquisition(
            sources=(source,),
            claims=(
                ObservedClaim(
                    claim_id="claim-current-closure",
                    entity_id=candidate.candidate_id,
                    field="closure",
                    value="闭馆",
                    applicable_date=applicable_date,
                    source_record_ids=(source.source_record_id,),
                    extractor="fixture",
                    extractor_version="1",
                    acquired_at=utc_now(),
                    confidence=1,
                    release_eligible=True,
                ),
            ),
        )


@dataclass
class CachedPlaceKnowledge(FixturePlaceKnowledge):
    search_calls: int = 0

    async def search_candidates(self, hypothesis: PlaceHypothesis) -> CandidateSearch:
        self.search_calls += 1
        if self.search_calls > 1:
            raise RuntimeError("provider quota exhausted")
        return await super().search_candidates(hypothesis)


def _tool(name: str, args: dict[str, Any] | str, index: int) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=f"call-{index}-{name}")],
        finish_reason="tool_call",
    )


@pytest.mark.anyio
async def test_raw_request_reaches_agent_created_candidate_and_release_without_legacy(tmp_path):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        scripted = {
            1: ("read_workspace", {}),
            2: ("analyze_place_mentions", {}),
            3: (
                "search_candidate_sets",
                {"hypothesis_ids": ["hypothesis-wuhou"]},
            ),
            4: (
                "resolve_place",
                {
                    "hypothesis_id": "hypothesis-wuhou",
                    "candidate_id": "candidate-wuhou",
                    "rationale": "Exact searched attraction identity.",
                },
            ),
            5: (
                "apply_draft_change",
                json.dumps(
                    {
                        "days": [
                            {
                                "day_index": 1,
                                "date": "2026-08-03",
                                "visits": [
                                    {
                                        "place_candidate_id": "candidate-wuhou",
                                        "duration_min": 90,
                                        "optional": False,
                                    }
                                ],
                                "meal_strategy": "normal lunch and dinner",
                            }
                        ],
                    }
                ),
            ),
            6: ("simulate_candidate", {}),
            7: (
                "submit_candidate",
                {"completion_reason": "The simulated draft is structurally viable."},
            ),
        }
        if step in scripted:
            name, args = scripted[step]
            assert name in {tool.name for tool in info.function_tools}
            return _tool(name, args, step)
        return ModelResponse(parts=[TextPart("published")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "vertical.sqlite3")
    service = TripAgentService(repository, FixturePlaceKnowledge())
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去，但不要太赶。",
        destination="成都",
        days=1,
        model=FunctionModel(model_function),
    )

    assert outcome.status is RunStatus.PUBLISHED
    assert [event["type"] for event in outcome.events] == [
        "run_started",
        "place_mentions_analyzed",
        "place_candidate_sets_found",
        "place_resolved",
        "draft_changed",
        "candidate_checkpoint_saved",
        "candidate_simulated",
        "candidate_published",
    ]
    workspace = repository.get_workspace(outcome.workspace_id)
    assert workspace is not None
    assert workspace.current_draft is not None
    assert workspace.current_draft.days[0].visits[0].place_candidate_id == "candidate-wuhou"
    release = repository.get_release_by_key(f"{outcome.workspace_id}:5:0")
    assert release is not None
    assert release.fact_status.value == "degraded"
    assert repository.get_candidate(release.candidate_id) is not None
    narrative = repository.get_release_narrative(release.release_id)
    assert narrative is not None
    assert narrative.route_fact_fingerprint == release.route_fact_fingerprint
    assert narrative.generator == "deterministic-template"
    repository.close()


@pytest.mark.anyio
async def test_recent_real_candidate_search_is_reused_when_provider_capacity_is_exhausted(tmp_path):
    def model() -> FunctionModel:
        step = 0

        async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
            nonlocal step
            step += 1
            scripted = {
                1: ("analyze_place_mentions", {}),
                2: ("search_candidate_sets", {"hypothesis_ids": ["hypothesis-wuhou"]}),
                3: (
                    "resolve_place",
                    {
                        "hypothesis_id": "hypothesis-wuhou",
                        "candidate_id": "candidate-wuhou",
                        "rationale": "cached real provider identity",
                    },
                ),
                4: (
                    "apply_draft_change",
                    json.dumps(
                        {
                            "days": [
                                {
                                    "day_index": 1,
                                    "date": "2026-08-03",
                                    "visits": [
                                        {
                                            "place_candidate_id": "candidate-wuhou",
                                            "duration_min": 90,
                                        }
                                    ],
                                }
                            ]
                        }
                    ),
                ),
                5: ("simulate_candidate", {}),
                6: ("submit_candidate", {"completion_reason": "complete cached route"}),
            }
            if step in scripted:
                name, args = scripted[step]
                return _tool(name, args, step)
            return ModelResponse(parts=[TextPart("published")], finish_reason="stop")

        return FunctionModel(model_function)

    repository = SqliteTripAgentRepository(tmp_path / "candidate-search-cache.sqlite3")
    knowledge = CachedPlaceKnowledge()
    service = TripAgentService(repository, knowledge)
    first = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        start_date=date(2026, 8, 3),
        days=1,
        model=model(),
    )
    second = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        start_date=date(2026, 8, 3),
        days=1,
        model=model(),
    )

    assert first.status is RunStatus.PUBLISHED
    assert second.status is RunStatus.PUBLISHED
    assert knowledge.search_calls == 1
    repository.close()


@pytest.mark.anyio
async def test_uncached_provider_capacity_error_stays_inside_tool_boundary(tmp_path):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:
            return _tool("analyze_place_mentions", {}, step)
        if step == 2:
            return _tool(
                "search_candidate_sets",
                {"hypothesis_ids": ["hypothesis-wuhou"]},
                step,
            )
        return ModelResponse(parts=[TextPart("provider unavailable")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "candidate-provider-unavailable.sqlite3")
    knowledge = CachedPlaceKnowledge(search_calls=1)
    service = TripAgentService(repository, knowledge)
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        start_date=date(2026, 8, 3),
        days=1,
        model=FunctionModel(model_function),
    )

    assert outcome.status is RunStatus.INCOMPLETE
    run = repository.get_run(outcome.run_id)
    assert run is not None
    assert run.error_code == "external_dependency_unavailable"
    assert run.failure_class.value == "transient_external"
    assert run.retryable is True
    assert not repository.list_releases(outcome.workspace_id)
    repository.close()


@pytest.mark.anyio
async def test_semantic_repeat_search_reuses_candidate_without_runtime_failure(tmp_path):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        scripted = {
            1: ("analyze_place_mentions", {}),
            2: ("search_candidate_sets", {"hypothesis_ids": ["hypothesis-wuhou"]}),
            3: ("search_place_candidates", {"hypothesis_id": "hypothesis-wuhou"}),
            4: (
                "resolve_place",
                {
                    "hypothesis_id": "hypothesis-wuhou",
                    "candidate_id": "candidate-wuhou",
                    "rationale": "stable provider identity",
                },
            ),
            5: (
                "apply_draft_change",
                json.dumps(
                    {
                        "days": [
                            {
                                "day_index": 1,
                                "date": "2026-08-03",
                                "visits": [
                                    {
                                        "place_candidate_id": "candidate-wuhou",
                                        "duration_min": 90,
                                    }
                                ],
                            }
                        ]
                    }
                ),
            ),
            6: ("simulate_candidate", {}),
            7: ("submit_candidate", {"completion_reason": "stable repeated observation"}),
        }
        if step in scripted:
            name, args = scripted[step]
            return _tool(name, args, step)
        return ModelResponse(parts=[TextPart("published")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "repeat-search.sqlite3")
    service = TripAgentService(repository, FixturePlaceKnowledge())
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        start_date=date(2026, 8, 3),
        days=1,
        model=FunctionModel(model_function),
    )

    workspace = repository.get_workspace(outcome.workspace_id)
    assert outcome.status is RunStatus.PUBLISHED
    assert workspace is not None
    assert len(workspace.place_candidates) == 1
    assert "candidate_observation_reused" in [item["type"] for item in outcome.events]
    repository.close()


@pytest.mark.anyio
async def test_agent_ending_without_submission_becomes_incomplete_not_fallback(tmp_path):
    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("I am done without a candidate.")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "incomplete.sqlite3")
    service = TripAgentService(repository, FixturePlaceKnowledge())
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        days=1,
        model=FunctionModel(model_function),
    )

    assert outcome.status is RunStatus.INCOMPLETE
    run = repository.get_run(outcome.run_id)
    assert run is not None
    assert run.error_code == "agent_action_repair_exhausted"
    assert repository.get_release_by_key(f"{outcome.workspace_id}:1:0") is None
    repository.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_status", "expected_status"),
    ((503, RunStatus.PUBLISHED), (402, RunStatus.FAILED)),
)
async def test_complete_checkpoint_respects_provider_failure_class(
    tmp_path, provider_status, expected_status
):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        scripted = {
            1: ("analyze_place_mentions", {}),
            2: (
                "search_candidate_sets",
                {"hypothesis_ids": ["hypothesis-wuhou"]},
            ),
            3: (
                "resolve_place",
                {
                    "hypothesis_id": "hypothesis-wuhou",
                    "candidate_id": "candidate-wuhou",
                    "rationale": "exact identity",
                },
            ),
            4: (
                "apply_draft_change",
                json.dumps(
                    {
                        "days": [
                            {
                                "day_index": 1,
                                "date": "2026-08-03",
                                "visits": [
                                    {
                                        "place_candidate_id": "candidate-wuhou",
                                        "duration_min": 90,
                                    }
                                ],
                            }
                        ]
                    }
                ),
            ),
            5: ("simulate_candidate", {}),
        }
        if step in scripted:
            name, args = scripted[step]
            return _tool(name, args, step)
        raise ModelHTTPError(
            provider_status,
            "fixture-provider",
            {"error": "late provider transport failure"},
        )

    repository = SqliteTripAgentRepository(tmp_path / "checkpoint-fallback.sqlite3")
    service = TripAgentService(repository, FixturePlaceKnowledge())
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        days=1,
        model=FunctionModel(model_function),
    )

    assert outcome.status is expected_status
    if provider_status == 503:
        assert outcome.output == "published_complete_checkpoint"
        assert "candidate_checkpoint_published" in [item["type"] for item in outcome.events]
        assert repository.list_releases(outcome.workspace_id)
    else:
        run = repository.get_run(outcome.run_id)
        assert run is not None
        assert run.failure_class.value == "permanent_external"
        assert not repository.list_releases(outcome.workspace_id)
    repository.close()


@pytest.mark.anyio
async def test_transient_provider_failure_retries_once_then_returns_incomplete(tmp_path):
    attempts = 0

    async def unavailable(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal attempts
        attempts += 1
        raise ModelHTTPError(503, "fixture-provider", {"error": "temporarily unavailable"})

    repository = SqliteTripAgentRepository(tmp_path / "provider-retry.sqlite3")
    service = TripAgentService(repository, FixturePlaceKnowledge())
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        days=1,
        model=FunctionModel(unavailable),
    )

    run = repository.get_run(outcome.run_id)
    assert outcome.status is RunStatus.INCOMPLETE
    assert run is not None
    assert run.failure_class.value == "transient_external"
    assert run.retryable is True
    assert run.provider_attempt_count == 2
    assert attempts == 2
    assert [item["type"] for item in outcome.events].count("provider_retry_scheduled") == 1
    repository.close()


@pytest.mark.anyio
async def test_complete_checkpoint_cannot_ignore_newer_blocking_facts(tmp_path):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        scripted = {
            1: ("analyze_place_mentions", {}),
            2: ("search_candidate_sets", {"hypothesis_ids": ["hypothesis-wuhou"]}),
            3: (
                "resolve_place",
                {
                    "hypothesis_id": "hypothesis-wuhou",
                    "candidate_id": "candidate-wuhou",
                    "rationale": "exact identity",
                },
            ),
            4: (
                "apply_draft_change",
                json.dumps(
                    {
                        "days": [
                            {
                                "day_index": 1,
                                "date": "2026-08-03",
                                "visits": [
                                    {
                                        "place_candidate_id": "candidate-wuhou",
                                        "duration_min": 90,
                                    }
                                ],
                            }
                        ]
                    }
                ),
            ),
            5: ("simulate_candidate", {}),
            6: (
                "acquire_place_facts",
                {"candidate_id": "candidate-wuhou", "applicable_date": "2026-08-03"},
            ),
        }
        if step in scripted:
            name, args = scripted[step]
            return _tool(name, args, step)
        raise RuntimeError("late provider transport failure")

    repository = SqliteTripAgentRepository(tmp_path / "checkpoint-current-facts.sqlite3")
    service = TripAgentService(repository, ClosingPlaceKnowledge())
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        start_date=date(2026, 8, 3),
        days=1,
        model=FunctionModel(model_function),
    )

    assert outcome.status is RunStatus.FAILED
    assert not repository.list_releases(outcome.workspace_id)
    repository.close()


@pytest.mark.anyio
async def test_usage_exhaustion_starts_convergence_episode_before_incomplete(tmp_path):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        scripted = {
            1: ("analyze_place_mentions", {}),
            2: (
                "search_candidate_sets",
                {"hypothesis_ids": ["hypothesis-wuhou"]},
            ),
            3: (
                "resolve_place",
                {
                    "hypothesis_id": "hypothesis-wuhou",
                    "candidate_id": "candidate-wuhou",
                    "rationale": "exact identity",
                },
            ),
            5: (
                "apply_draft_change",
                json.dumps(
                    {
                        "days": [
                            {
                                "day_index": 1,
                                "date": "2026-08-03",
                                "visits": [
                                    {
                                        "place_candidate_id": "candidate-wuhou",
                                        "duration_min": 90,
                                    }
                                ],
                            }
                        ]
                    }
                ),
            ),
            6: ("simulate_candidate", {}),
            7: ("submit_candidate", {"completion_reason": "converged complete route"}),
        }
        if step == 4:
            raise UsageLimitExceeded("initial episode expansion budget exhausted")
        if step in scripted:
            name, args = scripted[step]
            return _tool(name, args, step)
        return ModelResponse(parts=[TextPart("published")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "convergence.sqlite3")
    service = TripAgentService(repository, FixturePlaceKnowledge())
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        days=1,
        model=FunctionModel(model_function),
    )

    assert outcome.status is RunStatus.PUBLISHED
    event_types = [item["type"] for item in outcome.events]
    assert "convergence_episode_started" in event_types
    assert "convergence_episode_published" in event_types
    repository.close()


@pytest.mark.anyio
async def test_exploration_deadline_preserves_reserve_for_compact_convergence(tmp_path):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        scripted = {
            1: ("analyze_place_mentions", {}),
            2: ("search_candidate_sets", {"hypothesis_ids": ["hypothesis-wuhou"]}),
            3: (
                "resolve_place",
                {
                    "hypothesis_id": "hypothesis-wuhou",
                    "candidate_id": "candidate-wuhou",
                    "rationale": "exact identity",
                },
            ),
            4: (
                "apply_draft_change",
                json.dumps(
                    {
                        "days": [
                            {
                                "day_index": 1,
                                "date": "2026-08-03",
                                "visits": [
                                    {
                                        "place_candidate_id": "candidate-wuhou",
                                        "duration_min": 90,
                                    }
                                ],
                            }
                        ]
                    }
                ),
            ),
            6: ("simulate_candidate", {}),
            7: ("submit_candidate", {"completion_reason": "reserve convergence"}),
        }
        if step == 5:
            raise EpisodeDeadlineExceeded("exploration reserve boundary")
        if step in scripted:
            name, args = scripted[step]
            return _tool(name, args, step)
        return ModelResponse(parts=[TextPart("published")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "deadline-convergence.sqlite3")
    service = TripAgentService(repository, FixturePlaceKnowledge())
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        start_date=date(2026, 8, 3),
        days=1,
        model=FunctionModel(model_function),
    )

    assert outcome.status is RunStatus.PUBLISHED
    event_types = [item["type"] for item in outcome.events]
    assert "convergence_episode_started" in event_types
    assert "convergence_episode_published" in event_types
    repository.close()


@pytest.mark.anyio
async def test_parent_entity_mention_cannot_be_resolved_to_child_poi(tmp_path):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:
            return _tool("analyze_place_mentions", {}, step)
        if step == 2:
            return _tool(
                "search_candidate_sets",
                {"hypothesis_ids": ["hypothesis-wuhou"]},
                step,
            )
        if step == 3:
            return _tool(
                "resolve_place",
                {
                    "hypothesis_id": "hypothesis-wuhou",
                    "candidate_id": "candidate-child",
                    "rationale": "incorrect category-biased choice",
                    "action": "resolve",
                },
                step,
            )
        return ModelResponse(parts=[TextPart("unable to resolve")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "parent-child.sqlite3")
    service = TripAgentService(repository, ParentChildPlaceKnowledge())
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        days=1,
        model=FunctionModel(model_function),
    )

    assert outcome.status is RunStatus.INCOMPLETE
    workspace = repository.get_workspace(outcome.workspace_id)
    assert workspace is not None
    assert workspace.place_resolutions == ()
    assert workspace.place_hypotheses[0].status.value == "open"
    repository.close()


@pytest.mark.anyio
async def test_rejected_submission_freezes_candidate_and_counterexample(tmp_path):
    step = 0

    async def model_function(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal step
        step += 1
        if step == 1:
            return _tool("analyze_place_mentions", {}, step)
        if step == 2:
            return _tool(
                "search_candidate_sets",
                {"hypothesis_ids": ["hypothesis-wuhou"]},
                step,
            )
        if step == 3:
            return _tool(
                "apply_draft_change",
                json.dumps({
                    "days": [
                        {
                            "day_index": 1,
                            "date": "2026-08-03",
                            "visits": [
                                {
                                    "place_candidate_id": "candidate-wuhou",
                                    "duration_min": 60,
                                }
                            ],
                        }
                    ],
                }),
                step,
            )
        if step == 4:
            return _tool(
                "submit_candidate",
                {"completion_reason": "invalid unresolved draft"},
                step,
            )
        return ModelResponse(parts=[TextPart("unable to publish")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "rejected.sqlite3")
    service = TripAgentService(repository, FixturePlaceKnowledge())
    outcome = await service.start(
        raw_request="成都一日游，武侯祠必去。",
        destination="成都",
        start_date=None,
        days=1,
        model=FunctionModel(model_function),
    )

    assert outcome.status is RunStatus.INCOMPLETE
    rejections = repository.list_candidate_rejections(outcome.run_id)
    assert len(rejections) == 1, outcome.events
    assert "unresolved_candidate" in rejections[0].issue_codes
    assert repository.get_candidate(rejections[0].candidate_id) is not None
    assert "candidate_rejected" in [event["type"] for event in outcome.events]
    repository.close()


@pytest.mark.anyio
async def test_clarification_pauses_and_resumes_the_same_run_from_persisted_messages(tmp_path):
    async def ask_model(messages: list[Any], info: AgentInfo) -> ModelResponse:
        return _tool(
            "request_clarification",
            json.dumps(
                {
                    "questions": [
                        {
                            "question_id": "anchor-time",
                            "prompt": "预约是在上午还是下午？",
                            "reason": "这会改变当日路线顺序。",
                            "options": ["上午", "下午"],
                            "allow_other": True,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            1,
        )

    async def finish_model(messages: list[Any], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("clarification received")], finish_reason="stop")

    repository = SqliteTripAgentRepository(tmp_path / "clarification.sqlite3")
    persistence = HarnessPersistenceAdapter(
        SqliteStepStore(database=tmp_path / "provider-steps.sqlite3"),
        deferred_database=tmp_path / "deferred-transcripts.sqlite3",
    )
    service = TripAgentService(repository, FixturePlaceKnowledge(), persistence)
    paused = await service.start(
        raw_request="成都一日游，武侯祠必去，另有一个预约。",
        destination="成都",
        days=1,
        model=FunctionModel(ask_model),
    )

    assert paused.status is RunStatus.WAITING_USER
    paused_run = repository.get_run(paused.run_id)
    assert paused_run is not None
    assert paused_run.active_interruption_id is not None
    interruption = repository.get_interruption(paused_run.active_interruption_id)
    assert interruption is not None
    assert interruption.tool_call_id == "call-1-request_clarification"

    resumed = await service.resume(
        run_id=paused.run_id,
        answers={"anchor-time": "下午 14:00"},
        model=FunctionModel(finish_model),
    )

    assert resumed.run_id == paused.run_id
    assert resumed.workspace_id == paused.workspace_id
    assert resumed.status is RunStatus.INCOMPLETE
    assert resumed.events[0]["type"] == "run_resumed"
    repository.close()


@pytest.mark.anyio
async def test_resume_uses_same_transient_provider_failure_policy(tmp_path):
    async def ask_model(messages: list[Any], info: AgentInfo) -> ModelResponse:
        return _tool(
            "request_clarification",
            json.dumps(
                {
                    "questions": [
                        {
                            "question_id": "branch",
                            "prompt": "选择哪家分店？",
                            "reason": "地点身份会改变路线。",
                            "options": ["总店", "分店"],
                            "allow_other": True,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            1,
        )

    attempts = 0

    async def unavailable(messages: list[Any], info: AgentInfo) -> ModelResponse:
        nonlocal attempts
        attempts += 1
        raise ModelHTTPError(503, "fixture-provider", {"error": "temporary"})

    repository = SqliteTripAgentRepository(tmp_path / "resume-provider.sqlite3")
    persistence = HarnessPersistenceAdapter(
        SqliteStepStore(database=tmp_path / "resume-provider-steps.sqlite3"),
        deferred_database=tmp_path / "resume-deferred.sqlite3",
    )
    service = TripAgentService(repository, FixturePlaceKnowledge(), persistence)
    paused = await service.start(
        raw_request="成都一日游，需要确认分店。",
        destination="成都",
        days=1,
        model=FunctionModel(ask_model),
    )
    assert paused.status is RunStatus.WAITING_USER
    resumed = await service.resume(
        run_id=paused.run_id,
        answers={"branch": "总店"},
        model=FunctionModel(unavailable),
    )

    run = repository.get_run(paused.run_id)
    assert resumed.status is RunStatus.INCOMPLETE
    assert run is not None
    assert run.failure_class.value == "transient_external"
    assert run.provider_attempt_count == 3  # initial, resume, and one runtime retry
    assert attempts == 2
    repository.close()
