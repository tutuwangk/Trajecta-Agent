from app.api.routes import _planning_grounded_pois, _uncertain_grounded_pois


def test_planning_grounded_pois_uses_only_accepted_matched_rows():
    rows = [
        {"decision": "keep", "grounded_poi": {"raw_name": "IFS", "match_status": "matched"}},
        {"decision": "delete", "grounded_poi": {"raw_name": "太古里", "match_status": "matched"}},
        {"decision": "must_visit", "grounded_poi": {"raw_name": "晓市集", "match_status": "ambiguous"}},
        {"decision": "optional", "grounded_poi": {"raw_name": "人民公园", "match_status": "matched"}},
    ]

    accepted = _planning_grounded_pois(rows)
    uncertain = _uncertain_grounded_pois(rows)

    assert [poi["raw_name"] for poi in accepted] == ["IFS", "人民公园"]
    assert [poi["raw_name"] for poi in uncertain] == ["晓市集"]
