from app.services.route_service import classify_relation


def test_classify_relation_uses_duration_and_mode():
    assert classify_relation("walking", 9) == "same_cluster"
    assert classify_relation("walking", 20) == "nearby"
    assert classify_relation("driving", 35) == "same_day_possible"
    assert classify_relation("driving", 55) == "separate_day"
    assert classify_relation("walking", None) == "unknown"
