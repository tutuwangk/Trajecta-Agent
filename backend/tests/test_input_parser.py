from app.agents.input_parser import parse_user_profile


def test_parse_user_profile_extracts_core_trip_fields():
    text = """
    目的地：成都
    计划 2 天 1 晚，酒店名：成都太古里亚朵酒店。
    出行人数：2人，美食和拍照优先，不要太累。
    必去 IFS、太古里，不想去都江堰。
    """

    profile = parse_user_profile(text)

    assert profile["destination"] == "成都"
    assert profile["days"] == 2
    assert profile["nights"] == 1
    assert profile["hotel_name"] == "成都太古里亚朵酒店"
    assert profile["travelers"]["count"] == 2
    assert profile["travelers"]["type"] == "未说明"
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


def test_parse_user_profile_keeps_route_goal_to_visible_options():
    assert parse_user_profile("目的地：成都，路线目标：美食优先")["route_goal"] == "food_first"
    assert parse_user_profile("目的地：成都，路线目标：拍照优先")["route_goal"] == "photo_first"
    assert parse_user_profile("目的地：成都，路线目标：少绕路")["route_goal"] == "balanced"
    assert parse_user_profile("目的地：成都，路线目标：轻松一点")["route_goal"] == "balanced"


def test_parse_user_profile_keeps_hotel_name_with_spaces():
    profile = parse_user_profile("目的地：成都\n酒店名：成都 太古里 亚朵酒店\n出行人数：2人")

    assert profile["hotel_name"] == "成都 太古里 亚朵酒店"
    assert profile["travelers"]["count"] == 2
