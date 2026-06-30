from app.services.poi_grounder import ground_single_poi


class FakeAmapClient:
    def __init__(self, results):
        self.results = results

    def search_poi(self, keyword, city=None):
        return self.results


def test_ground_single_poi_marks_matched_for_high_confidence_candidate():
    raw_poi = {"raw_name": "太古里", "possible_category": "shopping_mall", "contexts": ["商圈"]}
    client = FakeAmapClient(
        [
            {
                "id": "B001",
                "name": "成都远洋太古里",
                "address": "成都市锦江区",
                "location": "104.080,30.657",
                "cityname": "成都市",
                "adname": "锦江区",
                "type": "购物服务;商场;购物中心",
            }
        ]
    )

    grounded = ground_single_poi(raw_poi, {"destination": "成都"}, client)

    assert grounded["match_status"] == "matched"
    assert grounded["standard_name"] == "成都远洋太古里"
    assert grounded["match_confidence"] >= 0.8


def test_ground_single_poi_marks_unmatched_when_amap_returns_no_results():
    grounded = ground_single_poi(
        {"raw_name": "春熙路旁边的咖啡店", "contexts": []},
        {"destination": "成都"},
        FakeAmapClient([]),
    )

    assert grounded["match_status"] == "unmatched"
    assert grounded["standard_name"] == ""


def test_ground_single_poi_handles_equal_score_candidates():
    raw_poi = {"raw_name": "武侯祠", "possible_category": "attraction", "contexts": ["必去"]}
    client = FakeAmapClient(
        [
            {
                "id": "B001",
                "name": "武侯祠",
                "address": "成都市武侯区",
                "location": "104.047,30.645",
                "cityname": "成都市",
                "adname": "武侯区",
                "type": "风景名胜;风景名胜;纪念馆",
            },
            {
                "id": "B002",
                "name": "武侯祠",
                "address": "成都市武侯区",
                "location": "104.047,30.645",
                "cityname": "成都市",
                "adname": "武侯区",
                "type": "风景名胜;风景名胜;纪念馆",
            },
        ]
    )

    grounded = ground_single_poi(raw_poi, {"destination": "成都"}, client)

    assert grounded["match_status"] == "matched"
    assert grounded["standard_name"] == "武侯祠"
