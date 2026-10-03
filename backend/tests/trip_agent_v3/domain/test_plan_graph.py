from __future__ import annotations

from datetime import date, time

import pytest
from pydantic import ValidationError

from app.trip_agent_v3.domain.plan import (
    DraftDay,
    DraftStop,
    StopKind,
    WorkingDraft,
)


def _stop(
    stop_id: str,
    candidate_id: str,
    name: str,
    kind: StopKind,
    *,
    obligation_ids: tuple[str, ...] = (),
    stay_duration_min: int = 60,
) -> DraftStop:
    return DraftStop(
        stop_id=stop_id,
        candidate_id=candidate_id,
        obligation_ids=obligation_ids,
        name=name,
        kind=kind,
        stay_duration_min=stay_duration_min,
        rationale="用户需求或当日交通锚点。",
    )


def test_named_restaurant_is_a_real_stop_not_a_meal_placeholder() -> None:
    day = DraftDay(
        day_number=1,
        calendar_date=date(2026, 8, 1),
        title="武侯祠与川味午餐",
        start_time=time(9, 0),
        stops=(
            _stop(
                "hotel-start",
                "cand-hotel",
                "成都太古里亚朵S酒店",
                StopKind.LODGING,
                stay_duration_min=0,
            ),
            _stop(
                "wuhou",
                "cand-wuhou",
                "成都武侯祠博物馆",
                StopKind.VISIT,
                obligation_ids=("obl-wuhou",),
                stay_duration_min=90,
            ),
            _stop(
                "restaurant",
                "cand-restaurant",
                "陈麻婆豆腐(骡马市店)",
                StopKind.MEAL,
                obligation_ids=("obl-restaurant",),
                stay_duration_min=60,
            ),
            _stop(
                "hotel-end",
                "cand-hotel",
                "成都太古里亚朵S酒店",
                StopKind.LODGING,
                stay_duration_min=0,
            ),
        ),
    )

    assert day.stops[2].kind is StopKind.MEAL
    assert day.stops[2].obligation_ids == ("obl-restaurant",)
    assert day.stops[2].candidate_id == "cand-restaurant"


def test_day_title_and_visible_start_end_anchors_are_required() -> None:
    with pytest.raises(ValidationError, match="title"):
        DraftDay(
            day_number=2,
            calendar_date=date(2026, 8, 2),
            title="",
            start_time=time(9, 0),
            stops=(),
        )

    with pytest.raises(ValidationError, match="lodging or airport anchor"):
        DraftDay(
            day_number=1,
            calendar_date=date(2026, 8, 1),
            title="缺少酒店锚点",
            start_time=time(9, 0),
            stops=(
                _stop(
                    "wuhou",
                    "cand-wuhou",
                    "武侯祠",
                    StopKind.VISIT,
                    obligation_ids=("obl-wuhou",),
                ),
                _stop(
                    "jinli",
                    "cand-jinli",
                    "锦里",
                    StopKind.VISIT,
                    obligation_ids=("obl-jinli",),
                ),
            ),
        )


def test_working_draft_requires_contiguous_day_numbers_and_dates() -> None:
    anchor = _stop(
        "hotel",
        "cand-hotel",
        "酒店",
        StopKind.LODGING,
        stay_duration_min=0,
    )
    with pytest.raises(ValidationError, match="contiguous"):
        WorkingDraft(
            draft_id="draft-1",
            goal_revision_id="goal-1",
            revision=1,
            days=(
                DraftDay(
                    day_number=2,
                    calendar_date=date(2026, 8, 2),
                    title="第二天",
                    start_time=time(9, 0),
                    stops=(anchor, anchor.model_copy(update={"stop_id": "hotel-2"})),
                ),
            ),
        )
