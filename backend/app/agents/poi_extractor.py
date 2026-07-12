from __future__ import annotations

import re

NON_PLACE_WORDS = {
    "冰粉",
    "蛋烘糕",
    "火锅",
    "咖啡",
    "奶茶",
    "氛围感",
    "松弛感",
    "附近",
    "这家店",
    "巷子里",
}

ALIASES = {
    "成都 IFS": "IFS",
    "成都IFS": "IFS",
    "爬墙熊猫": "IFS",
    "成都太古里": "太古里",
    "远洋太古里": "太古里",
}


def extract_poi_names(ugc_items: list[dict], user_input: str = "") -> list[dict]:
    grouped: dict[str, dict] = {}
    for item in ugc_items:
        for mention in item.get("mentioned_pois", []):
            raw_name = str(mention.get("raw_name", "")).strip()
            if not _is_valid_place_name(raw_name):
                continue
            canonical = ALIASES.get(raw_name, raw_name)
            bucket = grouped.setdefault(
                canonical,
                {
                    "raw_name": canonical,
                    "source": "xiaohongshu",
                    "contexts": [],
                    "experience_tags": [],
                    "possible_category": mention.get("possible_category") or _guess_category(canonical, mention),
                    "confidence": float(mention.get("confidence") or 0.75),
                },
            )
            context = mention.get("context")
            if context:
                bucket["contexts"].append(context)
            for tag in mention.get("experience_tags", []):
                if tag not in bucket["experience_tags"]:
                    bucket["experience_tags"].append(tag)
            bucket["confidence"] = max(bucket["confidence"], float(mention.get("confidence") or 0.75))
    for raw_name, meal_slot in re.findall(
        r"(?:^|[，。；;、\n])\s*([^，。；;、\n]{2,20}?)(?:很)?适合安排为?(早餐|午餐|晚餐)",
        user_input,
    ):
        normalized_name = raw_name.strip(" ，。；;、")
        if not _is_valid_place_name(normalized_name):
            continue
        canonical = ALIASES.get(normalized_name, normalized_name)
        bucket = grouped.setdefault(
            canonical,
            {
                "raw_name": canonical,
                "source": "user_input",
                "contexts": [],
                "experience_tags": [],
                "possible_category": "restaurant",
                "confidence": 0.9,
            },
        )
        context = f"明确希望作为{meal_slot}"
        if context not in bucket["contexts"]:
            bucket["contexts"].append(context)
        if "美食" not in bucket["experience_tags"]:
            bucket["experience_tags"].append("美食")
        bucket["possible_category"] = "restaurant"
        bucket["confidence"] = max(float(bucket.get("confidence") or 0), 0.9)
    for mention in _explicit_intent_mentions(user_input):
        raw_name = mention["raw_name"]
        if not _is_valid_place_name(raw_name):
            continue
        canonical = ALIASES.get(raw_name, raw_name)
        bucket = grouped.setdefault(
            canonical,
            {
                "raw_name": canonical,
                "source": "user_input",
                "contexts": [],
                "experience_tags": [],
                "possible_category": mention["possible_category"],
                "confidence": 0.92,
            },
        )
        context = mention["context"]
        if context not in bucket["contexts"]:
            bucket["contexts"].append(context)
        bucket["confidence"] = max(float(bucket.get("confidence") or 0), 0.92)
    return list(grouped.values())


_ACTION = r"(?:去(?:吃|喝(?:杯)?|逛|看|买(?:个)?)?|顺便(?:去|吃|喝(?:杯)?|逛|看|买(?:个)?)?|再(?:去|吃|喝(?:杯)?|逛|看|买(?:个)?)|逛|可以吃)"
_ACTION_PATTERN = re.compile(rf"(?P<action>{_ACTION})\s*(?P<name>.+?)(?={_ACTION}|[，。；;、\n]|$)", re.IGNORECASE)


def _explicit_intent_mentions(text: str) -> list[dict]:
    mentions: list[dict] = []
    for match in _ACTION_PATTERN.finditer(text or ""):
        action = str(match.group("action") or "")
        name = str(match.group("name") or "").strip(" ，。；;、")
        if not name:
            continue
        possible_category = "restaurant" if any(token in action for token in ["吃", "喝"]) else _guess_category(name, {})
        mentions.append(
            {
                "raw_name": name,
                "context": f"用户明确表达：{action}{name}",
                "possible_category": possible_category,
            }
        )
    return mentions


def _is_valid_place_name(name: str) -> bool:
    if len(name) < 2 or len(name) > 30:
        return False
    if name in NON_PLACE_WORDS:
        return False
    if name.endswith(("附近", "旁边", "周边")):
        return False
    return True


def _guess_category(name: str, mention: dict) -> str:
    text = name + " ".join(mention.get("experience_tags", [])) + str(mention.get("context", ""))
    if any(token in text for token in ["餐", "小吃", "火锅", "咖啡"]):
        return "restaurant"
    if any(token in text for token in ["商场", "购物", "IFS", "太古里"]):
        return "shopping_mall"
    if any(token in text for token in ["公园", "街", "巷", "citywalk"]):
        return "citywalk"
    if any(token in text for token in ["博物馆", "文化", "历史"]):
        return "museum"
    return "attraction"
