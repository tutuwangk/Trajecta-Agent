from __future__ import annotations

from time import perf_counter
from typing import Any

from app.agents.planning_workflow import run_planning_workflow
from app.core import AppError
from app.domain.run import PlanningCheckpoint


class PlanningOrchestrator:
    """Owns bounded execution, checkpoints and terminal planning state."""

    def __init__(self, store, run_id: str, *, max_business_repairs: int = 1):
        self.store = store
        self.run_id = run_id
        self.max_business_repairs = max_business_repairs
        self.started_at = perf_counter()

    def checkpoint(self, checkpoint: PlanningCheckpoint, **artifacts: Any) -> None:
        allowed = {
            key: value
            for key, value in artifacts.items()
            if key in {
                "input_snapshot",
                "fact_snapshot",
                "blueprint",
                "validation_report",
                "release_decision",
                "metrics",
                "result_snapshot",
                "debug",
            }
        }
        self.store.update_planning_run(
            self.run_id,
            status="running",
            stage=checkpoint,
            checkpoint=checkpoint,
            **allowed,
        )

    def run_workflow(self, *args, **kwargs):
        kwargs["max_replans"] = min(int(kwargs.get("max_replans", self.max_business_repairs)), self.max_business_repairs)
        kwargs["on_phase"] = self._workflow_phase
        return run_planning_workflow(*args, **kwargs)

    def _workflow_phase(self, phase: str, artifacts: dict) -> None:
        verification = artifacts.get("validation_report")
        if isinstance(verification, dict) and isinstance(verification.get("release_decision"), dict):
            artifacts = {**artifacts, "release_decision": verification["release_decision"]}
        self.checkpoint(phase, **artifacts)

    def complete(self, *, result_status: str, debug: dict, artifacts: dict | None = None) -> None:
        artifacts = artifacts or {}
        duration_ms = round((perf_counter() - self.started_at) * 1000)
        metrics = dict(artifacts.get("metrics") or {})
        metrics["planning_latency_ms"] = duration_ms
        artifacts = {**artifacts, "metrics": metrics}
        self.store.update_planning_run(
            self.run_id,
            status="completed",
            stage="completed",
            checkpoint="completed",
            result_status=result_status,
            attempt_count=int(debug.get("attempt_count") or 1),
            duration_ms=duration_ms,
            debug=debug,
            **{
                key: value
                for key, value in artifacts.items()
                if key in {
                    "fact_snapshot",
                    "blueprint",
                    "validation_report",
                    "release_decision",
                    "metrics",
                    "result_snapshot",
                }
            },
        )

    def needs_user_choice(self) -> None:
        self.store.update_planning_run(
            self.run_id,
            status="needs_user_choice",
            stage="repairing",
            checkpoint="repairing",
            attempt_count=1,
            duration_ms=round((perf_counter() - self.started_at) * 1000),
        )

    def fail(self, exc: Exception) -> None:
        code = exc.code if isinstance(exc, AppError) else "internal_error"
        stage = exc.step if isinstance(exc, AppError) and exc.step else "compiling"
        message = exc.message if isinstance(exc, AppError) else str(exc)
        debug = exc.details if isinstance(exc, AppError) else {"error_type": type(exc).__name__}
        checkpoint = stage if stage in {
            "understanding",
            "grounding",
            "fact_snapshot",
            "blueprint",
            "compiling",
            "repairing",
            "fallback",
            "release_gate",
            "copywriting",
            "completed",
        } else "compiling"
        duration_ms = round((perf_counter() - self.started_at) * 1000)
        attempt_count = max(1, int(debug.get("attempt_count") or 1))
        llm_metrics = dict(debug.get("llm_metrics") or {})
        self.store.update_planning_run(
            self.run_id,
            status="failed",
            stage=stage,
            checkpoint=checkpoint,
            result_status="failed",
            error_code=code,
            error_message=message,
            attempt_count=attempt_count,
            duration_ms=duration_ms,
            metrics={
                "model_call_count": int(llm_metrics.get("call_count") or 0),
                "model_error_count": int(llm_metrics.get("error_count") or 0),
                "business_repair_count": int(debug.get("repair_attempts") or 0),
                "verified_rate": 0.0,
                "degraded_rate": 0.0,
                "failed_rate": 1.0,
                "final_publish_success_rate": 0.0,
                "planning_latency_ms": duration_ms,
                "llm": llm_metrics,
            },
            debug=debug,
        )
