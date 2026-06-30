from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
                CREATE TABLE IF NOT EXISTS route_cache (
                    cache_key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

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
        return {
            "session_id": row["id"],
            "raw_input": row["raw_input"],
            "notes": row["notes"],
            "user_profile": json.loads(row["user_profile"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_pois(self, session_id: str, raw_pois: list[dict], grounded_pois: list[dict]) -> None:
        now = _now()
        with self.connect() as connection:
            connection.execute("DELETE FROM pois WHERE session_id = ?", (session_id,))
            for raw_poi, grounded_poi in zip(raw_pois, grounded_pois, strict=False):
                connection.execute(
                    "INSERT INTO pois (session_id, raw_poi, grounded_poi, decision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, _json(raw_poi), _json(grounded_poi), "keep", now, now),
                )

    def list_pois(self, session_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM pois WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        return [
            {
                "id": row["id"],
                "raw_poi": json.loads(row["raw_poi"]),
                "grounded_poi": json.loads(row["grounded_poi"]),
                "decision": row["decision"],
                "manual_name": row["manual_name"],
            }
            for row in rows
        ]

    def update_poi_decisions(self, session_id: str, decisions: list[dict], rematch_grounded=None) -> None:
        now = _now()
        by_poi_id = {decision["poi_id"]: decision for decision in decisions}
        with self.connect() as connection:
            rows = connection.execute("SELECT id, raw_poi, grounded_poi FROM pois WHERE session_id = ?", (session_id,)).fetchall()
            for row in rows:
                raw_poi = json.loads(row["raw_poi"])
                grounded = json.loads(row["grounded_poi"])
                poi_id = f"amap_{grounded.get('amap_id')}" if grounded.get("amap_id") else f"raw_{grounded.get('raw_name')}"
                decision = by_poi_id.get(poi_id)
                if not decision:
                    continue
                manual_name = (decision.get("manual_name") or "").strip()
                if manual_name:
                    raw_poi["raw_name"] = manual_name
                    if rematch_grounded:
                        grounded = rematch_grounded(raw_poi, grounded, manual_name)
                    else:
                        grounded["raw_name"] = manual_name
                connection.execute(
                    "UPDATE pois SET decision = ?, manual_name = ?, raw_poi = ?, grounded_poi = ?, updated_at = ? WHERE id = ?",
                    (decision.get("decision", "keep"), manual_name or None, _json(raw_poi), _json(grounded), now, row["id"]),
                )

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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_store() -> SQLiteStore:
    return SQLiteStore()
