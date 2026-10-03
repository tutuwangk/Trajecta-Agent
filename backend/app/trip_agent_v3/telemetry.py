from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RunTelemetry:
    source_count: int = 0
    source_char_count: int = 0
    obligation_count: int = 0
    constraint_count: int = 0
    query_target_count: int = 0
    query_targets_seen: dict[str, int] = field(default_factory=dict)
    provider_calls: dict[str, int] = field(default_factory=dict)
    place_search_by_target: dict[str, int] = field(default_factory=dict)
    provider_result_count: int = 0
    retained_candidate_count: int = 0
    excluded_candidate_count: int = 0
    truncated_candidate_count: int = 0
    selected_grounding_count: int = 0
    unresolved_grounding_count: int = 0
    disposition_counts: dict[str, int] = field(default_factory=dict)
    stop_count: int = 0
    leg_count: int = 0
    fact_need_count: int = 0
    route_fact_need_count: int = 0
    operational_fact_need_count: int = 0
    fact_cache_hit_count: int = 0
    fact_gap_count: int = 0
    local_context_read_count: int = 0
    local_context_total_chars: int = 0
    local_context_max_chars: int = 0
    model_request_count: int = 0
    tool_call_count: int = 0
    input_token_count: int = 0
    output_token_count: int = 0
    model_requests_by_role: dict[str, int] = field(default_factory=dict)
    tool_calls_by_role: dict[str, int] = field(default_factory=dict)
    input_tokens_by_role: dict[str, int] = field(default_factory=dict)
    output_tokens_by_role: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_snapshot(
        cls, payload: dict[str, object] | None
    ) -> "RunTelemetry":
        if not payload:
            return cls()
        telemetry = cls()
        for name in telemetry.__dataclass_fields__:
            if name not in payload:
                continue
            current = getattr(telemetry, name)
            value = payload[name]
            if isinstance(current, dict) and isinstance(value, dict):
                setattr(
                    telemetry,
                    name,
                    {str(key): int(count) for key, count in value.items()},
                )
            elif isinstance(current, int):
                setattr(telemetry, name, int(value))
        return telemetry

    def record_provider_call(self, operation: str) -> None:
        self.provider_calls[operation] = (
            self.provider_calls.get(operation, 0) + 1
        )

    def record_context(self, serialized: str) -> None:
        size = len(serialized)
        self.local_context_read_count += 1
        self.local_context_total_chars += size
        self.local_context_max_chars = max(
            self.local_context_max_chars, size
        )

    def record_model_usage(
        self, usage: Any, *, role: str = "unspecified"
    ) -> None:
        requests = int(getattr(usage, "requests", 0))
        tool_calls = int(getattr(usage, "tool_calls", 0))
        input_tokens = int(getattr(usage, "input_tokens", 0))
        output_tokens = int(getattr(usage, "output_tokens", 0))
        self.model_request_count += requests
        self.tool_call_count += tool_calls
        self.input_token_count += input_tokens
        self.output_token_count += output_tokens
        for bucket, value in (
            (self.model_requests_by_role, requests),
            (self.tool_calls_by_role, tool_calls),
            (self.input_tokens_by_role, input_tokens),
            (self.output_tokens_by_role, output_tokens),
        ):
            bucket[role] = bucket.get(role, 0) + value

    def snapshot(
        self,
        *,
        run_status: str,
        wall_clock_ms: int,
        delivery_state: str | None,
        fact_status: str | None,
        experience_status: str | None,
        candidate_snapshot_id: str | None,
        release_id: str | None,
    ) -> dict[str, object]:
        return {
            "source_count": self.source_count,
            "source_char_count": self.source_char_count,
            "obligation_count": self.obligation_count,
            "constraint_count": self.constraint_count,
            "query_target_count": self.query_target_count,
            "query_targets_seen": dict(sorted(self.query_targets_seen.items())),
            "provider_calls": dict(sorted(self.provider_calls.items())),
            "place_search_by_target": dict(
                sorted(self.place_search_by_target.items())
            ),
            "provider_result_count": self.provider_result_count,
            "retained_candidate_count": self.retained_candidate_count,
            "excluded_candidate_count": self.excluded_candidate_count,
            "truncated_candidate_count": self.truncated_candidate_count,
            "selected_grounding_count": self.selected_grounding_count,
            "unresolved_grounding_count": self.unresolved_grounding_count,
            "disposition_counts": dict(
                sorted(self.disposition_counts.items())
            ),
            "stop_count": self.stop_count,
            "leg_count": self.leg_count,
            "fact_need_count": self.fact_need_count,
            "route_fact_need_count": self.route_fact_need_count,
            "operational_fact_need_count": (
                self.operational_fact_need_count
            ),
            "fact_cache_hit_count": self.fact_cache_hit_count,
            "fact_gap_count": self.fact_gap_count,
            "local_context_read_count": self.local_context_read_count,
            "local_context_total_chars": self.local_context_total_chars,
            "local_context_max_chars": self.local_context_max_chars,
            "model_request_count": self.model_request_count,
            "tool_call_count": self.tool_call_count,
            "input_token_count": self.input_token_count,
            "output_token_count": self.output_token_count,
            "model_requests_by_role": dict(
                sorted(self.model_requests_by_role.items())
            ),
            "tool_calls_by_role": dict(
                sorted(self.tool_calls_by_role.items())
            ),
            "input_tokens_by_role": dict(
                sorted(self.input_tokens_by_role.items())
            ),
            "output_tokens_by_role": dict(
                sorted(self.output_tokens_by_role.items())
            ),
            "run_status": run_status,
            "wall_clock_ms": wall_clock_ms,
            "delivery_state": delivery_state,
            "fact_status": fact_status,
            "experience_status": experience_status,
            "candidate_snapshot_id": candidate_snapshot_id,
            "release_id": release_id,
        }
