from app.agents.poi_extractor import extract_poi_names


def test_extract_poi_names_merges_aliases_and_filters_non_places():
    ugc_items = [
        {
            "note_id": "note_001",
            "mentioned_pois": [
                {"raw_name": "IFS", "context": "爬墙熊猫适合拍照", "experience_tags": ["拍照"]},
                {"raw_name": "成都 IFS", "context": "商圈", "experience_tags": ["地标"]},
                {"raw_name": "冰粉", "context": "好吃", "experience_tags": ["美食"]},
                {"raw_name": "太古里", "context": "晚上氛围好", "experience_tags": ["夜景"]},
            ],
        }
    ]

    raw_pois = extract_poi_names(ugc_items, "")
    names = [poi["raw_name"] for poi in raw_pois]

    assert "IFS" in names
    assert "太古里" in names
    assert "成都 IFS" not in names
    assert "冰粉" not in names
    ifs = next(poi for poi in raw_pois if poi["raw_name"] == "IFS")
    assert "爬墙熊猫适合拍照" in ifs["contexts"]
    assert "地标" in ifs["experience_tags"]
