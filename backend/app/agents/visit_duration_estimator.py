from __future__ import annotations

import hashlib
import json


DURATION_PROMPT_VERSION = "v2"


TEXT_DURATION_RULES = [
    ("胡同", 75, "胡同街区适合轻量游逛。"),
    ("街", 75, "街区类地点适合轻量游逛。"),
    ("巷", 75, "街巷类地点适合轻量游逛。"),
    ("鼓楼", 75, "单体地标正常停留时间较短。"),
    ("钟楼", 75, "单体地标正常停留时间较短。"),
    ("景山公园", 90, "小型城市公园正常游玩约一个多小时。"),
    ("雍和宫", 90, "寺庙类地点正常参观约一个多小时。"),
    ("主题乐园", 600, "主题乐园通常需要接近全天游玩。"),
    ("主题公园", 480, "主题公园通常需要大半天或全天游玩。"),
    ("游乐园", 420, "游乐园通常需要较长游玩时间。"),
    ("游乐场", 420, "大型游乐场通常需要较长游玩时间。"),
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


def estimate_visit_durations(runtime_pois: list[dict], llm_client=None, cache=None) -> list[dict]:
    cache_keys = {
        str(poi.get("poi_id") or ""): _duration_cache_key(poi, getattr(llm_client, "model", ""))
        for poi in runtime_pois
    }
    cached_profiles: dict[str, dict] = {}
    uncached_pois: list[dict] = []
    for poi in runtime_pois:
        poi_id = str(poi.get("poi_id") or "")
        cached = _safe_cache_get(cache, cache_keys[poi_id]) if cache is not None and llm_client is not None else None
        if isinstance(cached, dict):
            cached_profiles[poi_id] = cached
        else:
            uncached_pois.append(poi)
    llm_estimates = _llm_duration_estimates(uncached_pois, llm_client) if llm_client and uncached_pois else {}
    estimated: list[dict] = []
    for poi in runtime_pois:
        item = dict(poi)
        fallback_duration, fallback_reason = _normal_visit_duration(item)
        fallback_intense = _fallback_intense_duration(item, fallback_duration)
        fallback_profile = _build_duration_profile(
            fallback_intense,
            "high" if _text_rule(item) else "medium",
            fallback_reason,
        )
        poi_id = str(item.get("poi_id") or "")
        cached_profile = cached_profiles.get(poi_id)
        llm_estimate = llm_estimates.get(poi_id)
        raw_profile = cached_profile or _llm_duration_profile(llm_estimate, fallback_profile)
        profile, guardrailed = _merge_duration_profile(item, raw_profile, fallback_profile)
        if cached_profile:
            source = "llm_cache"
        elif raw_profile:
            source = "llm_guardrailed" if guardrailed else "llm"
            if cache is not None:
                _safe_cache_set(cache, cache_keys[poi_id], profile)
        else:
            source = "deterministic_fallback"
        item["visit_duration_profile"] = profile
        item["estimated_duration_min"] = profile["intense_min"]
        item["duration_confidence"] = profile["confidence"]
        item["duration_reason"] = profile["reason"]
        item["duration_source"] = source
        estimated.append(item)
    return estimated


def _llm_duration_estimates(runtime_pois: list[dict], llm_client) -> dict[str, dict]:
    try:
        payload = llm_client.json_chat(
            [
                {"role": "system", "content": "你是旅行景点游玩时长估计助手。只输出 JSON，不输出解释性正文。"},
                {
                    "role": "user",
                    "content": f"""请为每个地点估计两档“正常人在这个地方玩一次要多久”。
估计口径：只估计地点内的正常游玩、参观、排队和休息时间，不包含酒店往返和点间交通；不要为了迁就用户选择的行程强度而压缩时长；大型景区、主题乐园、动物园、植物园、博物馆群、古镇、山岳景区应按实际游玩体量给出半天或全天估计。
输出两档时间：`relaxed_duration_min` 表示轻松但正常的游玩时长，`intense_duration_min` 表示更完整但仍然常规的深度游玩时长。两档差距要合理，通常控制在 15-120 分钟内；`relaxed_duration_min` 必须小于等于 `intense_duration_min`，且都不能离谱偏短。
必须严格输出 JSON。只能使用输入中已有的 poi_id，每个地点最多输出一次；时长使用 15 分钟倍数。无法可靠判断的地点可以不输出，系统会使用本地安全时长。

输出 JSON 格式：
{{"durations":[{{"poi_id":"...","relaxed_duration_min":150,"intense_duration_min":210,"duration_confidence":"high|medium|low","duration_reason":"..."}}]}}

地点：{_duration_prompt_pois(runtime_pois)}
""",
                },
            ],
            step="estimate_visit_duration",
            temperature=0.1,
        )
    except Exception:
        return {}
    values = payload.get("durations") if isinstance(payload, dict) else []
    estimates: dict[str, dict] = {}
    for value in values or []:
        if not isinstance(value, dict):
            continue
        poi_id = value.get("poi_id")
        if poi_id:
            estimates[poi_id] = value
    return estimates


def _merge_duration_profile(poi: dict, candidate: dict | None, fallback: dict) -> tuple[dict, bool]:
    if not isinstance(candidate, dict):
        return fallback, False
    relaxed = _positive_int(candidate.get("relaxed_min"))
    intense = _positive_int(candidate.get("intense_min"))
    if relaxed is None or intense is None:
        return fallback, False
    relaxed = _round_to_quarter_hour(max(45, min(relaxed, 720)))
    intense = _round_to_quarter_hour(max(relaxed, min(intense, 720)))
    original = (relaxed, intense)
    if fallback.get("confidence") == "high":
        relaxed = max(relaxed, _positive_int(fallback.get("relaxed_min")) or relaxed)
        intense = max(intense, _positive_int(fallback.get("intense_min")) or intense)
    ceiling = _duration_ceiling(poi)
    relaxed = min(relaxed, ceiling)
    intense = min(max(relaxed, intense), ceiling)
    guarded = original != (relaxed, intense)
    return {
        "relaxed_min": relaxed,
        "intense_min": intense,
        "confidence": str(candidate.get("confidence") or fallback.get("confidence") or "medium"),
        "reason": str(candidate.get("reason") or fallback.get("reason") or "按地点常规游玩体验估计。"),
    }, guarded


def _duration_ceiling(poi: dict) -> int:
    text = " ".join(
        [
            str(poi.get("standard_name") or ""),
            str(poi.get("category") or ""),
            str(poi.get("category_raw") or ""),
            " ".join(str(tag) for tag in poi.get("ugc_tags") or []),
        ]
    ).lower()
    if bool((poi.get("planning_semantics") or {}).get("quick_stop_eligible")):
        return 60
    if any(token in text for token in ["咖啡", "奶茶", "茶饮", "甜品", "饮品"]):
        return 120
    if "restaurant" in text or "餐饮" in text or poi.get("category") == "restaurant":
        return 180
    return 720


def _duration_cache_key(poi: dict, model: str) -> str:
    payload = {
        "version": DURATION_PROMPT_VERSION,
        "model": model,
        "poi_id": poi.get("poi_id"),
        "name": poi.get("standard_name") or poi.get("raw_name"),
        "category": poi.get("category") or poi.get("category_normalized"),
        "category_raw": poi.get("category_raw"),
        "tags": poi.get("ugc_tags") or poi.get("experience_tags") or [],
        "evidence": poi.get("ugc_evidence") or poi.get("contexts") or [],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _safe_cache_get(cache, cache_key: str) -> dict | None:
    try:
        value = cache.get_duration(cache_key)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _safe_cache_set(cache, cache_key: str, value: dict) -> None:
    try:
        cache.set_duration(cache_key, value)
    except Exception:
        return


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


def _fallback_intense_duration(poi: dict, fallback_duration: int) -> int:
    existing = _positive_int(poi.get("estimated_duration_min"))
    if _text_rule(poi):
        return fallback_duration
    return max(existing or 0, fallback_duration)


def _build_duration_profile(intense_duration: int, confidence: str, reason: str) -> dict:
    intense_min = _round_to_quarter_hour(max(intense_duration, 45))
    relaxed_min = _round_to_quarter_hour(max(45, intense_min - _duration_gap_minutes(intense_min)))
    return {
        "relaxed_min": min(relaxed_min, intense_min),
        "intense_min": intense_min,
        "confidence": confidence,
        "reason": reason,
    }


def _llm_duration_profile(llm_estimate: dict | None, fallback_profile: dict) -> dict | None:
    if not isinstance(llm_estimate, dict):
        return None
    intense_min = _positive_int(llm_estimate.get("intense_duration_min")) or _positive_int(llm_estimate.get("estimated_duration_min"))
    relaxed_min = _positive_int(llm_estimate.get("relaxed_duration_min"))
    if intense_min is None and relaxed_min is None:
        return None
    if intense_min is None:
        intense_min = relaxed_min
    if relaxed_min is None:
        relaxed_min = max(45, intense_min - _duration_gap_minutes(intense_min))
    intense_min = _round_to_quarter_hour(max(intense_min, relaxed_min, 45))
    relaxed_min = _round_to_quarter_hour(max(45, min(relaxed_min, intense_min)))
    return {
        "relaxed_min": relaxed_min,
        "intense_min": intense_min,
        "confidence": str(llm_estimate.get("duration_confidence") or fallback_profile.get("confidence") or "medium"),
        "reason": str(llm_estimate.get("duration_reason") or fallback_profile.get("reason") or "按地点常规游玩体验估计。"),
    }


def _text_rule(poi: dict) -> tuple[str, int, str] | None:
    text = " ".join(
        [
            str(poi.get("standard_name") or ""),
            str(poi.get("raw_name") or ""),
            " ".join(str(name) for name in poi.get("raw_names") or []),
            str(poi.get("category") or ""),
            str(poi.get("category_raw") or ""),
            " ".join(str(tag) for tag in poi.get("ugc_tags") or poi.get("experience_tags") or []),
            " ".join(str(value) for value in poi.get("ugc_evidence") or poi.get("contexts") or []),
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


def _duration_gap_minutes(intense_duration: int) -> int:
    if intense_duration <= 75:
        return 15
    if intense_duration <= 120:
        return 30
    if intense_duration <= 180:
        return 45
    if intense_duration <= 300:
        return 60
    if intense_duration <= 480:
        return 90
    return 120


def _round_to_quarter_hour(value: int) -> int:
    return max(15, round(value / 15) * 15)
