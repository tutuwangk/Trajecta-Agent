from __future__ import annotations

from datetime import time
from hashlib import sha256
import re
from typing import Iterable

from app.trip_agent_v3.domain.query import QueryPlan, QuerySkip, QueryTarget
from app.trip_agent_v3.domain.requirements import (
    DayAssignmentRequirement,
    ConstraintStrength,
    EvidenceSpan,
    PlaceObligation,
    PlaceRole,
    PaceRequirement,
    TransportPreferenceRequirement,
    QueryDecision,
    RequirementLedger,
    TimeWindowRequirement,
)
from app.trip_agent_v3.domain.sources import (
    DayAssignmentProposal,
    EvidenceSpanProposal,
    PlaceMentionProposal,
    RequirementProposal,
    PaceProposal,
    TransportPreferenceProposal,
    SourceDocument,
    SourceKind,
    TimeWindowProposal,
)


class RequirementCompilationError(ValueError):
    """Raised when an Agent proposal is not grounded in the supplied sources."""


def _verified_fixed_commitment(flag: bool, evidence: tuple[EvidenceSpan, ...]) -> bool:
    if not flag:
        return False
    marker = re.compile(
        r"已(?:经)?(?:预约|预订|订票|购票)|(?:预约|预订|订票|购票)(?:已|已经)?(?:成功|确认|完成)|"
        r"(?:不可|不能|无法|不允许|不得).{0,6}(?:调整|改期|更改|变更)|固定.{0,6}(?:预约|时间|时刻|日期)"
    )
    negation = re.compile(r"(?:未|尚未|还没|没有|无需|不需要|取消)(?:完成)?(?:预约|预订|订票|购票)")
    return any(marker.search(span.text) is not None and negation.search(span.text) is None for span in evidence)


_NON_PLACE_TERMS = {
    "上午",
    "中午",
    "下午",
    "晚上",
    "早上",
    "第一天",
    "第二天",
    "第三天",
    "去",
    "吃",
    "喝茶",
    "逛",
    "参观",
    "打卡",
    "午餐",
    "晚餐",
    "步行",
    "打车",
    "地铁",
    "公交",
    "轻松",
    "慢节奏",
}

_GENERIC_ANCHOR_REFERENCES = {
    "酒店",
    "住宿",
    "住处",
    "机场",
}

_MEAL_TIME_WINDOWS = {
    "早餐": (time(7, 0), time(10, 30)),
    "早饭": (time(7, 0), time(10, 30)),
    "午餐": (time(11, 0), time(14, 30)),
    "中午": (time(11, 0), time(14, 30)),
    "晚餐": (time(17, 0), time(21, 30)),
    "晚饭": (time(17, 0), time(21, 30)),
}
_CLAUSE_BOUNDARIES = "，。！？；、,.!?;"


def _normalized_mention(value: str) -> str:
    return "".join(value.strip().lower().split()).strip("，。！？、；：,.!?;:")


def content_hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def source_document(
    *, source_id: str, kind: SourceKind | str, content: str
) -> SourceDocument:
    return SourceDocument(
        source_id=source_id,
        kind=kind if isinstance(kind, SourceKind) else SourceKind(kind),
        content=content,
        content_hash=content_hash(content),
    )


def _obligation_id(
    proposal: PlaceMentionProposal, evidence: tuple[EvidenceSpan, ...]
) -> str:
    identity = "|".join(
        (
            proposal.role.value,
            proposal.mention,
            *(f"{item.source_id}:{item.start}:{item.end}" for item in evidence),
        )
    )
    return f"obl_{sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _compile_evidence(
    proposal: PlaceMentionProposal,
    source_by_id: dict[str, SourceDocument],
) -> tuple[EvidenceSpan, ...]:
    evidence: list[EvidenceSpan] = []
    for span in proposal.evidence:
        source = source_by_id.get(span.source_id)
        if source is None:
            raise RequirementCompilationError(
                f"{proposal.mention_key} references unknown source {span.source_id}"
            )
        if span.end > len(source.content):
            raise RequirementCompilationError(
                f"{proposal.mention_key} evidence exceeds source bounds"
            )
        text = source.content[span.start : span.end]
        if text != proposal.mention:
            if text.count(proposal.mention) == 1:
                mention_offset = text.index(proposal.mention)
                span = span.model_copy(
                    update={
                        "start": span.start + mention_offset,
                        "end": span.start
                        + mention_offset
                        + len(proposal.mention),
                    }
                )
            elif source.content.count(proposal.mention) == 1:
                mention_start = source.content.index(proposal.mention)
                span = span.model_copy(
                    update={
                        "start": mention_start,
                        "end": mention_start + len(proposal.mention),
                    }
                )
            else:
                raise RequirementCompilationError(
                    f"{proposal.mention_key} evidence text {text!r} "
                    f"does not equal mention {proposal.mention!r} and does "
                    "not uniquely identify it in the source"
                )
            text = proposal.mention
        evidence.append(
            EvidenceSpan(
                source_id=source.source_id,
                start=span.start,
                end=span.end,
                text=text,
            )
        )
    return tuple(evidence)


def _compile_constraint_evidence(
    *,
    proposal_key: str,
    spans: tuple[EvidenceSpanProposal, ...],
    source_by_id: dict[str, SourceDocument],
) -> tuple[EvidenceSpan, ...]:
    evidence: list[EvidenceSpan] = []
    for span in spans:
        source = source_by_id.get(span.source_id)
        if source is None:
            raise RequirementCompilationError(
                f"{proposal_key} references unknown source {span.source_id}"
            )
        if span.end > len(source.content):
            raise RequirementCompilationError(
                f"{proposal_key} evidence exceeds source bounds"
            )
        text = source.content[span.start : span.end]
        if not text:
            raise RequirementCompilationError(
                f"{proposal_key} evidence cannot be empty"
            )
        evidence.append(
            EvidenceSpan(
                source_id=source.source_id,
                start=span.start,
                end=span.end,
                text=text,
            )
        )
    return tuple(evidence)


def _contextual_anchor_root(
    proposal: PlaceMentionProposal,
    proposals_by_key: dict[str, PlaceMentionProposal],
) -> str | None:
    if (
        proposal.query_decision is not QueryDecision.MERGE
        or _normalized_mention(proposal.mention)
        not in _GENERIC_ANCHOR_REFERENCES
        or proposal.merge_into_mention_key is None
    ):
        return None
    root = proposals_by_key.get(proposal.merge_into_mention_key)
    if (
        root is None
        or root.query_decision is not QueryDecision.QUERY
        or root.role is not proposal.role
    ):
        return None
    return root.mention_key


def _subject_obligation_ids(
    mention_keys: Iterable[str],
    obligation_id_by_mention_key: dict[str, str],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            obligation_id_by_mention_key[key] for key in mention_keys
        )
    )


def _constraint_id(
    proposal_key: str, evidence: tuple[EvidenceSpan, ...]
) -> str:
    identity = "|".join(
        (
            proposal_key,
            *(f"{item.source_id}:{item.start}:{item.end}" for item in evidence),
        )
    )
    return f"constraint_{sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _infer_meal_time_constraint(
    *,
    obligation: PlaceObligation,
    source_by_id: dict[str, SourceDocument],
) -> TimeWindowRequirement | None:
    best: tuple[int, str, EvidenceSpan] | None = None
    for mention_span in obligation.evidence:
        source = source_by_id[mention_span.source_id]
        content = source.content
        left_boundary = max(
            (content.rfind(mark, 0, mention_span.start) for mark in _CLAUSE_BOUNDARIES),
            default=-1,
        )
        right_positions = [
            position
            for mark in _CLAUSE_BOUNDARIES
            if (position := content.find(mark, mention_span.end)) >= 0
        ]
        right_boundary = min(right_positions, default=len(content))
        local_start = max(left_boundary + 1, mention_span.start - 12)
        local_end = min(right_boundary, mention_span.end + 12)
        for cue in _MEAL_TIME_WINDOWS:
            cursor = local_start
            while (position := content.find(cue, cursor, local_end)) >= 0:
                distance = min(
                    abs(position - mention_span.end),
                    abs(mention_span.start - (position + len(cue))),
                )
                evidence = EvidenceSpan(
                    source_id=source.source_id,
                    start=position,
                    end=position + len(cue),
                    text=cue,
                )
                candidate = (distance, cue, evidence)
                if best is None or candidate[0] < best[0]:
                    best = candidate
                cursor = position + len(cue)
    if best is None:
        return None
    _, cue, evidence = best
    earliest, latest = _MEAL_TIME_WINDOWS[cue]
    return TimeWindowRequirement(
        constraint_id=_constraint_id(
            f"inferred-meal-slot:{obligation.obligation_id}:{cue}",
            (evidence,),
        ),
        subject_obligation_ids=(obligation.obligation_id,),
        evidence=(evidence,),
        strength=ConstraintStrength.REQUIRED,
        earliest=earliest,
        latest=latest,
    )


def compile_requirement_ledger(
    *,
    ledger_id: str,
    revision: int,
    sources: Iterable[SourceDocument],
    proposal: RequirementProposal,
) -> RequirementLedger:
    source_items = tuple(sources)
    source_by_id = {source.source_id: source for source in source_items}
    if len(source_by_id) != len(source_items):
        raise RequirementCompilationError("source ids must be unique")
    for source in source_items:
        if content_hash(source.content) != source.content_hash:
            raise RequirementCompilationError(
                f"source content hash mismatch: {source.source_id}"
            )

    compiled: list[PlaceObligation] = []
    obligation_id_by_mention_key: dict[str, str] = {}
    evidence_by_mention_key: dict[str, tuple[EvidenceSpan, ...]] = {}
    mentions_by_key = {
        mention.mention_key: mention for mention in proposal.mentions
    }
    for mention in proposal.mentions:
        if (
            mention.query_decision is QueryDecision.QUERY
            and _normalized_mention(mention.mention) in _NON_PLACE_TERMS
        ):
            raise RequirementCompilationError(
                f"{mention.mention_key} is an obvious non-place term and "
                "cannot enter a provider query"
            )
        evidence = _compile_evidence(mention, source_by_id)
        obligation_id = _obligation_id(mention, evidence)
        obligation_id_by_mention_key[mention.mention_key] = obligation_id
        evidence_by_mention_key[mention.mention_key] = evidence
    contextual_anchor_roots = {
        mention.mention_key: root_key
        for mention in proposal.mentions
        if (
            root_key := _contextual_anchor_root(
                mention, mentions_by_key
            )
        )
        is not None
    }
    for mention_key, root_key in contextual_anchor_roots.items():
        obligation_id_by_mention_key[mention_key] = (
            obligation_id_by_mention_key[root_key]
        )

    query_mentions: dict[tuple[str, str], list[str]] = {}
    for mention in proposal.mentions:
        if mention.query_decision is not QueryDecision.QUERY:
            continue
        query_mentions.setdefault(
            (_normalized_mention(mention.mention), mention.role.value),
            [],
        ).append(mention.mention_key)
    duplicate_queries = {
        identity: keys
        for identity, keys in query_mentions.items()
        if len(keys) > 1
    }
    if duplicate_queries:
        raise RequirementCompilationError(
            "duplicate place mentions must be explicitly merged before "
            f"provider search: {duplicate_queries}"
        )

    for mention in proposal.mentions:
        if mention.mention_key in contextual_anchor_roots:
            continue
        compiled.append(
            PlaceObligation(
                obligation_id=obligation_id_by_mention_key[mention.mention_key],
                proposal_key=mention.mention_key,
                mention=mention.mention,
                role=mention.role,
                priority=mention.priority,
                evidence=evidence_by_mention_key[mention.mention_key],
                explicit=mention.explicit,
                query_decision=mention.query_decision,
                query_text=mention.query_text,
                decision_reason=mention.decision_reason,
                merged_into_obligation_id=(
                    obligation_id_by_mention_key[mention.merge_into_mention_key]
                    if mention.merge_into_mention_key
                    else None
                ),
                aliases=mention.aliases,
                category_hints=mention.category_hints,
            )
        )

    constraints: list[
        TimeWindowRequirement
        | DayAssignmentRequirement
        | PaceRequirement
        | TransportPreferenceRequirement
    ] = []
    for constraint in proposal.constraints:
        evidence = _compile_constraint_evidence(
            proposal_key=constraint.proposal_key,
            spans=constraint.evidence,
            source_by_id=source_by_id,
        )
        common = {
            "constraint_id": _constraint_id(
                constraint.proposal_key, evidence
            ),
            "evidence": evidence,
            "strength": constraint.strength,
        }
        if isinstance(constraint, TimeWindowProposal):
            constraints.append(
                TimeWindowRequirement(
                    **common,
                    subject_obligation_ids=_subject_obligation_ids(
                        constraint.subject_mention_keys,
                        obligation_id_by_mention_key,
                    ),
                    day_number=constraint.day_number,
                    earliest=constraint.earliest,
                    latest=constraint.latest,
                    fixed_commitment=_verified_fixed_commitment(constraint.fixed_commitment, evidence),
                )
            )
        elif isinstance(constraint, DayAssignmentProposal):
            constraints.append(
                DayAssignmentRequirement(
                    **common,
                    subject_obligation_ids=_subject_obligation_ids(
                        constraint.subject_mention_keys,
                        obligation_id_by_mention_key,
                    ),
                    day_number=constraint.day_number,
                    fixed_commitment=_verified_fixed_commitment(constraint.fixed_commitment, evidence),
                )
            )
        elif isinstance(constraint, PaceProposal):
            constraints.append(
                PaceRequirement(
                    **common,
                    max_day_minutes=constraint.max_day_minutes,
                )
            )
        elif isinstance(constraint, TransportPreferenceProposal):
            constraints.append(
                TransportPreferenceRequirement(
                    **common,
                    preferred_modes=constraint.preferred_modes,
                    mode_policy=constraint.mode_policy,
                    max_walk_minutes=constraint.max_walk_minutes,
                )
            )

    constrained_time_obligations = {
        obligation_id
        for constraint in constraints
        if isinstance(constraint, TimeWindowRequirement)
        for obligation_id in constraint.subject_obligation_ids
    }
    for obligation in compiled:
        if (
            obligation.role is not PlaceRole.MEAL
            or obligation.obligation_id in constrained_time_obligations
        ):
            continue
        inferred = _infer_meal_time_constraint(
            obligation=obligation,
            source_by_id=source_by_id,
        )
        if inferred is not None:
            constraints.append(inferred)

    return RequirementLedger(
        ledger_id=ledger_id,
        goal_revision_id=proposal.goal_revision_id,
        revision=revision,
        obligations=tuple(compiled),
        constraints=tuple(constraints),
    )


def build_query_plan(
    *,
    plan_id: str,
    ledger: RequirementLedger,
    destination: str,
) -> QueryPlan:
    targets: list[QueryTarget] = []
    skips: list[QuerySkip] = []
    merged_by_root: dict[str, list[str]] = {}
    for obligation in ledger.obligations:
        if obligation.query_decision is QueryDecision.MERGE:
            root_id = obligation.merged_into_obligation_id
            if root_id is None:
                raise RequirementCompilationError(
                    "merge obligation lost its root identity"
                )
            merged_by_root.setdefault(root_id, []).append(
                obligation.obligation_id
            )
    for obligation in ledger.obligations:
        if obligation.query_decision is QueryDecision.QUERY:
            targets.append(
                QueryTarget(
                    target_id=f"qry_{obligation.obligation_id[4:]}",
                    obligation_ids=(
                        obligation.obligation_id,
                        *merged_by_root.get(obligation.obligation_id, []),
                    ),
                    query_text=obligation.query_text or obligation.mention,
                    destination=destination,
                    category_hints=obligation.category_hints,
                    max_candidates=3,
                    rationale="Only query an explicitly approved place obligation.",
                )
            )
        elif obligation.query_decision is not QueryDecision.MERGE:
            skips.append(
                QuerySkip(
                    obligation_id=obligation.obligation_id,
                    decision=obligation.query_decision,
                    reason_code=f"decision_{obligation.query_decision.value}",
                    rationale=obligation.decision_reason
                    or "This obligation does not require a map-provider query.",
                )
            )
    plan = QueryPlan(
        plan_id=plan_id,
        ledger_id=ledger.ledger_id,
        ledger_revision=ledger.revision,
        targets=tuple(targets),
        skips=tuple(skips),
    )
    plan.validate_against(ledger)
    return plan
