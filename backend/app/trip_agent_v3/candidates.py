from __future__ import annotations

from collections import Counter
from hashlib import sha256
from typing import Mapping, Sequence

from app.trip_agent_v3.domain.grounding import (
    CandidateEntityKind,
    CandidateSet,
    GroundingCandidate,
)
from app.trip_agent_v3.domain.query import QueryTarget


class CandidateIntakeError(ValueError):
    """Raised when a provider result cannot satisfy the grounding contract."""


def _entity_kind(name: str, category: str) -> CandidateEntityKind:
    normalized = f"{name}|{category}".lower()
    if any(
        token in normalized
        for token in ("地铁站", "公交站", "交通设施;地铁", "交通设施;公交")
    ):
        return CandidateEntityKind.TRANSIT
    if any(
        token in normalized
        for token in ("入口", "出口", "东门", "西门", "南门", "北门", "通行设施;门")
    ):
        return CandidateEntityKind.ENTRANCE
    if any(
        token in normalized
        for token in ("停车场", "售票处", "游客中心", "文创商店", "卫生间")
    ):
        return CandidateEntityKind.AUXILIARY
    if any(token in normalized for token in ("分店", "店)", "店）")):
        return CandidateEntityKind.BRANCH
    return CandidateEntityKind.PLACE


def _coordinates(raw: object) -> tuple[float, float]:
    if isinstance(raw, str):
        parts = raw.split(",", maxsplit=1)
        if len(parts) == 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError as exc:
                raise CandidateIntakeError(
                    f"invalid provider location: {raw!r}"
                ) from exc
    if isinstance(raw, Mapping):
        longitude = raw.get("lng", raw.get("longitude"))
        latitude = raw.get("lat", raw.get("latitude"))
        if longitude is not None and latitude is not None:
            try:
                return float(longitude), float(latitude)
            except (TypeError, ValueError) as exc:
                raise CandidateIntakeError(
                    f"invalid provider location: {raw!r}"
                ) from exc
    raise CandidateIntakeError(f"missing provider location: {raw!r}")


def _candidate_id(provider: str, provider_place_id: str) -> str:
    digest = sha256(
        f"{provider}|{provider_place_id}".encode("utf-8")
    ).hexdigest()[:20]
    return f"cand_{digest}"


def _provider_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def intake_provider_candidates(
    *,
    target: QueryTarget,
    provider: str,
    results: Sequence[Mapping[str, object]],
) -> CandidateSet:
    retained: list[GroundingCandidate] = []
    eligible_count = 0
    exclusion_reasons: Counter[str] = Counter()
    seen_provider_ids: set[str] = set()

    for raw in results:
        provider_place_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        category = str(raw.get("type") or raw.get("category") or "").strip()
        if not provider_place_id or not name:
            exclusion_reasons["invalid"] += 1
            continue
        if provider_place_id in seen_provider_ids:
            exclusion_reasons["duplicate"] += 1
            continue
        seen_provider_ids.add(provider_place_id)
        kind = _entity_kind(name, category)
        if kind in {
            CandidateEntityKind.ENTRANCE,
            CandidateEntityKind.TRANSIT,
            CandidateEntityKind.AUXILIARY,
        }:
            exclusion_reasons[kind.value] += 1
            continue
        try:
            longitude, latitude = _coordinates(raw.get("location"))
        except CandidateIntakeError:
            exclusion_reasons["invalid_coordinates"] += 1
            continue
        eligible_count += 1
        if len(retained) >= target.max_candidates:
            continue
        business = raw.get("business")
        if not isinstance(business, Mapping):
            business = {}
        biz_ext = raw.get("biz_ext")
        if not isinstance(biz_ext, Mapping):
            biz_ext = {}
        retained.append(
            GroundingCandidate(
                candidate_id=_candidate_id(provider, provider_place_id),
                provider=provider,
                provider_place_id=provider_place_id,
                name=name,
                address=str(raw.get("address") or "").strip() or None,
                category=category or None,
                business_status=_provider_text(
                    business.get("business_status"),
                    raw.get("business_status"),
                ),
                opening_hours=_provider_text(
                    business.get("opentime_today"),
                    business.get("opentime_week"),
                    biz_ext.get("opentime2"),
                    biz_ext.get("open_time"),
                ),
                entity_kind=kind,
                longitude=longitude,
                latitude=latitude,
            )
        )

    return CandidateSet(
        target_id=target.target_id,
        obligation_ids=target.obligation_ids,
        query_text=target.query_text,
        candidates=tuple(retained),
        provider_result_count=len(results),
        eligible_result_count=eligible_count,
        excluded_result_count=sum(exclusion_reasons.values()),
        truncated_result_count=max(eligible_count - len(retained), 0),
        exclusion_reasons=dict(exclusion_reasons),
    )
