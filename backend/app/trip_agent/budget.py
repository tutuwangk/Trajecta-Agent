from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from time import monotonic
from typing import Callable, Mapping


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
        return cls(
            max_seconds=float(state.get("max_seconds", 480)),
            reserve_ratio=float(state.get("reserve_ratio", 0.30)),
            max_repeated_signature=int(state.get("max_repeated_signature", 2)),
            carried_seconds=float(state.get("elapsed_seconds", 0)),
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
        self._changed()
        if self.signatures[key] > self.max_repeated_signature:
            return f"repeated tool signature rejected: {key}"
        if self.reserve_mode and tool_name in {
            "analyze_place_mentions",
            "compare_place_candidates",
            "search_place_candidates",
            "estimate_visit_profile",
            "estimate_visit_profiles",
        }:
            return f"reserve mode rejected expansion tool: {tool_name}"
        if self.simulation_no_progress >= 3 and tool_name in {
            "analyze_place_mentions",
            "compare_place_candidates",
            "search_place_candidates",
            "estimate_visit_profile",
            "estimate_visit_profiles",
            "review_experience",
        }:
            return (
                f"progress monitor rejected expansion after "
                f"{self.simulation_no_progress} non-improving simulations"
            )
        return None

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
