from __future__ import annotations

from pydantic import Field, model_validator

from app.trip_agent_v3.domain.common import DomainModel
from app.trip_agent_v3.domain.requirements import QueryDecision, RequirementLedger


class QueryTarget(DomainModel):
    target_id: str = Field(min_length=1, max_length=200)
    obligation_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    query_text: str = Field(min_length=1, max_length=500)
    destination: str | None = Field(default=None, max_length=200)
    category_hints: tuple[str, ...] = Field(default=(), max_length=10)
    max_candidates: int = Field(default=3, ge=1, le=3)
    rationale: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_obligation_ids(self) -> "QueryTarget":
        if len(self.obligation_ids) != len(set(self.obligation_ids)):
            raise ValueError("query target obligation_ids must be unique")
        return self


class QuerySkip(DomainModel):
    obligation_id: str = Field(min_length=1, max_length=200)
    decision: QueryDecision
    reason_code: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_skip_decision(self) -> "QuerySkip":
        if self.decision in {
            QueryDecision.PENDING,
            QueryDecision.QUERY,
            QueryDecision.MERGE,
        }:
            raise ValueError(
                "query, merge, and pending decisions cannot be query skips"
            )
        return self


class QueryPlan(DomainModel):
    plan_id: str = Field(min_length=1, max_length=200)
    ledger_id: str = Field(min_length=1, max_length=200)
    ledger_revision: int = Field(ge=1)
    targets: tuple[QueryTarget, ...] = ()
    skips: tuple[QuerySkip, ...] = ()

    @model_validator(mode="after")
    def validate_unique_coverage(self) -> "QueryPlan":
        target_ids = [item.target_id for item in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("query target ids must be unique")
        covered = [
            obligation_id
            for target in self.targets
            for obligation_id in target.obligation_ids
        ]
        skipped = [item.obligation_id for item in self.skips]
        if len(covered) != len(set(covered)):
            raise ValueError("an obligation may belong to only one query target")
        if len(skipped) != len(set(skipped)):
            raise ValueError("an obligation may have only one query skip")
        overlap = set(covered) & set(skipped)
        if overlap:
            raise ValueError(
                f"query target and skip coverage overlap: {sorted(overlap)}"
            )
        return self

    @property
    def query_obligation_ids(self) -> tuple[str, ...]:
        return tuple(
            obligation_id
            for target in self.targets
            for obligation_id in target.obligation_ids
        )

    @property
    def skipped_obligation_ids(self) -> tuple[str, ...]:
        return tuple(item.obligation_id for item in self.skips)

    def validate_against(self, ledger: RequirementLedger) -> None:
        if self.ledger_id != ledger.ledger_id:
            raise ValueError("query plan belongs to another requirement ledger")
        if self.ledger_revision != ledger.revision:
            raise ValueError("query plan was built from a stale ledger revision")
        obligations = {item.obligation_id: item for item in ledger.obligations}
        unknown = (
            set(self.query_obligation_ids) | set(self.skipped_obligation_ids)
        ) - obligations.keys()
        if unknown:
            raise ValueError(
                f"query plan references unknown obligations: {sorted(unknown)}"
            )
        expected_grounded = {
            item.obligation_id
            for item in ledger.obligations
            if item.query_decision
            in {QueryDecision.QUERY, QueryDecision.MERGE}
        }
        actual_query = set(self.query_obligation_ids)
        if actual_query != expected_grounded:
            raise ValueError(
                "query coverage mismatch: "
                f"missing={sorted(expected_grounded - actual_query)}, "
                f"unexpected={sorted(actual_query - expected_grounded)}"
            )
        target_by_obligation = {
            obligation_id: target
            for target in self.targets
            for obligation_id in target.obligation_ids
        }
        for obligation in ledger.obligations:
            if obligation.query_decision is not QueryDecision.MERGE:
                continue
            target = target_by_obligation[obligation.obligation_id]
            if (
                obligation.merged_into_obligation_id
                not in target.obligation_ids
            ):
                raise ValueError(
                    "merged obligation must share a query target with its "
                    f"root: {obligation.obligation_id}"
                )
        skip_by_id = {item.obligation_id: item for item in self.skips}
        expected_skips = {
            item.obligation_id
            for item in ledger.obligations
            if item.query_decision
            not in {
                QueryDecision.PENDING,
                QueryDecision.QUERY,
                QueryDecision.MERGE,
            }
        }
        if set(skip_by_id) != expected_skips:
            raise ValueError(
                "query skip coverage mismatch: "
                f"missing={sorted(expected_skips - set(skip_by_id))}, "
                f"unexpected={sorted(set(skip_by_id) - expected_skips)}"
            )
        mismatched = [
            obligation_id
            for obligation_id, skip in skip_by_id.items()
            if skip.decision is not obligations[obligation_id].query_decision
        ]
        if mismatched:
            raise ValueError(
                f"query skip decisions do not match ledger: {sorted(mismatched)}"
            )
