from __future__ import annotations

from pydantic_ai import Agent, DeferredToolRequests, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model
from pydantic_ai.tools import ToolDefinition

from app.trip_agent.adapters.provider import DeepSeekV4ChatModel, deepseek_v4_settings
from app.trip_agent.budget import CONVERGENCE_ALLOWED_TOOLS, SINGULAR_FOCUS_TOOLS
from app.trip_agent.toolsets import TripAgentDeps, build_planner_toolset


PLANNER_V2_INSTRUCTIONS = """
You are Trajecta's only root Trip Planner Agent.
You own place trade-offs, day allocation, ordering, meal strategy, draft repair, and candidate submission.
Start by reading the workspace. Use tools in the order demanded by current evidence; there is no fixed workflow.
You may only schedule candidate IDs returned by search tools and resolved by resolve_place.
Use resolve_place(action="ambiguous") when identity cannot be safely chosen and action="exclude" for an
explicitly unwanted optional mention. Never resolve an unspecified brand to an arbitrary branch, or a named
parent place to one of its child merchants. Ask the user only when the remaining ambiguity changes the route.
Use strong GoalLedger commitments first. Reference and soft hypotheses are optional exploration, not prerequisites.
For initial grounding prefer search_candidate_sets, compare_candidate_sets only for genuinely close identities, and
apply_place_resolutions. Use the single-place variants only for a focused follow-up ambiguity.
If apply_place_resolutions returns ambiguous_strong_advice, use that structured evidence to reconsider the
strong identity before asking the user. Ask only when the advice itself remains materially ambiguous.
Create an early complete draft covering every trip day, simulate it, repair every structural or completeness gap,
then call submit_candidate. An explicit arrival, departure, or rest day is valid; an empty touring day is not.
Do not acquire facts for every searched option. After resolving strong anchors, create the complete draft first,
then call hydrate_draft_context once to acquire scheduled-place facts, visit profiles, and directional routes.
Use the lower-level fact/profile/route tools only for a focused repair after simulation identifies a remaining gap.
Use apply_draft_change for the first complete draft and prefer atomic apply_draft_operations for focused repairs.
After two complete simulated checkpoints, stop reopening the route and submit the current candidate.
When a hotel or explicit meal occupancy is part of the goal, represent it in the strict draft.
For a user-requested restaurant, set DraftMealInput.place_candidate_id to the resolved restaurant and its
travel_mode_from_previous; never satisfy a named meal only in meal_strategy prose. Flexible unnamed meals may
remain unanchored. Runtime owns workspace versions and serializes mutations; never invent or pass storage
versions. Do not hide executable time
in meal_strategy prose.
Comfort, ordinary meal timing, and ordinary must-visit wording are optimization goals, not automatic blockers.
Treat recommended visit windows and long outing days as experience trade-offs. Only explicit appointments,
known closure, real entity integrity, chronology, and current release state are publication blockers.
Never invent a place, coordinate, route, opening fact, tool result, workspace version, or publication success.
If submission fails, use the returned counterexample. Do not claim success unless submit_candidate returns ok=true.
""".strip()


def build_trip_planner_agent(
    model: Model,
    *,
    capabilities: list[AbstractCapability[TripAgentDeps]] | None = None,
) -> Agent[TripAgentDeps, str | DeferredToolRequests]:
    def tool_available(
        ctx: RunContext[TripAgentDeps], tool_def: ToolDefinition
    ) -> bool:
        if ctx.deps.budget.convergence_mode:
            return tool_def.name in CONVERGENCE_ALLOWED_TOOLS
        if tool_def.name not in SINGULAR_FOCUS_TOOLS:
            return True
        workspace = ctx.deps.repository.get_workspace(ctx.deps.workspace_id)
        if workspace is None:
            return False
        if workspace.current_draft is not None:
            return True
        focused = [
            item
            for item in workspace.place_hypotheses
            if item.route_relevant
            and item.status.value in {"open", "ambiguous"}
            and item.polarity == "requested"
        ]
        if tool_def.name == "search_place_candidates":
            return (
                any(item.status.value == "ambiguous" for item in focused)
                or bool(workspace.place_candidates)
            ) and len(focused) <= 3
        if tool_def.name in {"compare_place_candidates", "resolve_place"}:
            return bool(workspace.place_candidates) and len(focused) <= 3
        return False

    agent = Agent(
        model,
        output_type=[str, DeferredToolRequests],
        deps_type=TripAgentDeps,
        instructions=PLANNER_V2_INSTRUCTIONS,
        model_settings=deepseek_v4_settings("root") if isinstance(model, DeepSeekV4ChatModel) else None,
        toolsets=[build_planner_toolset().filtered(tool_available)],
        capabilities=capabilities,
        retries={"tools": 2, "output": 3},
    )

    @agent.output_validator
    async def require_submitted_candidate(
        ctx: RunContext[TripAgentDeps], output: str | DeferredToolRequests
    ) -> str | DeferredToolRequests:
        if isinstance(output, DeferredToolRequests):
            return output
        run = ctx.deps.repository.get_run(ctx.deps.run_id)
        if run is None:
            raise RuntimeError(ctx.deps.run_id)
        if run.status.value != "published":
            workspace = ctx.deps.repository.get_workspace(ctx.deps.workspace_id)
            if workspace is None:
                raise RuntimeError(ctx.deps.workspace_id)
            searched_hypotheses = {
                candidate.hypothesis_id for candidate in workspace.place_candidates
            }
            unresolved_search_ids = [
                item.hypothesis_id
                for item in workspace.place_hypotheses
                if item.hypothesis_id not in searched_hypotheses
                and item.status.value == "open"
                and item.route_relevant
                and item.polarity == "requested"
            ]
            raise ModelRetry(
                "You cannot finish yet: no candidate has been successfully published. "
                f"Current workspace version is {workspace.version}. "
                f"Open hypotheses that may still be useful: {unresolved_search_ids}. "
                f"A draft exists: {workspace.current_draft is not None}. "
                "Only route-relevant places and explicit user commitments need to be closed; context, excluded, "
                "or optional mentions do not block publication. Call the tool needed for the first concrete "
                "release gap. Create and simulate a draft, then call submit_candidate. If a real blocker cannot "
                "be closed, request clarification."
            )
        return output

    return agent
