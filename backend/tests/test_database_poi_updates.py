from app.services.database import SQLiteStore


def test_update_poi_decisions_rematches_manual_name(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("raw", "notes", {"destination": "成都"})
    store.save_pois(
        session_id,
        [{"raw_name": "晓市集", "contexts": ["笔记提到"]}],
        [
            {
                "raw_name": "晓市集",
                "standard_name": "",
                "amap_id": "",
                "match_status": "unmatched",
                "location": {"lng": None, "lat": None},
                "contexts": ["笔记提到"],
                "experience_tags": [],
            }
        ],
    )

    def rematch(raw_poi, current_grounded, manual_name):
        assert raw_poi["raw_name"] == "成都晓市集"
        assert current_grounded["match_status"] == "unmatched"
        assert manual_name == "成都晓市集"
        return {
            "raw_name": "成都晓市集",
            "standard_name": "成都晓市集",
            "amap_id": "B001",
            "match_status": "matched",
            "location": {"lng": 104.1, "lat": 30.6},
            "contexts": raw_poi["contexts"],
            "experience_tags": [],
        }

    store.update_poi_decisions(
        session_id,
        [{"poi_id": "raw_晓市集", "decision": "keep", "manual_name": "成都晓市集"}],
        rematch_grounded=rematch,
    )

    row = store.list_pois(session_id)[0]
    assert row["manual_name"] == "成都晓市集"
    assert row["raw_poi"]["raw_name"] == "成都晓市集"
    assert row["grounded_poi"]["match_status"] == "matched"
    assert row["grounded_poi"]["amap_id"] == "B001"
    assert row["system_decision"] == "include"
    assert row["user_override"] == "rename_confirm"
    assert row["final_decision"] == "include"
    assert row["place_pool_item"]["status_label"] == "已识别"


def test_update_poi_decisions_does_not_rematch_decision_only_update(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("raw", "notes", {"destination": "成都"})
    store.save_pois(
        session_id,
        [{"raw_name": "IFS"}],
        [{"raw_name": "IFS", "standard_name": "成都IFS", "amap_id": "B001", "match_status": "matched"}],
    )

    def rematch(raw_poi, current_grounded, manual_name):
        raise AssertionError("decision-only update should not rematch")

    store.update_poi_decisions(
        session_id,
        [{"poi_id": "amap_B001", "decision": "must_visit"}],
        rematch_grounded=rematch,
    )

    row = store.list_pois(session_id)[0]
    assert row["decision"] == "must_visit"
    assert row["user_override"] == "must_include"
    assert row["final_decision"] == "include"
    assert row["grounded_poi"]["standard_name"] == "成都IFS"


def test_update_poi_decisions_accepts_new_remove_override(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("raw", "notes", {"destination": "成都", "constraints": {}})
    store.save_pois(
        session_id,
        [{"raw_name": "太古里"}],
        [{"raw_name": "太古里", "standard_name": "成都远洋太古里", "amap_id": "B002", "match_status": "matched"}],
    )

    store.update_poi_decisions(session_id, [{"poi_id": "amap_B002", "decision": "remove"}])

    row = store.list_pois(session_id)[0]
    assert row["decision"] == "delete"
    assert row["user_override"] == "remove"
    assert row["final_decision"] == "exclude"
    assert row["place_pool_item"]["decision_label"] == "未纳入"


def test_update_poi_decisions_keeps_legacy_optional_compatible(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("raw", "notes", {"destination": "成都", "constraints": {}})
    store.save_pois(
        session_id,
        [{"raw_name": "人民公园"}],
        [{"raw_name": "人民公园", "standard_name": "人民公园", "amap_id": "B003", "match_status": "matched"}],
    )

    store.update_poi_decisions(session_id, [{"poi_id": "amap_B003", "decision": "optional"}])

    row = store.list_pois(session_id)[0]
    assert row["decision"] == "optional"
    assert row["user_override"] == "optional"
    assert row["final_decision"] == "optional"


def test_must_include_confirms_ambiguous_place_with_map_candidate(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("raw", "notes", {"destination": "成都", "constraints": {}})
    store.save_pois(
        session_id,
        [{"raw_name": "晓市集", "contexts": ["想去晓市集"]}],
        [
            {
                "raw_name": "晓市集",
                "standard_name": "晓市集",
                "amap_id": "B004",
                "match_status": "ambiguous",
                "location": {"lng": 104.1, "lat": 30.6},
            }
        ],
    )

    store.update_poi_decisions(session_id, [{"poi_id": "amap_B004", "decision": "must_include"}])

    row = store.list_pois(session_id)[0]
    assert row["grounded_poi"]["match_status"] == "matched"
    assert row["system_decision"] == "include"
    assert row["user_override"] == "must_include"
    assert row["final_decision"] == "include"
    assert row["place_pool_item"]["status_label"] == "已识别"


def test_save_pois_keeps_ambiguous_map_candidate_unresolved(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("raw", "notes", {"destination": "成都", "constraints": {}})

    store.save_pois(
        session_id,
        [{"raw_name": "晓市集", "contexts": ["想去晓市集"]}],
        [
            {
                "raw_name": "晓市集",
                "standard_name": "晓市集",
                "amap_id": "B004",
                "match_status": "ambiguous",
                "location": {"lng": 104.1, "lat": 30.6},
            }
        ],
    )

    row = store.list_pois(session_id)[0]
    assert row["grounded_poi"]["match_status"] == "ambiguous"
    assert row["system_decision"] == "needs_confirmation"
    assert row["final_decision"] == "unresolved"
    assert row["place_pool_item"]["needs_attention"] is True


def test_save_pois_keeps_chain_candidate_unresolved(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("raw", "notes", {"destination": "成都", "constraints": {}})

    store.save_pois(
        session_id,
        [{"raw_name": "星巴克", "contexts": ["想喝咖啡"], "possible_category": "restaurant"}],
        [
            {
                "raw_name": "星巴克",
                "standard_name": "星巴克（待选择）",
                "amap_id": "S1",
                "match_status": "ambiguous",
                "is_chain": True,
                "selection_mode": "chain_needs_choice",
                "category_normalized": "restaurant",
                "location": {"lng": 104.081, "lat": 30.655},
                "candidate_options": [
                    {"id": "S1", "name": "星巴克(成都太古里店)"},
                    {"id": "S2", "name": "星巴克(成都IFS店)"},
                ],
            }
        ],
    )

    row = store.list_pois(session_id)[0]
    assert row["grounded_poi"]["match_status"] == "ambiguous"
    assert row["system_decision"] == "needs_confirmation"
    assert row["final_decision"] == "unresolved"
    assert row["place_pool_item"]["status_label"] == "需确认"
    assert "顺路安排" in row["place_pool_item"]["primary_actions"]


def test_update_poi_decisions_arranges_chain_with_nearby_context(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("raw", "notes", {"destination": "成都", "constraints": {}})
    store.save_pois(
        session_id,
        [
            {"raw_name": "IFS", "contexts": ["先去IFS"]},
            {"raw_name": "星巴克", "contexts": ["想喝咖啡"], "possible_category": "restaurant"},
            {"raw_name": "武侯祠", "contexts": ["再去武侯祠"]},
        ],
        [
            {
                "raw_name": "IFS",
                "standard_name": "成都IFS国际金融中心",
                "amap_id": "I1",
                "match_status": "matched",
                "location": {"lng": 104.08, "lat": 30.657},
            },
            {
                "raw_name": "星巴克",
                "standard_name": "星巴克（待选择）",
                "amap_id": "S1",
                "match_status": "ambiguous",
                "is_chain": True,
                "selection_mode": "chain_needs_choice",
                "category_normalized": "restaurant",
                "location": {"lng": 104.081, "lat": 30.655},
                "candidate_options": [
                    {"id": "S1", "name": "星巴克(成都太古里店)"},
                    {"id": "S2", "name": "星巴克(成都IFS店)"},
                ],
            },
            {
                "raw_name": "武侯祠",
                "standard_name": "武侯祠",
                "amap_id": "W1",
                "match_status": "matched",
                "location": {"lng": 104.047, "lat": 30.645},
            },
        ],
    )

    def arrange_nearby(raw_poi, current_grounded, context):
        assert raw_poi["raw_name"] == "星巴克"
        assert current_grounded["is_chain"] is True
        assert context["previous_grounded"]["standard_name"] == "成都IFS国际金融中心"
        assert context["next_grounded"]["standard_name"] == "武侯祠"
        return {
            "raw_name": "星巴克",
            "standard_name": "星巴克(成都IFS店)",
            "amap_id": "S2",
            "match_status": "matched",
            "category_normalized": "restaurant",
            "location": {"lng": 104.0805, "lat": 30.6572},
            "is_chain": True,
            "selection_mode": "arranged_nearby",
        }

    store.update_poi_decisions(
        session_id,
        [{"poi_id": "amap_S1", "decision": "arrange_nearby"}],
        arrange_nearby_grounded=arrange_nearby,
    )

    row = store.list_pois(session_id)[1]
    assert row["grounded_poi"]["standard_name"] == "星巴克(成都IFS店)"
    assert row["grounded_poi"]["match_status"] == "matched"
    assert row["user_override"] == "arrange_nearby"
    assert row["final_decision"] == "include"


def test_optional_confirms_ambiguous_place_as_tentative(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("raw", "notes", {"destination": "成都", "constraints": {}})
    store.save_pois(
        session_id,
        [{"raw_name": "人民公园", "contexts": ["有时间可以去"]}],
        [
            {
                "raw_name": "人民公园",
                "standard_name": "人民公园",
                "amap_id": "B005",
                "match_status": "ambiguous",
                "location": {"lng": 104.05, "lat": 30.67},
            }
        ],
    )

    store.update_poi_decisions(session_id, [{"poi_id": "amap_B005", "decision": "optional"}])

    row = store.list_pois(session_id)[0]
    assert row["grounded_poi"]["match_status"] == "matched"
    assert row["user_override"] == "optional"
    assert row["final_decision"] == "optional"
    assert row["place_pool_item"]["status_label"] == "已识别"
    assert row["place_pool_item"]["decision_label"] == "待定"
