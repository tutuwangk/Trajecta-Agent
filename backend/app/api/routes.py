from __future__ import annotations

from copy import deepcopy
import logging
import re
from time import perf_counter

from fastapi import APIRouter, HTTPException

from app.agents.input_parser import parse_user_profile
from app.agents.intensity import sync_day_total_time
from app.agents.itinerary_normalizer import normalize_itinerary
from app.agents.planning_workflow import PlanningInterventionRequired, run_planning_workflow
from app.agents.poi_extractor import extract_poi_names
from app.agents.ugc_reader import extract_ugc_items
from app.core import AppError, api_error, api_success
from app.agents.visit_duration_estimator import estimate_visit_durations
from app.schemas.models import PlanningDecisionRequest, PoiDecisionUpdate, RevisionRequest, SessionCreate, UserProfile
from app.services.amap_client import default_amap_client
from app.services.chain_arranger import arrange_chain_to_anchor
from app.services.database import default_store
from app.services.link_builder import build_navigation_link, build_poi_link
from app.services.llm_client import default_copy_llm_client, default_llm_client, default_planning_llm_client
from app.services.poi_enricher import enrich_pois
from app.services.poi_grounder import ground_pois, ground_single_poi
from app.services.route_service import build_route_edge, build_spatial_route_matrix


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
            "planning_intervention": store.get_open_planning_intervention(session_id),
            "latest_planning_run": store.get_latest_planning_run(session_id),
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
        raw_pois = extract_poi_names(ugc_items, f"{session['raw_input']}\n{session['notes']}")
        grounded_pois = ground_pois(raw_pois, session["user_profile"], amap_client)
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
        has_arrange_confirmation = any(decision.get("decision") == "confirm_arrange_nearby" for decision in decisions)
        has_manual_match = any((decision.get("manual_name") or "").strip() for decision in decisions)
        amap_client = default_amap_client() if (has_manual_match or has_arrange_confirmation) else None
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

        def arrange_nearby_grounded(raw_poi: dict, current_grounded: dict, anchor_row: dict, user_profile: dict) -> dict:
            anchor_poi = dict(anchor_row.get("grounded_poi") or {})
            if anchor_row.get("poi_id") == "hotel_anchor":
                anchor_poi = _hotel_anchor(user_profile, amap_client)
                anchor_poi["poi_id"] = "hotel_anchor"
                anchor_poi["standard_name"] = anchor_poi.get("standard_name") or user_profile.get("hotel_name") or user_profile.get("hotel_area") or "酒店"
            else:
                anchor_poi["poi_id"] = anchor_row.get("poi_id")
            return arrange_chain_to_anchor(current_grounded, anchor_poi, amap_client)

        store.update_poi_decisions(
            session_id,
            decisions,
            rematch_grounded=rematch_grounded if has_manual_match else None,
            arrange_nearby_grounded=arrange_nearby_grounded if has_arrange_confirmation else None,
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
        data, step_status = _plan_session(session_id, session)
        return api_success(data, step_status)
    except Exception as exc:
        return api_error(exc, {"plan": "failed"})


@router.post("/sessions/{session_id}/planning-decisions")
def submit_planning_decision(session_id: str, payload: PlanningDecisionRequest):
    try:
        session = _require_session(session_id)
        provisional_decision = store.preview_planning_decision(session_id, payload.intervention_id, payload.choice_id)
        data, step_status = _plan_session(
            session_id,
            session,
            provisional_planning_decision=provisional_decision,
        )
        if data.get("status") == "completed":
            store.resolve_planning_intervention(session_id, payload.intervention_id, payload.choice_id)
        return api_success(data, {"planning_decision": "done", **step_status})
    except Exception as exc:
        return api_error(exc, {"planning_decision": "failed"})


@router.post("/sessions/{session_id}/revise")
def revise_plan(session_id: str, payload: RevisionRequest):
    try:
        session = _require_session(session_id)
        instruction = payload.quick_action or payload.instruction
        state = store.get_itinerary(session_id)
        if not state:
            raise HTTPException(status_code=400, detail="itinerary not found")
        revision_intent = _compile_revision_intent(instruction, store.list_pois(session_id))
        data, step_status = _plan_session(session_id, session, revision_intent=revision_intent)
        return api_success(data, {"revise_itinerary": "done", **step_status})
    except Exception as exc:
        return api_error(exc, {"revise": "failed"})


def _plan_session(
    session_id: str,
    session: dict,
    revision_intent: dict | None = None,
    provisional_planning_decision: dict | None = None,
) -> tuple[dict, dict]:
    run_id = store.start_planning_run(session_id)
    started_at = perf_counter()
    store.update_planning_run(run_id, status="running", stage="planning")
    try:
        data, step_status = _execute_plan_session(
            session_id,
            session,
            revision_intent=revision_intent,
            provisional_planning_decision=provisional_planning_decision,
            run_id=run_id,
        )
        run_debug = data.pop("_run_debug", {})
        data["run_id"] = run_id
        if data.get("status") == "needs_user_choice":
            store.update_planning_run(
                run_id,
                status="needs_user_choice",
                stage="preference_resolution",
                attempt_count=1,
                duration_ms=round((perf_counter() - started_at) * 1000),
            )
        else:
            store.update_planning_run(
                run_id,
                status="completed",
                stage="completed",
                attempt_count=int(run_debug.get("attempt_count") or 1),
                duration_ms=round((perf_counter() - started_at) * 1000),
                debug=run_debug,
            )
        return data, step_status
    except Exception as exc:
        code = exc.code if isinstance(exc, AppError) else "internal_error"
        stage = exc.step if isinstance(exc, AppError) and exc.step else "planning"
        message = exc.message if isinstance(exc, AppError) else str(exc)
        debug = exc.details if isinstance(exc, AppError) else {}
        store.update_planning_run(
            run_id,
            status="failed",
            stage=stage,
            error_code=code,
            error_message=message,
            attempt_count=1,
            duration_ms=round((perf_counter() - started_at) * 1000),
            debug=debug,
        )
        if isinstance(exc, AppError):
            exc.details.setdefault("run_id", run_id)
        raise


def _execute_plan_session(
    session_id: str,
    session: dict,
    revision_intent: dict | None = None,
    provisional_planning_decision: dict | None = None,
    run_id: str | None = None,
) -> tuple[dict, dict]:
    pois = store.list_pois(session_id)
    accepted_grounded = _planning_grounded_pois(pois)
    user_profile = _planning_user_profile(session["user_profile"], revision_intent)
    planning_llm = default_planning_llm_client()
    copy_llm = default_copy_llm_client()
    amap_client = default_amap_client()
    uncertain_pois = enrich_pois(_uncertain_grounded_pois(pois), [])
    hotel_anchor = _hotel_anchor(user_profile, amap_client)
    runtime_pois = estimate_visit_durations(enrich_pois(accepted_grounded, []))
    route_matrix = build_spatial_route_matrix(runtime_pois, user_profile)
    order_constraints = _extract_order_constraints(session["raw_input"], session["notes"], runtime_pois)
    time_constraints = _extract_time_constraints(session["raw_input"], session["notes"], runtime_pois)
    planning_decisions = store.list_resolved_planning_decisions(session_id)
    if provisional_planning_decision:
        planning_decisions.append(provisional_planning_decision)
    if revision_intent:
        order_constraints.extend(_revision_order_constraints(revision_intent, runtime_pois))
        time_constraints.extend(_revision_time_constraints(revision_intent, runtime_pois))
        planning_decisions.extend(revision_intent.get("planning_decisions") or [])
    precise_edge_cache: dict[tuple[str, str], dict] = {}

    def prepare_itinerary(itinerary: dict) -> None:
        _sync_precise_transport_edges(itinerary, runtime_pois, route_matrix, user_profile, amap_client)
        _sync_hotel_transport_edges(
            itinerary,
            runtime_pois,
            user_profile,
            amap_client,
            hotel=hotel_anchor,
            edge_cache=precise_edge_cache,
        )
        _sync_hotel_rest_breaks(
            itinerary,
            runtime_pois,
            user_profile,
            amap_client,
            hotel=hotel_anchor,
            edge_cache=precise_edge_cache,
        )
        normalize_itinerary(itinerary, user_profile, runtime_pois, route_matrix)

    try:
        final, final_verification, debug = run_planning_workflow(
            user_profile,
            runtime_pois,
            route_matrix,
            planning_llm,
            copy_llm,
            uncertain_pois=uncertain_pois,
            hotel_anchor=hotel_anchor,
            order_constraints=order_constraints,
            time_constraints=time_constraints,
            planning_decisions=planning_decisions,
            prepare_itinerary=prepare_itinerary,
        )
    except PlanningInterventionRequired as exc:
        intervention = dict(exc.intervention)
        intervention_id = store.save_planning_intervention(session_id, intervention)
        intervention["id"] = intervention_id
        return (
            {"status": "needs_user_choice", "planning_intervention": intervention},
            {"build_route_matrix": "done", "plan_itinerary": "needs_user_choice"},
        )

    logger.info("Planning workflow debug snapshot: %s", debug)
    _assert_publishable(final_verification, run_id=run_id)
    _clean_final_messages(final, final_verification)
    _attach_links(final, runtime_pois)
    store.save_itinerary(session_id, runtime_pois, route_matrix, final, final_verification)
    if revision_intent and revision_intent.get("instruction"):
        store.add_revision(session_id, revision_intent["instruction"], final)
    return (
        {
            "status": "completed",
            "runtime_pois": runtime_pois,
            "route_matrix": route_matrix,
            "itinerary": final,
            "verification": final_verification,
            "_run_debug": {
                "attempt_count": len(debug.get("skeleton_versions") or []) or 1,
                "repair_attempts": debug.get("repair_attempts", 0),
            },
        },
        {"build_route_matrix": "done", "plan_itinerary": "done", "verify_itinerary": "done"},
    )


def _assert_publishable(verification: dict, run_id: str | None = None) -> None:
    if verification.get("passed"):
        return
    issues = list(verification.get("issues") or [])
    raise AppError(
        "路线未通过发布校验，系统没有保存这次不完整结果。",
        code="itinerary_publish_blocked",
        step="verify_itinerary",
        details={"issues": issues, **({"run_id": run_id} if run_id else {})},
    )


def _planning_user_profile(base_user_profile: dict, revision_intent: dict | None = None) -> dict:
    user_profile = deepcopy(base_user_profile)
    constraints = dict(user_profile.get("constraints") or {})
    user_profile["constraints"] = constraints
    if revision_intent:
        avoid_visit_names = list(constraints.get("avoid_visit") or [])
        must_visit_names = [str(name) for name in constraints.get("must_visit") or []]
        for name in revision_intent.get("avoid_visit_names") or []:
            if name not in avoid_visit_names:
                avoid_visit_names.append(name)
            must_visit_names = [item for item in must_visit_names if item != name]
        if revision_intent.get("pace_mode") == "slow_down":
            constraints["avoid_too_tired"] = True
            constraints["physical_intensity"] = "low"
        constraints["avoid_visit"] = avoid_visit_names
        constraints["must_visit"] = must_visit_names
    return user_profile


def _compile_revision_intent(instruction: str, pois: list[dict]) -> dict:
    text = str(instruction or "").strip()
    if not text:
        raise AppError("请告诉我想怎么改路线。", code="invalid_revision_instruction", step="revise")
    if _looks_like_unsupported_revision_scope(text):
        raise AppError(
            "这类调整会改变酒店、天数、交通偏好或资料事实，请回到行程设置修改后重新生成路线。",
            code="unsupported_revision_scope",
            step="revise",
        )
    intent = {
        "instruction": text,
        "avoid_visit_names": _extract_revision_avoid_names(text, pois),
        "planning_decisions": [],
        "extract_order_constraints": _has_order_signal(text),
        "extract_time_constraints": _has_time_signal(text),
        "pace_mode": "slow_down" if _has_slow_pace_signal(text) else "",
    }
    if not intent["avoid_visit_names"] and not intent["extract_order_constraints"] and not intent["extract_time_constraints"] and not intent["pace_mode"]:
        raise AppError(
            "这次只支持删点、调整先后顺序、晨间/夜间偏好和节奏调整。酒店、天数、交通或资料修改请回到行程设置处理。",
            code="unsupported_revision_scope",
            step="revise",
        )
    return intent


def _revision_order_constraints(revision_intent: dict, runtime_pois: list[dict]) -> list[dict]:
    if not revision_intent.get("extract_order_constraints"):
        return []
    return _extract_order_constraints(revision_intent.get("instruction", ""), "", runtime_pois)


def _revision_time_constraints(revision_intent: dict, runtime_pois: list[dict]) -> list[dict]:
    constraints: list[dict] = []
    if revision_intent.get("extract_time_constraints"):
        constraints.extend(_extract_time_constraints(revision_intent.get("instruction", ""), "", runtime_pois))
    constraints.extend(_extract_single_place_revision_constraints(revision_intent.get("instruction", ""), runtime_pois))
    return constraints


def _extract_revision_avoid_names(text: str, pois: list[dict]) -> list[str]:
    if not any(token in text for token in ["不要", "别去", "避开", "删掉", "删除", "去掉"]):
        return []
    names: list[str] = []
    for row in pois:
        for name in _poi_constraint_names(_grounded_with_decision(row)):
            if name and name in text and name not in names:
                names.append(name)
                break
    return names


def _extract_single_place_revision_constraints(text: str, runtime_pois: list[dict]) -> list[dict]:
    constraints: list[dict] = []
    for poi in runtime_pois:
        poi_id = str(poi.get("poi_id") or "").strip()
        for name in _poi_constraint_names(poi):
            if not name or name not in text:
                continue
            if any(token in text for token in [f"把{name}放前面", f"{name}放前面", f"前置{name}"]):
                constraints.append({"poi_id": poi_id, "name": name, "preferred_window": "morning", "strength": "quasi_hard", "source_text": f"前置{name}"})
                break
            if any(token in text for token in [f"把{name}放后面", f"{name}放后面", f"后置{name}"]):
                constraints.append({"poi_id": poi_id, "name": name, "preferred_window": "evening", "strength": "quasi_hard", "source_text": f"后置{name}"})
                break
    return constraints


def _looks_like_unsupported_revision_scope(text: str) -> bool:
    unsupported_patterns = [
        r"酒店",
        r"住在",
        r"换酒店",
        r"改.*天",
        r"多住",
        r"少住",
        r"增加.*天",
        r"减少.*天",
        r"交通偏好",
        r"只坐地铁",
        r"只打车",
        r"自驾",
        r"公共交通",
        r"资料",
        r"笔记",
        r"结构化",
    ]
    return any(re.search(pattern, text) for pattern in unsupported_patterns)


def _has_order_signal(text: str) -> bool:
    tokens = ["先去", "先逛", "第一站", "再去", "接着去", "然后去", "最后去", "收尾去", "前置", "后置", "放前面", "放后面"]
    return any(token in text for token in tokens)


def _has_time_signal(text: str) -> bool:
    tokens = ["白天", "上午", "早上", "午后", "下午", "傍晚", "晚上", "夜里", "夜晚", "夜间"]
    return any(token in text for token in tokens)


def _has_slow_pace_signal(text: str) -> bool:
    tokens = ["放慢节奏", "慢一点", "轻松一点", "别太赶", "不要太赶", "宽松一点"]
    return any(token in text for token in tokens)


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
    route_index_by_pair = {
        (edge.get("origin_poi_id"), edge.get("destination_poi_id")): index
        for index, edge in enumerate(route_matrix)
    }
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
            pair = (item.get("poi_id"), next_item.get("poi_id"))
            edge = route_by_pair.get(pair)
            if (
                origin
                and destination
                and _has_location(origin)
                and _has_location(destination)
                and (not edge or edge.get("source") != "amap_direction_api")
            ):
                precise_edge = build_route_edge(origin, destination, amap_client, user_profile)
                if precise_edge.get("duration_min") is not None:
                    edge = precise_edge
                    route_by_pair[pair] = precise_edge
                    matrix_index = route_index_by_pair.get(pair)
                    if matrix_index is None:
                        route_index_by_pair[pair] = len(route_matrix)
                        route_matrix.append(precise_edge)
                    else:
                        route_matrix[matrix_index] = precise_edge
            if not edge:
                item.pop("transport_to_next", None)
                continue
            item["transport_to_next"] = {
                "mode": edge.get("mode", "unknown"),
                "duration_min": edge.get("duration_min"),
                "distance_m": edge.get("distance_m"),
            }


def _sync_hotel_transport_edges(
    itinerary: dict,
    runtime_pois: list[dict],
    user_profile: dict,
    amap_client,
    hotel: dict | None = None,
    edge_cache: dict[tuple[str, str], dict] | None = None,
) -> None:
    hotel = hotel if hotel is not None else _hotel_anchor(user_profile, amap_client)
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
            edge = _cached_precise_edge(hotel, first, amap_client, user_profile, edge_cache)
            if edge.get("duration_min") is not None:
                day["hotel_departure_transport_min"] = edge["duration_min"]
        if last and _has_location(last):
            edge = _cached_precise_edge(last, hotel, amap_client, user_profile, edge_cache)
            if edge.get("duration_min") is not None:
                day["hotel_return_transport_min"] = edge["duration_min"]


def _sync_hotel_rest_breaks(
    itinerary: dict,
    runtime_pois: list[dict],
    user_profile: dict,
    amap_client,
    hotel: dict | None = None,
    edge_cache: dict[tuple[str, str], dict] | None = None,
) -> None:
    hotel = hotel if hotel is not None else _hotel_anchor(user_profile, amap_client)
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
            return_edge = _cached_precise_edge(after_poi, hotel, amap_client, user_profile, edge_cache)
            depart_edge = _cached_precise_edge(hotel, before_poi, amap_client, user_profile, edge_cache)
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


def _cached_precise_edge(
    origin: dict,
    destination: dict,
    amap_client,
    user_profile: dict,
    edge_cache: dict[tuple[str, str], dict] | None,
) -> dict:
    origin_location = origin.get("location") or {}
    destination_location = destination.get("location") or {}
    key = (
        f"{origin_location.get('lng')},{origin_location.get('lat')}",
        f"{destination_location.get('lng')},{destination_location.get('lat')}",
    )
    if edge_cache is not None and key in edge_cache:
        return edge_cache[key]
    edge = build_route_edge(origin, destination, amap_client, user_profile)
    if edge_cache is not None:
        edge_cache[key] = edge
    return edge


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
    location = grounded.get("location") or {}
    return (
        row.get("user_override") == "must_include"
        and grounded.get("match_status") == "ambiguous"
        and bool(grounded.get("amap_id"))
        and location.get("lng") is not None
        and location.get("lat") is not None
    )


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


def _extract_time_constraints(raw_input: str, notes: str, runtime_pois: list[dict]) -> list[dict]:
    text = f"{raw_input}\n{notes}"
    constraints: list[dict] = []
    for poi in runtime_pois:
        poi_id = str(poi.get("poi_id") or "").strip()
        if not poi_id:
            continue
        names = _poi_constraint_names(poi)
        for name in names:
            matched = _time_constraint_for_name(text, name)
            if not matched:
                continue
            preferred_window, source_text = matched
            constraints.append(
                {
                    "poi_id": poi_id,
                    "name": name,
                    "preferred_window": preferred_window,
                    "strength": "quasi_hard",
                    "source_text": source_text,
                }
            )
            break
    return constraints


def _poi_constraint_names(poi: dict) -> list[str]:
    raw_values = [
        poi.get("standard_name"),
        poi.get("raw_name"),
        poi.get("name"),
        *(poi.get("raw_names") or []),
    ]
    names: list[str] = []
    for value in raw_values:
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def _time_constraint_for_name(text: str, name: str) -> tuple[str, str] | None:
    patterns = [
        ("evening", [f"晚上去{name}", f"夜里去{name}", f"夜晚去{name}", f"傍晚去{name}", f"最后去{name}", f"收尾去{name}"]),
        ("morning", [f"上午去{name}", f"早上去{name}", f"先去{name}", f"第一站{name}", f"上午先去{name}"]),
        ("afternoon", [f"下午去{name}", f"午后去{name}"]),
        ("midday", [f"白天去{name}", f"白天逛{name}"]),
    ]
    for window, phrases in patterns:
        for phrase in phrases:
            if phrase in text:
                return window, phrase
    suffix_patterns = [
        ("evening", [f"{name}晚上", f"{name}夜景", f"{name}夜生活"]),
        ("morning", [f"{name}上午", f"{name}早上"]),
        ("midday", [f"{name}白天"]),
        ("afternoon", [f"{name}下午"]),
    ]
    for window, phrases in suffix_patterns:
        for phrase in phrases:
            if phrase in text:
                return window, phrase
    return None


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
    if any(token in text for token in [f"先去{name}", f"先{name}", f"先逛{name}", f"第一站{name}", f"上午先去{name}"]):
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
