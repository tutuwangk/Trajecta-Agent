from __future__ import annotations

from difflib import SequenceMatcher


def ground_pois(raw_pois: list[dict], user_profile: dict, amap_client) -> list[dict]:
    return [ground_single_poi(raw_poi, user_profile, amap_client) for raw_poi in raw_pois]


def ground_single_poi(raw_poi: dict, user_profile: dict, amap_client) -> dict:
    raw_name = raw_poi.get("raw_name", "")
    city = user_profile.get("destination") or None
    candidates = amap_client.search_poi(raw_name, city=city)
    if not candidates:
        return _unmatched(raw_poi)

    scored = sorted(
        ((_score_candidate(raw_poi, candidate, city), candidate) for candidate in candidates),
        key=lambda item: item[0],
    )
    score, best = scored[-1]
    status = "matched" if score >= 0.8 else "ambiguous" if score >= 0.55 else "unmatched"
    if status == "unmatched":
        return _unmatched(raw_poi, candidate_count=len(candidates), confidence=score)

    lng, lat = _parse_location(best.get("location", ""))
    return {
        "raw_name": raw_name,
        "standard_name": best.get("name", ""),
        "amap_id": best.get("id", ""),
        "address": best.get("address") or "",
        "location": {"lng": lng, "lat": lat},
        "city": best.get("cityname") or "",
        "district": best.get("adname") or "",
        "category_raw": best.get("type") or "",
        "category_normalized": _normalize_category(best.get("type") or raw_poi.get("possible_category", "")),
        "match_confidence": round(score, 3),
        "match_status": status,
        "candidate_count": len(candidates),
        "source": "amap",
        "contexts": raw_poi.get("contexts", []),
        "experience_tags": raw_poi.get("experience_tags", []),
    }


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


def _unmatched(raw_poi: dict, candidate_count: int = 0, confidence: float = 0.0) -> dict:
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
        "source": "amap",
        "contexts": raw_poi.get("contexts", []),
        "experience_tags": raw_poi.get("experience_tags", []),
    }
