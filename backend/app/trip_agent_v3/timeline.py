from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Iterable

from app.trip_agent_v3.domain.facts import (
    FactResolutionStatus,
    RouteFact,
    RouteFactSource,
)
from app.trip_agent_v3.domain.plan import (
    CompiledTimeline,
    DayTimeline,
    TimelineLeg,
    TimelineStop,
    WorkingDraft,
)


class TimelineCompilationError(ValueError):
    """Raised when deterministic timeline compilation cannot complete."""


def _leg_id(day_number: int, from_stop_id: str, to_stop_id: str) -> str:
    digest = sha256(
        f"{day_number}|{from_stop_id}|{to_stop_id}".encode("utf-8")
    ).hexdigest()[:20]
    return f"leg_{digest}"


def compile_timeline(
    *,
    snapshot_id: str,
    draft: WorkingDraft,
    route_facts: Iterable[RouteFact],
) -> CompiledTimeline:
    facts = tuple(route_facts)
    by_pair: dict[tuple[str, str], RouteFact] = {}
    by_stop_pair: dict[tuple[int, str, str], RouteFact] = {}
    for fact in facts:
        if (
            fact.origin_stop_id is not None
            and fact.destination_stop_id is not None
            and fact.day_number is not None
        ):
            stop_key = (
                fact.day_number,
                fact.origin_stop_id,
                fact.destination_stop_id,
            )
            if stop_key in by_stop_pair:
                raise TimelineCompilationError(
                    f"duplicate route fact for stop leg {stop_key}"
                )
            by_stop_pair[stop_key] = fact
            continue
        key = (fact.origin_candidate_id, fact.destination_candidate_id)
        if key in by_pair:
            raise TimelineCompilationError(
                f"duplicate route fact for {key[0]} -> {key[1]}"
            )
        by_pair[key] = fact

    compiled_days: list[DayTimeline] = []
    for day in draft.days:
        current = datetime.combine(day.calendar_date, day.start_time)
        compiled_stops: list[TimelineStop] = []
        compiled_legs: list[TimelineLeg] = []
        for index, stop in enumerate(day.stops):
            arrival_at = current
            departure_at = arrival_at + timedelta(
                minutes=stop.stay_duration_min
            )
            compiled_stops.append(
                TimelineStop(
                    stop_id=stop.stop_id,
                    candidate_id=stop.candidate_id,
                    obligation_ids=stop.obligation_ids,
                    name=stop.name,
                    kind=stop.kind,
                    arrival_at=arrival_at,
                    departure_at=departure_at,
                    stay_duration_min=stop.stay_duration_min,
                )
            )
            if index == len(day.stops) - 1:
                current = departure_at
                continue
            next_stop = day.stops[index + 1]
            if stop.candidate_id == next_stop.candidate_id:
                compiled_legs.append(
                    TimelineLeg(
                        leg_id=_leg_id(
                            day.day_number,
                            stop.stop_id,
                            next_stop.stop_id,
                        ),
                        from_stop_id=stop.stop_id,
                        to_stop_id=next_stop.stop_id,
                        departure_at=departure_at,
                        arrival_at=departure_at,
                        duration_min=0,
                        mode="stay",
                        fact_id=(
                            f"same_place_{stop.candidate_id}"
                        )[:200],
                        fact_source=RouteFactSource.SAME_PLACE,
                        fact_status=FactResolutionStatus.VERIFIED,
                    )
                )
                current = departure_at
                continue
            fact = by_stop_pair.get(
                (day.day_number, stop.stop_id, next_stop.stop_id)
            ) or by_pair.get((stop.candidate_id, next_stop.candidate_id))
            if (
                fact is None
                or fact.status
                not in {
                    FactResolutionStatus.VERIFIED,
                    FactResolutionStatus.ESTIMATED,
                }
            ):
                raise TimelineCompilationError(
                    "missing route fact for "
                    f"day {day.day_number}: {stop.name} -> {next_stop.name}"
                )
            duration_min = fact.duration_min
            mode = fact.mode
            if duration_min is None or mode is None:
                raise TimelineCompilationError(
                    f"route fact {fact.fact_id} has no usable values"
                )
            leg_arrival = departure_at + timedelta(minutes=duration_min)
            compiled_legs.append(
                TimelineLeg(
                    leg_id=_leg_id(
                        day.day_number, stop.stop_id, next_stop.stop_id
                    ),
                    from_stop_id=stop.stop_id,
                    to_stop_id=next_stop.stop_id,
                    departure_at=departure_at,
                    arrival_at=leg_arrival,
                    duration_min=duration_min,
                    mode=mode,
                    fact_id=fact.fact_id,
                    fact_source=fact.source,
                    fact_status=fact.status,
                )
            )
            current = leg_arrival
        compiled_days.append(
            DayTimeline(
                day_number=day.day_number,
                calendar_date=day.calendar_date,
                title=day.title,
                stops=tuple(compiled_stops),
                legs=tuple(compiled_legs),
            )
        )
    return CompiledTimeline(
        snapshot_id=snapshot_id,
        draft_id=draft.draft_id,
        draft_revision=draft.revision,
        days=tuple(compiled_days),
    )
