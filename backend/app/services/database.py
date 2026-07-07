from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.input_parser import parse_user_profile
from app.agents.place_organizer import build_place_pool_item, legacy_decision, organize_place


class SQLiteStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("DATABASE_PATH", "data/travel_agent.sqlite3")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    raw_input TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    user_profile TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pois (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    raw_poi TEXT NOT NULL,
                    grounded_poi TEXT NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'keep',
                    system_decision TEXT NOT NULL DEFAULT 'include',
                    user_override TEXT NOT NULL DEFAULT 'none',
                    final_decision TEXT NOT NULL DEFAULT 'include',
                    inferred_role TEXT NOT NULL DEFAULT 'visit',
                    decision_reason TEXT NOT NULL DEFAULT '',
                    manual_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS itineraries (
                    session_id TEXT PRIMARY KEY,
                    runtime_pois TEXT NOT NULL,
                    route_matrix TEXT NOT NULL,
                    itinerary TEXT NOT NULL,
                    verification TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS revision_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    itinerary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS planning_interventions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    choice_id TEXT,
                    choice_label TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS route_cache (
                    cache_key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_poi_columns(connection)

    def _ensure_poi_columns(self, connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(pois)").fetchall()}
        migrations = {
            "system_decision": "ALTER TABLE pois ADD COLUMN system_decision TEXT NOT NULL DEFAULT 'include'",
            "user_override": "ALTER TABLE pois ADD COLUMN user_override TEXT NOT NULL DEFAULT 'none'",
            "final_decision": "ALTER TABLE pois ADD COLUMN final_decision TEXT NOT NULL DEFAULT 'include'",
            "inferred_role": "ALTER TABLE pois ADD COLUMN inferred_role TEXT NOT NULL DEFAULT 'visit'",
            "decision_reason": "ALTER TABLE pois ADD COLUMN decision_reason TEXT NOT NULL DEFAULT ''",
        }
        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)

    def create_session(self, raw_input: str, notes: str, user_profile: dict) -> str:
        session_id = str(uuid.uuid4())
        now = _now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO sessions (id, raw_input, notes, user_profile, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, raw_input, notes, _json(user_profile), now, now),
            )
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        raw_input = row["raw_input"]
        notes = row["notes"]
        user_profile = _backfill_user_profile(json.loads(row["user_profile"]), raw_input, notes)
        return {
            "session_id": row["id"],
            "raw_input": raw_input,
            "notes": notes,
            "user_profile": user_profile,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_pois(self, session_id: str, raw_pois: list[dict], grounded_pois: list[dict], user_profile: dict | None = None) -> None:
        now = _now()
        session = self.get_session(session_id)
        profile = user_profile or (session.get("user_profile", {}) if session else {})
        with self.connect() as connection:
            self._invalidate_itinerary_state(connection, session_id)
            connection.execute("DELETE FROM pois WHERE session_id = ?", (session_id,))
            for raw_poi, grounded_poi in zip(raw_pois, grounded_pois, strict=False):
                organized = organize_place(raw_poi, grounded_poi, profile)
                connection.execute(
                    """
                    INSERT INTO pois (
                        session_id, raw_poi, grounded_poi, decision, system_decision, user_override,
                        final_decision, inferred_role, decision_reason, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        _json(raw_poi),
                        _json(grounded_poi),
                        legacy_decision(organized["user_override"], organized["final_decision"]),
                        organized["system_decision"],
                        organized["user_override"],
                        organized["final_decision"],
                        organized["inferred_role"],
                        organized["decision_reason"],
                        now,
                        now,
                    ),
                )

    def list_pois(self, session_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM pois WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        return [
            _poi_row(row)
            for row in rows
        ]

    def update_poi_decisions(self, session_id: str, decisions: list[dict], rematch_grounded=None, arrange_nearby_grounded=None) -> None:
        now = _now()
        by_poi_id = {decision["poi_id"]: decision for decision in decisions}
        with self.connect() as connection:
            session = connection.execute("SELECT user_profile FROM sessions WHERE id = ?", (session_id,)).fetchone()
            user_profile = json.loads(session["user_profile"]) if session else {}
            self._invalidate_itinerary_state(connection, session_id)
            rows = connection.execute(
                "SELECT id, raw_poi, grounded_poi, system_decision, user_override, final_decision FROM pois WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
            context_rows = [_decode_poi_context_row(row) for row in rows]
            for index, row in enumerate(rows):
                raw_poi = json.loads(row["raw_poi"])
                grounded = json.loads(row["grounded_poi"])
                poi_id = _poi_id_from_grounded(grounded)
                decision = by_poi_id.get(poi_id)
                if not decision:
                    continue
                requested_decision = decision.get("decision", "keep")
                if requested_decision == "confirm_arrange_nearby":
                    anchor_poi_id = str(decision.get("anchor_poi_id") or "").strip()
                    anchor_row = _resolve_anchor_row(context_rows, user_profile, anchor_poi_id)
                    if not arrange_nearby_grounded:
                        raise ValueError("arrange_nearby_grounded callback is required for confirm_arrange_nearby")
                    grounded = arrange_nearby_grounded(raw_poi, _ensure_unresolved_chain_state(grounded), anchor_row, user_profile)
                    manual_name = None
                    user_override = _sync_user_override_from_anchor(anchor_row)
                else:
                    manual_name = (decision.get("manual_name") or "").strip()
                    if manual_name:
                        raw_poi["raw_name"] = manual_name
                        if rematch_grounded:
                            grounded = rematch_grounded(raw_poi, grounded, manual_name)
                        else:
                            grounded["raw_name"] = manual_name
                    if requested_decision in {"must_include", "must_visit", "optional"} and _has_map_candidate(grounded):
                        grounded["match_status"] = "matched"
                    user_override = "rename_confirm" if manual_name and requested_decision in {"keep", "none"} else requested_decision
                organized = organize_place(raw_poi, grounded, user_profile, user_override)
                legacy = legacy_decision(organized["user_override"], organized["final_decision"])
                connection.execute(
                    """
                    UPDATE pois SET
                      decision = ?,
                      system_decision = ?,
                      user_override = ?,
                      final_decision = ?,
                      inferred_role = ?,
                      decision_reason = ?,
                      manual_name = ?,
                      raw_poi = ?,
                      grounded_poi = ?,
                      updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        legacy,
                        organized["system_decision"],
                        organized["user_override"],
                        organized["final_decision"],
                        organized["inferred_role"],
                        organized["decision_reason"],
                        manual_name or None,
                        _json(raw_poi),
                        _json(grounded),
                        now,
                        row["id"],
                    ),
                )
                context_rows[index] = {
                    "id": row["id"],
                    "poi_id": _poi_id_from_grounded(grounded),
                    "raw_poi": raw_poi,
                    "grounded_poi": grounded,
                    "system_decision": organized["system_decision"],
                    "user_override": organized["user_override"],
                    "final_decision": organized["final_decision"],
                }
                if requested_decision == "remove":
                    _cascade_reset_resolved_chains(connection, session_id, poi_id, user_profile, now)
                if requested_decision in {"must_include", "must_visit"}:
                    _cascade_promote_resolved_chains(connection, session_id, poi_id, user_profile, now)

    def save_itinerary(self, session_id: str, runtime_pois: list[dict], route_matrix: list[dict], itinerary: dict, verification: dict) -> None:
        now = _now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO itineraries (session_id, runtime_pois, route_matrix, itinerary, verification, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  runtime_pois = excluded.runtime_pois,
                  route_matrix = excluded.route_matrix,
                  itinerary = excluded.itinerary,
                  verification = excluded.verification,
                  updated_at = excluded.updated_at
                """,
                (session_id, _json(runtime_pois), _json(route_matrix), _json(itinerary), _json(verification), now),
            )

    def get_itinerary(self, session_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM itineraries WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            return None
        return {
            "runtime_pois": json.loads(row["runtime_pois"]),
            "route_matrix": json.loads(row["route_matrix"]),
            "itinerary": json.loads(row["itinerary"]),
            "verification": json.loads(row["verification"]),
            "updated_at": row["updated_at"],
        }

    def add_revision(self, session_id: str, instruction: str, itinerary: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO revision_history (session_id, instruction, itinerary, created_at) VALUES (?, ?, ?, ?)",
                (session_id, instruction, _json(itinerary), _now()),
            )

    def list_revisions(self, session_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT instruction, itinerary, created_at FROM revision_history WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [{"instruction": row["instruction"], "itinerary": json.loads(row["itinerary"]), "created_at": row["created_at"]} for row in rows]

    def save_planning_intervention(self, session_id: str, payload: dict) -> str:
        intervention_id = str(uuid.uuid4())
        now = _now()
        stored_payload = dict(payload)
        with self.connect() as connection:
            connection.execute(
                "UPDATE planning_interventions SET status = 'superseded', updated_at = ? WHERE session_id = ? AND status = 'open'",
                (now, session_id),
            )
            connection.execute(
                """
                INSERT INTO planning_interventions (id, session_id, payload, status, created_at, updated_at)
                VALUES (?, ?, ?, 'open', ?, ?)
                """,
                (intervention_id, session_id, _json(stored_payload), now, now),
            )
        return intervention_id

    def get_open_planning_intervention(self, session_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM planning_interventions WHERE session_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        payload["id"] = row["id"]
        return payload

    def resolve_planning_intervention(self, session_id: str, intervention_id: str, choice_id: str) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM planning_interventions WHERE session_id = ? AND id = ? AND status = 'open'",
                (session_id, intervention_id),
            ).fetchone()
            if not row:
                raise ValueError("planning intervention not found")
            payload = json.loads(row["payload"])
            choice = next((option for option in payload.get("options") or [] if option.get("id") == choice_id), None)
            if not choice:
                raise ValueError("planning intervention choice not found")
            connection.execute(
                """
                UPDATE planning_interventions
                SET status = 'resolved', choice_id = ?, choice_label = ?, updated_at = ?
                WHERE session_id = ? AND id = ?
                """,
                (choice_id, choice.get("label") or choice_id, _now(), session_id, intervention_id),
            )

    def list_resolved_planning_decisions(self, session_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, choice_id, choice_label
                FROM planning_interventions
                WHERE session_id = ? AND status = 'resolved'
                ORDER BY updated_at
                """,
                (session_id,),
            ).fetchall()
        return [
            {
                "intervention_id": row["id"],
                "choice_id": row["choice_id"],
                "choice_label": row["choice_label"],
            }
            for row in rows
        ]

    def _invalidate_itinerary_state(self, connection, session_id: str) -> None:
        connection.execute("DELETE FROM itineraries WHERE session_id = ?", (session_id,))
        connection.execute("DELETE FROM revision_history WHERE session_id = ?", (session_id,))
        connection.execute("DELETE FROM planning_interventions WHERE session_id = ?", (session_id,))

    def get_cache(self, table: str, cache_key: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(f"SELECT value FROM {table} WHERE cache_key = ?", (cache_key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_cache(self, table: str, cache_key: str, value: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                f"INSERT INTO {table} (cache_key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (cache_key, _json(value), _now()),
            )


def _poi_row(row) -> dict:
    raw_poi = json.loads(row["raw_poi"])
    grounded_poi = json.loads(row["grounded_poi"])
    return {
        "id": row["id"],
        "raw_poi": raw_poi,
        "grounded_poi": grounded_poi,
        "decision": row["decision"],
        "system_decision": row["system_decision"],
        "user_override": row["user_override"],
        "final_decision": row["final_decision"],
        "inferred_role": row["inferred_role"],
        "decision_reason": row["decision_reason"],
        "place_pool_item": build_place_pool_item(
            raw_poi,
            grounded_poi,
            row["system_decision"],
            row["user_override"],
            row["final_decision"],
            row["inferred_role"],
        ),
        "manual_name": row["manual_name"],
    }


def _decode_poi_context_row(row) -> dict:
    grounded_poi = json.loads(row["grounded_poi"])
    return {
        "id": row["id"],
        "raw_poi": json.loads(row["raw_poi"]),
        "grounded_poi": grounded_poi,
        "poi_id": _poi_id_from_grounded(grounded_poi),
        "system_decision": row["system_decision"],
        "user_override": row["user_override"],
        "final_decision": row["final_decision"],
    }


def _nearby_context(rows: list[dict], index: int) -> dict:
    previous_grounded = None
    next_grounded = None
    for row in reversed(rows[:index]):
        grounded = row["grounded_poi"]
        if _is_route_anchor(row):
            previous_grounded = grounded
            break
    for row in rows[index + 1:]:
        grounded = row["grounded_poi"]
        if _is_route_anchor(row):
            next_grounded = grounded
            break
    return {
        "previous_grounded": previous_grounded,
        "next_grounded": next_grounded,
        "accepted_grounded": [row["grounded_poi"] for row in rows if _is_route_anchor(row)],
    }


def _is_route_anchor(row: dict) -> bool:
    grounded = row["grounded_poi"]
    return row.get("final_decision") in {"include", "optional"} and grounded.get("match_status") == "matched" and _has_map_candidate(grounded)


def _has_map_candidate(grounded: dict) -> bool:
    location = grounded.get("location") or {}
    return bool(grounded.get("amap_id") and location.get("lng") is not None and location.get("lat") is not None)


def _poi_id_from_grounded(grounded: dict) -> str:
    if grounded.get("amap_id"):
        return f"amap_{grounded.get('amap_id')}"
    return f"raw_{grounded.get('raw_name', '')}"


def _resolve_anchor_row(rows: list[dict], user_profile: dict, anchor_poi_id: str) -> dict:
    if anchor_poi_id == "hotel_anchor":
        hotel_name = str(user_profile.get("hotel_name") or user_profile.get("hotel_area") or "").strip()
        if not hotel_name:
            raise ValueError("hotel anchor requires hotel_name or hotel_area")
        return {
            "id": "hotel_anchor",
            "poi_id": "hotel_anchor",
            "raw_poi": {"raw_name": hotel_name},
            "grounded_poi": {
                "raw_name": hotel_name,
                "standard_name": hotel_name,
                "category_normalized": "hotel",
                "match_status": "matched",
                "location": {},
            },
            "system_decision": "include",
            "user_override": "none",
            "final_decision": "include",
        }
    for row in rows:
        if row.get("poi_id") == anchor_poi_id:
            return row
    raise ValueError(f"anchor poi not found: {anchor_poi_id}")


def _sync_user_override_from_anchor(anchor_row: dict) -> str:
    if anchor_row.get("user_override") == "must_include" or anchor_row.get("final_decision") == "include":
        return "must_include"
    if anchor_row.get("user_override") == "optional" or anchor_row.get("final_decision") == "optional":
        return "optional"
    return "none"


def _ensure_unresolved_chain_state(grounded: dict) -> dict:
    if grounded.get("chain_status"):
        return grounded
    return {**grounded, "chain_status": "unresolved"}


def _cascade_reset_resolved_chains(connection, session_id: str, anchor_poi_id: str, user_profile: dict, now: str) -> None:
    rows = connection.execute(
        "SELECT id, raw_poi, grounded_poi FROM pois WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    for row in rows:
        raw_poi = json.loads(row["raw_poi"])
        grounded = json.loads(row["grounded_poi"])
        if str(grounded.get("resolved_from_anchor_poi_id") or "") != anchor_poi_id:
            continue
        reset_grounded = _reset_chain_grounded(raw_poi, grounded)
        organized = organize_place(raw_poi, reset_grounded, user_profile, "none")
        connection.execute(
            """
            UPDATE pois SET
              decision = ?,
              system_decision = ?,
              user_override = ?,
              final_decision = ?,
              inferred_role = ?,
              decision_reason = ?,
              grounded_poi = ?,
              updated_at = ?
            WHERE id = ?
            """,
            (
                legacy_decision(organized["user_override"], organized["final_decision"]),
                organized["system_decision"],
                organized["user_override"],
                organized["final_decision"],
                organized["inferred_role"],
                organized["decision_reason"],
                _json(reset_grounded),
                now,
                row["id"],
            ),
        )


def _cascade_promote_resolved_chains(connection, session_id: str, anchor_poi_id: str, user_profile: dict, now: str) -> None:
    rows = connection.execute(
        "SELECT id, raw_poi, grounded_poi FROM pois WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    for row in rows:
        raw_poi = json.loads(row["raw_poi"])
        grounded = json.loads(row["grounded_poi"])
        if str(grounded.get("resolved_from_anchor_poi_id") or "") != anchor_poi_id:
            continue
        organized = organize_place(raw_poi, grounded, user_profile, "must_include")
        connection.execute(
            """
            UPDATE pois SET
              decision = ?,
              system_decision = ?,
              user_override = ?,
              final_decision = ?,
              inferred_role = ?,
              decision_reason = ?,
              updated_at = ?
            WHERE id = ?
            """,
            (
                legacy_decision(organized["user_override"], organized["final_decision"]),
                organized["system_decision"],
                organized["user_override"],
                organized["final_decision"],
                organized["inferred_role"],
                organized["decision_reason"],
                now,
                row["id"],
            ),
        )


def _reset_chain_grounded(raw_poi: dict, grounded: dict) -> dict:
    candidate_options = list(grounded.get("candidate_options") or [])
    first_candidate = candidate_options[0] if candidate_options else {}
    raw_name = str(raw_poi.get("raw_name") or grounded.get("raw_name") or "").strip()
    return {
        **grounded,
        "raw_name": raw_name or grounded.get("raw_name", ""),
        "standard_name": f"{raw_name}（待选择）" if raw_name else grounded.get("standard_name", ""),
        "amap_id": first_candidate.get("id", ""),
        "address": first_candidate.get("address", ""),
        "location": first_candidate.get("location", {}),
        "city": first_candidate.get("city", grounded.get("city", "")),
        "district": first_candidate.get("district", grounded.get("district", "")),
        "category_raw": first_candidate.get("category_raw", grounded.get("category_raw", "")),
        "category_normalized": first_candidate.get("category_normalized", grounded.get("category_normalized", "")),
        "match_status": "ambiguous",
        "chain_status": "unresolved",
        "selection_mode": "chain_needs_choice",
        "resolved_branch_id": "",
        "resolved_branch_name": "",
        "resolved_from_anchor_poi_id": "",
        "resolved_from_anchor_name": "",
        "resolved_by": "",
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _backfill_user_profile(user_profile: dict, raw_input: str, notes: str) -> dict:
    if user_profile.get("hotel_name") or "酒店名" not in f"{raw_input}\n{notes}":
        return user_profile
    parsed = parse_user_profile(f"{raw_input}\n{notes}")
    hotel_name = parsed.get("hotel_name")
    if hotel_name:
        user_profile["hotel_name"] = hotel_name
        user_profile["start_point"] = user_profile.get("start_point") or hotel_name
    return user_profile


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_store() -> SQLiteStore:
    return SQLiteStore()
