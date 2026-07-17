from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.domain.common import DomainModel


IssueSeverity = Literal["info", "low", "medium", "high", "blocking"]


class ValidationIssue(DomainModel):
    code: str = Field(min_length=1)
    severity: IssueSeverity
    message: str = Field(min_length=1)
    day: int | None = Field(default=None, ge=1)
    poi_ids: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggestion: str = ""
    agent_repairable: bool = True
    release_blocking: bool = False


class ValidationReport(DomainModel):
    passed: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    fact_version: str = ""
    repair_attempt: int = Field(default=0, ge=0)

    @property
    def blocking_issues(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.release_blocking]

    @property
    def repairable_issues(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.agent_repairable]
