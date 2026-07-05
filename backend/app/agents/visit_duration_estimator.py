from __future__ import annotations


TEXT_DURATION_RULES = [
    ("胡同", 75, "胡同街区适合轻量游逛。"),
    ("街", 75, "街区类地点适合轻量游逛。"),
    ("巷", 75, "街巷类地点适合轻量游逛。"),
    ("鼓楼", 75, "单体地标正常停留时间较短。"),
    ("钟楼", 75, "单体地标正常停留时间较短。"),
    ("景山公园", 90, "小型城市公园正常游玩约一个多小时。"),
    ("雍和宫", 90, "寺庙类地点正常参观约一个多小时。"),
    ("主题乐园", 600, "主题乐园通常需要接近全天游玩。"),
    ("游乐园", 420, "游乐园通常需要较长游玩时间。"),
    ("度假区", 600, "大型度假区通常需要接近全天游玩。"),
    ("动物园", 210, "动物园通常需要完整半天游玩。"),
    ("植物园", 180, "植物园通常需要较长停留。"),
    ("海洋馆", 180, "海洋馆通常需要较长参观时间。"),
    ("风景区", 240, "大型风景区正常游玩需要半天左右。"),
    ("景区", 180, "景区正常游玩通常需要较长停留。"),
    ("古镇", 180, "古镇类地点通常需要较完整半天游览。"),
    ("园林", 180, "园林类地点正常游玩需要较长停留。"),
    ("环球影城", 600, "主题乐园通常需要接近全天游玩。"),
    ("环球度假区", 600, "主题乐园通常需要接近全天游玩。"),
    ("Universal", 600, "主题乐园通常需要接近全天游玩。"),
    ("迪士尼", 600, "主题乐园通常需要接近全天游玩。"),
    ("颐和园", 240, "大型皇家园林正常游玩需要半天左右。"),
    ("圆明园", 180, "大型遗址公园正常游玩需要较长停留。"),
    ("故宫", 240, "大型博物院和宫殿建筑群正常游玩需要半天左右。"),
    ("长城", 240, "长城类景点正常游玩和排队需要较长停留。"),
    ("博物馆", 150, "博物馆正常参观通常需要预留较完整时段。"),
]

CATEGORY_DURATION_MINUTES = {
    "restaurant": 75,
    "shopping_mall": 90,
    "museum": 150,
    "park": 120,
    "citywalk": 120,
    "attraction": 120,
}


def estimate_visit_durations(runtime_pois: list[dict], llm_client=None) -> list[dict]:
    llm_estimates = _llm_duration_estimates(runtime_pois, llm_client) if llm_client and runtime_pois else {}
    estimated: list[dict] = []
    for poi in runtime_pois:
        item = dict(poi)
        fallback_duration, fallback_reason = _normal_visit_duration(item)
        llm_estimate = llm_estimates.get(item.get("poi_id"))
        llm_duration = _positive_int((llm_estimate or {}).get("estimated_duration_min"))
        if llm_duration is not None:
            item["estimated_duration_min"] = max(llm_duration, fallback_duration)
            item["duration_confidence"] = (llm_estimate or {}).get("duration_confidence") or "medium"
            item["duration_reason"] = (llm_estimate or {}).get("duration_reason") or "按地点常规游玩体验估计。"
        else:
            existing = _positive_int(item.get("estimated_duration_min"))
            item["estimated_duration_min"] = max(existing or 0, fallback_duration) if not _text_rule(item) else fallback_duration
            item["duration_confidence"] = "high" if _text_rule(item) else "medium"
            item["duration_reason"] = fallback_reason
        estimated.append(item)
    return estimated


def _llm_duration_estimates(runtime_pois: list[dict], llm_client) -> dict[str, dict]:
    payload = llm_client.json_chat(
        [
            {"role": "system", "content": "你是旅行景点游玩时长估计助手。只输出 JSON，不输出解释性正文。"},
            {
                "role": "user",
                "content": f"""请为每个地点估计“正常人在这个地方玩一次要多久”。
估计口径：只估计地点内的正常游玩、参观、排队和休息时间，不包含酒店往返和点间交通；不要为了迁就用户选择的行程强度而压缩时长；大型景区、主题乐园、动物园、植物园、博物馆群、古镇、山岳景区应按实际游玩体量给出半天或全天估计。

输出 JSON 格式：
{{"durations":[{{"poi_id":"...","estimated_duration_min":180,"duration_confidence":"high|medium|low","duration_reason":"..."}}]}}

地点：{_duration_prompt_pois(runtime_pois)}
""",
            },
        ],
        step="estimate_visit_duration",
        temperature=0.1,
    )
    values = payload.get("durations") if isinstance(payload, dict) else []
    estimates: dict[str, dict] = {}
    for value in values or []:
        if not isinstance(value, dict):
            continue
        poi_id = value.get("poi_id")
        if poi_id:
            estimates[poi_id] = value
    return estimates


def _duration_prompt_pois(runtime_pois: list[dict]) -> list[dict]:
    return [
        {
            "poi_id": poi.get("poi_id"),
            "name": poi.get("standard_name") or poi.get("raw_names") or poi.get("raw_name"),
            "category": poi.get("category") or poi.get("category_normalized"),
            "tags": poi.get("ugc_tags") or poi.get("experience_tags") or [],
            "evidence": poi.get("ugc_evidence") or poi.get("contexts") or [],
        }
        for poi in runtime_pois
    ]


def _normal_visit_duration(poi: dict) -> tuple[int, str]:
    rule = _text_rule(poi)
    if rule:
        _, minutes, reason = rule
        return minutes, reason
    category = poi.get("category") or poi.get("category_normalized") or "unknown"
    minutes = CATEGORY_DURATION_MINUTES.get(category, 60)
    return minutes, "按地点类型估计正常停留时间。"


def _text_rule(poi: dict) -> tuple[str, int, str] | None:
    text = " ".join(
        [
            str(poi.get("standard_name") or ""),
            str(poi.get("raw_name") or ""),
            " ".join(str(name) for name in poi.get("raw_names") or []),
        ]
    )
    for token, minutes, reason in TEXT_DURATION_RULES:
        if token in text:
            return token, minutes, reason
    return None


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
