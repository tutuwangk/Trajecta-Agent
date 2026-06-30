from __future__ import annotations

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
    return list(grouped.values())


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
