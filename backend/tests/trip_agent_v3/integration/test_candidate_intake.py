from __future__ import annotations

from app.trip_agent_v3.candidates import intake_provider_candidates
from app.trip_agent_v3.domain.grounding import (
    CandidateEntityKind,
    ResolutionStatus,
)
from app.trip_agent_v3.domain.query import QueryPlan, QueryTarget
from app.trip_agent_v3.grounding import (
    GroundingDecisionProposal,
    compile_grounding_registry,
)


def test_candidate_intake_filters_provider_noise_before_agent_context() -> None:
    target = QueryTarget(
        target_id="query-wuhou",
        obligation_ids=("obl-wuhou",),
        query_text="武侯祠",
        destination="成都",
        max_candidates=3,
        rationale="用户明确地点。",
    )
    provider_results = (
        {
            "id": "B-entrance",
            "name": "武侯祠东门",
            "address": "武侯祠大街",
            "type": "通行设施;门",
            "location": "104.04,30.64",
        },
        {
            "id": "B-subway",
            "name": "高升桥地铁站",
            "address": "地铁3号线",
            "type": "交通设施;地铁站",
            "location": "104.04,30.64",
        },
        {
            "id": "B-main",
            "name": "成都武侯祠博物馆",
            "address": "武侯祠大街231号",
            "type": "风景名胜;博物馆",
            "location": "104.04,30.64",
            "biz_ext": {
                "opentime2": "周一至周日 08:30-18:30 最晚进入17:30"
            },
        },
        {
            "id": "B-street",
            "name": "武侯祠东街",
            "address": "武侯区",
            "type": "地名地址;道路",
            "location": "104.03,30.64",
        },
        {
            "id": "B-shop",
            "name": "武侯祠文创商店",
            "address": "景区内",
            "type": "购物服务",
            "location": "104.04,30.64",
        },
        {
            "id": "B-hotel",
            "name": "武侯祠亚朵酒店",
            "address": "武侯区",
            "type": "住宿服务",
            "location": "104.02,30.63",
        },
        {
            "id": "B-invalid-location",
            "name": "武侯祠测试点",
            "address": "武侯区",
            "type": "风景名胜",
            "location": "",
        },
    )

    candidate_set = intake_provider_candidates(
        target=target,
        provider="amap",
        results=provider_results,
    )

    assert [item.provider_place_id for item in candidate_set.candidates] == [
        "B-main",
        "B-street",
        "B-hotel",
    ]
    assert len(candidate_set.candidates) == 3
    assert (
        candidate_set.candidates[0].opening_hours
        == "周一至周日 08:30-18:30 最晚进入17:30"
    )
    assert candidate_set.provider_result_count == 7
    assert candidate_set.excluded_result_count == 4
    assert candidate_set.exclusion_reasons == {
        "entrance": 1,
        "transit": 1,
        "auxiliary": 1,
        "invalid_coordinates": 1,
    }
    assert all(
        item.entity_kind
        not in {
            CandidateEntityKind.ENTRANCE,
            CandidateEntityKind.TRANSIT,
            CandidateEntityKind.AUXILIARY,
        }
        for item in candidate_set.candidates
    )


def test_agent_decision_compiles_to_provider_bound_grouped_resolution() -> None:
    target = QueryTarget(
        target_id="query-restaurant",
        obligation_ids=("obl-restaurant",),
        query_text="陈麻婆豆腐",
        destination="成都",
        rationale="指定餐厅必须成为真实停靠点。",
    )
    plan = QueryPlan(
        plan_id="query-plan-1",
        ledger_id="ledger-1",
        ledger_revision=1,
        targets=(target,),
    )
    candidate_set = intake_provider_candidates(
        target=target,
        provider="amap",
        results=(
            {
                "id": "B-main",
                "name": "陈麻婆豆腐(骡马市店)",
                "address": "青羊区",
                "type": "餐饮服务;中餐厅",
                "location": "104.07,30.67",
            },
            {
                "id": "B-other",
                "name": "陈麻婆豆腐(太古里店)",
                "address": "锦江区",
                "type": "餐饮服务;中餐厅",
                "location": "104.08,30.65",
            },
        ),
    )

    registry = compile_grounding_registry(
        registry_id="grounding-1",
        query_plan=plan,
        candidate_sets=(candidate_set,),
        decisions=(
            GroundingDecisionProposal(
                target_id="query-restaurant",
                status=ResolutionStatus.NEEDS_CONFIRMATION,
                rationale="资料只给出品牌名，两家分店都会显著改变当天路线。",
                confidence=0.55,
                provider_place_ids_requiring_confirmation=("B-main", "B-other"),
                clarification_question="你想去骡马市店还是太古里店？",
            ),
        ),
    )

    assert registry.query_plan_id == plan.plan_id
    assert registry.resolutions[0].status is ResolutionStatus.NEEDS_CONFIRMATION
    assert len(
        registry.resolutions[0].candidate_ids_requiring_confirmation
    ) == 2
