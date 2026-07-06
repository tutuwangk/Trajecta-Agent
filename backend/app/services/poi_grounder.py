from __future__ import annotations

from difflib import SequenceMatcher


def ground_pois(raw_pois: list[dict], user_profile: dict, amap_client, llm_client=None) -> list[dict]:
    return [ground_single_poi(raw_poi, user_profile, amap_client, llm_client) for raw_poi in raw_pois]


def ground_single_poi(raw_poi: dict, user_profile: dict, amap_client, llm_client=None) -> dict:
    raw_name = raw_poi.get("raw_name", "")
    city = user_profile.get("destination") or None
    search_keyword = _select_search_keyword(raw_poi, city, llm_client)
    candidates = amap_client.search_poi(search_keyword, city=city)
    if not candidates:
        return _unmatched(raw_poi, search_keyword=search_keyword)

    candidate_options = [_candidate_option(candidate) for candidate in candidates[:5]]
    if _is_chain_place(raw_poi, candidates):
        first = candidate_options[0]
        return {
            "raw_name": raw_name,
            "standard_name": f"{raw_name}（待选择）",
            "amap_id": first.get("id", ""),
            "address": first.get("address", ""),
            "location": first.get("location", {"lng": None, "lat": None}),
            "city": first.get("city", ""),
            "district": first.get("district", ""),
            "category_raw": first.get("category_raw", ""),
            "category_normalized": first.get("category_normalized") or raw_poi.get("possible_category", "unknown"),
            "match_confidence": 0.65,
            "match_status": "ambiguous",
            "candidate_count": len(candidates),
            "candidate_options": candidate_options,
            "is_chain": True,
            "chain_status": "unresolved",
            "selection_mode": "chain_needs_choice",
            "search_keyword": search_keyword,
            "source": "amap",
            "contexts": raw_poi.get("contexts", []),
            "experience_tags": raw_poi.get("experience_tags", []),
        }

    llm_selection = _select_candidate_with_llm(raw_poi, city, candidate_options, llm_client)
    if llm_selection:
        selected_index = llm_selection["selected_index"]
        confidence = llm_selection["confidence"]
        status = llm_selection["match_status"]
        if status == "unmatched":
            return _unmatched(
                raw_poi,
                candidate_count=len(candidates),
                confidence=confidence,
                candidate_options=candidate_options,
                search_keyword=search_keyword,
            )
        selected = candidates[selected_index]
        return _grounded_from_candidate(
            raw_poi,
            selected,
            confidence,
            status,
            candidate_options,
            search_keyword,
            match_reason=llm_selection.get("reason", ""),
            selection_mode="llm_candidate_selection",
        )

    scored = sorted(
        ((_score_candidate(raw_poi, candidate, city), candidate) for candidate in candidates),
        key=lambda item: item[0],
    )
    score, best = scored[-1]
    status = "matched" if score >= 0.8 else "ambiguous" if score >= 0.55 else "unmatched"
    if status == "unmatched":
        return _unmatched(
            raw_poi,
            candidate_count=len(candidates),
            confidence=score,
            candidate_options=candidate_options,
            search_keyword=search_keyword,
        )

    return _grounded_from_candidate(raw_poi, best, score, status, candidate_options, search_keyword)


def _grounded_from_candidate(
    raw_poi: dict,
    candidate: dict,
    score: float,
    status: str,
    candidate_options: list[dict],
    search_keyword: str,
    match_reason: str = "",
    selection_mode: str = "score_fallback",
) -> dict:
    raw_name = raw_poi.get("raw_name", "")
    lng, lat = _parse_location(candidate.get("location", ""))
    return {
        "raw_name": raw_name,
        "standard_name": candidate.get("name", ""),
        "amap_id": candidate.get("id", ""),
        "address": candidate.get("address") or "",
        "location": {"lng": lng, "lat": lat},
        "city": candidate.get("cityname") or "",
        "district": candidate.get("adname") or "",
        "category_raw": candidate.get("type") or "",
        "category_normalized": _normalize_category(candidate.get("type") or raw_poi.get("possible_category", "")),
        "match_confidence": round(score, 3),
        "match_status": status,
        "candidate_count": len(candidate_options),
        "candidate_options": candidate_options,
        "is_chain": False,
        "selection_mode": selection_mode,
        "search_keyword": search_keyword,
        "match_reason": match_reason,
        "source": "amap",
        "contexts": raw_poi.get("contexts", []),
        "experience_tags": raw_poi.get("experience_tags", []),
    }


def _select_search_keyword(raw_poi: dict, city: str | None, llm_client) -> str:
    raw_name = str(raw_poi.get("raw_name") or "").strip()
    if not llm_client or not raw_name:
        return raw_name
    payload = llm_client.json_chat(
        [
            {
                "role": "system",
                "content": "你是高德地图检索词整理助手。只输出 JSON，给每个用户地点整理一个最适合直接检索的关键词。",
            },
            {
                "role": "user",
                "content": f"""请把地点整理成一个高德地图检索关键词。不要添加多个备选，不要解释。

目的地城市：{city or ""}
原始地点：{raw_name}
地点类别：{raw_poi.get("possible_category", "")}
上下文：{raw_poi.get("contexts", [])}

输出格式：{{"search_keyword":"..."}}""",
            },
        ],
        step="prepare_poi_search_keyword",
        temperature=0,
    )
    keyword = str(payload.get("search_keyword") or "").strip()
    if not keyword or len(keyword) > 40:
        return raw_name
    return keyword


def _select_candidate_with_llm(raw_poi: dict, city: str | None, candidate_options: list[dict], llm_client) -> dict | None:
    if not llm_client or not candidate_options:
        return None
    payload = llm_client.json_chat(
        [
            {
                "role": "system",
                "content": "你是高德候选地点判断助手。根据用户想去的地点和候选结果，选择真正要去的地点。必须输出 JSON。",
            },
            {
                "role": "user",
                "content": f"""请判断候选列表里哪一个最符合用户真正想去的地点。

目的地城市：{city or ""}
原始地点：{raw_poi.get("raw_name", "")}
地点类别：{raw_poi.get("possible_category", "")}
上下文：{raw_poi.get("contexts", [])}
候选列表按 0 开始编号：{candidate_options}

规则：
1. 城市核心地标、商圈、景区要优先选择主地点，不要选择服务台、东街、停车场、出入口、滑板公园等局部或附属地点。
2. 如果候选明显是同一连锁品牌的不同分店，输出 ambiguous。
3. 如果没有可靠候选，输出 unmatched。

输出格式：{{"selected_index":0,"match_status":"matched|ambiguous|unmatched","confidence":0.0,"reason":"..."}}""",
            },
        ],
        step="select_poi_candidate",
        temperature=0,
    )
    try:
        selected_index = int(payload.get("selected_index"))
        confidence = float(payload.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    match_status = str(payload.get("match_status") or "").strip()
    if selected_index < 0 or selected_index >= len(candidate_options):
        return None
    if match_status not in {"matched", "ambiguous", "unmatched"}:
        return None
    return {
        "selected_index": selected_index,
        "match_status": match_status,
        "confidence": max(0.0, min(confidence, 1.0)),
        "reason": str(payload.get("reason") or ""),
    }


def _candidate_option(candidate: dict) -> dict:
    lng, lat = _parse_location(candidate.get("location", ""))
    category_raw = candidate.get("type") or ""
    return {
        "id": candidate.get("id", ""),
        "name": candidate.get("name", ""),
        "address": candidate.get("address") or "",
        "location": {"lng": lng, "lat": lat},
        "city": candidate.get("cityname") or "",
        "district": candidate.get("adname") or "",
        "category_raw": category_raw,
        "category_normalized": _normalize_category(category_raw),
    }


def _is_chain_place(raw_poi: dict, candidates: list[dict]) -> bool:
    raw_name = str(raw_poi.get("raw_name") or "").strip()
    if not raw_name:
        return False
    if any(_is_landmark_candidate(raw_name, candidate) for candidate in candidates[:5]):
        return False
    branch_candidates = [_is_branch_candidate(raw_name, candidate) for candidate in candidates[:5]]
    return sum(branch_candidates) >= 2


def _is_branch_candidate(raw_name: str, candidate: dict) -> bool:
    name = str(candidate.get("name") or "")
    category = str(candidate.get("type") or "")
    if raw_name.lower() not in name.lower():
        return False
    if any(token in category for token in ["道路名", "交通地名", "风景名胜", "公园广场"]):
        return False
    if any(token in name for token in ["服务台", "停车场", "出入口", "东街", "西街", "南街", "北街"]):
        return False
    branch_name = any(token in name for token in ["(", "（", "店", "分店", "旗舰店"])
    branch_category = any(token in category for token in ["餐饮服务", "咖啡厅", "茶饮", "购物服务", "生活服务"])
    return branch_name and branch_category


def _is_landmark_candidate(raw_name: str, candidate: dict) -> bool:
    name = str(candidate.get("name") or "")
    category = str(candidate.get("type") or "")
    if raw_name.lower() not in name.lower():
        return False
    if any(token in name for token in ["服务台", "停车场", "出入口", "东街", "西街", "南街", "北街"]):
        return False
    landmark_category = any(token in category for token in ["风景名胜", "纪念馆", "博物馆", "购物中心", "商场", "商务住宅"])
    branch_marker = any(token in name for token in ["(", "（", "分店"])
    return landmark_category and not branch_marker


def _score_candidate(raw_poi: dict, candidate: dict, city: str | None) -> float:
    raw_name = raw_poi.get("raw_name", "")
    candidate_name = candidate.get("name", "")
    name_similarity = SequenceMatcher(None, raw_name.lower(), candidate_name.lower()).ratio()
    if raw_name and raw_name.lower() in candidate_name.lower():
        name_similarity = max(name_similarity, 0.92)
    city_match = 1.0 if city and city in str(candidate.get("cityname", "")) else 0.5 if not city else 0.0
    category_match = _category_matches(raw_poi.get("possible_category", ""), candidate.get("type", ""))
    context_match = 0.7 if raw_poi.get("contexts") else 0.4
    district_match = 0.7 if candidate.get("adname") else 0.4
    return (
        0.35 * name_similarity
        + 0.25 * city_match
        + 0.15 * category_match
        + 0.15 * context_match
        + 0.10 * district_match
    )


def _category_matches(expected: str, amap_type: str) -> float:
    category = _normalize_category(amap_type)
    if not expected:
        return 0.5
    if expected == category:
        return 1.0
    if expected in {"attraction", "citywalk"} and category in {"attraction", "citywalk", "park"}:
        return 0.7
    return 0.3


def _normalize_category(raw: str) -> str:
    if any(token in raw for token in ["餐饮", "美食", "餐厅"]):
        return "restaurant"
    if any(token in raw for token in ["购物", "商场", "购物中心"]):
        return "shopping_mall"
    if "博物馆" in raw:
        return "museum"
    if "公园" in raw:
        return "park"
    if any(token in raw for token in ["街", "道路", "风景名胜"]):
        return "attraction"
    return raw or "unknown"


def _parse_location(location: str) -> tuple[float | None, float | None]:
    if not location or "," not in location:
        return None, None
    lng, lat = location.split(",", 1)
    try:
        return float(lng), float(lat)
    except ValueError:
        return None, None


def _unmatched(
    raw_poi: dict,
    candidate_count: int = 0,
    confidence: float = 0.0,
    candidate_options: list[dict] | None = None,
    search_keyword: str = "",
) -> dict:
    return {
        "raw_name": raw_poi.get("raw_name", ""),
        "standard_name": "",
        "amap_id": "",
        "address": "",
        "location": {"lng": None, "lat": None},
        "city": "",
        "district": "",
        "category_raw": "",
        "category_normalized": raw_poi.get("possible_category", "unknown"),
        "match_confidence": round(confidence, 3),
        "match_status": "unmatched",
        "candidate_count": candidate_count,
        "candidate_options": candidate_options or [],
        "is_chain": False,
        "selection_mode": "unmatched",
        "search_keyword": search_keyword or raw_poi.get("raw_name", ""),
        "source": "amap",
        "contexts": raw_poi.get("contexts", []),
        "experience_tags": raw_poi.get("experience_tags", []),
    }
