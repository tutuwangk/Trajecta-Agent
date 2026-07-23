from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from time import monotonic, time
from typing import Callable, Mapping

from pydantic_ai.usage import RunUsage


TOOL_CATEGORIES: dict[str, str] = {
    "read_workspace": "read",
    "analyze_place_mentions": "discovery",
    "search_place_candidates": "expansion",
    "search_candidate_sets": "expansion",
    "compare_place_candidates": "expansion",
    "compare_candidate_sets": "expansion",
    "resolve_place": "mutation",
    "apply_place_resolutions": "mutation",
    "acquire_place_facts": "fact",
    "acquire_place_fact_batch": "fact",
    "acquire_route_facts": "fact",
    "hydrate_draft_context": "fact",
    "estimate_visit_profile": "expansion",
    "estimate_visit_profiles": "expansion",
    "apply_draft_change": "mutation",
    "apply_draft_operations": "mutation",
    "simulate_candidate": "validation",
    "review_experience": "experience",
    "request_clarification": "clarification",
    "submit_candidate": "publication",
}

MUTATION_TOOLS = frozenset(
    name
    for name, category in TOOL_CATEGORIES.items()
    if category in {"discovery", "expansion", "mutation", "fact", "clarification", "publication"}
)
EXPANSION_TOOLS = frozenset(
    name
    for name, category in TOOL_CATEGORIES.items()
    if category in {"discovery", "expansion", "experience"}
)
CONVERGENCE_ALLOWED_TOOLS = frozenset(
    {
        "read_workspace",
        "hydrate_draft_context",
        "apply_draft_change",
        "apply_draft_operations",
        "simulate_candidate",
        "request_clarification",
        "submit_candidate",
    }
)
SINGULAR_FOCUS_TOOLS = frozenset(
    {
        "search_place_candidates",
        "compare_place_candidates",
        "resolve_place",
        "acquire_place_facts",
        "estimate_visit_profile",
    }
)


class BudgetExhausted(RuntimeError):
    pass


@dataclass(slots=True)
class RuntimeBudget:
    max_seconds: float = 480
    reserve_ratio: float = 0.30
    max_repeated_signature: int = 2
    started_at: float = field(default_factory=monotonic)
    carried_seconds: float = 0
    signatures: Counter[str] = field(default_factory=Counter)
    last_workspace_version: int = 0
    no_progress_actions: int = 0
    last_blocker_count: int | None = None
    simulation_no_progress: int = 0
    convergence_mode: bool = False
    draft_created: bool = False
    complete_checkpoint_created: bool = False
    complete_checkpoint_count: int = 0
    expansion_actions_before_draft: int = 0
    max_model_requests: int = 40
    max_tool_calls: int = 128
    model_requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    provider_calls: Counter[str] = field(default_factory=Counter)
    active_episode_started_epoch: float | None = None
    active_episode_base_seconds: float = 0
    on_change: Callable[[dict[str, object]], None] | None = None

    @property
    def elapsed_seconds(self) -> float:
        return self.carried_seconds + monotonic() - self.started_at

    @classmethod
    def from_state(
        cls,
        state: Mapping[str, object] | None,
        *,
        on_change: Callable[[dict[str, object]], None] | None = None,
    ) -> "RuntimeBudget":
        if not state:
            return cls(on_change=on_change)
        active_started = (
            float(state["active_episode_started_epoch"])
            if state.get("active_episode_started_epoch") is not None
            else None
        )
        carried_seconds = float(state.get("elapsed_seconds", 0))
        if active_started is not None:
            carried_seconds = float(
                state.get("active_episode_base_seconds", carried_seconds)
            ) + max(0.0, time() - active_started)
        return cls(
            max_seconds=float(state.get("max_seconds", 480)),
            reserve_ratio=float(state.get("reserve_ratio", 0.30)),
            max_repeated_signature=int(state.get("max_repeated_signature", 2)),
            carried_seconds=carried_seconds,
            signatures=Counter(
                {str(key): int(value) for key, value in dict(state.get("signatures", {})).items()}
            ),
            last_workspace_version=int(state.get("last_workspace_version", 0)),
            no_progress_actions=int(state.get("no_progress_actions", 0)),
            last_blocker_count=(
                int(state["last_blocker_count"])
                if state.get("last_blocker_count") is not None
                else None
            ),
            simulation_no_progress=int(state.get("simulation_no_progress", 0)),
            convergence_mode=bool(state.get("convergence_mode", False)),
            draft_created=bool(state.get("draft_created", False)),
            complete_checkpoint_created=bool(state.get("complete_checkpoint_created", False)),
            complete_checkpoint_count=int(state.get("complete_checkpoint_count", 0)),
            expansion_actions_before_draft=int(state.get("expansion_actions_before_draft", 0)),
            max_model_requests=int(state.get("max_model_requests", 40)),
            max_tool_calls=int(state.get("max_tool_calls", 128)),
            model_requests=int(state.get("model_requests", 0)),
            tool_calls=int(state.get("tool_calls", 0)),
            input_tokens=int(state.get("input_tokens", 0)),
            output_tokens=int(state.get("output_tokens", 0)),
            provider_calls=Counter(
                {str(key): int(value) for key, value in dict(state.get("provider_calls", {})).items()}
            ),
            active_episode_started_epoch=None,
            active_episode_base_seconds=carried_seconds,
            on_change=on_change,
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "max_seconds": self.max_seconds,
            "reserve_ratio": self.reserve_ratio,
            "max_repeated_signature": self.max_repeated_signature,
            "elapsed_seconds": self.elapsed_seconds,
            "signatures": dict(self.signatures),
            "last_workspace_version": self.last_workspace_version,
            "no_progress_actions": self.no_progress_actions,
            "last_blocker_count": self.last_blocker_count,
            "simulation_no_progress": self.simulation_no_progress,
            "convergence_mode": self.convergence_mode,
            "draft_created": self.draft_created,
            "complete_checkpoint_created": self.complete_checkpoint_created,
            "complete_checkpoint_count": self.complete_checkpoint_count,
            "expansion_actions_before_draft": self.expansion_actions_before_draft,
            "max_model_requests": self.max_model_requests,
            "max_tool_calls": self.max_tool_calls,
            "model_requests": self.model_requests,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "provider_calls": dict(self.provider_calls),
            "active_episode_started_epoch": self.active_episode_started_epoch,
            "active_episode_base_seconds": self.active_episode_base_seconds,
        }

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(self.snapshot())

    @property
    def reserve_mode(self) -> bool:
        return self.elapsed_seconds >= self.max_seconds * (1 - self.reserve_ratio)

    def authorize(self, tool_name: str, signature: str) -> str | None:
        if self.elapsed_seconds >= self.max_seconds:
            raise BudgetExhausted("agent wall-clock budget exhausted")
        key = f"{tool_name}:{signature}"
        self.signatures[key] += 1
        if tool_name in EXPANSION_TOOLS and not self.draft_created:
            self.expansion_actions_before_draft += 1
        self._changed()
        if self.signatures[key] > self.max_repeated_signature:
            return f"repeated tool signature rejected: {key}"
        if self.complete_checkpoint_count >= 2 and tool_name in {
            "analyze_place_mentions",
            "compare_place_candidates",
            "compare_candidate_sets",
            "search_place_candidates",
            "search_candidate_sets",
            "resolve_place",
            "apply_place_resolutions",
            "acquire_place_facts",
            "acquire_place_fact_batch",
            "acquire_route_facts",
            "hydrate_draft_context",
            "estimate_visit_profile",
            "estimate_visit_profiles",
            "apply_draft_change",
            "apply_draft_operations",
            "review_experience",
        }:
            return (
                "two complete Agent-authored checkpoints already exist; "
                "submit_candidate now instead of reopening the route"
            )
        if self.convergence_mode and tool_name not in CONVERGENCE_ALLOWED_TOOLS:
            return f"convergence episode rejected non-essential expansion tool: {tool_name}"
        if not self.draft_created and self.expansion_actions_before_draft > 12 and tool_name in EXPANSION_TOOLS:
            return "early-draft reserve rejected further expansion before the first draft"
        if self.reserve_mode and tool_name in {
            "analyze_place_mentions",
            "compare_place_candidates",
            "compare_candidate_sets",
            "search_place_candidates",
            "search_candidate_sets",
            "estimate_visit_profile",
            "estimate_visit_profiles",
        }:
            return f"reserve mode rejected expansion tool: {tool_name}"
        if self.simulation_no_progress >= 3 and tool_name in {
            "analyze_place_mentions",
            "compare_place_candidates",
            "compare_candidate_sets",
            "search_place_candidates",
            "search_candidate_sets",
            "estimate_visit_profile",
            "estimate_visit_profiles",
            "review_experience",
        }:
            return (
                f"progress monitor rejected expansion after "
                f"{self.simulation_no_progress} non-improving simulations"
            )
        return None

    def run_usage(self) -> RunUsage:
        return RunUsage(
            requests=self.model_requests,
            tool_calls=self.tool_calls,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )

    def begin_episode(self) -> None:
        self.active_episode_base_seconds = self.elapsed_seconds
        self.active_episode_started_epoch = time()
        self._changed()

    def end_episode(self) -> None:
        elapsed = self.elapsed_seconds
        self.carried_seconds = elapsed
        self.started_at = monotonic()
        self.active_episode_started_epoch = None
        self.active_episode_base_seconds = elapsed
        self._changed()

    def observe_run_usage(self, usage: RunUsage) -> None:
        request_delta = max(0, usage.requests - self.model_requests)
        if request_delta:
            self.provider_calls["deepseek:root_model"] += request_delta
        self.model_requests = usage.requests
        self.tool_calls = max(self.tool_calls, usage.tool_calls)
        self.input_tokens = usage.input_tokens
        self.output_tokens = usage.output_tokens
        self._changed()

    def observe_provider_call(self, provider: str, operation: str) -> None:
        self.provider_calls[f"{provider}:{operation}"] += 1
        self._changed()

    def enter_convergence(self) -> None:
        self.convergence_mode = True
        self._changed()

    def observe_draft(self) -> None:
        self.draft_created = True
        self._changed()

    def observe_complete_checkpoint(self) -> None:
        self.complete_checkpoint_created = True
        self.complete_checkpoint_count += 1
        self._changed()

    def observe_workspace_version(self, version: int) -> None:
        if version > self.last_workspace_version:
            self.last_workspace_version = version
            self.no_progress_actions = 0
        else:
            self.no_progress_actions += 1
        self._changed()

    def observe_simulation(self, blocker_count: int) -> None:
        if self.last_blocker_count is None or blocker_count < self.last_blocker_count:
            self.simulation_no_progress = 0
            self.last_blocker_count = blocker_count
        else:
            self.simulation_no_progress += 1
        self._changed()
