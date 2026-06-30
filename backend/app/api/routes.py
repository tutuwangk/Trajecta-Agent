from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agents.input_parser import parse_user_profile
from app.agents.planner import plan_itinerary
from app.agents.poi_extractor import extract_poi_names
from app.agents.reviser import revise_from_user_instruction, revise_itinerary
from app.agents.ugc_reader import extract_ugc_items
from app.agents.verifier import verify_itinerary
from app.core import api_error, api_success
from app.schemas.models import PoiDecisionUpdate, RevisionRequest, SessionCreate
from app.services.amap_client import default_amap_client
from app.services.cache_service import CacheService
from app.services.database import default_store
from app.services.link_builder import build_navigation_link, build_poi_link
from app.services.llm_client import default_llm_client
from app.services.poi_enricher import enrich_pois
from app.services.poi_grounder import ground_pois, ground_single_poi
from app.services.route_service import build_route_matrix


router = APIRouter()
store = default_store()


@router.post("/sessions")
def create_session(payload: SessionCreate):
    try:
        user_profile = payload.user_profile or parse_user_profile(f"{payload.raw_input}\n{payload.notes}")
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
    try:
        session = _require_session(session_id)
        llm_client = default_llm_client()
        amap_client = default_amap_client()
        ugc_items = extract_ugc_items(session["notes"] or session["raw_input"], llm_client)
        raw_pois = extract_poi_names(ugc_items, session["raw_input"])
        grounded_pois = ground_pois(raw_pois, session["user_profile"], amap_client)
        store.save_pois(session_id, raw_pois, grounded_pois)
        return api_success(
            {"ugc_items": ugc_items, "raw_pois": raw_pois, "grounded_pois": grounded_pois, "pois": store.list_pois(session_id)},
            {"extract_ugc": "done", "ground_pois": "done"},
        )
    except Exception as exc:
        return api_error(exc, {"extract_pois": "failed"})


@router.patch("/sessions/{session_id}/pois")
def update_pois(session_id: str, payload: PoiDecisionUpdate):
    try:
        session = _require_session(session_id)
        decisions = [decision.model_dump() for decision in payload.decisions]
        has_manual_match = any((decision.get("manual_name") or "").strip() for decision in decisions)
        amap_client = default_amap_client() if has_manual_match else None

        def rematch_grounded(raw_poi: dict, current_grounded: dict, manual_name: str) -> dict:
            match_input = {
                **raw_poi,
                "raw_name": manual_name,
                "possible_category": raw_poi.get("possible_category") or current_grounded.get("category_normalized", "unknown"),
                "contexts": raw_poi.get("contexts") or current_grounded.get("contexts", []),
                "experience_tags": raw_poi.get("experience_tags") or current_grounded.get("experience_tags", []),
            }
            return ground_single_poi(match_input, session["user_profile"], amap_client)

        store.update_poi_decisions(session_id, decisions, rematch_grounded=rematch_grounded if has_manual_match else None)
        return api_success({"pois": store.list_pois(session_id)}, {"update_pois": "done"})
    except Exception as exc:
        return api_error(exc, {"update_pois": "failed"})


@router.post("/sessions/{session_id}/plan")
def create_plan(session_id: str):
    try:
        session = _require_session(session_id)
        pois = store.list_pois(session_id)
        accepted_grounded = _planning_grounded_pois(pois)
        uncertain_pois = enrich_pois(_uncertain_grounded_pois(pois), [])
        runtime_pois = enrich_pois(accepted_grounded, [])
        route_matrix = build_route_matrix(runtime_pois, default_amap_client(), CacheService(store))
        draft = plan_itinerary(session["user_profile"], runtime_pois, route_matrix, default_llm_client())
        if uncertain_pois:
            draft["uncertain_pois"] = uncertain_pois
        _attach_links(draft, runtime_pois)
        verification = verify_itinerary(draft, session["user_profile"], route_matrix, runtime_pois)
        final = revise_itinerary(draft, verification, session["user_profile"], runtime_pois=runtime_pois)
        _attach_links(final, runtime_pois)
        store.save_itinerary(session_id, runtime_pois, route_matrix, final, verification)
        return api_success(
            {"runtime_pois": runtime_pois, "route_matrix": route_matrix, "itinerary": final, "verification": verification},
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
            default_llm_client(),
        )
        verification = verify_itinerary(revised, session["user_profile"], state["route_matrix"], state["runtime_pois"])
        final = revise_itinerary(revised, verification, session["user_profile"], runtime_pois=state["runtime_pois"], instruction=instruction)
        _attach_links(final, state["runtime_pois"])
        store.save_itinerary(session_id, state["runtime_pois"], state["route_matrix"], final, verification)
        store.add_revision(session_id, instruction, final)
        return api_success({"itinerary": final, "verification": verification}, {"revise_itinerary": "done"})
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
            poi = by_id.get(item.get("poi_id"))
            if poi:
                item["amap_link"] = build_poi_link(poi)
            if index >= len(items) - 1:
                continue
            next_poi = by_id.get(items[index + 1].get("poi_id"))
            if poi and next_poi:
                transport = item.setdefault("transport_to_next", {})
                transport["amap_navigation_link"] = build_navigation_link(poi, next_poi, transport.get("mode", "walking"))


def _planning_grounded_pois(rows: list[dict]) -> list[dict]:
    return [
        row["grounded_poi"]
        for row in rows
        if row.get("decision") in {"keep", "must_visit", "optional"} and row["grounded_poi"].get("match_status") == "matched"
    ]


def _uncertain_grounded_pois(rows: list[dict]) -> list[dict]:
    return [
        row["grounded_poi"]
        for row in rows
        if row.get("decision") in {"keep", "must_visit", "optional"} and row["grounded_poi"].get("match_status") != "matched"
    ]
