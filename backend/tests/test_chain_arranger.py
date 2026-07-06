import pytest

from app.core import AppError
from app.services import chain_arranger
from app.services.chain_arranger import arrange_chain_near_route, prepare_chain_for_planning


class FakeAmapClient:
    def __init__(self, durations):
        self.durations = durations
        self.calls = []

    def walking_direction(self, origin, destination):
        self.calls.append(("walking", origin, destination))
        return _direction(self.durations[(origin, destination)])

    def driving_direction(self, origin, destination):
        self.calls.append(("driving", origin, destination))
        return _direction(self.durations[(origin, destination)])


def test_arrange_chain_near_route_selects_lowest_detour_candidate():
    chain_poi = {
        "raw_name": "星巴克",
        "standard_name": "星巴克（待选择）",
        "is_chain": True,
        "candidate_options": [
            {
                "id": "S1",
                "name": "星巴克(成都太古里店)",
                "address": "太古里",
                "location": {"lng": 104.081, "lat": 30.655},
                "city": "成都市",
                "district": "锦江区",
                "category_raw": "餐饮服务;咖啡厅;星巴克咖啡",
            },
            {
                "id": "S2",
                "name": "星巴克(成都IFS店)",
                "address": "IFS",
                "location": {"lng": 104.0805, "lat": 30.6572},
                "city": "成都市",
                "district": "锦江区",
                "category_raw": "餐饮服务;咖啡厅;星巴克咖啡",
            },
        ],
        "contexts": ["想喝咖啡"],
        "experience_tags": [],
    }
    previous_grounded = {
        "standard_name": "成都IFS国际金融中心",
        "location": {"lng": 104.080, "lat": 30.657},
    }
    next_grounded = {
        "standard_name": "武侯祠",
        "location": {"lng": 104.047, "lat": 30.645},
    }
    durations = {
        ("104.08,30.657", "104.081,30.655"): 12 * 60,
        ("104.081,30.655", "104.047,30.645"): 35 * 60,
        ("104.08,30.657", "104.0805,30.6572"): 4 * 60,
        ("104.0805,30.6572", "104.047,30.645"): 28 * 60,
    }

    arranged = arrange_chain_near_route(
        chain_poi,
        {"previous_grounded": previous_grounded, "next_grounded": next_grounded},
        FakeAmapClient(durations),
    )

    assert arranged["standard_name"] == "星巴克(成都IFS店)"
    assert arranged["amap_id"] == "S2"
    assert arranged["match_status"] == "matched"
    assert arranged["selection_mode"] == "arranged_nearby"
    assert arranged["arranged_by"] == "route_context"
    assert arranged["detour_minutes"] == 32


def test_arrange_chain_near_route_rejects_non_chain_place():
    with pytest.raises(AppError) as error:
        arrange_chain_near_route(
            {"raw_name": "武侯祠", "is_chain": False, "candidate_options": []},
            {"previous_grounded": {}, "next_grounded": {}},
            FakeAmapClient({}),
        )

    assert error.value.code == "not_chain_place"


def test_arrange_chain_to_anchor_resolves_nearest_branch():
    resolver = getattr(chain_arranger, "arrange_chain_to_anchor", None)
    assert callable(resolver)

    chain_poi = {
        "raw_name": "星巴克",
        "standard_name": "星巴克（待选择）",
        "is_chain": True,
        "chain_status": "unresolved",
        "candidate_options": [
            {
                "id": "S1",
                "name": "星巴克(成都太古里店)",
                "address": "太古里",
                "location": {"lng": 104.081, "lat": 30.655},
                "city": "成都市",
                "district": "锦江区",
                "category_raw": "餐饮服务;咖啡厅;星巴克咖啡",
            },
            {
                "id": "S2",
                "name": "星巴克(成都IFS店)",
                "address": "IFS",
                "location": {"lng": 104.0805, "lat": 30.6572},
                "city": "成都市",
                "district": "锦江区",
                "category_raw": "餐饮服务;咖啡厅;星巴克咖啡",
            },
        ],
    }
    anchor_poi = {
        "poi_id": "amap_I1",
        "standard_name": "成都IFS国际金融中心",
        "location": {"lng": 104.08, "lat": 30.657},
        "final_decision": "optional",
    }
    durations = {
        ("104.08,30.657", "104.081,30.655"): 12 * 60,
        ("104.08,30.657", "104.0805,30.6572"): 4 * 60,
    }

    arranged = resolver(chain_poi, anchor_poi, FakeAmapClient(durations))

    assert arranged["standard_name"] == "星巴克(成都IFS店)"
    assert arranged["amap_id"] == "S2"
    assert arranged["match_status"] == "matched"
    assert arranged["chain_status"] == "resolved"
    assert arranged["resolved_branch_id"] == "S2"
    assert arranged["resolved_from_anchor_poi_id"] == "amap_I1"


def test_prepare_chain_for_planning_scores_branches_against_anchor_places():
    chain_poi = {
        "raw_name": "喜茶",
        "standard_name": "喜茶（待选择）",
        "is_chain": True,
        "candidate_options": [
            {
                "id": "H1",
                "name": "喜茶(IFS店)",
                "address": "IFS",
                "location": {"lng": 104.0805, "lat": 30.6572},
                "city": "成都市",
                "district": "锦江区",
                "category_raw": "餐饮服务;冷饮店;喜茶",
            },
            {
                "id": "H2",
                "name": "喜茶(武侯祠店)",
                "address": "武侯祠",
                "location": {"lng": 104.047, "lat": 30.645},
                "city": "成都市",
                "district": "武侯区",
                "category_raw": "餐饮服务;冷饮店;喜茶",
            },
        ],
        "contexts": ["下午想喝奶茶"],
        "experience_tags": [],
    }
    anchors = [
        {"poi_id": "p1", "standard_name": "IFS", "location": {"lng": 104.08, "lat": 30.657}},
        {"poi_id": "p2", "standard_name": "太古里", "location": {"lng": 104.081, "lat": 30.655}},
    ]
    durations = {
        ("104.08,30.657", "104.0805,30.6572"): 4 * 60,
        ("104.081,30.655", "104.0805,30.6572"): 8 * 60,
        ("104.08,30.657", "104.047,30.645"): 28 * 60,
        ("104.081,30.655", "104.047,30.645"): 35 * 60,
    }

    prepared = prepare_chain_for_planning(chain_poi, anchors, FakeAmapClient(durations))

    assert prepared["standard_name"] == "喜茶（待选择）"
    assert prepared["match_status"] == "ambiguous"
    assert prepared["selection_mode"] == "route_dependent_chain"
    assert prepared["chain_resolution_mode"] == "route_dependent_chain"
    assert prepared["route_branch_options"][0]["branch_id"] == "H1"
    assert prepared["route_branch_options"][0]["anchor_poi_ids"] == ["p1", "p2"]
    assert prepared["route_branch_options"][0]["quick_stop_total_cost_min"] == 27
    assert prepared["route_branch_options"][0]["meal_stop_total_cost_min"] == 72


def _direction(duration_seconds):
    return {"route": {"paths": [{"duration": str(duration_seconds), "distance": "800"}]}}
