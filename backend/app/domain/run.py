from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field

from app.domain.common import DomainModel
from app.domain.facts import FactSnapshot
from app.domain.itinerary import ReleaseDecision
from app.domain.planning import PlanBlueprint
from app.domain.validation import ValidationReport


PlanningRunStatus = Literal["pending", "running", "needs_user_choice", "completed", "failed"]
PlanningCheckpoint = Literal[
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
]


class PlanningRunRecord(DomainModel):
    run_id: str
    session_id: str
    status: PlanningRunStatus
    checkpoint: PlanningCheckpoint
    result_status: Literal["", "verified", "degraded", "failed"] = ""
    attempt_count: int = Field(default=0, ge=0)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    fact_snapshot: FactSnapshot | None = None
    blueprint: PlanBlueprint | None = None
    validation_report: ValidationReport | None = None
    release_decision: ReleaseDecision | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    result_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    idempotency_key: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
