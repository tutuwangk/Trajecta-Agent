from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from app.trip_agent.domain.common import DomainModel, utc_now


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    VALIDATING = "validating"
    PUBLISHED = "published"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunFailureClass(StrEnum):
    TRANSIENT_EXTERNAL = "transient_external"
    PERMANENT_EXTERNAL = "permanent_external"
    PROVIDER_PROTOCOL = "provider_protocol"
    BUDGET = "budget"
    INTERNAL = "internal"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.PUBLISHED, RunStatus.INCOMPLETE, RunStatus.FAILED, RunStatus.CANCELLED}
)

_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_USER,
            RunStatus.VALIDATING,
            RunStatus.PUBLISHED,
            RunStatus.INCOMPLETE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.WAITING_USER: frozenset(
        {RunStatus.RUNNING, RunStatus.INCOMPLETE, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.VALIDATING: frozenset(
        {RunStatus.RUNNING, RunStatus.PUBLISHED, RunStatus.INCOMPLETE, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
}


class AgentRun(DomainModel):
    run_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    status: RunStatus = RunStatus.CREATED
    idempotency_key: str = Field(min_length=1, max_length=300)
    provider_conversation_id: str = Field(min_length=1, max_length=200)
    revision_of_run_id: str | None = Field(default=None, max_length=200)
    active_interruption_id: str | None = Field(default=None, max_length=200)
    error_code: str | None = Field(default=None, max_length=200)
    error_message: str | None = Field(default=None, max_length=4_000)
    failure_class: RunFailureClass | None = None
    retryable: bool | None = None
    provider_attempt_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime

    def transition(
        self,
        status: RunStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        failure_class: RunFailureClass | None = None,
        retryable: bool | None = None,
        provider_attempt_count: int | None = None,
        active_interruption_id: str | None = None,
        at: datetime | None = None,
    ) -> "AgentRun":
        if self.status in TERMINAL_RUN_STATUSES:
            raise ValueError(f"terminal run {self.run_id} cannot transition from {self.status}")
        if status not in _ALLOWED_TRANSITIONS.get(self.status, frozenset()):
            raise ValueError(f"invalid run transition: {self.status} -> {status}")
        return self.model_copy(
            update={
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
                "failure_class": failure_class,
                "retryable": retryable,
                "provider_attempt_count": (
                    self.provider_attempt_count
                    if provider_attempt_count is None
                    else provider_attempt_count
                ),
                "active_interruption_id": active_interruption_id,
                "updated_at": at or utc_now(),
            }
        )


class ClarificationQuestion(DomainModel):
    question_id: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4_000)
    reason: str = Field(min_length=1, max_length=2_000)
    options: tuple[str, ...] = Field(default=(), max_length=3)
    allow_other: bool = True


class ClarificationBatch(DomainModel):
    interruption_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    provider_run_id: str = Field(min_length=1, max_length=200)
    tool_call_id: str = Field(min_length=1, max_length=300)
    questions: tuple[ClarificationQuestion, ...] = Field(min_length=1, max_length=5)
    created_at: datetime
    answered_at: datetime | None = None

    @model_validator(mode="after")
    def validate_question_ids(self) -> "ClarificationBatch":
        ids = [question.question_id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("clarification question ids must be unique")
        return self


class ClarificationAnswer(DomainModel):
    question_id: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=4_000)


class ClarificationAnswers(DomainModel):
    interruption_id: str = Field(min_length=1, max_length=200)
    tool_call_id: str = Field(min_length=1, max_length=300)
    answers: tuple[ClarificationAnswer, ...] = Field(min_length=1, max_length=5)
    answered_at: datetime

    @model_validator(mode="after")
    def validate_answer_ids(self) -> "ClarificationAnswers":
        ids = [answer.question_id for answer in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("clarification answer question ids must be unique")
        return self
