from __future__ import annotations

import re

from app.schemas.models import UserProfile


CITY_PATTERN = re.compile(r"(?:目的地|去|到)[:：\s]*([\u4e00-\u9fa5A-Za-z]{2,12})")
HOTEL_PATTERN = re.compile(r"(?:酒店|住|住宿|起点)(?:在|：|:)?\s*([\u4e00-\u9fa5A-Za-z0-9]+?)(?:附近|周边|一带|。|，|,|\s|$)")


def parse_user_profile(user_input: str) -> dict:
    profile = UserProfile()
    text = user_input.strip()

    profile.destination = _parse_destination(text)
    profile.days = _parse_days(text)
    profile.nights = max(profile.days - 1, 0)
    profile.hotel_area = _parse_hotel_area(text)
    profile.start_point = profile.hotel_area
    profile.travelers.type = _parse_traveler_type(text)
    profile.budget_level = _parse_budget(text)
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


def _parse_hotel_area(text: str) -> str | None:
    match = HOTEL_PATTERN.search(text)
    if not match:
        return None
    area = match.group(1).strip(" ：:，,。")
    return area or None


def _parse_traveler_type(text: str) -> str:
    if any(token in text for token in ["对象", "情侣", "女朋友", "男朋友"]):
        return "情侣"
    if "孩子" in text or "亲子" in text:
        return "亲子"
    if "老人" in text or "父母" in text:
        return "家庭"
    if "朋友" in text or "闺蜜" in text:
        return "朋友"
    return "未说明"


def _parse_budget(text: str) -> str:
    if any(token in text for token in ["省钱", "预算低", "便宜", "低一点"]):
        return "low"
    if any(token in text for token in ["高预算", "不差钱", "品质"]):
        return "high"
    return "medium"


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
