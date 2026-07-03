import pytest

from app.core import AppError
from app.services.chain_arranger import arrange_chain_near_route


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


def _direction(duration_seconds):
    return {"route": {"paths": [{"duration": str(duration_seconds), "distance": "800"}]}}
