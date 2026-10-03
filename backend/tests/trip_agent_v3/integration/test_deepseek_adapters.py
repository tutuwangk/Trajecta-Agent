from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    UserPromptPart,
)
from pydantic_ai import ModelRetry, UnexpectedModelBehavior

from app.trip_agent_v3.adapters.deepseek import (
    DeepSeekRequirementInterpreter,
    PlaceMentionDTO,
    PlanningSubmissionDTO,
    PydanticAIRootTripPlannerAgent,
    _finalize_candidate_or_retry,
    _should_request_grounding_clarification,
    _should_request_fact_clarification,
    _submit_plan_or_retry,
    _submit_grounding_or_retry,
    _submit_grounding_batch_or_retry,
    GroundingDecisionDTO,
    _to_domain_planning_submission,
)
from app.trip_agent_v3.commitments import PlanCommitmentError
from app.trip_agent_v3.grounding import GroundingCompilationError
from app.trip_agent_v3.domain.execution import JourneyGoal
from app.trip_agent_v3.requirements import (
    compile_requirement_ledger,
    source_document,
)


@pytest.mark.anyio
async def test_interpreter_accepts_walking_limit_without_mode_preference():
    raw = "步行单段不要超过20分钟"
    source = source_document(source_id="source-1", kind="user_request", content=raw)
    interpreter = DeepSeekRequirementInterpreter(TestModel(custom_output_args={
        "mentions": [], "constraints": [{
            "kind": "transport_preference", "proposal_key": "walk-cap", "strength": "required",
            "evidence": [{"source_id": "source-1", "start": 0, "end": len(raw)}],
            "preferred_modes": [], "max_walk_minutes": 20,
        }],
    }))
    goal = JourneyGoal(goal_revision_id="goal-1", destination="成都", start_date=date(2026, 8, 1), days=1)
    proposal = await interpreter.interpret(goal=goal, sources=(source,))
    assert proposal.constraints[0].preferred_modes == ()
    assert proposal.constraints[0].max_walk_minutes == 20


@pytest.mark.anyio
async def test_structured_interpreter_keeps_time_words_out_of_place_mentions() -> None:
    raw = "上午去武侯祠，中午吃陈麻婆豆腐。"
    interpreter = DeepSeekRequirementInterpreter(
        TestModel(
            custom_output_args={
                "mentions": [
                    {
                        "mention_key": "wuhou",
                        "mention": "武侯祠",
                        "role": "visit",
                        "priority": "required",
                        "evidence": [
                            {
                                "source_id": "source-1",
                                "start": 3,
                                "end": 6,
                            }
                        ],
                        "query_decision": "query",
                        "query_text": "武侯祠",
                    },
                    {
                        "mention_key": "meal",
                        "mention": "陈麻婆豆腐",
                        "role": "meal",
                        "priority": "required",
                        "evidence": [
                            {
                                "source_id": "source-1",
                                "start": 10,
                                "end": 15,
                            }
                        ],
                        "query_decision": "query",
                        "query_text": "陈麻婆豆腐",
                    },
                ],
                "constraints": [
                    {
                        "kind": "time_window",
                        "proposal_key": "morning",
                        "subject_mention_keys": ["wuhou"],
                        "evidence": [
                            {
                                "source_id": "source-1",
                                "start": 0,
                                "end": 2,
                            }
                        ],
                        "strength": "required",
                        "day_number": 1,
                        "earliest": "08:00:00",
                        "latest": "12:00:00",
                    }
                ],
            }
        )
    )
    goal = JourneyGoal(
        goal_revision_id="goal-1",
        destination="成都",
        start_date=date(2026, 8, 1),
        days=1,
    )
    source = source_document(
        source_id="source-1",
        kind="user_request",
        content=raw,
    )

    proposal = await interpreter.interpret(
        goal=goal, sources=(source,)
    )
    ledger = compile_requirement_ledger(
        ledger_id="ledger-1",
        revision=1,
        sources=(source,),
        proposal=proposal,
    )

    assert [item.mention for item in ledger.obligations] == [
        "武侯祠",
        "陈麻婆豆腐",
    ]
    assert [item.evidence[0].text for item in ledger.constraints] == [
        "上午",
        "中午",
    ]


def test_plan_tool_uses_local_keys_and_runtime_owned_metadata() -> None:
    goal = JourneyGoal(
        goal_revision_id="goal-1",
        destination="成都",
        start_date=date(2026, 8, 1),
        days=1,
    )
    proposal = PlanningSubmissionDTO.model_validate(
        {
            "draft": {
                "days": [
                    {
                        "day_number": 1,
                        "title": "酒店休整",
                        "start_time": "09:00:00",
                        "stops": [
                            {
                                "stop_key": "hotel-start",
                                "candidate_id": "cand-hotel",
                                "obligation_ids": ["obl-hotel"],
                                "name": "测试酒店",
                                "kind": "lodging",
                                "stay_duration_min": 0,
                                "rationale": "酒店出发。",
                            },
                            {
                                "stop_key": "hotel-end",
                                "candidate_id": "cand-hotel",
                                "obligation_ids": [],
                                "name": "测试酒店",
                                "kind": "lodging",
                                "stay_duration_min": 0,
                                "rationale": "酒店返回。",
                            },
                        ],
                    }
                ]
            },
            "dispositions": [
                {
                    "obligation_id": "obl-hotel",
                    "status": "scheduled",
                    "stop_key": "hotel-start",
                }
            ],
        }
    )

    submission = _to_domain_planning_submission(proposal, goal=goal)

    assert submission.draft.draft_id == "runtime-owned"
    assert submission.draft.days[0].calendar_date == date(2026, 8, 1)
    assert submission.draft.days[0].stops[0].stop_id == "hotel-start"
    assert submission.dispositions[0].stop_id == "hotel-start"


def test_explicit_schedulable_place_cannot_be_silently_referenced() -> None:
    with pytest.raises(
        ValueError, match="must be queried, merged, or clarified"
    ):
        PlaceMentionDTO.model_validate(
            {
                "mention_key": "meal",
                "mention": "陈麻婆豆腐（骡马市店）",
                "role": "meal",
                "priority": "required",
                "evidence": [
                    {"source_id": "source-1", "start": 0, "end": 10}
                ],
                "query_decision": "reference",
                "decision_reason": "错误地当作背景资料。",
            }
        )


def test_plan_commitment_error_becomes_model_retry() -> None:
    goal = JourneyGoal(
        goal_revision_id="goal-1",
        destination="成都",
        start_date=date(2026, 8, 1),
        days=1,
    )
    proposal = PlanningSubmissionDTO.model_validate(
        {
            "draft": {
                "days": [
                    {
                        "day_number": 1,
                        "title": "测试",
                        "start_time": "09:00:00",
                        "stops": [
                            {
                                "stop_key": "hotel-start",
                                "candidate_id": "hotel",
                                "obligation_ids": ["obl-hotel"],
                                "name": "测试酒店",
                                "kind": "lodging",
                                "stay_duration_min": 0,
                                "rationale": "出发锚点。",
                            },
                            {
                                "stop_key": "invalid",
                                "candidate_id": "not-selected",
                                "obligation_ids": ["obl-visit"],
                                "name": "错误候选",
                                "kind": "visit",
                                "stay_duration_min": 60,
                                "rationale": "测试软反馈。",
                            },
                            {
                                "stop_key": "hotel-end",
                                "candidate_id": "hotel",
                                "obligation_ids": [],
                                "name": "测试酒店",
                                "kind": "lodging",
                                "stay_duration_min": 0,
                                "rationale": "返回锚点。",
                            }
                        ],
                    }
                ]
            },
            "dispositions": [
                {
                    "obligation_id": "obl-hotel",
                    "status": "scheduled",
                    "stop_key": "hotel-start",
                },
                {
                    "obligation_id": "obl-visit",
                    "status": "scheduled",
                    "stop_key": "invalid",
                },
            ],
        }
    )
    runtime = SimpleNamespace(goal=goal)

    def reject(_submission) -> None:
        raise PlanCommitmentError(
            "stop invalid is not a selected grounding candidate"
        )

    runtime.submit_plan = reject

    with pytest.raises(ModelRetry, match="current Requirement Ledger"):
        _submit_plan_or_retry(runtime, proposal)


def test_invalid_domain_plan_shape_becomes_model_retry() -> None:
    goal = JourneyGoal(
        goal_revision_id="goal-domain-shape",
        destination="上海",
        start_date=date(2026, 9, 1),
        days=1,
    )
    proposal = PlanningSubmissionDTO.model_validate(
        {
            "draft": {
                "days": [
                    {
                        "day_number": 1,
                        "title": "上海一日",
                        "start_time": "09:00:00",
                        "stops": [
                            {
                                "stop_key": "hotel-start",
                                "candidate_id": "hotel",
                                "obligation_ids": ["obl-hotel"],
                                "name": "测试酒店",
                                "kind": "lodging",
                                "stay_duration_min": 0,
                                "rationale": "酒店出发。",
                            },
                            {
                                "stop_key": "museum",
                                "candidate_id": "museum",
                                "obligation_ids": [],
                                "name": "测试博物馆",
                                "kind": "visit",
                                "stay_duration_min": 120,
                                "rationale": "参观。",
                            },
                            {
                                "stop_key": "hotel-end",
                                "candidate_id": "hotel",
                                "obligation_ids": [],
                                "name": "测试酒店",
                                "kind": "lodging",
                                "stay_duration_min": 0,
                                "rationale": "返回酒店。",
                            },
                        ],
                    }
                ]
            },
            "dispositions": [
                {
                    "obligation_id": "obl-hotel",
                    "status": "scheduled",
                    "stop_key": "hotel-start",
                }
            ],
        }
    )
    runtime = SimpleNamespace(goal=goal)
    runtime.submit_plan = lambda _submission: None

    with pytest.raises(ModelRetry, match="current Requirement Ledger"):
        _submit_plan_or_retry(runtime, proposal)


def test_fact_only_blockers_close_as_clarification() -> None:
    runtime = SimpleNamespace(
        assessment=SimpleNamespace(
            may_publish=False,
            issues=(
                SimpleNamespace(
                    code="fact_gap_operational_evidence_insufficient",
                    severity=SimpleNamespace(value="blocking"),
                ),
                SimpleNamespace(
                    code="operational_fact_missing",
                    severity=SimpleNamespace(value="blocking"),
                ),
            ),
        ),
        fact_gap_report=SimpleNamespace(gaps=(object(),)),
    )

    assert _should_request_fact_clarification(runtime) is True

    runtime.assessment.issues += (
        SimpleNamespace(
            code="required_time_window_violated",
            severity=SimpleNamespace(value="blocking"),
        ),
    )

    assert _should_request_fact_clarification(runtime) is False


def test_known_operational_incompatibility_is_repaired_by_agent() -> None:
    runtime = SimpleNamespace(
        assessment=SimpleNamespace(
            may_publish=False,
            issues=(
                SimpleNamespace(
                    code="fact_gap_planned_visit_operationally_incompatible",
                    severity=SimpleNamespace(value="blocking"),
                ),
                SimpleNamespace(
                    code="operational_fact_missing",
                    severity=SimpleNamespace(value="blocking"),
                ),
            ),
        ),
        fact_gap_report=SimpleNamespace(
            gaps=(
                SimpleNamespace(
                    failure_code=(
                        "planned_visit_operationally_incompatible"
                    )
                ),
            )
        ),
    )

    assert _should_request_fact_clarification(runtime) is False


def test_unresolved_grounding_requests_clarification_before_planning() -> None:
    runtime = SimpleNamespace(
        grounding=SimpleNamespace(
            resolutions=(
                SimpleNamespace(
                    status=SimpleNamespace(value="selected")
                ),
                SimpleNamespace(
                    status=SimpleNamespace(value="needs_confirmation")
                ),
            )
        )
    )

    assert _should_request_grounding_clarification(runtime) is True


@pytest.mark.anyio
async def test_finalize_without_committed_draft_becomes_model_retry() -> None:
    async def should_not_finalize():
        raise AssertionError("Runtime finalization must not start without a draft")

    runtime = SimpleNamespace(
        grounding=object(),
        draft=None,
        finalize_candidate=should_not_finalize,
    )

    with pytest.raises(ModelRetry, match="No WorkingDraft"):
        await _finalize_candidate_or_retry(runtime)


def test_unread_grounding_decision_returns_repair_feedback():
    decision = GroundingDecisionDTO(
        target_id="target-a", status="selected", selected_provider_place_id="place-a",
        rationale="same place", confidence=0.9, selection_factors=["name"],
    )
    runtime = SimpleNamespace(candidate_sets={})
    with pytest.raises(ModelRetry, match="Read this grounding target"):
        _submit_grounding_or_retry(runtime, decision)


def test_invalid_grounding_candidate_returns_repair_feedback():
    decision = GroundingDecisionDTO(
        target_id="target-a", status="selected", selected_provider_place_id="unknown-place",
        rationale="same place", confidence=0.9, selection_factors=["name"],
    )

    def reject_unknown_candidate(proposal):
        raise GroundingCompilationError("selected provider place id not retained")

    runtime = SimpleNamespace(candidate_sets={"target-a": object()}, submit_grounding_decision=reject_unknown_candidate)
    with pytest.raises(ModelRetry, match="retained candidate group"):
        _submit_grounding_or_retry(runtime, decision)


class _FakeAgentRun:
    def __init__(self, transcript: bytes, callback=None) -> None:
        self.transcript = transcript
        self.callback = callback
        self.called = False
        self.usage = SimpleNamespace(requests=0, tool_calls=0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.called and self.callback is not None:
            self.called = True
            self.callback()
        raise StopAsyncIteration

    def all_messages_json(self) -> bytes:
        return self.transcript


class _FakeIterContext:
    def __init__(self, run: _FakeAgentRun) -> None:
        self.run = run

    async def __aenter__(self) -> _FakeAgentRun:
        return self.run

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeAgent:
    def __init__(self, transcript: bytes) -> None:
        self.transcript = transcript
        self.calls: list[dict[str, object]] = []

    def iter(self, user_prompt, **kwargs):
        self.calls.append(
            {
                "user_prompt": user_prompt,
                "message_history": kwargs.get("message_history"),
            }
        )
        return _FakeIterContext(_FakeAgentRun(self.transcript))


class _CompactingFakeAgent(_FakeAgent):
    def iter(self, user_prompt, **kwargs):
        runtime = kwargs["deps"].runtime
        call_index = len(self.calls)
        self.calls.append(
            {
                "user_prompt": user_prompt,
                "message_history": kwargs.get("message_history"),
            }
        )

        def callback() -> None:
            if call_index == 0:
                runtime.grounding = object()
                runtime.draft = None
            else:
                runtime.run_record = SimpleNamespace(
                    status=SimpleNamespace(value="waiting_user")
                )

        return _FakeIterContext(
            _FakeAgentRun(self.transcript, callback=callback)
        )


class _Runtime:
    def __init__(self, transcript: bytes | None = None) -> None:
        self.goal = SimpleNamespace(
            destination="成都",
            start_date=date(2026, 8, 1),
            days=1,
        )
        self.provider_transcript_json = transcript
        self.persisted: bytes | None = None
        self.grounding = None
        self.draft = None
        self.assessment = None
        self.fact_gap_report = None
        self.release = None
        self.events = []
        self.run_record = SimpleNamespace(
            status=SimpleNamespace(value="active")
        )

    def persist_provider_transcript(self, payload: bytes) -> None:
        self.provider_transcript_json = payload
        self.persisted = payload

    def _record_event(self, event):
        self.events.append(event)

    def persist_tool_repair_feedback(self, feedback):
        self.last_tool_repair_feedback = feedback


@pytest.mark.anyio
async def test_root_stops_at_published_release_before_another_model_request():
    transcript = ModelMessagesTypeAdapter.dump_json([])
    runtime = _Runtime()

    class PublishingRun(_FakeAgentRun):
        async def __anext__(self):
            if self.called:
                raise AssertionError("another model request after publication")
            self.called = True
            runtime.release = object()
            return object()

    run = PublishingRun(transcript)

    class PublishingAgent:
        def iter(self, *_args, **_kwargs):
            return _FakeIterContext(run)

    adapter = object.__new__(PydanticAIRootTripPlannerAgent)
    adapter.agent = PublishingAgent()
    await adapter.run(runtime)
    assert run.called
    assert runtime.persisted == transcript


@pytest.mark.anyio
async def test_sdk_batch_closes_all_tools_after_publication():
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    runtime = _Runtime()
    runtime.grounding = object()
    runtime.draft = object()
    runtime.assessment = SimpleNamespace(may_publish=True, issues=())
    calls = []

    async def publish():
        calls.append("publish")
        runtime.release = SimpleNamespace(release_id="release-1")
        runtime.run_record.status = SimpleNamespace(value="succeeded")
        return SimpleNamespace(may_publish=True, state=SimpleNamespace(value="publishable"), issues=())

    def clarify():
        raise AssertionError("queued write after publication")

    runtime.finalize_candidate = publish
    runtime.request_clarification = clarify

    def model_request(_messages, _info):
        calls.append("model")
        assert calls == ["model"]
        return ModelResponse(parts=[
            ToolCallPart("finalize_candidate", {}),
            ToolCallPart("request_clarification", {}),
            ToolCallPart("finalize_candidate", {}),
        ])

    await PydanticAIRootTripPlannerAgent(FunctionModel(model_request)).run(runtime)
    assert calls == ["model", "publish"]
    messages = ModelMessagesTypeAdapter.validate_json(runtime.persisted)
    assert len(messages) == 3
    assert {part.tool_call_id for part in messages[1].parts} == {
        part.tool_call_id for part in messages[2].parts
    }


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "waiting_user"])
async def test_lifecycle_closes_every_tool_for_terminal_state(status):
    from app.trip_agent_v3.adapters.deepseek import RunLifecycleToolset
    runtime = _Runtime()
    runtime.run_record.status = SimpleNamespace(value=status)
    ctx = SimpleNamespace(deps=SimpleNamespace(runtime=runtime))

    async def unexpected(*_args):
        raise AssertionError("tool executed in closed run")

    toolset = RunLifecycleToolset(SimpleNamespace(call_tool=unexpected, get_tools=unexpected))
    assert await toolset.get_tools(ctx) == {}
    for name in ("read_grounding_target", "submit_plan", "assess_candidate", "finalize_candidate", "request_clarification"):
        assert (await toolset.call_tool(name, {}, ctx, None))["state"] == status


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["succeeded", "retry", "interrupted", "checkpoint"])
async def test_tool_progress_pairs_calls_without_changing_execution_or_exposing_plan(outcome):
    from app.trip_agent_v3.adapters.deepseek import RunLifecycleToolset, _ProviderContextCheckpoint
    runtime = _Runtime()
    ctx = SimpleNamespace(deps=SimpleNamespace(runtime=runtime))
    args = {"day_number": 2, "submission": {"private_notes": "user material"}}

    async def execute(name, tool_args, *_args):
        assert name == "submit_plan"
        assert tool_args is args
        if outcome == "retry":
            raise ModelRetry("repair needed")
        if outcome == "interrupted":
            raise RuntimeError("provider failed")
        if outcome == "checkpoint":
            raise _ProviderContextCheckpoint()
        return {"saved": True}

    toolset = RunLifecycleToolset(SimpleNamespace(call_tool=execute))
    if outcome == "succeeded":
        assert await toolset.call_tool("submit_plan", args, ctx, None) == {"saved": True}
    else:
        expected = {"retry": ModelRetry, "interrupted": RuntimeError, "checkpoint": _ProviderContextCheckpoint}[outcome]
        with pytest.raises(expected):
            await toolset.call_tool("submit_plan", args, ctx, None)
    start, finish = runtime.events
    assert start["type"] == "tool_started"
    assert start["context"] == {"day_number": 2}
    assert finish["type"] == "tool_finished"
    assert finish["call_id"] == start["call_id"]
    assert finish["duration_ms"] >= 0
    assert finish["outcome"] == ("succeeded" if outcome == "checkpoint" else outcome)


@pytest.mark.anyio
async def test_root_agent_persists_and_resumes_provider_valid_transcript() -> None:
    transcript = ModelMessagesTypeAdapter.dump_json(
        [ModelRequest(parts=[UserPromptPart(content="继续规划")])]
    )
    fake_agent = _FakeAgent(transcript)
    adapter = object.__new__(PydanticAIRootTripPlannerAgent)
    adapter.agent = fake_agent

    first_runtime = _Runtime()
    await adapter.run(first_runtime)
    second_runtime = _Runtime(first_runtime.persisted)
    await adapter.run(second_runtime)

    assert first_runtime.persisted == transcript
    assert fake_agent.calls[0]["user_prompt"] is not None
    assert fake_agent.calls[0]["message_history"] is None
    assert fake_agent.calls[1]["user_prompt"] is None
    assert len(fake_agent.calls[1]["message_history"]) == 1


@pytest.mark.anyio
async def test_root_agent_compacts_provider_history_at_business_checkpoint() -> None:
    transcript = ModelMessagesTypeAdapter.dump_json(
        [ModelRequest(parts=[UserPromptPart(content="episode")])]
    )
    fake_agent = _CompactingFakeAgent(transcript)
    adapter = object.__new__(PydanticAIRootTripPlannerAgent)
    adapter.agent = fake_agent
    runtime = _Runtime()

    await adapter.run(runtime)

    assert len(fake_agent.calls) == 2
    assert fake_agent.calls[0]["message_history"] is None
    assert fake_agent.calls[1]["message_history"] is None
    assert "Grounding is complete" in fake_agent.calls[1]["user_prompt"]


@pytest.mark.anyio
async def test_compact_episodes_share_budget_and_count_usage_once() -> None:
    from app.trip_agent_v3.telemetry import RunTelemetry

    transcript = ModelMessagesTypeAdapter.dump_json([])
    limits = []

    class BudgetAgent:
        def iter(self, user_prompt, **kwargs):
            limits.append(kwargs["usage_limits"].request_limit)
            runtime = kwargs["deps"].runtime
            runtime.grounding = object()
            run = _FakeAgentRun(transcript)
            run.usage = SimpleNamespace(requests=8, tool_calls=12)
            return _FakeIterContext(run)

    adapter = object.__new__(PydanticAIRootTripPlannerAgent)
    adapter.agent = BudgetAgent()
    adapter.request_limit = 16
    runtime = _Runtime()
    runtime.telemetry = RunTelemetry()

    await adapter.run(runtime)

    assert limits == [16, 8]
    assert runtime.telemetry.model_request_count == 16
    assert runtime.telemetry.tool_call_count == 24
    assert runtime.events[-1]["type"] == "planning_budget_paused"
    assert runtime.events[-1]["model_requests"] == 16
    assert runtime.provider_transcript_json == transcript


def test_grounding_batch_validation_failure_becomes_model_retry():
    def reject_batch(decisions):
        raise GroundingCompilationError("selected provider place id not retained")
    runtime = SimpleNamespace(submit_grounding_decisions=reject_batch)
    decision = GroundingDecisionDTO(
        target_id="target-a", status="selected", selected_provider_place_id="bad-id",
        rationale="same place", confidence=0.9, selection_factors=["name"],
    )
    with pytest.raises(ModelRetry, match="Grounding batch was rejected"):
        _submit_grounding_batch_or_retry(runtime, [decision])


@pytest.mark.anyio
@pytest.mark.parametrize("correctable", [True, False])
async def test_tool_retry_exhaustion_pauses_but_protocol_error_propagates(correctable):
    transcript = ModelMessagesTypeAdapter.dump_json([])

    class FailingAgent:
        def iter(self, user_prompt, **kwargs):
            def fail():
                if correctable:
                    raise UnexpectedModelBehavior("Tool 'submit_plan' exceeded max retries count of 2") from ModelRetry("Unknown stop key")
                raise UnexpectedModelBehavior("Provider returned invalid tool protocol")
            return _FakeIterContext(_FakeAgentRun(transcript, fail))

    adapter = object.__new__(PydanticAIRootTripPlannerAgent)
    adapter.agent = FailingAgent()
    runtime = _Runtime()
    checkpoint = object()
    runtime.grounding = checkpoint

    if correctable:
        await adapter.run(runtime)
        assert runtime.events[-1]["reason_code"] == "tool_input_retries_exhausted"
        assert runtime.grounding is checkpoint
        assert runtime.provider_transcript_json == transcript
        assert runtime.last_tool_repair_feedback == "Unknown stop key"
        from app.trip_agent_v3.adapters.deepseek import _root_episode_prompt
        assert "Unknown stop key" in _root_episode_prompt(runtime)
    else:
        with pytest.raises(UnexpectedModelBehavior, match="invalid tool protocol"):
            await adapter.run(runtime)
        assert runtime.events == []
