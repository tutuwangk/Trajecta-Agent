from __future__ import annotations

from pydantic_ai import Agent, DeferredToolRequests, ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from app.trip_agent.adapters.provider import DeepSeekV4ChatModel, deepseek_v4_settings
from app.trip_agent.toolsets import TripAgentDeps, build_planner_toolset


PLANNER_V2_INSTRUCTIONS = """
You are Trajecta's only root Trip Planner Agent.
You own place trade-offs, day allocation, ordering, meal strategy, draft repair, and candidate submission.
Start by reading the workspace. Use tools in the order demanded by current evidence; there is no fixed workflow.
You may only schedule candidate IDs returned by search tools and resolved by resolve_place.
Use resolve_place(action="ambiguous") when identity cannot be safely chosen and action="exclude" for an
explicitly unwanted optional mention. Never resolve an unspecified brand to an arbitrary branch, or a named
parent place to one of its child merchants. Ask the user only when the remaining ambiguity changes the route.
Use compare_place_candidates when searched identities are genuinely close; its output is advice, not a mutation.
Create an early viable draft, simulate it, repair every structural blocker, then call submit_candidate.
Use apply_draft_change for the first complete draft and prefer atomic apply_draft_operations for focused repairs.
Use estimate_visit_profiles when several scheduled candidates need duration estimates; it commits the batch once.
When a hotel or explicit meal occupancy is part of the goal, represent it in the strict draft and acquire the
required directional routes; send related routes in one acquire_route_facts batch when possible. Runtime owns
workspace versions and serializes mutations; never invent or pass storage versions. Do not hide executable time
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
    agent = Agent(
        model,
        output_type=[str, DeferredToolRequests],
        deps_type=TripAgentDeps,
        instructions=PLANNER_V2_INSTRUCTIONS,
        model_settings=deepseek_v4_settings("root") if isinstance(model, DeepSeekV4ChatModel) else None,
        toolsets=[build_planner_toolset()],
        capabilities=capabilities,
        retries=8,
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
