from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.trip_agent.domain.common import DomainModel, utc_now
from app.trip_agent.domain.draft import DraftSnapshot
from app.trip_agent.domain.goal import GoalLedger
from app.trip_agent.domain.places import (
    HypothesisStatus,
    PlaceCandidate,
    PlaceHypothesis,
    PlaceResolution,
    ResolutionStatus,
)


class TripWorkspace(DomainModel):
    workspace_id: str = Field(min_length=1, max_length=200)
    goal_ledger: GoalLedger
    version: int = Field(default=1, ge=1)
    place_set_revision: int = Field(default=0, ge=0)
    fact_version: int = Field(default=0, ge=0)
    place_hypotheses: tuple[PlaceHypothesis, ...] = ()
    place_candidates: tuple[PlaceCandidate, ...] = ()
    place_resolutions: tuple[PlaceResolution, ...] = ()
    current_draft: DraftSnapshot | None = None
    latest_release_id: str | None = Field(default=None, max_length=200)
    created_at: datetime
    updated_at: datetime

    def with_goal_ledger(self, goal_ledger: GoalLedger, *, at: datetime | None = None) -> "TripWorkspace":
        if goal_ledger.revision != self.goal_ledger.revision + 1:
            raise ValueError("goal ledger revision must advance exactly one version")
        return self.model_copy(
            update={
                "goal_ledger": goal_ledger,
                "version": self.version + 1,
                "updated_at": at or utc_now(),
            }
        )

    def with_hypotheses(
        self, hypotheses: tuple[PlaceHypothesis, ...], *, at: datetime | None = None
    ) -> "TripWorkspace":
        existing = {item.hypothesis_id for item in self.place_hypotheses}
        incoming = [item.hypothesis_id for item in hypotheses]
        if existing.intersection(incoming) or len(incoming) != len(set(incoming)):
            raise ValueError("place hypothesis ids must be unique")
        return self.model_copy(
            update={
                "place_hypotheses": self.place_hypotheses + hypotheses,
                "place_set_revision": self.place_set_revision + 1,
                "version": self.version + 1,
                "updated_at": at or utc_now(),
            }
        )

    def with_candidates(
        self, candidates: tuple[PlaceCandidate, ...], *, at: datetime | None = None
    ) -> "TripWorkspace":
        existing = {item.candidate_id for item in self.place_candidates}
        incoming = [item.candidate_id for item in candidates]
        if existing.intersection(incoming) or len(incoming) != len(set(incoming)):
            raise ValueError("place candidate ids must be unique")
        hypotheses = {item.hypothesis_id: item for item in self.place_hypotheses}
        if any(item.hypothesis_id not in hypotheses for item in candidates):
            raise ValueError("candidate must belong to a workspace hypothesis")
        updated_hypotheses = []
        for hypothesis in self.place_hypotheses:
            new_ids = tuple(item.candidate_id for item in candidates if item.hypothesis_id == hypothesis.hypothesis_id)
            updated_hypotheses.append(
                hypothesis.model_copy(update={"candidate_ids": hypothesis.candidate_ids + new_ids})
            )
        return self.model_copy(
            update={
                "place_hypotheses": tuple(updated_hypotheses),
                "place_candidates": self.place_candidates + candidates,
                "version": self.version + 1,
                "updated_at": at or utc_now(),
            }
        )

    def with_resolution(self, resolution: PlaceResolution, *, at: datetime | None = None) -> "TripWorkspace":
        if resolution.workspace_version != self.version:
            raise ValueError("resolution was not based on current workspace version")
        hypotheses = {item.hypothesis_id: item for item in self.place_hypotheses}
        hypothesis = hypotheses.get(resolution.hypothesis_id)
        if not hypothesis:
            raise ValueError("resolution references unknown hypothesis")
        if resolution.status is ResolutionStatus.RESOLVED:
            candidates = {item.candidate_id for item in self.place_candidates}
            if resolution.candidate_id not in candidates or resolution.candidate_id not in hypothesis.candidate_ids:
                raise ValueError("resolution candidate does not belong to hypothesis")
        hypothesis_status = {
            ResolutionStatus.RESOLVED: HypothesisStatus.RESOLVED,
            ResolutionStatus.AMBIGUOUS: HypothesisStatus.AMBIGUOUS,
            ResolutionStatus.EXCLUDED: HypothesisStatus.EXCLUDED,
            ResolutionStatus.UNRESOLVED: HypothesisStatus.OPEN,
        }[resolution.status]
        updated_hypotheses = tuple(
            item.model_copy(update={"status": hypothesis_status})
            if item.hypothesis_id == resolution.hypothesis_id
            else item
            for item in self.place_hypotheses
        )
        prior = tuple(item for item in self.place_resolutions if item.hypothesis_id != resolution.hypothesis_id)
        return self.model_copy(
            update={
                "place_hypotheses": updated_hypotheses,
                "place_resolutions": prior + (resolution,),
                "version": self.version + 1,
                "updated_at": at or utc_now(),
            }
        )

    def with_draft(self, draft: DraftSnapshot, *, at: datetime | None = None) -> "TripWorkspace":
        if draft.workspace_id != self.workspace_id:
            raise ValueError("draft belongs to another workspace")
        if draft.workspace_version != self.version:
            raise ValueError("draft was not based on current workspace version")
        return self.model_copy(
            update={"current_draft": draft, "version": self.version + 1, "updated_at": at or utc_now()}
        )

    def with_fact_revision(self, *, at: datetime | None = None) -> "TripWorkspace":
        return self.model_copy(
            update={
                "fact_version": self.fact_version + 1,
                "version": self.version + 1,
                "updated_at": at or utc_now(),
            }
        )
