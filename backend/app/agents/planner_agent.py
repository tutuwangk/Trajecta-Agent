from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import Field
from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits
from pydantic_ai.models import Model

from app.adapters.deepseek import build_deepseek_chat_model, planning_model_settings
from app.agents.planner_prompt import PLANNER_INSTRUCTIONS, build_planner_prompt
from app.core import AppError
from app.domain.common import DomainModel
from app.domain.facts import GroundedPOI, RouteEdge
from app.domain.planning import PlanBlueprint, PlannerTurn, PlanningContext


class RoutePair(DomainModel):
    origin_poi_id: str = Field(min_length=1)
    destination_poi_id: str = Field(min_length=1)


@dataclass(frozen=True)
class PlannerDependencies:
    context: PlanningContext
    poi_index: dict[str, GroundedPOI]
    route_index: dict[tuple[str, str], RouteEdge]

    @classmethod
    def from_context(cls, context: PlanningContext) -> "PlannerDependencies":
        return cls(
            context=context,
            poi_index={poi.poi_id: poi for poi in context.fact_snapshot.pois},
            route_index={
                (edge.origin_poi_id, edge.destination_poi_id): edge
                for edge in context.fact_snapshot.route_edges
            },
        )


class PlannerAgent:
    def __init__(
        self,
        model: Model | str | None = None,
        *,
        model_settings: dict[str, Any] | None = None,
        enable_fact_tools: bool = False,
    ):
        self.model = model or build_deepseek_chat_model("planning")
        self.model_settings = model_settings if model_settings is not None else (
            None if model is not None else planning_model_settings("planning")
        )
        self.agent = Agent(
            self.model,
            deps_type=PlannerDependencies,
            # DeepSeek's OpenAI-compatible endpoint is not consistently
            # compatible with PydanticAI structured-output modes.  Keep the
            # Agent run provider-neutral, then validate its JSON through the
            # strict PlanBlueprint model at the adapter boundary.
            output_type=str,
            instructions=PLANNER_INSTRUCTIONS,
            retries=1,
            name="planner_agent",
        )
        self.call_metrics: list[dict[str, Any]] = []
        self._register_tools_and_validator(enable_fact_tools=enable_fact_tools)

    def _register_tools_and_validator(self, *, enable_fact_tools: bool) -> None:
        if enable_fact_tools:
            @self.agent.tool
            def get_place_details(ctx: RunContext[PlannerDependencies], poi_ids: list[str]) -> list[dict[str, Any]]:
                """读取候选地点的已核验详情；poi_ids 必须全部属于当前候选集合。"""

                unknown = set(poi_ids) - ctx.deps.context.allowed_poi_ids
                if unknown:
                    raise ModelRetry(f"地点工具只能查询候选 poi_id，未知值：{sorted(unknown)}")
                return [ctx.deps.poi_index[poi_id].model_dump(mode="json") for poi_id in poi_ids if poi_id in ctx.deps.poi_index]

            @self.agent.tool
            def get_route_facts(ctx: RunContext[PlannerDependencies], pairs: list[RoutePair]) -> list[dict[str, Any]]:
                """读取候选地点之间已有的交通事实，不发明或补写缺失路线。"""

                result = []
                for pair in pairs:
                    if pair.origin_poi_id not in ctx.deps.context.allowed_poi_ids or pair.destination_poi_id not in ctx.deps.context.allowed_poi_ids:
                        raise ModelRetry("路线工具只能查询当前候选地点之间的路线。")
                    edge = ctx.deps.route_index.get((pair.origin_poi_id, pair.destination_poi_id))
                    if edge:
                        result.append(edge.model_dump(mode="json"))
                    else:
                        result.append(
                            {
                                "origin_poi_id": pair.origin_poi_id,
                                "destination_poi_id": pair.destination_poi_id,
                                "confidence": "unavailable",
                                "degradation_reason": "当前事实快照没有该路线边。",
                            }
                        )
                return result

    def run(self, context: PlanningContext) -> PlanBlueprint:
        turn = self.run_turn(context, allow_fact_requests=False)
        if turn.blueprint is None:
            raise AppError(
                "PlannerAgent 在事实请求额度耗尽后仍未提交路线蓝图。",
                code="planner_agent_fact_loop_exhausted",
                step="plan_itinerary_blueprint",
            )
        return turn.blueprint

    def run_turn(self, context: PlanningContext, *, allow_fact_requests: bool = True) -> PlannerTurn:
        deps = PlannerDependencies.from_context(context)
        prompt = build_planner_prompt(context, allow_fact_requests=allow_fact_requests)
        try:
            result = self.agent.run_sync(
                prompt,
                deps=deps,
                model_settings=self.model_settings,
                # Keep the Agent turn bounded.  DeepSeek can otherwise keep
                # requesting facts without producing the structured output;
                # deterministic compilation takes over after this limit.
                usage_limits=UsageLimits(request_limit=2, tool_calls_limit=2),
            )
        except AppError as exc:
            self._record_failure(exc)
            raise
        except Exception as exc:
            self._record_failure(exc)
            raise AppError(
                "PlannerAgent 未能生成有效路线蓝图，系统将尝试确定性回退。",
                code="planner_agent_failed",
                step="plan_itinerary_blueprint",
                details={"error_type": type(exc).__name__},
            ) from exc
        try:
            turn = _validated_planner_turn_output(result.output, context, allow_fact_requests=allow_fact_requests)
        except AppError as exc:
            self._record_failure(exc)
            raise
        usage = result.usage
        self.call_metrics.append(
            {
                "step": (
                    "request_planning_facts"
                    if turn.action == "need_facts"
                    else "replan_itinerary_blueprint" if context.previous_blueprint else "plan_itinerary_blueprint"
                ),
                "role": "planning",
                "model": getattr(self.model, "model_name", type(self.model).__name__),
                "framework": "pydantic_ai",
                "status": "success",
                "prompt_characters": len(prompt),
                "request_attempts": int(usage.requests),
                "prompt_tokens": int(usage.input_tokens or 0),
                "completion_tokens": int(usage.output_tokens or 0),
                "total_tokens": int(usage.total_tokens or 0),
                "tool_calls": int(usage.tool_calls or 0),
            }
        )
        return turn

    def _record_failure(self, exc: Exception) -> None:
        self.call_metrics.append(
            {
                "step": "plan_itinerary_blueprint",
                "role": "planning",
                "model": getattr(self.model, "model_name", type(self.model).__name__),
                "framework": "pydantic_ai",
                "status": "error",
                "request_attempts": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "tool_calls": 0,
                "error_type": type(exc).__name__,
            }
        )


def default_planner_agent() -> PlannerAgent:
    """Return the production Agent without the legacy workflow facade."""

    return PlannerAgent()


def _planner_prompt(context: PlanningContext) -> str:
    return build_planner_prompt(context, allow_fact_requests=False)


def _validated_planner_turn_output(
    raw_output: str,
    context: PlanningContext,
    *,
    allow_fact_requests: bool,
) -> PlannerTurn:
    text = str(raw_output or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise AppError(
            "PlannerAgent 未返回有效 JSON 行动，系统将尝试确定性回退。",
            code="planner_agent_invalid_output",
            step="plan_itinerary_blueprint",
        )
    try:
        payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("planner output must be a JSON object")
        if "action" not in payload:
            payload = {"action": "revise_blueprint" if context.previous_blueprint else "propose_blueprint", "blueprint": payload}
        turn = PlannerTurn.model_validate(payload)
        if turn.action == "need_facts":
            if not allow_fact_requests:
                raise ValueError("fact request budget is exhausted")
            unknown = {request.poi_id for request in turn.fact_requests} - context.allowed_poi_ids
            if unknown:
                raise ValueError(f"fact requests reference unknown poi_ids: {sorted(unknown)}")
            allowed_dates = set(context.trip_dates)
            if allowed_dates and any(request.visit_date not in allowed_dates for request in turn.fact_requests):
                raise ValueError("fact requests must use dates from the current trip")
            return turn
        assert turn.blueprint is not None
        turn.blueprint = _normalize_and_validate_blueprint(turn.blueprint.model_dump(mode="json"), context)
        return turn
    except (json.JSONDecodeError, ValueError) as exc:
        raise AppError(
            "PlannerAgent 返回的行动未通过结构或业务校验，系统将尝试确定性回退。",
            code="planner_agent_invalid_output",
            step="plan_itinerary_blueprint",
            details={"error_type": type(exc).__name__},
        ) from exc


def _validated_blueprint_output(raw_output: str, context: PlanningContext) -> PlanBlueprint:
    turn = _validated_planner_turn_output(raw_output, context, allow_fact_requests=False)
    if turn.blueprint is None:
        raise AppError("PlannerAgent 未提交路线蓝图。", code="planner_agent_invalid_output", step="plan_itinerary_blueprint")
    return turn.blueprint


def _normalize_and_validate_blueprint(payload: dict, context: PlanningContext) -> PlanBlueprint:
    try:
        scheduled_ids = {
            str(poi_id)
            for day in payload.get("days") or []
            if isinstance(day, dict)
            for poi_id in day.get("poi_ids") or []
        }
        declared_unscheduled = {
            str(item.get("poi_id") or "")
            for item in payload.get("unscheduled") or []
            if isinstance(item, dict)
        }
        missing_ids = context.allowed_poi_ids - scheduled_ids - declared_unscheduled
        must_ids = {candidate.poi_id for candidate in context.candidates if candidate.priority == "must"}
        safely_omitted = sorted(missing_ids - must_ids)
        if safely_omitted:
            payload.setdefault("unscheduled", [])
            payload["unscheduled"].extend(
                {"poi_id": poi_id, "reason_codes": ["agent_omitted"]}
                for poi_id in safely_omitted
            )
        blueprint = PlanBlueprint.model_validate(payload)
        blueprint.validate_against(context)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AppError(
            "PlannerAgent 返回的蓝图未通过结构或业务校验，系统将尝试确定性回退。",
            code="planner_agent_invalid_output",
            step="plan_itinerary_blueprint",
            details={"error_type": type(exc).__name__},
        ) from exc
    return blueprint
