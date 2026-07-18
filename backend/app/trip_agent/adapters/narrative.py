from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

from pydantic_ai import Agent, UsageLimits

from app.trip_agent.adapters.provider import build_deepseek_v4_model, deepseek_v4_settings
from app.trip_agent.domain import (
    DomainModel,
    NarrativeDay,
    NarrativeVisit,
    ReleaseNarrative,
    ReleaseRecord,
    TripWorkspace,
    utc_now,
)
from app.trip_agent.validation import SimulationReport


CopyStyle = Literal["balanced", "relaxed", "focused", "cautious"]


class NarrativeChoices(DomainModel):
    overview_style: CopyStyle
    day_styles: tuple[CopyStyle, ...]
    visit_styles: tuple[CopyStyle, ...]


_OVERVIEWS: dict[CopyStyle, str] = {
    "balanced": "路线事实已冻结，建议按发布顺序执行，并为现场变化保留合理弹性。",
    "relaxed": "路线事实已冻结，可在不破坏硬约束的前提下，根据现场体感适当放慢节奏。",
    "focused": "路线事实已冻结，执行时优先保护已确认地点和不可调整承诺。",
    "cautious": "路线事实已冻结；存在已标注的不确定项，出发前应再次查看风险说明。",
}

_DAY_COPY: dict[CopyStyle, tuple[str, str]] = {
    "balanced": ("均衡推进", "按已发布的地点顺序游览，交通与停留时间以冻结时间线为准。"),
    "relaxed": ("从容体验", "按冻结路线执行；现场调整不得造成后续时间重叠或错过固定承诺。"),
    "focused": ("重点优先", "优先保护已确认地点，其余体验调整以不改变发布事实为边界。"),
    "cautious": ("稳妥执行", "先核对风险项，再按冻结时间线推进；事实不足处不要自行升级为已核验。"),
}

_VISIT_COPY: dict[CopyStyle, str] = {
    "balanced": "按已发布时长停留，调整时同时检查后续时间线。",
    "relaxed": "可按现场体感微调，但不得破坏后续硬约束。",
    "focused": "优先完成该访问，地点身份和发布时长保持不变。",
    "cautious": "执行前核对关联风险，未核验信息不要当作确定事实。",
}


class DeepSeekNarrativeGenerator:
    """The model selects copy styles; deterministic templates own all visible prose."""

    async def generate(
        self,
        release: ReleaseRecord,
        workspace: TripWorkspace,
        simulation: SimulationReport,
    ) -> ReleaseNarrative:
        assert workspace.current_draft is not None
        visits = [visit for day in workspace.current_draft.days for visit in day.visits]
        agent = Agent(
            build_deepseek_v4_model("lightweight"),
            output_type=NarrativeChoices,
            model_settings=deepseek_v4_settings("lightweight"),
            retries=2,
            instructions=(
                "Select only copy style enums for a released itinerary. Do not write prose. "
                "Use cautious when issue codes indicate uncertainty. Return exactly the requested counts."
            ),
        )
        result = await agent.run(
            json.dumps(
                {
                    "day_count": len(workspace.current_draft.days),
                    "visit_count": len(visits),
                    "issue_codes": list(release.issue_codes),
                },
                ensure_ascii=False,
            ),
            usage_limits=UsageLimits(request_limit=4),
        )
        choices = result.output
        if len(choices.day_styles) != len(workspace.current_draft.days):
            raise ValueError("narrative day style count does not match release")
        if len(choices.visit_styles) != len(visits):
            raise ValueError("narrative visit style count does not match release")
        return ReleaseNarrative(
            narrative_id=f"narrative-{uuid4()}",
            release_id=release.release_id,
            route_fact_fingerprint=release.route_fact_fingerprint,
            overview=_OVERVIEWS[choices.overview_style],
            days=tuple(
                NarrativeDay(
                    day_index=day.day_index,
                    theme=_DAY_COPY[style][0],
                    summary=_DAY_COPY[style][1],
                )
                for day, style in zip(
                    workspace.current_draft.days, choices.day_styles, strict=True
                )
            ),
            visits=tuple(
                NarrativeVisit(visit_id=visit.visit_id, note=_VISIT_COPY[style])
                for visit, style in zip(visits, choices.visit_styles, strict=True)
            ),
            risk_notes=tuple(release.issue_codes),
            generator="deepseek-v4-flash-style-selector-v1",
            created_at=utc_now(),
        )
