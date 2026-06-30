from app.agents.input_parser import parse_user_profile


def test_parse_user_profile_extracts_core_trip_fields():
    text = """
    目的地：成都
    计划 2 天 1 晚，酒店在春熙路附近。
    我和对象一起去，美食和拍照优先，不要太累。
    必去 IFS、太古里，不想去都江堰。
    """

    profile = parse_user_profile(text)

    assert profile["destination"] == "成都"
    assert profile["days"] == 2
    assert profile["nights"] == 1
    assert profile["hotel_area"] == "春熙路"
    assert profile["travelers"]["type"] == "情侣"
    assert profile["preferences"]["food"] == 5
    assert profile["preferences"]["photo"] == 5
    assert profile["constraints"]["avoid_too_tired"] is True
    assert profile["constraints"]["physical_intensity"] == "low"
    assert "IFS" in profile["constraints"]["must_visit"]
    assert "都江堰" in profile["constraints"]["avoid_visit"]


def test_parse_user_profile_extracts_physical_intensity_levels():
    assert parse_user_profile("目的地：成都，特种兵旅游，计划 2 天")["constraints"]["physical_intensity"] == "high"
    assert parse_user_profile("目的地：成都，常规节奏，计划 2 天")["constraints"]["physical_intensity"] == "medium"

    low_profile = parse_user_profile("目的地：成都，躺平式旅游，计划 2 天")

    assert low_profile["constraints"]["physical_intensity"] == "low"
    assert low_profile["constraints"]["avoid_too_tired"] is True
