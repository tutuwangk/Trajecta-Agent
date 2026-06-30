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
    assert row["grounded_poi"]["standard_name"] == "成都IFS"
