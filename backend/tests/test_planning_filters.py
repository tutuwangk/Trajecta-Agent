from app.api.routes import _planning_grounded_pois, _uncertain_grounded_pois


def test_planning_grounded_pois_uses_only_accepted_matched_rows():
    rows = [
        {"final_decision": "include", "grounded_poi": {"raw_name": "IFS", "match_status": "matched"}},
        {"final_decision": "exclude", "grounded_poi": {"raw_name": "太古里", "match_status": "matched"}},
        {"final_decision": "unresolved", "grounded_poi": {"raw_name": "晓市集", "match_status": "ambiguous"}},
        {"final_decision": "optional", "grounded_poi": {"raw_name": "人民公园", "match_status": "matched"}},
    ]

    accepted = _planning_grounded_pois(rows)
    uncertain = _uncertain_grounded_pois(rows)

    assert [poi["raw_name"] for poi in accepted] == ["IFS", "人民公园"]
    assert [poi["raw_name"] for poi in uncertain] == ["晓市集"]


def test_planning_grounded_pois_keeps_legacy_decisions_compatible():
    rows = [
        {"decision": "keep", "grounded_poi": {"raw_name": "IFS", "match_status": "matched"}},
        {"decision": "delete", "grounded_poi": {"raw_name": "太古里", "match_status": "matched"}},
        {"decision": "must_visit", "grounded_poi": {"raw_name": "人民公园", "match_status": "matched"}},
    ]

    accepted = _planning_grounded_pois(rows)

    assert [poi["raw_name"] for poi in accepted] == ["IFS", "人民公园"]


def test_planning_grounded_pois_includes_user_confirmed_ambiguous_candidate():
    rows = [
        {
            "final_decision": "include",
            "user_override": "must_include",
            "grounded_poi": {
                "raw_name": "晓市集",
                "match_status": "ambiguous",
                "amap_id": "B004",
                "location": {"lng": 104.1, "lat": 30.6},
            },
        },
        {
            "final_decision": "include",
            "user_override": "must_include",
            "grounded_poi": {
                "raw_name": "没有坐标的地点",
                "match_status": "unmatched",
                "amap_id": "",
                "location": {"lng": None, "lat": None},
            },
        },
    ]

    accepted = _planning_grounded_pois(rows)

    assert [poi["raw_name"] for poi in accepted] == ["晓市集"]
