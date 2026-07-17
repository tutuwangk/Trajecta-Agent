from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import logging
import re
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from app.agents.input_parser import parse_user_profile
from app.agents.intensity import sync_day_total_time
from app.agents.itinerary_normalizer import normalize_itinerary
from app.agents.planning_workflow import run_planning_workflow
from app.agents.planner import build_copy_context, compile_planning_context
from app.agents.planner_agent import default_planner_agent
from app.agents.reviser import generate_copy
from app.agents.poi_extractor import extract_poi_names
from app.agents.ugc_reader import extract_ugc_items
from app.core import AppError, api_error, api_success
from app.agents.visit_duration_estimator import estimate_visit_durations
from app.adapters.legacy import fact_snapshot_from_legacy, validation_report_from_legacy
from app.domain.facts import FactSnapshot
from app.domain.itinerary import FinalItinerary, ReleaseDecision
from app.planning.release_gate import ReleaseGate, apply_release_decision
from app.planning.poi_fact_resolver import default_poi_fact_resolver
from app.planning.segment_boundaries import hotel_rest_boundary_pairs
from app.orchestration.planning_orchestrator import PlanningOrchestrator
from app.schemas.models import PlanningDecisionRequest, PoiDecisionUpdate, RevisionRequest, SessionCreate, UserProfile
from app.services.amap_client import default_amap_client
from app.services.chain_arranger import arrange_chain_to_anchor
from app.services.cache_service import CacheService
from app.services.database import default_store
from app.services.link_builder import build_navigation_link, build_poi_link
from app.services.llm_client import default_copy_llm_client, default_duration_llm_client, default_fact_llm_client, default_llm_client
from app.services.poi_enricher import enrich_pois
from app.services.poi_grounder import ground_pois, ground_single_poi
from app.services.route_service import build_route_edge, build_spatial_route_edge, build_spatial_route_matrix


router = APIRouter()
store = default_store()
duration_cache = CacheService(store)
logger = logging.getLogger(__name__)
_active_plan_runs: set[str] = set()
_active_plan_runs_lock = Lock()


@router.post("/sessions")
def create_session(payload: SessionCreate):
    try:
        user_profile = (
            UserProfile.model_validate(payload.user_profile).model_dump(mode="json")
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
    latest_run = store.get_latest_planning_run(session_id)
    return api_success(
        {
            **session,
            "pois": store.list_pois(session_id),
            "itinerary_state": store.get_itinerary(session_id),
            "planning_intervention": store.get_open_planning_intervention(session_id),
            "latest_planning_run": _public_planning_run(latest_run) if latest_run else None,
            "revision_history": store.list_revisions(session_id),
        }
    )


def _public_planning_run(run: dict) -> dict:
    debug = dict(run.get("debug") or {})
    return {
        key: run.get(key)
        for key in (
            "id",
            "status",
            "stage",
            "checkpoint",
            "result_status",
            "error_code",
            "error_message",
            "attempt_count",
            "duration_ms",
            "metrics",
            "created_at",
            "updated_at",
        )
    } | {"blockers": list(debug.get("blockers") or [])}


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
                if not anchor_poi:
                    raise AppError(
                        "酒店位置暂时无法可靠确认，请修改酒店名或改用已确认地点作为顺路锚点。",
                        code="hotel_anchor_unresolved",
                        step="arrange_nearby",
                    )
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
def create_plan(
    session_id: str,
    background_tasks: BackgroundTasks,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    try:
        session = _require_session(session_id)
        existing = store.find_planning_run_by_idempotency(session_id, idempotency_key) if idempotency_key else None
        if existing:
            return api_success(_planning_run_response(existing), {"plan_itinerary": existing["status"]})
        input_snapshot = _capture_planning_input(session_id, session)
        run_id = store.start_planning_run(
            session_id,
            idempotency_key=idempotency_key or "",
            input_snapshot=input_snapshot,
        )
        _enqueue_planning(background_tasks, run_id, session_id, session)
        return api_success(
            {"status": "running", "run_id": run_id, "stage": "understanding"},
            {"plan_itinerary": "running"},
        )
    except Exception as exc:
        return api_error(exc, {"plan": "failed"})


@router.get("/planning-runs/{run_id}")
def get_planning_run(run_id: str):
    run = store.get_planning_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="planning run not found")
    return api_success(run)


@router.post("/sessions/{session_id}/planning-runs/{run_id}/resume")
def resume_planning_run(session_id: str, run_id: str, background_tasks: BackgroundTasks):
    try:
        session = _require_session(session_id)
        run = store.get_planning_run(run_id)
        if not run or run.get("session_id") != session_id:
            raise HTTPException(status_code=404, detail="planning run not found")
        if run.get("status") in {"completed", "failed"}:
            return api_success(_planning_run_response(run), {"resume_planning": run["status"]})
        _enqueue_planning(background_tasks, run_id, session_id, session)
        return api_success(
            {"status": "running", "run_id": run_id, "stage": run.get("checkpoint") or run.get("stage")},
            {"resume_planning": "running"},
        )
    except Exception as exc:
        return api_error(exc, {"resume_planning": "failed"})


def _enqueue_planning(background_tasks: BackgroundTasks, run_id: str, session_id: str, session: dict) -> None:
    with _active_plan_runs_lock:
        if run_id in _active_plan_runs:
            return
        _active_plan_runs.add(run_id)
    background_tasks.add_task(_run_planning_in_background, run_id, session_id, session)


def _run_planning_in_background(run_id: str, session_id: str, session: dict) -> None:
    try:
        _plan_session(session_id, session, existing_run_id=run_id)
    except Exception:
        logger.exception("Background planning run %s failed", run_id)
    finally:
        with _active_plan_runs_lock:
            _active_plan_runs.discard(run_id)


def _planning_run_response(run: dict) -> dict:
    status = str(run.get("status") or "running")
    if status == "completed":
        state = dict(run.get("result_snapshot") or {})
        if not state:
            latest = store.get_latest_planning_run(str(run.get("session_id") or "")) or {}
            if latest.get("id") == run.get("id"):
                state = store.get_itinerary(str(run.get("session_id") or "")) or {}
        return {
            "status": "completed",
            "run_id": run.get("id"),
            "runtime_pois": state.get("runtime_pois") or [],
            "route_matrix": state.get("route_matrix") or [],
            "itinerary": state.get("itinerary") or {},
            "verification": state.get("verification") or {},
        }
    if status == "needs_user_choice":
        intervention = store.get_open_planning_intervention(str(run.get("session_id") or ""))
        if intervention:
            return {"status": "needs_user_choice", "run_id": run.get("id"), "planning_intervention": intervention}
    if status == "failed":
        debug = dict(run.get("debug") or {})
        return {
            "status": "failed",
            "run_id": run.get("id"),
            "stage": run.get("checkpoint") or run.get("stage"),
            "error_code": run.get("error_code"),
            "error_message": run.get("error_message"),
            "blockers": list(debug.get("blockers") or []),
        }
    return {
        "status": status,
        "run_id": run.get("id"),
        "stage": run.get("checkpoint") or run.get("stage"),
        "result_status": run.get("result_status") or "",
    }


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
    existing_run_id: str | None = None,
) -> tuple[dict, dict]:
    existing_run: dict = {}
    if existing_run_id:
        existing_run = store.get_planning_run(existing_run_id) or {}
        input_snapshot = dict(existing_run.get("input_snapshot") or {})
        if not input_snapshot.get("schema_version"):
            input_snapshot = _capture_planning_input(
                session_id,
                session,
                revision_intent=revision_intent,
                provisional_planning_decision=provisional_planning_decision,
            )
        run_id = existing_run_id
    else:
        input_snapshot = _capture_planning_input(
            session_id,
            session,
            revision_intent=revision_intent,
            provisional_planning_decision=provisional_planning_decision,
        )
        run_id = store.start_planning_run(session_id, input_snapshot=input_snapshot)
    orchestrator = PlanningOrchestrator(store, run_id)
    if not existing_run_id or str(existing_run.get("checkpoint") or "") == "understanding":
        orchestrator.checkpoint("understanding", input_snapshot=input_snapshot)
    try:
        data, step_status = _execute_plan_session(
            session_id,
            input_snapshot,
            run_id=run_id,
            orchestrator=orchestrator,
            resume_run=existing_run or None,
        )
        run_debug = data.pop("_run_debug", {})
        data["run_id"] = run_id
        if data.get("status") == "needs_user_choice":
            orchestrator.needs_user_choice()
        else:
            orchestrator.complete(
                result_status=str(data.get("verification", {}).get("result_status") or "verified"),
                debug=run_debug,
                artifacts={
                    "fact_snapshot": run_debug.get("fact_snapshot") or {},
                    "blueprint": run_debug.get("last_blueprint") or {},
                    "validation_report": data.get("verification") or {},
                    "release_decision": data.get("verification", {}).get("release_decision") or {},
                    "metrics": run_debug.get("metrics") or {},
                    "result_snapshot": {
                        "runtime_pois": data.get("runtime_pois") or [],
                        "route_matrix": data.get("route_matrix") or [],
                        "itinerary": data.get("itinerary") or {},
                        "verification": data.get("verification") or {},
                    },
                },
            )
        return data, step_status
    except Exception as exc:
        orchestrator.fail(exc)
        if isinstance(exc, AppError):
            exc.details.setdefault("run_id", run_id)
        raise


def _execute_plan_session(
    session_id: str,
    input_snapshot: dict,
    run_id: str | None = None,
    orchestrator: PlanningOrchestrator | None = None,
    resume_run: dict | None = None,
) -> tuple[dict, dict]:
    session = dict(input_snapshot.get("session") or {})
    revision_intent = dict(input_snapshot.get("revision_intent") or {}) or None
    provisional_planning_decision = dict(input_snapshot.get("provisional_planning_decision") or {}) or None
    pois = deepcopy(input_snapshot.get("pois") or [])
    accepted_grounded = _planning_grounded_pois(pois)
    user_profile = _planning_user_profile(session["user_profile"], revision_intent)
    if resume_run and _can_resume_after_release(resume_run):
        return _resume_after_release(
            session_id=session_id,
            session=session,
            user_profile=user_profile,
            run=resume_run,
        )
    planning_llm = default_planner_agent()
    copy_llm = default_copy_llm_client()
    fact_llm = default_fact_llm_client()
    fact_resolver = default_poi_fact_resolver(fact_llm)
    amap_client = default_amap_client()
    resumed_facts = _resume_fact_snapshot(resume_run)
    if resumed_facts is not None:
        runtime_pois, route_matrix, hotel_anchor = _legacy_facts(resumed_facts)
        uncertain_pois = []
        preflight_clients = [fact_llm]
    else:
        duration_llm = default_duration_llm_client()
        if orchestrator:
            orchestrator.checkpoint("grounding")
        uncertain_pois = enrich_pois(_uncertain_grounded_pois(pois), [])
        hotel_anchor = _hotel_anchor(user_profile, amap_client)
        runtime_pois = estimate_visit_durations(enrich_pois(accepted_grounded, []), duration_llm, cache=duration_cache)
        route_matrix = build_spatial_route_matrix(runtime_pois, user_profile)
        preflight_clients = [duration_llm, fact_llm]
    order_constraints = _extract_order_constraints(session["raw_input"], session["notes"], runtime_pois)
    time_constraints = _extract_time_constraints(session["raw_input"], session["notes"], runtime_pois)
    planning_decisions = deepcopy(input_snapshot.get("planning_decisions") or [])
    if provisional_planning_decision:
        planning_decisions.append(provisional_planning_decision)
    if revision_intent:
        order_constraints.extend(_revision_order_constraints(revision_intent, runtime_pois))
        time_constraints.extend(_revision_time_constraints(revision_intent, runtime_pois))
        planning_decisions.extend(revision_intent.get("planning_decisions") or [])
    precise_edge_cache: dict[tuple[str, str], dict] = {}
    initial_facts = fact_snapshot_from_legacy(
        user_profile.get("destination", ""), runtime_pois, route_matrix, hotel_anchor=hotel_anchor
    )
    if orchestrator:
        orchestrator.checkpoint("fact_snapshot", fact_snapshot=initial_facts.model_dump(mode="json"))

    def prepare_itinerary(itinerary: dict) -> None:
        _sync_precise_transport_edges(
            itinerary, runtime_pois, route_matrix, user_profile, amap_client, route_fact_cache=duration_cache
        )
        _sync_hotel_transport_edges(
            itinerary,
            runtime_pois,
            user_profile,
            amap_client,
            hotel=hotel_anchor,
            edge_cache=precise_edge_cache,
            route_fact_cache=duration_cache,
        )
        _sync_hotel_rest_breaks(
            itinerary,
            runtime_pois,
            user_profile,
            amap_client,
            hotel=hotel_anchor,
            edge_cache=precise_edge_cache,
            route_fact_cache=duration_cache,
        )
        normalize_itinerary(itinerary, user_profile, runtime_pois, route_matrix)

    release_state: dict = {}

    def release_candidate(itinerary: dict, verification: dict) -> dict:
        facts = fact_snapshot_from_legacy(
            user_profile.get("destination", ""),
            runtime_pois,
            route_matrix,
            hotel_anchor=hotel_anchor,
        )
        report = validation_report_from_legacy(
            verification,
            fact_version=facts.version,
            repair_attempt=0,
        )
        decision = ReleaseGate().decide(
            itinerary=itinerary,
            report=report,
            facts=facts,
            user_profile=UserProfile.model_validate(user_profile),
        )
        released = apply_release_decision(verification, decision)
        release_state.update({"facts": facts, "decision": decision, "report": report})
        if orchestrator:
            orchestrator.checkpoint(
                "release_gate",
                fact_snapshot=facts.model_dump(mode="json"),
                validation_report=released,
                release_decision=decision.model_dump(mode="json"),
                result_snapshot={
                    "runtime_pois": runtime_pois,
                    "route_matrix": route_matrix,
                    "itinerary": itinerary,
                    "verification": released,
                },
            )
        _assert_publishable(released, run_id=run_id)
        return released

    workflow = orchestrator.run_workflow if orchestrator else run_planning_workflow
    final, final_verification, debug = workflow(
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
        preflight_llm_clients=preflight_clients,
        release_candidate=release_candidate,
        fact_resolver=fact_resolver,
        user_request=f"{session.get('raw_input', '')}\n{session.get('notes', '')}",
        initial_blueprint=_resume_blueprint(resume_run),
    )

    final_facts = release_state["facts"]
    release_decision = release_state["decision"]
    final["result_status"] = release_decision.status
    final["release_decision"] = release_decision.model_dump(mode="json")
    final["fact_version"] = final_facts.version
    run_metrics = _build_planning_metrics(
        itinerary=final,
        facts=final_facts,
        verification=final_verification,
        release_decision=release_decision,
        debug=debug,
    )
    logger.info("Planning workflow debug snapshot: %s", debug)
    _clean_final_messages(final, final_verification)
    _attach_links(final, runtime_pois)
    final = FinalItinerary.model_validate(final).model_dump(mode="json", exclude_none=True)
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
                "auto_fallback_used": bool(debug.get("auto_fallback_used")),
                "planning_packet_meta": dict(debug.get("planning_packet_meta") or {}),
                "llm_metrics": dict(debug.get("llm_metrics") or {}),
                "metrics": run_metrics,
                "hard_issue_types": _debug_issue_types(debug.get("hard_issue_history") or []),
                "preference_issue_types": _debug_issue_types(debug.get("preference_issue_history") or []),
                "candidate_scores": list(debug.get("candidate_scores") or []),
                "fact_requests": list(debug.get("fact_requests") or []),
                "last_blueprint": (debug.get("skeleton_versions") or [{}])[-1],
                "fact_snapshot": final_facts.model_dump(mode="json"),
            },
        },
        {"build_route_matrix": "done", "plan_itinerary": "done", "verify_itinerary": "done"},
    )


def _build_planning_metrics(*, itinerary: dict, facts, verification: dict, release_decision, debug: dict) -> dict:
    scheduled_ids = {
        str(item.get("poi_id"))
        for day in itinerary.get("days") or []
        for item in day.get("items") or []
        if item.get("poi_id")
    }
    pois_by_id = {poi.poi_id: poi for poi in facts.pois}
    used_pairs: list[tuple[str, str]] = []
    hotel_route_count = 0
    hotel_degraded_count = 0
    for day in itinerary.get("days") or []:
        items = day.get("items") or []
        for origin, destination in zip(items, items[1:]):
            if origin.get("transport_to_next"):
                used_pairs.append((str(origin.get("poi_id") or ""), str(destination.get("poi_id") or "")))
        if facts.hotel_anchor is not None and items:
            for prefix in ("hotel_departure", "hotel_return"):
                hotel_route_count += 1
                if day.get(f"{prefix}_transport_source") == "spatial_estimate":
                    hotel_degraded_count += 1
            for rest in day.get("hotel_rest_breaks") or []:
                for prefix in ("return_to_hotel", "depart_from_hotel"):
                    hotel_route_count += 1
                    if rest.get(f"{prefix}_transport_source") == "spatial_estimate":
                        hotel_degraded_count += 1

    edges_by_pair = {(edge.origin_poi_id, edge.destination_poi_id): edge for edge in facts.route_edges}
    fact_total = len(scheduled_ids) + len(used_pairs) + hotel_route_count + (1 if facts.hotel_anchor is not None else 0)
    fact_available = sum(
        1 for poi_id in scheduled_ids if pois_by_id.get(poi_id) is not None and pois_by_id[poi_id].confidence != "unavailable"
    )
    fact_available += sum(
        1
        for pair in used_pairs
        if edges_by_pair.get(pair) is not None and edges_by_pair[pair].duration_min is not None
    )
    fact_available += sum(
        1
        for day in itinerary.get("days") or []
        if facts.hotel_anchor is not None and day.get("items") and day.get("hotel_departure_transport_min") is not None
    )
    fact_available += sum(
        1
        for day in itinerary.get("days") or []
        if facts.hotel_anchor is not None and day.get("items") and day.get("hotel_return_transport_min") is not None
    )
    fact_available += sum(
        1
        for day in itinerary.get("days") or []
        for rest in day.get("hotel_rest_breaks") or []
        for prefix in ("return_to_hotel", "depart_from_hotel")
        if facts.hotel_anchor is not None
        and isinstance(rest.get(f"{prefix}_transport_min"), int)
        and rest.get(f"{prefix}_transport_min") > 0
        and rest.get(f"{prefix}_transport_source") != "unknown"
    )
    if facts.hotel_anchor is not None and facts.hotel_anchor.confidence != "unavailable":
        fact_available += 1

    degraded_routes = hotel_degraded_count + sum(
        1 for pair in used_pairs if edges_by_pair.get(pair) is not None and edges_by_pair[pair].confidence == "estimated"
    )
    route_fact_count = len(used_pairs) + hotel_route_count
    llm_metrics = dict(debug.get("llm_metrics") or {})
    calls = list(llm_metrics.get("calls") or [])
    result_status = release_decision.status
    return {
        "hard_constraint_pass_rate": 1.0 if not verification.get("blocking_issues") else 0.0,
        "fact_completeness_rate": round(fact_available / fact_total, 4) if fact_total else 1.0,
        "model_call_count": int(llm_metrics.get("call_count") or 0),
        "schema_retry_count": sum(max(0, int(call.get("request_attempts") or 1) - 1) for call in calls),
        "business_repair_count": int(debug.get("repair_attempts") or 0),
        "fact_request_count": len(debug.get("fact_requests") or []),
        "deterministic_fallback_rate": 1.0 if debug.get("auto_fallback_used") else 0.0,
        "amap_degradation_rate": round(degraded_routes / route_fact_count, 4) if route_fact_count else 0.0,
        "verified_rate": 1.0 if result_status == "verified" else 0.0,
        "degraded_rate": 1.0 if result_status == "degraded" else 0.0,
        "failed_rate": 1.0 if result_status == "failed" else 0.0,
        "final_publish_success_rate": 1.0 if result_status in {"verified", "degraded"} else 0.0,
        "copy_fact_drift_count": 0,
        "llm": llm_metrics,
    }


def _can_resume_after_release(run: dict) -> bool:
    return (
        str(run.get("checkpoint") or "") in {"release_gate", "copywriting"}
        and bool((run.get("result_snapshot") or {}).get("itinerary"))
        and bool(run.get("fact_snapshot"))
        and bool(run.get("release_decision"))
    )


def _resume_after_release(
    *,
    session_id: str,
    session: dict,
    user_profile: dict,
    run: dict,
) -> tuple[dict, dict]:
    snapshot = deepcopy(run.get("result_snapshot") or {})
    runtime_pois = list(snapshot.get("runtime_pois") or [])
    route_matrix = list(snapshot.get("route_matrix") or [])
    itinerary = dict(snapshot.get("itinerary") or {})
    verification = dict(snapshot.get("verification") or run.get("validation_report") or {})
    facts = FactSnapshot.model_validate(run.get("fact_snapshot") or {})
    decision = ReleaseDecision.model_validate(run.get("release_decision") or {})
    planning_context = compile_planning_context(
        user_profile,
        runtime_pois,
        route_matrix,
        user_request=f"{session.get('raw_input', '')}\n{session.get('notes', '')}",
    )
    copy_context = build_copy_context(
        planning_context,
        itinerary,
        {"passed": True, "issues": []},
        list(verification.get("quality_issues") or []),
    )
    copy_llm = default_copy_llm_client()
    final = generate_copy(itinerary, copy_context, user_profile, copy_llm)
    final["result_status"] = decision.status
    final["release_decision"] = decision.model_dump(mode="json")
    final["fact_version"] = facts.version
    _clean_final_messages(final, verification)
    _attach_links(final, runtime_pois)
    final = FinalItinerary.model_validate(final).model_dump(mode="json", exclude_none=True)
    store.save_itinerary(session_id, runtime_pois, route_matrix, final, verification)
    debug = {
        "resumed_from_checkpoint": str(run.get("checkpoint") or "release_gate"),
        "attempt_count": int(run.get("attempt_count") or 1),
        "repair_attempts": 0,
        "auto_fallback_used": False,
        "llm_metrics": _collect_client_metrics(copy_llm),
    }
    metrics = _build_planning_metrics(
        itinerary=final,
        facts=facts,
        verification=verification,
        release_decision=decision,
        debug=debug,
    )
    return (
        {
            "status": "completed",
            "runtime_pois": runtime_pois,
            "route_matrix": route_matrix,
            "itinerary": final,
            "verification": verification,
            "_run_debug": {
                **debug,
                "metrics": metrics,
                "last_blueprint": dict(run.get("blueprint") or {}),
                "fact_snapshot": facts.model_dump(mode="json"),
            },
        },
        {"resume_checkpoint": "copywriting", "plan_itinerary": "reused", "verify_itinerary": "reused"},
    )


def _resume_fact_snapshot(run: dict | None) -> FactSnapshot | None:
    if not run or str(run.get("checkpoint") or "") not in {
        "fact_snapshot",
        "blueprint",
        "compiling",
        "repairing",
        "fallback",
    }:
        return None
    payload = run.get("fact_snapshot") or {}
    if not payload:
        return None
    try:
        return FactSnapshot.model_validate(payload)
    except ValueError:
        return None


def _resume_blueprint(run: dict | None) -> dict | None:
    if not run or str(run.get("checkpoint") or "") not in {"blueprint", "compiling", "repairing", "fallback"}:
        return None
    blueprint = run.get("blueprint") or {}
    return dict(blueprint) if blueprint else None


def _legacy_facts(facts: FactSnapshot) -> tuple[list[dict], list[dict], dict | None]:
    availability_by_id: dict[str, list[dict]] = {}
    for fact in facts.availability_facts:
        availability_by_id.setdefault(fact.poi_id, []).append(fact.model_dump(mode="json"))
    gaps_by_id: dict[str, list[dict]] = {}
    for gap in facts.gaps:
        for entity_id in gap.entity_ids:
            gaps_by_id.setdefault(entity_id, []).append(gap.model_dump(mode="json"))
    runtime_pois = []
    for poi in facts.pois:
        raw = poi.model_dump(mode="json")
        metadata = dict(raw.pop("metadata", {}) or {})
        runtime_pois.append(
            {
                **metadata,
                **raw,
                "category_normalized": raw.get("category", "unknown"),
                "availability_facts": availability_by_id.get(poi.poi_id, []),
                "fact_gaps": gaps_by_id.get(poi.poi_id, []),
            }
        )
    route_matrix = [edge.model_dump(mode="json") for edge in facts.route_edges]
    hotel_anchor = facts.hotel_anchor.model_dump(mode="json") if facts.hotel_anchor else None
    return runtime_pois, route_matrix, hotel_anchor


def _collect_client_metrics(client) -> dict:
    calls = [dict(item) for item in getattr(client, "call_metrics", []) or [] if isinstance(item, dict)]
    return {
        "call_count": len(calls),
        "error_count": sum(1 for item in calls if item.get("status") == "error"),
        "prompt_tokens": sum(int(item.get("prompt_tokens") or 0) for item in calls),
        "completion_tokens": sum(int(item.get("completion_tokens") or 0) for item in calls),
        "reasoning_tokens": sum(int(item.get("reasoning_tokens") or 0) for item in calls),
        "duration_ms": sum(int(item.get("duration_ms") or 0) for item in calls),
        "calls": calls,
    }


def _assert_publishable(verification: dict, run_id: str | None = None) -> None:
    if verification.get("publishable") is True:
        return
    from app.agents.verifier import RELEASE_BLOCKING_TYPES

    blocking_issues = list(verification.get("blocking_issues") or [])
    if not blocking_issues:
        blocking_issues = [
            issue for issue in verification.get("issues") or [] if issue.get("type") in RELEASE_BLOCKING_TYPES
        ]
    if not blocking_issues:
        release_decision = verification.get("release_decision") or {}
        blocking_issues = [
            {"type": code, "message": message}
            for code, message in zip(
                release_decision.get("blocking_issue_codes") or ["release_gate_failed"],
                release_decision.get("reasons") or ["路线未通过发布门禁。"],
            )
        ]
    raise AppError(
        "路线未通过发布校验，系统没有保存这次不完整结果。",
        code="itinerary_publish_blocked",
        step="release_gate",
        details={"issues": blocking_issues, **({"run_id": run_id} if run_id else {})},
    )


def _debug_issue_types(histories: list) -> list[str]:
    result: list[str] = []
    stack = list(histories)
    while stack:
        item = stack.pop(0)
        if isinstance(item, list):
            stack.extend(item)
            continue
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("issues"), list):
            stack.extend(item["issues"])
        issue_type = str(item.get("type") or "").strip()
        if issue_type and issue_type not in result:
            result.append(issue_type)
    return result


def _capture_planning_input(
    session_id: str,
    session: dict,
    *,
    revision_intent: dict | None = None,
    provisional_planning_decision: dict | None = None,
) -> dict:
    """Freeze every mutable session input needed to replay one PlanningRun."""

    return {
        "schema_version": 1,
        "session": {
            "raw_input": str(session.get("raw_input") or ""),
            "notes": str(session.get("notes") or ""),
            "user_profile": deepcopy(session.get("user_profile") or {}),
        },
        "pois": deepcopy(store.list_pois(session_id)),
        "planning_decisions": deepcopy(store.list_resolved_planning_decisions(session_id)),
        "revision_intent": deepcopy(revision_intent or {}),
        "provisional_planning_decision": deepcopy(provisional_planning_decision or {}),
    }


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
        rest_boundaries = hotel_rest_boundary_pairs(day)
        for index, item in enumerate(items):
            if index >= len(items) - 1:
                item.pop("transport_to_next", None)
                continue
            if (str(item.get("poi_id") or ""), str(items[index + 1].get("poi_id") or "")) in rest_boundaries:
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


def _sync_precise_transport_edges(
    itinerary: dict,
    runtime_pois: list[dict],
    route_matrix: list[dict],
    user_profile: dict,
    amap_client,
    route_fact_cache=None,
) -> None:
    route_index_by_pair = {
        (edge.get("origin_poi_id"), edge.get("destination_poi_id")): index
        for index, edge in enumerate(route_matrix)
    }
    route_by_pair = {(edge.get("origin_poi_id"), edge.get("destination_poi_id")): edge for edge in route_matrix}
    by_id = {poi.get("poi_id"): poi for poi in runtime_pois}
    for day in itinerary.get("days", []):
        items = day.get("items", [])
        rest_boundaries = hotel_rest_boundary_pairs(day)
        for index, item in enumerate(items):
            if index >= len(items) - 1:
                item.pop("transport_to_next", None)
                continue
            next_item = items[index + 1]
            if (str(item.get("poi_id") or ""), str(next_item.get("poi_id") or "")) in rest_boundaries:
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
                cache_key = _route_fact_cache_key(origin, destination, user_profile)
                precise_edge = route_fact_cache.get_route(cache_key) if route_fact_cache else None
                if not precise_edge:
                    try:
                        precise_edge = build_route_edge(origin, destination, amap_client, user_profile)
                        if route_fact_cache and precise_edge.get("duration_min") is not None:
                            route_fact_cache.set_route(cache_key, precise_edge)
                    except AppError as exc:
                        precise_edge = build_spatial_route_edge(origin, destination, user_profile)
                        precise_edge["degradation_reason"] = f"高德精确路线获取失败（{exc.code}），当前使用空间估算。"
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
    route_fact_cache=None,
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
            edge = _cached_precise_edge(hotel, first, amap_client, user_profile, edge_cache, route_fact_cache)
            if edge.get("duration_min") is not None:
                day["hotel_departure_transport_min"] = edge["duration_min"]
                day["hotel_departure_transport_source"] = edge.get("source", "unknown")
                if edge.get("degradation_reason"):
                    day["hotel_departure_transport_degradation_reason"] = edge["degradation_reason"]
        if last and _has_location(last):
            edge = _cached_precise_edge(last, hotel, amap_client, user_profile, edge_cache, route_fact_cache)
            if edge.get("duration_min") is not None:
                day["hotel_return_transport_min"] = edge["duration_min"]
                day["hotel_return_transport_source"] = edge.get("source", "unknown")
                if edge.get("degradation_reason"):
                    day["hotel_return_transport_degradation_reason"] = edge["degradation_reason"]


def _sync_hotel_rest_breaks(
    itinerary: dict,
    runtime_pois: list[dict],
    user_profile: dict,
    amap_client,
    hotel: dict | None = None,
    edge_cache: dict[tuple[str, str], dict] | None = None,
    route_fact_cache=None,
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
            return_edge = _cached_precise_edge(after_poi, hotel, amap_client, user_profile, edge_cache, route_fact_cache)
            depart_edge = _cached_precise_edge(hotel, before_poi, amap_client, user_profile, edge_cache, route_fact_cache)
            next_period = str(next_outing.get("segment_time") or "").strip().lower()
            target_start_min = 19 * 60 if next_period == "night" else 17 * 60 + 30
            day["hotel_rest_breaks"].append(
                {
                    "after_poi_id": after_poi_id,
                    "before_poi_id": before_poi_id,
                    "duration_min": int(segment.get("duration_min") or 0),
                    "dynamic": bool(segment.get("rest_until")),
                    "target_start_min": target_start_min,
                    "reason": str(segment.get("reason") or "回酒店休息").strip(),
                    "return_to_hotel_transport_min": return_edge.get("duration_min") or 0,
                    "depart_from_hotel_transport_min": depart_edge.get("duration_min") or 0,
                    "return_to_hotel_transport_source": return_edge.get("source", "unknown"),
                    "depart_from_hotel_transport_source": depart_edge.get("source", "unknown"),
                    "return_to_hotel_transport_degradation_reason": return_edge.get("degradation_reason", ""),
                    "depart_from_hotel_transport_degradation_reason": depart_edge.get("degradation_reason", ""),
                }
            )


def _cached_precise_edge(
    origin: dict,
    destination: dict,
    amap_client,
    user_profile: dict,
    edge_cache: dict[tuple[str, str], dict] | None,
    route_fact_cache=None,
) -> dict:
    origin_location = origin.get("location") or {}
    destination_location = destination.get("location") or {}
    key = (
        f"{origin_location.get('lng')},{origin_location.get('lat')}",
        f"{destination_location.get('lng')},{destination_location.get('lat')}",
    )
    if edge_cache is not None and key in edge_cache:
        return edge_cache[key]
    cache_key = _route_fact_cache_key(origin, destination, user_profile)
    edge = route_fact_cache.get_route(cache_key) if route_fact_cache else None
    if not edge:
        try:
            edge = build_route_edge(origin, destination, amap_client, user_profile)
            if route_fact_cache and edge.get("duration_min") is not None:
                route_fact_cache.set_route(cache_key, edge)
        except AppError as exc:
            edge = build_spatial_route_edge(origin, destination, user_profile)
            edge["degradation_reason"] = f"高德精确路线获取失败（{exc.code}），当前使用空间估算。"
    if edge_cache is not None:
        edge_cache[key] = edge
    return edge


def _route_fact_cache_key(origin: dict, destination: dict, user_profile: dict) -> str:
    origin_location = origin.get("location") or {}
    destination_location = destination.get("location") or {}
    preferences = ",".join(str(item) for item in user_profile.get("transport_preference") or ["taxi", "public_transport"])
    return (
        f"route:{origin_location.get('lng')},{origin_location.get('lat')}"
        f"->{destination_location.get('lng')},{destination_location.get('lat')}:{preferences}"
    )


def _hotel_anchor(user_profile: dict, amap_client) -> dict | None:
    hotel_name = str(user_profile.get("hotel_name") or "").strip()
    if not hotel_name:
        return None
    city = str(user_profile.get("destination") or "").strip()

    # A hotel name is a POI query, not a street address.  Geocoding arbitrary
    # names can return a syntactically valid but unrelated city-level point.
    candidates = amap_client.search_poi(hotel_name, city)
    selected = _select_hotel_candidate(hotel_name, city, candidates)
    if selected:
        location = _location_from_value(selected.get("location"))
        if location:
            return {
                "poi_id": "hotel",
                "standard_name": str(selected.get("name") or hotel_name),
                "amap_id": str(selected.get("id") or ""),
                "city": _amap_text(selected.get("cityname")) or city,
                "district": _amap_text(selected.get("adname")),
                "address": _amap_text(selected.get("address")),
                "location": location,
                "match_status": "matched",
                "match_confidence": round(_hotel_candidate_score(hotel_name, city, selected), 3),
                "confidence": "verified",
                "source": "amap_poi_search",
            }

    # Only explicit street-style inputs may fall back to address geocoding.
    if not _looks_like_address(hotel_name):
        return None
    geocode = amap_client.geocode(hotel_name, city)
    location = _location_from_value((geocode or {}).get("location"))
    if not location:
        return None
    return {
        "poi_id": "hotel",
        "standard_name": hotel_name,
        "city": _amap_text((geocode or {}).get("city")) or city,
        "district": _amap_text((geocode or {}).get("district")),
        "address": _amap_text((geocode or {}).get("formatted_address")) or hotel_name,
        "location": location,
        "match_status": "matched",
        "match_confidence": 0.85,
        "confidence": "verified",
        "source": "amap_geocode",
    }


def _select_hotel_candidate(hotel_name: str, city: str, candidates: list[dict] | None) -> dict | None:
    ranked = sorted(
        (
            (_hotel_candidate_score(hotel_name, city, candidate), candidate)
            for candidate in candidates or []
            if _location_from_value(candidate.get("location")) and _is_hotel_candidate(candidate)
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.8:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.04:
        return None
    return ranked[0][1]


def _hotel_candidate_score(hotel_name: str, city: str, candidate: dict) -> float:
    expected = _normalized_hotel_name(hotel_name)
    actual = _normalized_hotel_name(_amap_text(candidate.get("name")))
    if not expected or not actual:
        return 0.0
    name_score = SequenceMatcher(None, expected, actual).ratio()
    if expected in actual or actual in expected:
        name_score = max(name_score, 0.94)
    candidate_city = _amap_text(candidate.get("cityname"))
    city_score = 1.0 if not city or city in candidate_city else 0.0
    address_score = 1.0 if _amap_text(candidate.get("address")) else 0.4
    return 0.72 * name_score + 0.18 * city_score + 0.10 * address_score


def _is_hotel_candidate(candidate: dict) -> bool:
    category = _amap_text(candidate.get("type"))
    name = _amap_text(candidate.get("name"))
    return any(token in category for token in ["住宿服务", "宾馆酒店", "酒店"]) or any(
        token in name for token in ["酒店", "宾馆", "旅馆", "民宿"]
    )


def _normalized_hotel_name(value: str) -> str:
    normalized = value.lower().replace("（", "(").replace("）", ")")
    normalized = normalized.replace("成都", "")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def _looks_like_address(value: str) -> bool:
    return bool(re.search(r"(?:路|街|道|巷|弄|号|栋|大厦|广场|中心).*(?:\d|号)", value))


def _amap_text(value) -> str:
    if isinstance(value, list):
        return "".join(str(item) for item in value if item)
    return str(value or "").strip()


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
    ranked: list[tuple[int, str, str]] = []
    for poi in runtime_pois:
        poi_id = str(poi.get("poi_id") or "").strip()
        name = str(poi.get("standard_name") or poi.get("raw_name") or "").strip()
        evidence = " ".join(str(item) for item in poi.get("ugc_evidence") or [])
        combined = f"{text}\n{evidence}"
        rank = _order_rank_for_text(combined, name)
        if rank is not None:
            ranked.append((rank, name, poi_id))
    ranked.sort(key=lambda item: item[0])
    constraints: list[dict] = []
    for index in range(len(ranked) - 1):
        before, before_poi_id = ranked[index][1], ranked[index][2]
        after, after_poi_id = ranked[index + 1][1], ranked[index + 1][2]
        if before and after and before != after:
            constraints.append(
                {
                    "before": before,
                    "after": after,
                    "before_poi_id": before_poi_id,
                    "after_poi_id": after_poi_id,
                    "strength": "strong_preference",
                    "source": "user_text",
                }
            )
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
            constraints.append(
                {
                    "poi_id": poi_id,
                    "name": name,
                    **matched,
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


def _time_constraint_for_name(text: str, name: str) -> dict | None:
    fixed = _fixed_time_constraint_for_name(text, name)
    if fixed:
        return fixed
    patterns = [
        ("night", [f"晚上去{name}", f"夜里去{name}", f"夜晚去{name}"]),
        ("evening", [f"傍晚去{name}", f"最后去{name}", f"收尾去{name}"]),
        ("morning", [f"上午去{name}", f"早上去{name}", f"先去{name}", f"第一站{name}", f"上午先去{name}"]),
        ("afternoon", [f"下午去{name}", f"午后去{name}"]),
        ("midday", [f"白天去{name}", f"白天逛{name}"]),
    ]
    for window, phrases in patterns:
        for phrase in phrases:
            if phrase in text:
                return {"preferred_window": window, "strength": "quasi_hard", "source_text": phrase}
    suffix_patterns = [
        (
            "night",
            [
                f"{name}晚上",
                f"{name}安排在晚上",
                f"{name}安排到晚上",
                f"{name}放在晚上",
                f"{name}留到晚上",
                f"{name}夜景",
                f"{name}夜生活",
            ],
        ),
        ("morning", [f"{name}上午", f"{name}早上"]),
        ("midday", [f"{name}白天"]),
        ("afternoon", [f"{name}下午"]),
    ]
    for window, phrases in suffix_patterns:
        for phrase in phrases:
            if phrase in text:
                return {"preferred_window": window, "strength": "quasi_hard", "source_text": phrase}
    return None


def _fixed_time_constraint_for_name(text: str, name: str) -> dict | None:
    if not name:
        return None
    time_pattern = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*(?::|：|点|时)\s*([0-5]\d)?")
    commitment_tokens = ("预约", "演出", "入场", "开场", "不能迟到", "准时", "截止")
    for match in time_pattern.finditer(text):
        context = text[max(0, match.start() - 48) : min(len(text), match.end() + 48)]
        if name not in context or not any(token in context for token in commitment_tokens):
            continue
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        fixed_time = f"{hour:02d}:{minute:02d}"
        window = "morning" if hour < 11 else "midday" if hour < 14 else "afternoon" if hour < 18 else "evening"
        return {
            "preferred_window": window,
            "appointment_time": fixed_time,
            "strength": "hard",
            "source_text": context.strip(),
        }
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


def _nearest_outing_segment(segments: list[dict], start_index: int, step: int) -> dict | None:
    index = start_index + step
    while 0 <= index < len(segments):
        segment = segments[index]
        if segment.get("kind") == "outing":
            return segment
        index += step
    return None
