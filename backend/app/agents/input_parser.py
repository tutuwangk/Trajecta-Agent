from __future__ import annotations

import re

from app.schemas.models import UserProfile


CITY_PATTERN = re.compile(r"(?:目的地|去|到)[:：\s]*([\u4e00-\u9fa5A-Za-z]{2,12})")
HOTEL_PATTERN = re.compile(r"(?:酒店名|酒店|住|住宿|起点)(?:在|：|:)?\s*([^\n。。，,]+?)(?:附近|周边|一带|。|，|,|\n|$)")
TRAVELER_COUNT_PATTERN = re.compile(r"(?:出行人数|人数|几个人|几人|同行人数)(?:是|：|:)?\s*(\d+)\s*(?:人|位)?")


def parse_user_profile(user_input: str) -> dict:
    profile = UserProfile()
    text = user_input.strip()

    profile.destination = _parse_destination(text)
    profile.days = _parse_days(text)
    profile.nights = max(profile.days - 1, 0)
    profile.hotel_name = _parse_hotel_name(text)
    profile.start_point = profile.hotel_name
    profile.travelers.count = _parse_traveler_count(text)
    profile.budget_level = _parse_budget(text)
    profile.transport_preference = _parse_transport_preference(text)
    profile.route_goal = _parse_route_goal(text)
    profile.preferences = _parse_preferences(text)
    profile.constraints.physical_intensity = _parse_physical_intensity(text)
    profile.constraints.avoid_too_tired = profile.constraints.physical_intensity == "low"
    profile.constraints.must_visit = _parse_named_list(text, ["必去", "一定要去", "想去"])
    profile.constraints.avoid_visit = _parse_named_list(text, ["不想去", "不要去", "避开"])
    return profile.model_dump()


def _parse_destination(text: str) -> str:
    match = CITY_PATTERN.search(text)
    if match:
        candidate = match.group(1).strip()
        return candidate.removesuffix("旅游").removesuffix("旅行")
    known_cities = ["成都", "重庆", "上海", "北京", "广州", "深圳", "杭州", "南京", "西安", "长沙"]
    for city in known_cities:
        if city in text:
            return city
    return ""


def _parse_days(text: str) -> int:
    match = re.search(r"(\d+)\s*天", text)
    if match:
        return max(1, min(int(match.group(1)), 5))
    cn_days = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5}
    match = re.search(r"([一二两三四五])\s*天", text)
    if match:
        return cn_days[match.group(1)]
    return 1


def _parse_hotel_name(text: str) -> str | None:
    match = HOTEL_PATTERN.search(text)
    if not match:
        return None
    name = match.group(1).strip(" ：:，,。")
    return name or None


def _parse_traveler_count(text: str) -> int:
    match = TRAVELER_COUNT_PATTERN.search(text)
    if match:
        return max(1, min(int(match.group(1)), 20))
    match = re.search(r"(\d+)\s*(?:个人|人|位)\s*(?:出行|旅行|旅游|去|同行)?", text)
    if match:
        return max(1, min(int(match.group(1)), 20))
    cn_counts = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    match = re.search(r"([一二两三四五六七八九十])\s*(?:个人|人|位)", text)
    if match:
        return cn_counts[match.group(1)]
    return 1


def _parse_budget(text: str) -> str:
    if any(token in text for token in ["省钱", "预算低", "便宜", "低一点"]):
        return "low"
    if any(token in text for token in ["高预算", "不差钱", "品质"]):
        return "high"
    return "medium"


def _parse_transport_preference(text: str) -> list[str]:
    preferences: list[str] = []
    if any(token in text for token in ["步行", "走路", "少坐车"]):
        preferences.append("walking")
    if any(token in text for token in ["打车", "出租车", "网约车"]):
        preferences.append("taxi")
    if any(token in text for token in ["地铁", "公交", "公共交通"]):
        preferences.append("public_transport")
    return preferences or ["walking", "taxi", "public_transport"]


def _parse_route_goal(text: str) -> str:
    if any(token in text for token in ["美食优先", "多安排美食", "吃"]):
        return "food_first"
    if any(token in text for token in ["拍照优先", "出片", "打卡"]):
        return "photo_first"
    return "balanced"


def _parse_physical_intensity(text: str) -> str:
    if any(token in text for token in ["特种兵", "赶路", "多安排", "紧凑"]):
        return "high"
    if any(token in text for token in ["躺平", "松弛", "不要太累", "轻松", "休闲", "慢一点", "带老人"]):
        return "low"
    if any(token in text for token in ["常规", "标准", "普通"]):
        return "medium"
    return "medium"


def _parse_preferences(text: str):
    from app.schemas.models import PreferenceWeights

    weights = PreferenceWeights()
    if any(token in text for token in ["美食", "吃", "小吃", "餐厅"]):
        weights.food = 5
    if any(token in text for token in ["拍照", "出片", "打卡", "好看"]):
        weights.photo = 5
    if "citywalk" in text.lower() or any(token in text for token in ["城市漫步", "城市漫游", "街区", "散步", "逛"]):
        weights.citywalk = 5
    if any(token in text for token in ["购物", "商场", "买"]):
        weights.shopping = 5
    if any(token in text for token in ["历史", "博物馆", "文化"]):
        weights.history = 5
    if any(token in text for token in ["休闲", "松弛", "不要太累", "轻松"]):
        weights.relaxation = 5
    return weights


def _parse_named_list(text: str, triggers: list[str]) -> list[str]:
    names: list[str] = []
    stop_words = ["必去", "一定要去", "想去", "不想去", "不要去", "避开"]
    for trigger in triggers:
        index = text.find(trigger)
        if index < 0:
            continue
        segment = text[index + len(trigger) : index + len(trigger) + 80]
        for stop_word in stop_words:
            if stop_word in triggers:
                continue
            stop_index = segment.find(stop_word)
            if stop_index >= 0:
                segment = segment[:stop_index]
        segment = re.split(r"[。；;\n]", segment)[0]
        for raw in re.split(r"[、,，和\s]+", segment):
            item = raw.strip(" ：:。,.，")
            if 1 < len(item) <= 18 and item not in ["不想", "不要", "附近"]:
                names.append(item)
    return list(dict.fromkeys(names))
