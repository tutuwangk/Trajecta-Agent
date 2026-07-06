from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException

from app.agents.input_parser import parse_user_profile
from app.agents.intensity import sync_day_total_time
from app.agents.itinerary_normalizer import normalize_itinerary
from app.agents.planning_workflow import run_planning_workflow
from app.agents.poi_extractor import extract_poi_names
from app.agents.reviser import revise_from_user_instruction, revise_itinerary
from app.agents.ugc_reader import extract_ugc_items
from app.agents.verifier import verify_itinerary
from app.agents.visit_duration_estimator import estimate_visit_durations
from app.core import api_error, api_success
from app.schemas.models import PoiDecisionUpdate, RevisionRequest, SessionCreate, UserProfile
from app.services.amap_client import default_amap_client
from app.services.cache_service import CacheService
from app.services.chain_arranger import prepare_chain_for_planning
from app.services.database import default_store
from app.services.link_builder import build_navigation_link, build_poi_link
from app.services.llm_client import default_copy_llm_client, default_llm_client, default_planning_llm_client
from app.services.poi_enricher import enrich_pois
from app.services.poi_grounder import ground_pois, ground_single_poi
from app.services.route_service import build_route_edge, build_route_matrix


router = APIRouter()
store = default_store()
logger = logging.getLogger(__name__)


@router.post("/sessions")
def create_session(payload: SessionCreate):
    try:
        user_profile = (
            UserProfile.model_validate(payload.user_profile).model_dump()
            if payload.user_profile
            else parse_user_profile(f"{payload.raw_input}\n{payload.notes}")
        )
        session_id = store.create_session(payload.raw_input, payload.notes, user_profile)
        return api_success({"session_id": session_id, "user_profile": user_profile}, {"create_session": "done"})
    except Exception as exc:
        return api_error(exc, {"create_session": "failed"})


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return api_success(
        {
            **session,
            "pois": store.list_pois(session_id),
            "itinerary_state": store.get_itinerary(session_id),
            "revision_history": store.list_revisions(session_id),
        }
    )


@router.post("/sessions/{session_id}/extract-pois")
def extract_pois(session_id: str):
    return recognize_places(session_id)


@router.post("/sessions/{session_id}/recognize-places")
def recognize_places(session_id: str):
    try:
        session = _require_session(session_id)
        llm_client = default_llm_client()
        amap_client = default_amap_client()
        ugc_items = extract_ugc_items(session["notes"] or session["raw_input"], llm_client)
        raw_pois = extract_poi_names(ugc_items, session["raw_input"])
        grounded_pois = ground_pois(raw_pois, session["user_profile"], amap_client, llm_client)
        store.save_pois(session_id, raw_pois, grounded_pois, session["user_profile"])
        pois = store.list_pois(session_id)
        return api_success(
            {
                "ugc_items": ugc_items,
                "raw_pois": raw_pois,
                "grounded_pois": grounded_pois,
                "pois": pois,
                "place_pool": [row["place_pool_item"] for row in pois],
            },
            {"extract_ugc": "done", "ground_pois": "done", "organize_places": "done"},
        )
    except Exception as exc:
        return api_error(exc, {"recognize_places": "failed"})


@router.patch("/sessions/{session_id}/pois")
def update_pois(session_id: str, payload: PoiDecisionUpdate):
    return update_place_overrides(session_id, payload)


@router.post("/sessions/{session_id}/place-overrides")
def update_place_overrides(session_id: str, payload: PoiDecisionUpdate):
    try:
        session = _require_session(session_id)
        decisions = [decision.model_dump() for decision in payload.decisions]
        has_manual_match = any((decision.get("manual_name") or "").strip() for decision in decisions)
        amap_client = default_amap_client() if has_manual_match else None
        llm_client = default_llm_client() if has_manual_match else None

        def rematch_grounded(raw_poi: dict, current_grounded: dict, manual_name: str) -> dict:
            match_input = {
                **raw_poi,
                "raw_name": manual_name,
                "possible_category": raw_poi.get("possible_category") or current_grounded.get("category_normalized", "unknown"),
                "contexts": raw_poi.get("contexts") or current_grounded.get("contexts", []),
                "experience_tags": raw_poi.get("experience_tags") or current_grounded.get("experience_tags", []),
            }
            return ground_single_poi(match_input, session["user_profile"], amap_client, llm_client)

        store.update_poi_decisions(
            session_id,
            decisions,
            rematch_grounded=rematch_grounded if has_manual_match else None,
        )
        pois = store.list_pois(session_id)
        return api_success(
            {"pois": pois, "place_pool": [row["place_pool_item"] for row in pois]},
            {"update_place_overrides": "done"},
        )
    except Exception as exc:
        return api_error(exc, {"update_pois": "failed"})


@router.post("/sessions/{session_id}/plan")
def create_plan(session_id: str):
    try:
        session = _require_session(session_id)
        pois = store.list_pois(session_id)
        accepted_grounded = _planning_grounded_pois(pois)
        uncertain_pois = enrich_pois(_uncertain_grounded_pois(pois), [])
        planning_llm = default_planning_llm_client()
        copy_llm = default_copy_llm_client()
        amap_client = default_amap_client()
        hotel_anchor = _hotel_anchor(session["user_profile"], amap_client)
        accepted_grounded = _resolve_route_dependent_chains_for_planning(accepted_grounded, hotel_anchor, amap_client)
        runtime_pois = estimate_visit_durations(enrich_pois(accepted_grounded, []), planning_llm)
        route_matrix = build_route_matrix(runtime_pois, amap_client, CacheService(store), session["user_profile"])
        order_constraints = _extract_order_constraints(session["raw_input"], session["notes"], runtime_pois)

        def prepare_itinerary(itinerary: dict) -> None:
            _sync_precise_transport_edges(itinerary, runtime_pois, route_matrix, session["user_profile"], amap_client)
            _sync_hotel_transport_edges(itinerary, runtime_pois, session["user_profile"], amap_client)
            _sync_hotel_rest_breaks(itinerary, runtime_pois, session["user_profile"], amap_client)
            normalize_itinerary(itinerary, session["user_profile"], runtime_pois, route_matrix)

        final, final_verification, _debug = run_planning_workflow(
            session["user_profile"],
            runtime_pois,
            route_matrix,
            planning_llm,
            copy_llm,
            uncertain_pois=uncertain_pois,
            hotel_anchor=hotel_anchor,
            order_constraints=order_constraints,
            prepare_itinerary=prepare_itinerary,
        )
        logger.info("Planning workflow debug snapshot: %s", _debug)
        _clean_final_messages(final, final_verification)
        _attach_links(final, runtime_pois)
        store.save_itinerary(session_id, runtime_pois, route_matrix, final, final_verification)
        return api_success(
            {"runtime_pois": runtime_pois, "route_matrix": route_matrix, "itinerary": final, "verification": final_verification},
            {"build_route_matrix": "done", "plan_itinerary": "done", "verify_itinerary": "done"},
        )
    except Exception as exc:
        return api_error(exc, {"plan": "failed"})


@router.post("/sessions/{session_id}/revise")
def revise_plan(session_id: str, payload: RevisionRequest):
    try:
        session = _require_session(session_id)
        state = store.get_itinerary(session_id)
        if not state:
            raise HTTPException(status_code=400, detail="itinerary not found")
        instruction = payload.quick_action or payload.instruction
        revised = revise_from_user_instruction(
            state["itinerary"],
            instruction,
            session["user_profile"],
            state["runtime_pois"],
            state["route_matrix"],
            default_planning_llm_client(),
        )
        _sync_precise_transport_edges(revised, state["runtime_pois"], state["route_matrix"], session["user_profile"], default_amap_client())
        _sync_hotel_transport_edges(revised, state["runtime_pois"], session["user_profile"], default_amap_client())
        _sync_hotel_rest_breaks(revised, state["runtime_pois"], session["user_profile"], default_amap_client())
        normalize_itinerary(revised, session["user_profile"], state["runtime_pois"], state["route_matrix"])
        verification = verify_itinerary(revised, session["user_profile"], state["route_matrix"], state["runtime_pois"])
        final = revise_itinerary(revised, verification, session["user_profile"], runtime_pois=state["runtime_pois"], instruction=instruction)
        _sync_precise_transport_edges(final, state["runtime_pois"], state["route_matrix"], session["user_profile"], default_amap_client())
        _sync_hotel_transport_edges(final, state["runtime_pois"], session["user_profile"], default_amap_client())
        _sync_hotel_rest_breaks(final, state["runtime_pois"], session["user_profile"], default_amap_client())
        normalize_itinerary(final, session["user_profile"], state["runtime_pois"], state["route_matrix"])
        final_verification = verify_itinerary(final, session["user_profile"], state["route_matrix"], state["runtime_pois"])
        _clean_final_messages(final, final_verification)
        _attach_links(final, state["runtime_pois"])
        store.save_itinerary(session_id, state["runtime_pois"], state["route_matrix"], final, final_verification)
        store.add_revision(session_id, instruction, final)
        return api_success({"itinerary": final, "verification": final_verification}, {"revise_itinerary": "done"})
    except Exception as exc:
        return api_error(exc, {"revise": "failed"})


def _require_session(session_id: str) -> dict:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _attach_links(itinerary: dict, runtime_pois: list[dict]) -> None:
    by_id = {poi["poi_id"]: poi for poi in runtime_pois}
    for day in itinerary.get("days", []):
        items = day.get("items", [])
        for index, item in enumerate(items):
            poi = _resolved_runtime_poi(by_id.get(item.get("poi_id")), item)
            if poi:
                item["amap_link"] = build_poi_link(poi)
            if index >= len(items) - 1:
                continue
            next_poi = _resolved_runtime_poi(by_id.get(items[index + 1].get("poi_id")), items[index + 1])
            if poi and next_poi:
                transport = item.setdefault("transport_to_next", {})
                transport["amap_navigation_link"] = build_navigation_link(poi, next_poi, transport.get("mode", "walking"))


def _sync_transport_edges(itinerary: dict, route_matrix: list[dict]) -> None:
    route_by_pair = {(edge.get("origin_poi_id"), edge.get("destination_poi_id")): edge for edge in route_matrix}
    for day in itinerary.get("days", []):
        items = day.get("items", [])
        segment_index_by_poi = _segment_index_by_poi(day)
        for index, item in enumerate(items):
            if index >= len(items) - 1:
                item.pop("transport_to_next", None)
                continue
            if segment_index_by_poi.get(str(item.get("poi_id"))) != segment_index_by_poi.get(str(items[index + 1].get("poi_id"))):
                item.pop("transport_to_next", None)
                continue
            edge = route_by_pair.get((item.get("poi_id"), items[index + 1].get("poi_id")))
            if not edge:
                item.pop("transport_to_next", None)
                continue
            item["transport_to_next"] = {
                "mode": edge.get("mode", "unknown"),
                "duration_min": edge.get("duration_min"),
                "distance_m": edge.get("distance_m"),
            }


def _sync_precise_transport_edges(itinerary: dict, runtime_pois: list[dict], route_matrix: list[dict], user_profile: dict, amap_client) -> None:
    route_by_pair = {(edge.get("origin_poi_id"), edge.get("destination_poi_id")): edge for edge in route_matrix}
    by_id = {poi.get("poi_id"): poi for poi in runtime_pois}
    for day in itinerary.get("days", []):
        items = day.get("items", [])
        segment_index_by_poi = _segment_index_by_poi(day)
        for index, item in enumerate(items):
            if index >= len(items) - 1:
                item.pop("transport_to_next", None)
                continue
            next_item = items[index + 1]
            if segment_index_by_poi.get(str(item.get("poi_id"))) != segment_index_by_poi.get(str(next_item.get("poi_id"))):
                item.pop("transport_to_next", None)
                continue
            origin = _resolved_runtime_poi(by_id.get(item.get("poi_id")), item)
            destination = _resolved_runtime_poi(by_id.get(next_item.get("poi_id")), next_item)
            edge = None
            if origin and destination and (item.get("selected_branch_id") or next_item.get("selected_branch_id")) and _has_location(origin) and _has_location(destination):
                precise_edge = build_route_edge(origin, destination, amap_client, user_profile)
                if precise_edge.get("duration_min") is not None:
                    edge = precise_edge
            if edge is None:
                edge = route_by_pair.get((item.get("poi_id"), next_item.get("poi_id")))
            if not edge:
                item.pop("transport_to_next", None)
                continue
            item["transport_to_next"] = {
                "mode": edge.get("mode", "unknown"),
                "duration_min": edge.get("duration_min"),
                "distance_m": edge.get("distance_m"),
            }


def _sync_hotel_transport_edges(itinerary: dict, runtime_pois: list[dict], user_profile: dict, amap_client) -> None:
    hotel = _hotel_anchor(user_profile, amap_client)
    if not hotel:
        return
    by_id = {poi.get("poi_id"): poi for poi in runtime_pois}
    for day in itinerary.get("days", []):
        items = day.get("items") or []
        if not items:
            continue
        first = _resolved_runtime_poi(by_id.get(items[0].get("poi_id")), items[0])
        last = _resolved_runtime_poi(by_id.get(items[-1].get("poi_id")), items[-1])
        if first and _has_location(first):
            edge = build_route_edge(hotel, first, amap_client, user_profile)
            if edge.get("duration_min") is not None:
                day["hotel_departure_transport_min"] = edge["duration_min"]
        if last and _has_location(last):
            edge = build_route_edge(last, hotel, amap_client, user_profile)
            if edge.get("duration_min") is not None:
                day["hotel_return_transport_min"] = edge["duration_min"]


def _sync_hotel_rest_breaks(itinerary: dict, runtime_pois: list[dict], user_profile: dict, amap_client) -> None:
    hotel = _hotel_anchor(user_profile, amap_client)
    if not hotel:
        return
    by_id = {poi.get("poi_id"): poi for poi in runtime_pois}
    for day in itinerary.get("days", []):
        day["hotel_rest_breaks"] = []
        segments = day.get("segments") or []
        for index, segment in enumerate(segments):
            if segment.get("kind") != "hotel_rest":
                continue
            previous_outing = _nearest_outing_segment(segments, index, -1)
            next_outing = _nearest_outing_segment(segments, index, 1)
            if not previous_outing or not next_outing:
                continue
            after_poi_id = (previous_outing.get("poi_ids") or [None])[-1]
            before_poi_id = (next_outing.get("poi_ids") or [None])[0]
            after_item = next((item for item in day.get("items", []) if item.get("poi_id") == after_poi_id), None)
            before_item = next((item for item in day.get("items", []) if item.get("poi_id") == before_poi_id), None)
            after_poi = _resolved_runtime_poi(by_id.get(after_poi_id), after_item)
            before_poi = _resolved_runtime_poi(by_id.get(before_poi_id), before_item)
            if not after_poi or not before_poi:
                continue
            return_edge = build_route_edge(after_poi, hotel, amap_client, user_profile)
            depart_edge = build_route_edge(hotel, before_poi, amap_client, user_profile)
            day["hotel_rest_breaks"].append(
                {
                    "after_poi_id": after_poi_id,
                    "before_poi_id": before_poi_id,
                    "duration_min": int(segment.get("duration_min") or 0),
                    "reason": str(segment.get("reason") or "回酒店休息").strip(),
                    "return_to_hotel_transport_min": return_edge.get("duration_min") or 0,
                    "depart_from_hotel_transport_min": depart_edge.get("duration_min") or 0,
                }
            )


def _hotel_anchor(user_profile: dict, amap_client) -> dict | None:
    hotel_name = user_profile.get("hotel_name")
    if not hotel_name:
        return None
    location = None
    geocode = amap_client.geocode(hotel_name, user_profile.get("destination"))
    if geocode:
        location = _location_from_value(geocode.get("location"))
    if not location:
        candidates = amap_client.search_poi(hotel_name, user_profile.get("destination"))
        if candidates:
            location = _location_from_value(candidates[0].get("location"))
    if not location:
        return None
    return {
        "poi_id": "hotel",
        "standard_name": hotel_name,
        "city": user_profile.get("destination", ""),
        "location": location,
        "match_status": "matched",
    }


def _location_from_value(value) -> dict | None:
    if isinstance(value, dict) and value.get("lng") is not None and value.get("lat") is not None:
        return value
    if isinstance(value, str) and "," in value:
        lng, lat = value.split(",", 1)
        try:
            return {"lng": float(lng), "lat": float(lat)}
        except ValueError:
            return None
    return None


def _has_location(poi: dict) -> bool:
    location = poi.get("location") or {}
    return location.get("lng") is not None and location.get("lat") is not None


def _sync_itinerary_timing(itinerary: dict) -> None:
    for day in itinerary.get("days", []):
        sync_day_total_time(day)


def _clean_final_messages(itinerary: dict, verification: dict | None = None) -> None:
    context = _final_message_context(itinerary, verification or {"issues": []})
    itinerary["global_risks"] = _clean_message_list(itinerary.get("global_risks", []), context)
    itinerary["revision_notes"] = _clean_message_list(itinerary.get("revision_notes", []), context)


def _clean_message_list(values, context: dict) -> list[str]:
    messages = values if isinstance(values, list) else [values]
    cleaned = []
    for value in messages:
        text = str(value or "").strip()
        if not text or _is_stale_or_technical_message(text, context):
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


def _is_stale_or_technical_message(text: str, context: dict) -> bool:
    if any(name and name in text for name in context["unscheduled_names"]):
        return True
    if not context["has_time_over_issue"] and any(token in text for token in ["超上限", "超过当前强度", "超过所选行程强度"]):
        return True
    if _looks_like_non_user_facing_english(text):
        return True
    return (
        "矩阵数据" in text
        or "按20分钟估算" in text
        or "estimated_duration_min" in text
        or "route matrix" in text.lower()
        or "cache hit" in text.lower()
        or "fallback nearby" in text.lower()
        or ("酒店" in text and "估算" in text)
        or text.startswith("Day ") and "预计总耗时约" in text
        or text == "缩短停留时间，减少移动距离，或把部分地点拆到其他天。"
    )


def _final_message_context(itinerary: dict, verification: dict) -> dict:
    scheduled_names = {
        item.get("name")
        for day in itinerary.get("days", [])
        for item in day.get("items", [])
        if item.get("name")
    }
    unscheduled_names = {
        item.get("name")
        for item in itinerary.get("unscheduled_places", [])
        if item.get("name") and item.get("name") not in scheduled_names
    }
    return {
        "unscheduled_names": unscheduled_names,
        "has_time_over_issue": any(issue.get("type") == "daily_time_over_intensity_limit" for issue in verification.get("issues", [])),
    }


def _looks_like_non_user_facing_english(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if re.search(r"[\u4e00-\u9fff]", normalized):
        return False
    return bool(re.search(r"[A-Za-z]{3,}", normalized))


def _planning_grounded_pois(rows: list[dict]) -> list[dict]:
    return [
        _grounded_with_decision(row)
        for row in rows
        if _is_plannable(row) and _has_plannable_location(row)
    ]


def _uncertain_grounded_pois(rows: list[dict]) -> list[dict]:
    return [
        _grounded_with_decision(row)
        for row in rows
        if _is_attention(row)
    ]


def _is_plannable(row: dict) -> bool:
    if "final_decision" in row:
        return row.get("final_decision") in {"include", "optional"}
    return row.get("decision") in {"keep", "must_visit", "optional"}


def _is_attention(row: dict) -> bool:
    if row.get("final_decision") == "exclude":
        return False
    if row.get("final_decision") == "unresolved":
        return True
    return row.get("decision") in {"keep", "must_visit", "optional"} and row["grounded_poi"].get("match_status") != "matched"


def _grounded_with_decision(row: dict) -> dict:
    grounded = dict(row["grounded_poi"])
    for key in ("system_decision", "user_override", "final_decision", "inferred_role", "decision_reason"):
        if key in row:
            grounded[key] = row[key]
    return grounded


def _has_plannable_location(row: dict) -> bool:
    grounded = row["grounded_poi"]
    if grounded.get("match_status") == "matched":
        return True
    if row.get("user_override") == "arrange_nearby" and grounded.get("is_chain") and grounded.get("candidate_options"):
        return True
    location = grounded.get("location") or {}
    return (
        row.get("user_override") == "must_include"
        and grounded.get("match_status") == "ambiguous"
        and bool(grounded.get("amap_id"))
        and location.get("lng") is not None
        and location.get("lat") is not None
    )


def _resolve_route_dependent_chains_for_planning(grounded_pois: list[dict], hotel_anchor: dict | None, amap_client) -> list[dict]:
    anchors = [
        poi
        for poi in grounded_pois
        if _has_location(poi) and not poi.get("is_chain") and (poi.get("user_override") == "must_include" or poi.get("final_decision") == "include")
    ]
    if hotel_anchor:
        anchors.append(hotel_anchor)
    if not anchors:
        anchors = [poi for poi in grounded_pois if _has_location(poi) and not poi.get("is_chain")]
    resolved: list[dict] = []
    for poi in grounded_pois:
        if poi.get("is_chain") and poi.get("user_override") == "arrange_nearby" and poi.get("candidate_options"):
            resolved.append(prepare_chain_for_planning(poi, anchors, amap_client))
            continue
        resolved.append(poi)
    return resolved


def _extract_order_constraints(raw_input: str, notes: str, runtime_pois: list[dict]) -> list[dict]:
    text = f"{raw_input}\n{notes}"
    ranked: list[tuple[int, str]] = []
    for poi in runtime_pois:
        name = str(poi.get("standard_name") or poi.get("raw_name") or "").strip()
        evidence = " ".join(str(item) for item in poi.get("ugc_evidence") or [])
        combined = f"{text}\n{evidence}"
        rank = _order_rank_for_text(combined, name)
        if rank is not None:
            ranked.append((rank, name))
    ranked.sort(key=lambda item: item[0])
    constraints: list[dict] = []
    for index in range(len(ranked) - 1):
        before = ranked[index][1]
        after = ranked[index + 1][1]
        if before and after and before != after:
            constraints.append({"before": before, "after": after, "strength": "strong_preference", "source": "user_text"})
    return constraints


def _resolved_runtime_poi(runtime_poi: dict | None, item: dict | None) -> dict | None:
    if not runtime_poi:
        return None
    selected_branch_id = str((item or {}).get("selected_branch_id") or "").strip()
    if not selected_branch_id:
        return runtime_poi
    for option in runtime_poi.get("route_branch_options") or []:
        if str(option.get("branch_id") or "") != selected_branch_id:
            continue
        resolved = dict(runtime_poi)
        resolved.update(
            {
                "amap_id": selected_branch_id,
                "standard_name": option.get("name") or runtime_poi.get("standard_name") or runtime_poi.get("raw_name") or "",
                "address": option.get("address", runtime_poi.get("address", "")),
                "location": option.get("location", runtime_poi.get("location", {})),
                "city": option.get("city", runtime_poi.get("city", "")),
                "district": option.get("district", runtime_poi.get("district", "")),
                "category_raw": option.get("category_raw", runtime_poi.get("category_raw", "")),
                "category_normalized": option.get("category_normalized", runtime_poi.get("category_normalized", "")),
            }
        )
        return resolved
    return runtime_poi


def _order_rank_for_text(text: str, name: str) -> int | None:
    if not name:
        return None
    if any(token in text for token in [f"先去{name}", f"先逛{name}", f"第一站{name}", f"上午先去{name}"]):
        return 0
    if any(token in text for token in [f"再去{name}", f"接着去{name}", f"然后去{name}", f"下一站{name}"]):
        return 1
    if any(token in text for token in [f"最后去{name}", f"晚上去{name}", f"夜里去{name}", f"收尾去{name}"]):
        return 2
    return None


def _segment_index_by_poi(day: dict) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, segment in enumerate(day.get("segments") or []):
        if segment.get("kind") != "outing":
            continue
        for poi_id in segment.get("poi_ids") or []:
            result[str(poi_id)] = index
    return result


def _nearest_outing_segment(segments: list[dict], start_index: int, step: int) -> dict | None:
    index = start_index + step
    while 0 <= index < len(segments):
        segment = segments[index]
        if segment.get("kind") == "outing":
            return segment
        index += step
    return None
