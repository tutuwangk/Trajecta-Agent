from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.trip_agent.domain.common import DomainModel, utc_now
from app.trip_agent.domain.draft import DraftSnapshot
from app.trip_agent.domain.goal import GoalCommitment, GoalLedger
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
        existing = {item.hypothesis_id: item for item in self.place_hypotheses}
        incoming = [item.hypothesis_id for item in hypotheses]
        if len(incoming) != len(set(incoming)):
            raise ValueError("place hypothesis ids must be unique")
        additions = []
        for item in hypotheses:
            prior = existing.get(item.hypothesis_id)
            if prior is None:
                additions.append(item)
            elif prior != item:
                raise ValueError(f"place hypothesis identity conflict: {item.hypothesis_id}")
        if not additions:
            return self
        return self.model_copy(
            update={
                "place_hypotheses": self.place_hypotheses + tuple(additions),
                "place_set_revision": self.place_set_revision + 1,
                "version": self.version + 1,
                "updated_at": at or utc_now(),
            }
        )

    def with_intent_analysis(
        self,
        hypotheses: tuple[PlaceHypothesis, ...],
        commitments: tuple[GoalCommitment, ...],
        *,
        at: datetime | None = None,
    ) -> "TripWorkspace":
        existing_hypotheses = {item.hypothesis_id: item for item in self.place_hypotheses}
        incoming_hypotheses = [item.hypothesis_id for item in hypotheses]
        if len(incoming_hypotheses) != len(set(incoming_hypotheses)):
            raise ValueError("place hypothesis ids must be unique")
        existing_commitments = {
            item.commitment_id: item for item in self.goal_ledger.commitments
        }
        incoming_commitments = [item.commitment_id for item in commitments]
        if len(incoming_commitments) != len(set(incoming_commitments)):
            raise ValueError("goal commitment ids must be unique")
        new_hypotheses = []
        for item in hypotheses:
            prior = existing_hypotheses.get(item.hypothesis_id)
            if prior is None:
                new_hypotheses.append(item)
            elif prior != item:
                raise ValueError(f"place hypothesis identity conflict: {item.hypothesis_id}")
        new_commitments = []
        for item in commitments:
            prior = existing_commitments.get(item.commitment_id)
            if prior is None:
                new_commitments.append(item)
            elif prior != item:
                raise ValueError(f"goal commitment identity conflict: {item.commitment_id}")
        if not new_hypotheses and not new_commitments:
            return self
        ledger = GoalLedger(
            goal=self.goal_ledger.goal,
            commitments=self.goal_ledger.commitments + tuple(new_commitments),
            revision=self.goal_ledger.revision + 1,
        )
        return self.model_copy(
            update={
                "goal_ledger": ledger,
                "place_hypotheses": self.place_hypotheses + tuple(new_hypotheses),
                "place_set_revision": self.place_set_revision + 1,
                "version": self.version + 1,
                "updated_at": at or utc_now(),
            }
        )

    def with_candidates(
        self, candidates: tuple[PlaceCandidate, ...], *, at: datetime | None = None
    ) -> "TripWorkspace":
        existing = {item.candidate_id: item for item in self.place_candidates}
        existing_by_identity = {
            (item.hypothesis_id, item.provider, item.provider_place_id): item
            for item in self.place_candidates
        }
        stable_ids = {
            identity: item.candidate_id for identity, item in existing_by_identity.items()
        }
        normalized: dict[str, PlaceCandidate] = {}
        for item in candidates:
            identity = (item.hypothesis_id, item.provider, item.provider_place_id)
            stable_id = stable_ids.setdefault(identity, item.candidate_id)
            observation = (
                item.model_copy(update={"candidate_id": stable_id})
                if stable_id != item.candidate_id
                else item
            )
            normalized[stable_id] = observation
        candidates = tuple(normalized.values())
        incoming = [item.candidate_id for item in candidates]
        if len(incoming) != len(set(incoming)):
            raise ValueError("place candidate ids must be unique")
        hypotheses = {item.hypothesis_id: item for item in self.place_hypotheses}
        if any(item.hypothesis_id not in hypotheses for item in candidates):
            raise ValueError("candidate must belong to a workspace hypothesis")
        additions: list[PlaceCandidate] = []
        replacements: dict[str, PlaceCandidate] = {}
        for item in candidates:
            prior = existing.get(item.candidate_id)
            if prior is None:
                additions.append(item)
                continue
            identity = (item.hypothesis_id, item.provider, item.provider_place_id)
            prior_identity = (prior.hypothesis_id, prior.provider, prior.provider_place_id)
            if identity != prior_identity:
                raise ValueError(f"place candidate identity conflict: {item.candidate_id}")
            if prior == item:
                continue
            else:
                replacements[item.candidate_id] = item
        if not additions and not replacements:
            return self
        updated_hypotheses = []
        for hypothesis in self.place_hypotheses:
            new_ids = tuple(
                item.candidate_id
                for item in additions
                if item.hypothesis_id == hypothesis.hypothesis_id
            )
            updated_hypotheses.append(
                hypothesis.model_copy(update={"candidate_ids": hypothesis.candidate_ids + new_ids})
            )
        return self.model_copy(
            update={
                "place_hypotheses": tuple(updated_hypotheses),
                "place_candidates": tuple(
                    replacements.get(item.candidate_id, item)
                    for item in self.place_candidates
                )
                + tuple(additions),
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

    def with_resolutions(
        self, resolutions: tuple[PlaceResolution, ...], *, at: datetime | None = None
    ) -> "TripWorkspace":
        if not resolutions:
            raise ValueError("at least one place resolution is required")
        hypothesis_ids = [item.hypothesis_id for item in resolutions]
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("a batch may resolve each hypothesis only once")
        if any(item.workspace_version != self.version for item in resolutions):
            raise ValueError("all resolutions must be based on the current workspace version")
        hypotheses = {item.hypothesis_id: item for item in self.place_hypotheses}
        candidates = {item.candidate_id: item for item in self.place_candidates}
        for resolution in resolutions:
            hypothesis = hypotheses.get(resolution.hypothesis_id)
            if hypothesis is None:
                raise ValueError("resolution references unknown hypothesis")
            if resolution.status is ResolutionStatus.RESOLVED:
                candidate = candidates.get(resolution.candidate_id or "")
                if candidate is None or candidate.hypothesis_id != resolution.hypothesis_id:
                    raise ValueError("resolution candidate does not belong to hypothesis")
        resolution_by_hypothesis = {item.hypothesis_id: item for item in resolutions}
        status_by_resolution = {
            ResolutionStatus.RESOLVED: HypothesisStatus.RESOLVED,
            ResolutionStatus.AMBIGUOUS: HypothesisStatus.AMBIGUOUS,
            ResolutionStatus.EXCLUDED: HypothesisStatus.EXCLUDED,
            ResolutionStatus.UNRESOLVED: HypothesisStatus.OPEN,
        }
        updated_hypotheses = tuple(
            item.model_copy(
                update={
                    "status": status_by_resolution[
                        resolution_by_hypothesis[item.hypothesis_id].status
                    ]
                }
            )
            if item.hypothesis_id in resolution_by_hypothesis
            else item
            for item in self.place_hypotheses
        )
        prior = tuple(
            item
            for item in self.place_resolutions
            if item.hypothesis_id not in resolution_by_hypothesis
        )
        return self.model_copy(
            update={
                "place_hypotheses": updated_hypotheses,
                "place_resolutions": prior + resolutions,
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
