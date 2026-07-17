from app.services.poi_grounder import ground_single_poi


class FakeAmapClient:
    def __init__(self, results):
        self.results = results
        self.searches = []

    def search_poi(self, keyword, city=None):
        self.searches.append({"keyword": keyword, "city": city})
        return self.results


class FakeLLMClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def json_chat(self, messages, step, temperature=0.2):
        self.messages.append({"messages": messages, "step": step, "temperature": temperature})
        return self.responses.pop(0)


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


def test_ground_single_poi_uses_llm_search_keyword_and_candidate_selection():
    raw_poi = {"raw_name": "太古里", "possible_category": "shopping_mall", "contexts": ["想去太古里"]}
    client = FakeAmapClient(
        [
            {
                "id": "BAD1",
                "name": "太古里滑板公园",
                "address": "成都市锦江区",
                "location": "104.082,30.655",
                "cityname": "成都市",
                "adname": "锦江区",
                "type": "风景名胜;公园广场;公园",
            },
            {
                "id": "GOOD1",
                "name": "成都远洋太古里",
                "address": "中纱帽街8号",
                "location": "104.081,30.655",
                "cityname": "成都市",
                "adname": "锦江区",
                "type": "购物服务;商场;购物中心",
            },
        ]
    )
    llm = FakeLLMClient(
        [
            {"search_keyword": "成都远洋太古里"},
            {"selected_index": 1, "match_status": "matched", "confidence": 0.96, "reason": "主商圈地标"},
        ]
    )

    grounded = ground_single_poi(raw_poi, {"destination": "成都"}, client, llm)

    assert client.searches == [{"keyword": "成都远洋太古里", "city": "成都"}]
    assert grounded["match_status"] == "matched"
    assert grounded["standard_name"] == "成都远洋太古里"
    assert grounded["amap_id"] == "GOOD1"
    assert grounded["match_confidence"] == 0.96
    assert grounded["match_reason"] == "主商圈地标"
    assert grounded["candidate_options"][0]["name"] == "太古里滑板公园"


def test_ground_single_poi_marks_chain_branches_as_ambiguous():
    raw_poi = {"raw_name": "星巴克", "possible_category": "restaurant", "contexts": ["想喝咖啡"]}
    client = FakeAmapClient(
        [
            {
                "id": "S1",
                "name": "星巴克(成都太古里店)",
                "address": "中纱帽街",
                "location": "104.081,30.655",
                "cityname": "成都市",
                "adname": "锦江区",
                "type": "餐饮服务;咖啡厅;星巴克咖啡",
            },
            {
                "id": "S2",
                "name": "星巴克(成都IFS店)",
                "address": "红星路三段",
                "location": "104.080,30.657",
                "cityname": "成都市",
                "adname": "锦江区",
                "type": "餐饮服务;咖啡厅;星巴克咖啡",
            },
            {
                "id": "S3",
                "name": "星巴克(宽窄巷子店)",
                "address": "宽窄巷子",
                "location": "104.058,30.669",
                "cityname": "成都市",
                "adname": "青羊区",
                "type": "餐饮服务;咖啡厅;星巴克咖啡",
            },
        ]
    )

    grounded = ground_single_poi(raw_poi, {"destination": "成都"}, client)

    assert grounded["match_status"] == "ambiguous"
    assert grounded["is_chain"] is True
    assert grounded["standard_name"] == "星巴克（待选择）"
    assert grounded["selection_mode"] == "chain_needs_choice"
    assert [candidate["name"] for candidate in grounded["candidate_options"]] == [
        "星巴克(成都太古里店)",
        "星巴克(成都IFS店)",
        "星巴克(宽窄巷子店)",
    ]


def test_ground_single_poi_does_not_mark_landmark_variants_as_chain():
    raw_poi = {"raw_name": "武侯祠", "possible_category": "attraction", "contexts": ["必去"]}
    client = FakeAmapClient(
        [
            {
                "id": "BAD2",
                "name": "武侯祠东街",
                "address": "武侯区",
                "location": "104.049,30.646",
                "cityname": "成都市",
                "adname": "武侯区",
                "type": "地名地址信息;交通地名;道路名",
            },
            {
                "id": "GOOD2",
                "name": "武侯祠",
                "address": "武侯祠大街231号",
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
    assert grounded["amap_id"] == "GOOD2"
    assert grounded["is_chain"] is False


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
    assert grounded["amap_id"] == "B001"


def test_ground_single_poi_rejects_store_auxiliary_place_in_favor_of_main_store():
    raw_poi = {"raw_name": "山姆会员超市", "possible_category": "购物", "contexts": ["去山姆采购"]}
    client = FakeAmapClient(
        [
            {
                "id": "AUX",
                "name": "山姆会员店收货部",
                "address": "成都市金牛区",
                "location": "104.010,30.700",
                "cityname": "成都市",
                "adname": "金牛区",
                "type": "购物服务;超级市场;仓储会员店",
            },
            {
                "id": "MAIN",
                "name": "山姆会员商店(金牛店)",
                "address": "成都市金牛区北三环路一段",
                "location": "104.012,30.701",
                "cityname": "成都市",
                "adname": "金牛区",
                "type": "购物服务;超级市场;仓储会员店",
            },
            {
                "id": "OTHER_MAIN",
                "name": "山姆会员商店(天府店)",
                "address": "成都市双流区",
                "location": "104.085,30.486",
                "cityname": "成都市",
                "adname": "双流区",
                "type": "购物服务;超级市场;仓储会员店",
            },
        ]
    )

    grounded = ground_single_poi(raw_poi, {"destination": "成都"}, client)

    assert grounded["match_status"] == "matched"
    assert grounded["amap_id"] == "MAIN"
    assert grounded["standard_name"] == "山姆会员商店(金牛店)"
    assert grounded["is_chain"] is False


def test_ground_single_poi_accepts_unique_candidate_when_name_only_differs_by_punctuation():
    grounded = ground_single_poi(
        {"raw_name": "谭家钵钵鸡", "possible_category": "餐厅", "contexts": ["去吃谭家钵钵鸡"]},
        {"destination": "成都"},
        FakeAmapClient(
            [
                {
                    "id": "TAN",
                    "name": "谭家·钵钵鸡",
                    "address": "玉林南路",
                    "location": "104.057,30.623",
                    "cityname": "成都市",
                    "adname": "武侯区",
                    "type": "餐饮服务;中餐厅;中餐厅",
                }
            ]
        ),
    )

    assert grounded["match_status"] == "matched"
    assert grounded["standard_name"] == "谭家·钵钵鸡"
    assert grounded["is_chain"] is False


def test_ground_single_poi_accepts_primary_landmark_name_with_city_prefix():
    raw_poi = {"raw_name": "武侯祠", "possible_category": "景点", "contexts": ["想去"]}
    client = FakeAmapClient(
        [
            {
                "id": "MAIN",
                "name": "成都武侯祠博物馆",
                "address": "武侯祠大街231号",
                "location": "104.047,30.646",
                "cityname": "成都市",
                "adname": "武侯区",
                "type": "科教文化服务;博物馆;博物馆",
            },
            {
                "id": "ROAD",
                "name": "武侯祠东街",
                "address": "武侯区",
                "location": "104.046,30.643",
                "cityname": "成都市",
                "adname": "武侯区",
                "type": "地名地址信息;交通地名;道路名",
            },
        ]
    )

    grounded = ground_single_poi(raw_poi, {"destination": "成都"}, client)

    assert grounded["amap_id"] == "MAIN"
    assert grounded["match_status"] == "matched"
