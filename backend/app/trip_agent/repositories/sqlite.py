from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from hashlib import sha256
import json
import sqlite3
from threading import RLock

from pydantic import TypeAdapter

from app.trip_agent.domain import (
    AgentRun,
    CandidateCheckpoint,
    CandidateRejected,
    CandidateSnapshot,
    ClarificationAnswers,
    ClarificationBatch,
    ReleaseRecord,
    ReleaseNarrative,
    KnowledgeClaim,
    PlaceCandidate,
    SourceRecord,
    RunStatus,
    RunFailureClass,
    TripWorkspace,
)


_KNOWLEDGE_CLAIM_ADAPTER = TypeAdapter(KnowledgeClaim)
ToolEffect = tuple[str, str, str, dict[str, object]]


class RepositoryConflict(RuntimeError):
    pass


def _claim_fingerprint(claim: KnowledgeClaim) -> str:
    payload = claim.model_dump(mode="json")
    for field in ("claim_id", "acquired_at"):
        payload.pop(field, None)
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SqliteTripAgentRepository:
    """V2-owned SQLite state; no Legacy session or planning schema is reused."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        self._connection = sqlite3.connect(self.database, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._initialize()

    def close(self) -> None:
        self._connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trip_workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_snapshots (
                    workspace_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, version),
                    FOREIGN KEY (workspace_id) REFERENCES trip_workspaces(workspace_id)
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE (workspace_id, idempotency_key),
                    FOREIGN KEY (workspace_id) REFERENCES trip_workspaces(workspace_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_agent_run_per_workspace
                ON agent_runs(workspace_id)
                WHERE status IN ('created', 'running', 'waiting_user', 'validating');
                CREATE TABLE IF NOT EXISTS agent_interruptions (
                    interruption_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    answer_payload TEXT,
                    answered_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS agent_releases (
                    release_id TEXT PRIMARY KEY,
                    release_key TEXT NOT NULL UNIQUE,
                    workspace_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (workspace_id) REFERENCES trip_workspaces(workspace_id),
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS agent_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (workspace_id) REFERENCES trip_workspaces(workspace_id),
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS candidate_rejections (
                    rejection_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (candidate_id) REFERENCES agent_candidates(candidate_id),
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS candidate_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (workspace_id) REFERENCES trip_workspaces(workspace_id),
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS source_records (
                    source_record_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (workspace_id) REFERENCES trip_workspaces(workspace_id)
                );
                CREATE TABLE IF NOT EXISTS source_artifacts (
                    source_record_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS place_search_cache (
                    cache_key TEXT PRIMARY KEY,
                    stored_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_source_observations (
                    workspace_id TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    first_observed_at TEXT NOT NULL,
                    last_observed_at TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, source_record_id),
                    FOREIGN KEY (workspace_id) REFERENCES trip_workspaces(workspace_id),
                    FOREIGN KEY (source_record_id) REFERENCES source_artifacts(source_record_id)
                );
                CREATE TABLE IF NOT EXISTS knowledge_claims (
                    claim_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (workspace_id) REFERENCES trip_workspaces(workspace_id)
                );
                CREATE TABLE IF NOT EXISTS agent_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS agent_tool_effects (
                    tool_call_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS agent_tool_effects_v2 (
                    run_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, tool_call_id),
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS agent_run_budgets (
                    run_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS release_narratives (
                    narrative_id TEXT PRIMARY KEY,
                    release_id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (release_id) REFERENCES agent_releases(release_id)
                );
                """
            )
            claim_columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(knowledge_claims)")
            }
            if "fingerprint" not in claim_columns:
                self._connection.execute(
                    "ALTER TABLE knowledge_claims ADD COLUMN fingerprint TEXT"
                )
            for row in self._connection.execute(
                "SELECT claim_id, workspace_id, payload FROM knowledge_claims WHERE fingerprint IS NULL"
            ).fetchall():
                claim = _KNOWLEDGE_CLAIM_ADAPTER.validate_json(row["payload"])
                fingerprint = _claim_fingerprint(claim)
                duplicate = self._connection.execute(
                    "SELECT 1 FROM knowledge_claims WHERE workspace_id = ? AND fingerprint = ?",
                    (row["workspace_id"], fingerprint),
                ).fetchone()
                if duplicate is None:
                    self._connection.execute(
                        "UPDATE knowledge_claims SET fingerprint = ? WHERE claim_id = ?",
                        (fingerprint, row["claim_id"]),
                    )
            self._connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS unique_claim_fingerprint_per_workspace
                ON knowledge_claims(workspace_id, fingerprint)
                WHERE fingerprint IS NOT NULL"""
            )
            # Migrate the original workspace-owned source table into the V2
            # content-addressed artifact/reference model. The old table stays
            # readable for existing databases but receives no new writes.
            self._connection.execute(
                """INSERT OR IGNORE INTO source_artifacts(
                    source_record_id, content_hash, provider, payload
                )
                SELECT source_record_id,
                       json_extract(payload, '$.content_hash'),
                       json_extract(payload, '$.provider'),
                       payload
                FROM source_records"""
            )
            self._connection.execute(
                """INSERT OR IGNORE INTO agent_tool_effects_v2(
                    run_id, tool_call_id, tool_name, payload, created_at
                )
                SELECT run_id, tool_call_id, tool_name, payload, created_at
                FROM agent_tool_effects"""
            )
            self._connection.execute(
                """INSERT OR IGNORE INTO workspace_source_observations(
                    workspace_id, source_record_id, first_observed_at, last_observed_at
                )
                SELECT workspace_id,
                       source_record_id,
                       COALESCE(json_extract(payload, '$.retrieved_at'), CURRENT_TIMESTAMP),
                       COALESCE(json_extract(payload, '$.retrieved_at'), CURRENT_TIMESTAMP)
                FROM source_records"""
            )

    def _link_sources(
        self, workspace_id: str, sources: tuple[SourceRecord, ...]
    ) -> None:
        """Persist immutable artifacts and idempotent per-workspace observations."""

        for source in sources:
            row = self._connection.execute(
                """SELECT content_hash, provider, payload
                FROM source_artifacts WHERE source_record_id = ?""",
                (source.source_record_id,),
            ).fetchone()
            if row is not None:
                existing = SourceRecord.model_validate_json(row["payload"])
                if (
                    row["content_hash"] != source.content_hash
                    or row["provider"] != source.provider
                    or existing.source_type != source.source_type
                ):
                    raise RepositoryConflict(
                        "source artifact identity collision: "
                        f"{source.source_record_id}"
                    )
            else:
                self._connection.execute(
                    """INSERT INTO source_artifacts(
                        source_record_id, content_hash, provider, payload
                    ) VALUES (?, ?, ?, ?)""",
                    (
                        source.source_record_id,
                        source.content_hash,
                        source.provider,
                        source.model_dump_json(),
                    ),
                )
            observed_at = source.retrieved_at.isoformat()
            self._connection.execute(
                """INSERT INTO workspace_source_observations(
                    workspace_id, source_record_id, first_observed_at, last_observed_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(workspace_id, source_record_id) DO UPDATE SET
                    last_observed_at = excluded.last_observed_at""",
                (workspace_id, source.source_record_id, observed_at, observed_at),
            )

    def _record_tool_effect_locked(self, effect: ToolEffect | None) -> None:
        if effect is None:
            return
        tool_call_id, run_id, tool_name, result = effect
        self._connection.execute(
            """INSERT INTO agent_tool_effects_v2(
                run_id, tool_call_id, tool_name, payload, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, tool_call_id) DO NOTHING""",
            (
                run_id,
                tool_call_id,
                tool_name,
                json.dumps(result, ensure_ascii=False),
                datetime.now().astimezone().isoformat(),
            ),
        )

    def create_workspace(self, workspace: TripWorkspace) -> TripWorkspace:
        payload = workspace.model_dump_json()
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    "INSERT INTO trip_workspaces(workspace_id, version, payload) VALUES (?, ?, ?)",
                    (workspace.workspace_id, workspace.version, payload),
                )
                self._connection.execute(
                    "INSERT INTO workspace_snapshots(workspace_id, version, payload) VALUES (?, ?, ?)",
                    (workspace.workspace_id, workspace.version, payload),
                )
            except sqlite3.IntegrityError as exc:
                raise RepositoryConflict(f"workspace already exists: {workspace.workspace_id}") from exc
        return workspace

    def get_workspace(self, workspace_id: str) -> TripWorkspace | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM trip_workspaces WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
        return TripWorkspace.model_validate_json(row["payload"]) if row else None

    def save_workspace(
        self,
        workspace: TripWorkspace,
        *,
        expected_version: int,
        tool_effect: ToolEffect | None = None,
    ) -> TripWorkspace:
        if workspace.version != expected_version + 1:
            raise RepositoryConflict("workspace save must advance exactly one version")
        payload = workspace.model_dump_json()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE trip_workspaces SET version = ?, payload = ? WHERE workspace_id = ? AND version = ?",
                (workspace.version, payload, workspace.workspace_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict(
                    f"stale workspace version: {workspace.workspace_id}@{expected_version}"
                )
            self._connection.execute(
                "INSERT INTO workspace_snapshots(workspace_id, version, payload) VALUES (?, ?, ?)",
                (workspace.workspace_id, workspace.version, payload),
            )
            self._record_tool_effect_locked(tool_effect)
        return workspace

    def save_workspace_with_sources(
        self,
        workspace: TripWorkspace,
        *,
        expected_version: int,
        sources: tuple[SourceRecord, ...],
        tool_effect: ToolEffect | None = None,
    ) -> TripWorkspace:
        if workspace.version != expected_version + 1:
            raise RepositoryConflict("workspace save must advance exactly one version")
        payload = workspace.model_dump_json()
        with self._lock, self._connection:
            self._link_sources(workspace.workspace_id, sources)
            cursor = self._connection.execute(
                "UPDATE trip_workspaces SET version = ?, payload = ? WHERE workspace_id = ? AND version = ?",
                (workspace.version, payload, workspace.workspace_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict(
                    f"stale workspace version: {workspace.workspace_id}@{expected_version}"
                )
            self._connection.execute(
                "INSERT INTO workspace_snapshots(workspace_id, version, payload) VALUES (?, ?, ?)",
                (workspace.workspace_id, workspace.version, payload),
            )
            self._record_tool_effect_locked(tool_effect)
        return workspace

    def create_run(self, run: AgentRun) -> AgentRun:
        existing = self.find_run_by_idempotency(run.workspace_id, run.idempotency_key)
        if existing:
            return existing
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    """INSERT INTO agent_runs(run_id, workspace_id, idempotency_key, status, payload)
                    VALUES (?, ?, ?, ?, ?)""",
                    (run.run_id, run.workspace_id, run.idempotency_key, run.status.value, run.model_dump_json()),
                )
            except sqlite3.IntegrityError as exc:
                existing = self.find_run_by_idempotency(run.workspace_id, run.idempotency_key)
                if existing:
                    return existing
                raise RepositoryConflict(f"run already exists: {run.run_id}") from exc
        return run

    def create_revision_run(
        self,
        workspace: TripWorkspace,
        *,
        expected_version: int,
        run: AgentRun,
    ) -> AgentRun:
        """Commit a revised goal snapshot and its run as one idempotent unit."""

        existing = self.find_run_by_idempotency(run.workspace_id, run.idempotency_key)
        if existing:
            return existing
        if workspace.workspace_id != run.workspace_id:
            raise ValueError("revision run must belong to the revised workspace")
        if workspace.version != expected_version + 1:
            raise RepositoryConflict("workspace revision must advance exactly one version")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE trip_workspaces SET version = ?, payload = ? WHERE workspace_id = ? AND version = ?",
                (
                    workspace.version,
                    workspace.model_dump_json(),
                    workspace.workspace_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict(
                    f"stale workspace version: {workspace.workspace_id}@{expected_version}"
                )
            self._connection.execute(
                "INSERT INTO workspace_snapshots(workspace_id, version, payload) VALUES (?, ?, ?)",
                (workspace.workspace_id, workspace.version, workspace.model_dump_json()),
            )
            self._connection.execute(
                """INSERT INTO agent_runs(run_id, workspace_id, idempotency_key, status, payload)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    run.run_id,
                    run.workspace_id,
                    run.idempotency_key,
                    run.status.value,
                    run.model_dump_json(),
                ),
            )
        return run

    def get_run(self, run_id: str) -> AgentRun | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return AgentRun.model_validate_json(row["payload"]) if row else None

    def increment_provider_attempt(self, run_id: str) -> AgentRun:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT payload FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = AgentRun.model_validate_json(row["payload"])
            updated = current.model_copy(
                update={"provider_attempt_count": current.provider_attempt_count + 1}
            )
            cursor = self._connection.execute(
                "UPDATE agent_runs SET payload = ? WHERE run_id = ? AND payload = ?",
                (updated.model_dump_json(), run_id, row["payload"]),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict(f"run changed concurrently: {run_id}")
        return updated

    def append_event(self, run_id: str, event: dict[str, object]) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO agent_events(run_id, payload, created_at) VALUES (?, ?, ?)",
                (run_id, json.dumps(event, ensure_ascii=False), datetime.now().astimezone().isoformat()),
            )
        return int(cursor.lastrowid)

    def get_tool_effect(
        self, tool_call_id: str, *, run_id: str, tool_name: str
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT tool_name, payload FROM agent_tool_effects_v2
                WHERE run_id = ? AND tool_call_id = ?""",
                (run_id, tool_call_id),
            ).fetchone()
        if row is None:
            return None
        if row["tool_name"] != tool_name:
            raise RepositoryConflict(
                f"tool call id {tool_call_id} in run {run_id} was already used by {row['tool_name']}"
            )
        return json.loads(row["payload"])

    def record_tool_effect(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        tool_name: str,
        result: dict[str, object],
    ) -> dict[str, object]:
        payload = json.dumps(result, ensure_ascii=False)
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO agent_tool_effects_v2(run_id, tool_call_id, tool_name, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, tool_call_id) DO NOTHING""",
                (
                    run_id,
                    tool_call_id,
                    tool_name,
                    payload,
                    datetime.now().astimezone().isoformat(),
                ),
            )
        existing = self.get_tool_effect(tool_call_id, run_id=run_id, tool_name=tool_name)
        if existing is None:
            raise RepositoryConflict(f"tool effect was not persisted: {tool_call_id}")
        return existing

    def get_run_budget(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM agent_run_budgets WHERE run_id = ?", (run_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_run_budget(self, run_id: str, state: dict[str, object]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO agent_run_budgets(run_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at""",
                (
                    run_id,
                    json.dumps(state, ensure_ascii=False),
                    datetime.now().astimezone().isoformat(),
                ),
            )

    def list_events(self, run_id: str, *, after_event_id: int = 0) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            """SELECT event_id, payload, created_at FROM agent_events
            WHERE run_id = ? AND event_id > ? ORDER BY event_id""",
            (run_id, after_event_id),
        ).fetchall()
        return tuple(
            {
                "event_id": int(row["event_id"]),
                "created_at": row["created_at"],
                **json.loads(row["payload"]),
            }
            for row in rows
        )

    def find_run_by_idempotency(self, workspace_id: str, idempotency_key: str) -> AgentRun | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM agent_runs WHERE workspace_id = ? AND idempotency_key = ?",
                (workspace_id, idempotency_key),
            ).fetchone()
        return AgentRun.model_validate_json(row["payload"]) if row else None

    def list_runs(self, workspace_id: str) -> tuple[AgentRun, ...]:
        rows = self._connection.execute(
            "SELECT payload FROM agent_runs WHERE workspace_id = ? ORDER BY rowid",
            (workspace_id,),
        ).fetchall()
        return tuple(AgentRun.model_validate_json(row["payload"]) for row in rows)

    def transition_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        failure_class: RunFailureClass | None = None,
        retryable: bool | None = None,
        active_interruption_id: str | None = None,
        at: datetime | None = None,
    ) -> AgentRun:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status, payload FROM agent_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not row:
                raise KeyError(run_id)
            current = AgentRun.model_validate_json(row["payload"])
            updated = current.transition(
                status,
                error_code=error_code,
                error_message=error_message,
                failure_class=failure_class,
                retryable=retryable,
                active_interruption_id=active_interruption_id,
                at=at,
            )
            cursor = self._connection.execute(
                "UPDATE agent_runs SET status = ?, payload = ? WHERE run_id = ? AND status = ?",
                (updated.status.value, updated.model_dump_json(), run_id, current.status.value),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict(f"run changed concurrently: {run_id}")
        return updated

    def create_interruption(self, batch: ClarificationBatch) -> ClarificationBatch:
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    """INSERT INTO agent_interruptions(
                        interruption_id, run_id, tool_call_id, payload
                    ) VALUES (?, ?, ?, ?)""",
                    (batch.interruption_id, batch.run_id, batch.tool_call_id, batch.model_dump_json()),
                )
            except sqlite3.IntegrityError as exc:
                row = self._connection.execute(
                    "SELECT payload FROM agent_interruptions WHERE tool_call_id = ?", (batch.tool_call_id,)
                ).fetchone()
                if row:
                    return ClarificationBatch.model_validate_json(row["payload"])
                raise RepositoryConflict(f"interruption already exists: {batch.interruption_id}") from exc
        return batch

    def create_interruption_and_wait(self, batch: ClarificationBatch) -> ClarificationBatch:
        """Persist the deferred call and waiting run state as one recoverable boundary."""

        with self._lock, self._connection:
            existing_row = self._connection.execute(
                "SELECT payload FROM agent_interruptions WHERE tool_call_id = ?",
                (batch.tool_call_id,),
            ).fetchone()
            if existing_row:
                existing = ClarificationBatch.model_validate_json(existing_row["payload"])
                run = self.get_run(existing.run_id)
                if (
                    run is None
                    or run.status is not RunStatus.WAITING_USER
                    or run.active_interruption_id != existing.interruption_id
                ):
                    raise RepositoryConflict(
                        f"interruption exists without matching waiting run: {existing.interruption_id}"
                    )
                return existing
            row = self._connection.execute(
                "SELECT payload FROM agent_runs WHERE run_id = ?", (batch.run_id,)
            ).fetchone()
            if not row:
                raise KeyError(batch.run_id)
            current = AgentRun.model_validate_json(row["payload"])
            waiting = current.transition(
                RunStatus.WAITING_USER,
                active_interruption_id=batch.interruption_id,
                at=batch.created_at,
            )
            self._connection.execute(
                """INSERT INTO agent_interruptions(
                    interruption_id, run_id, tool_call_id, payload
                ) VALUES (?, ?, ?, ?)""",
                (batch.interruption_id, batch.run_id, batch.tool_call_id, batch.model_dump_json()),
            )
            cursor = self._connection.execute(
                "UPDATE agent_runs SET status = ?, payload = ? WHERE run_id = ? AND status = ?",
                (
                    waiting.status.value,
                    waiting.model_dump_json(),
                    waiting.run_id,
                    current.status.value,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict(f"run changed concurrently: {waiting.run_id}")
        return batch

    def get_interruption(self, interruption_id: str) -> ClarificationBatch | None:
        row = self._connection.execute(
            "SELECT payload FROM agent_interruptions WHERE interruption_id = ?",
            (interruption_id,),
        ).fetchone()
        return ClarificationBatch.model_validate_json(row["payload"]) if row else None

    def consume_answers(self, answers: ClarificationAnswers) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE agent_interruptions
                SET answer_payload = ?, answered_at = ?
                WHERE interruption_id = ? AND tool_call_id = ? AND answered_at IS NULL""",
                (
                    answers.model_dump_json(),
                    answers.answered_at.isoformat(),
                    answers.interruption_id,
                    answers.tool_call_id,
                ),
            )
        return cursor.rowcount == 1

    def consume_answers_and_resume(self, answers: ClarificationAnswers) -> bool:
        """Consume a user answer exactly once and resume its run atomically."""

        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT i.run_id, i.answered_at, r.payload
                FROM agent_interruptions i
                JOIN agent_runs r ON r.run_id = i.run_id
                WHERE i.interruption_id = ? AND i.tool_call_id = ?""",
                (answers.interruption_id, answers.tool_call_id),
            ).fetchone()
            if row is None or row["answered_at"] is not None:
                return False
            current = AgentRun.model_validate_json(row["payload"])
            if (
                current.status is not RunStatus.WAITING_USER
                or current.active_interruption_id != answers.interruption_id
            ):
                raise RepositoryConflict(
                    f"run is not waiting for interruption: {answers.interruption_id}"
                )
            resumed = current.transition(RunStatus.RUNNING, at=answers.answered_at)
            answer_cursor = self._connection.execute(
                """UPDATE agent_interruptions
                SET answer_payload = ?, answered_at = ?
                WHERE interruption_id = ? AND tool_call_id = ? AND answered_at IS NULL""",
                (
                    answers.model_dump_json(),
                    answers.answered_at.isoformat(),
                    answers.interruption_id,
                    answers.tool_call_id,
                ),
            )
            run_cursor = self._connection.execute(
                "UPDATE agent_runs SET status = ?, payload = ? WHERE run_id = ? AND status = ?",
                (
                    resumed.status.value,
                    resumed.model_dump_json(),
                    resumed.run_id,
                    current.status.value,
                ),
            )
            if answer_cursor.rowcount != 1 or run_cursor.rowcount != 1:
                raise RepositoryConflict(f"answer/run changed concurrently: {resumed.run_id}")
        return True

    def record_facts(
        self,
        workspace_id: str,
        *,
        expected_version: int,
        sources: tuple[SourceRecord, ...],
        claims: tuple[KnowledgeClaim, ...],
        tool_effect: ToolEffect | None = None,
    ) -> TripWorkspace:
        if not claims:
            raise ValueError("record_facts requires at least one claim")
        workspace = self.get_workspace(workspace_id)
        if workspace is None:
            raise KeyError(workspace_id)
        if workspace.version != expected_version:
            raise RepositoryConflict(
                f"stale workspace version: expected {expected_version}, current {workspace.version}"
            )
        source_ids = {source.source_record_id for source in sources}
        existing_source_ids = {
            source.source_record_id for source in self.list_sources(workspace_id)
        }
        if any(
            not set(claim.source_record_ids).issubset(source_ids | existing_source_ids)
            for claim in claims
        ):
            raise ValueError("every claim source must already exist or be in the atomic fact batch")
        with self._lock, self._connection:
            self._link_sources(workspace_id, sources)
            inserted = 0
            for claim in claims:
                payload = _KNOWLEDGE_CLAIM_ADAPTER.dump_json(claim).decode()
                fingerprint = _claim_fingerprint(claim)
                semantic_existing = self._connection.execute(
                    "SELECT claim_id FROM knowledge_claims WHERE workspace_id = ? AND fingerprint = ?",
                    (workspace_id, fingerprint),
                ).fetchone()
                if semantic_existing is not None:
                    continue
                existing = self._connection.execute(
                    "SELECT workspace_id, payload FROM knowledge_claims WHERE claim_id = ?",
                    (claim.claim_id,),
                ).fetchone()
                if existing is not None:
                    if existing["workspace_id"] == workspace_id and existing["payload"] == payload:
                        continue
                    raise RepositoryConflict(
                        f"claim id collision with different content or workspace: {claim.claim_id}"
                    )
                self._connection.execute(
                    """INSERT INTO knowledge_claims(
                        claim_id, workspace_id, kind, entity_id, field_name, payload, fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        claim.claim_id,
                        workspace_id,
                        claim.kind,
                        claim.entity_id,
                        claim.field,
                        payload,
                        fingerprint,
                    ),
                )
                inserted += 1
            if inserted == 0:
                return workspace
            updated = workspace.with_fact_revision()
            cursor = self._connection.execute(
                "UPDATE trip_workspaces SET version = ?, payload = ? WHERE workspace_id = ? AND version = ?",
                (updated.version, updated.model_dump_json(), workspace_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict(f"workspace changed concurrently: {workspace_id}")
            self._connection.execute(
                "INSERT INTO workspace_snapshots(workspace_id, version, payload) VALUES (?, ?, ?)",
                (workspace_id, updated.version, updated.model_dump_json()),
            )
            self._record_tool_effect_locked(tool_effect)
        return updated

    def list_sources(self, workspace_id: str) -> tuple[SourceRecord, ...]:
        rows = self._connection.execute(
            """SELECT artifact.payload
            FROM workspace_source_observations AS observation
            JOIN source_artifacts AS artifact
              ON artifact.source_record_id = observation.source_record_id
            WHERE observation.workspace_id = ?
            ORDER BY artifact.source_record_id""",
            (workspace_id,),
        ).fetchall()
        return tuple(SourceRecord.model_validate_json(row["payload"]) for row in rows)

    def get_place_search_cache(
        self, cache_key: str, *, max_age: timedelta = timedelta(days=30)
    ) -> tuple[tuple[SourceRecord, ...], tuple[PlaceCandidate, ...]] | None:
        row = self._connection.execute(
            "SELECT stored_at, payload FROM place_search_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None or datetime.fromisoformat(row["stored_at"]) < datetime.now().astimezone() - max_age:
            return None
        payload = json.loads(row["payload"])
        return (
            tuple(
                SourceRecord.model_validate_json(json.dumps(item))
                for item in payload["sources"]
            ),
            tuple(
                PlaceCandidate.model_validate_json(json.dumps(item))
                for item in payload["candidates"]
            ),
        )

    def save_place_search_cache(
        self,
        cache_key: str,
        *,
        sources: tuple[SourceRecord, ...],
        candidates: tuple[PlaceCandidate, ...],
    ) -> None:
        payload = json.dumps(
            {
                "sources": [item.model_dump(mode="json") for item in sources],
                "candidates": [item.model_dump(mode="json") for item in candidates],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO place_search_cache(cache_key, stored_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    stored_at = excluded.stored_at,
                    payload = excluded.payload""",
                (cache_key, datetime.now().astimezone().isoformat(), payload),
            )

    def list_claims(
        self,
        workspace_id: str,
        *,
        entity_id: str | None = None,
        field: str | None = None,
    ) -> tuple[KnowledgeClaim, ...]:
        clauses = ["workspace_id = ?"]
        params: list[str] = [workspace_id]
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        if field is not None:
            clauses.append("field_name = ?")
            params.append(field)
        rows = self._connection.execute(
            f"SELECT payload FROM knowledge_claims WHERE {' AND '.join(clauses)} ORDER BY claim_id",
            params,
        ).fetchall()
        return tuple(_KNOWLEDGE_CLAIM_ADAPTER.validate_json(row["payload"]) for row in rows)

    def create_release(self, release: ReleaseRecord) -> ReleaseRecord:
        existing = self.get_release_by_key(release.release_key)
        if existing:
            return existing
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    """INSERT INTO agent_releases(release_id, release_key, workspace_id, run_id, payload)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        release.release_id,
                        release.release_key,
                        release.workspace_id,
                        release.run_id,
                        release.model_dump_json(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = self.get_release_by_key(release.release_key)
                if existing:
                    return existing
                raise RepositoryConflict(f"release already exists: {release.release_id}") from exc
        return release

    def create_candidate_checkpoint(
        self, checkpoint: CandidateCheckpoint
    ) -> CandidateCheckpoint:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO candidate_checkpoints(
                    checkpoint_id, workspace_id, run_id, payload
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO NOTHING""",
                (
                    checkpoint.checkpoint_id,
                    checkpoint.workspace_id,
                    checkpoint.agent_run_id,
                    checkpoint.model_dump_json(),
                ),
            )
            row = self._connection.execute(
                "SELECT payload FROM candidate_checkpoints WHERE checkpoint_id = ?",
                (checkpoint.checkpoint_id,),
            ).fetchone()
        if row is None:
            raise RepositoryConflict(
                f"candidate checkpoint was not persisted: {checkpoint.checkpoint_id}"
            )
        return CandidateCheckpoint.model_validate_json(row["payload"])

    def latest_candidate_checkpoint(self, run_id: str) -> CandidateCheckpoint | None:
        row = self._connection.execute(
            """SELECT payload FROM candidate_checkpoints
            WHERE run_id = ? ORDER BY rowid DESC LIMIT 1""",
            (run_id,),
        ).fetchone()
        return CandidateCheckpoint.model_validate_json(row["payload"]) if row else None

    def publish_candidate(
        self,
        candidate: CandidateSnapshot,
        release: ReleaseRecord,
        narrative: ReleaseNarrative,
        *,
        tool_effect: ToolEffect | None = None,
    ) -> ReleaseRecord:
        if release.candidate_id != candidate.candidate_id:
            raise ValueError("release must reference the candidate being published")
        if narrative.release_id != release.release_id:
            raise ValueError("narrative must reference the release being published")
        if narrative.route_fact_fingerprint != release.route_fact_fingerprint:
            raise ValueError("narrative fingerprint must match the release")
        with self._lock, self._connection:
            existing_row = self._connection.execute(
                "SELECT payload FROM agent_releases WHERE release_key = ?",
                (release.release_key,),
            ).fetchone()
            if existing_row:
                return ReleaseRecord.model_validate_json(existing_row["payload"])
            row = self._connection.execute(
                "SELECT payload FROM agent_runs WHERE run_id = ?", (candidate.agent_run_id,)
            ).fetchone()
            if not row:
                raise KeyError(candidate.agent_run_id)
            current = AgentRun.model_validate_json(row["payload"])
            published = current.transition(RunStatus.PUBLISHED, at=release.created_at)
            self._connection.execute(
                """INSERT INTO agent_candidates(candidate_id, workspace_id, run_id, payload)
                VALUES (?, ?, ?, ?)""",
                (
                    candidate.candidate_id,
                    candidate.workspace_id,
                    candidate.agent_run_id,
                    candidate.model_dump_json(),
                ),
            )
            self._connection.execute(
                """INSERT INTO agent_releases(release_id, release_key, workspace_id, run_id, payload)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    release.release_id,
                    release.release_key,
                    release.workspace_id,
                    release.run_id,
                    release.model_dump_json(),
                ),
            )
            self._connection.execute(
                "INSERT INTO release_narratives(narrative_id, release_id, payload) VALUES (?, ?, ?)",
                (narrative.narrative_id, narrative.release_id, narrative.model_dump_json()),
            )
            cursor = self._connection.execute(
                "UPDATE agent_runs SET status = ?, payload = ? WHERE run_id = ? AND status = ?",
                (
                    published.status.value,
                    published.model_dump_json(),
                    published.run_id,
                    current.status.value,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryConflict(f"run changed concurrently: {published.run_id}")
            self._record_tool_effect_locked(tool_effect)
        return release

    def create_candidate(self, candidate: CandidateSnapshot) -> CandidateSnapshot:
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    """INSERT INTO agent_candidates(candidate_id, workspace_id, run_id, payload)
                    VALUES (?, ?, ?, ?)""",
                    (
                        candidate.candidate_id,
                        candidate.workspace_id,
                        candidate.agent_run_id,
                        candidate.model_dump_json(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = self.get_candidate(candidate.candidate_id)
                if existing:
                    return existing
                raise RepositoryConflict(f"candidate already exists: {candidate.candidate_id}") from exc
        return candidate

    def reject_candidate(
        self,
        candidate: CandidateSnapshot,
        rejection: CandidateRejected,
    ) -> CandidateRejected:
        if rejection.candidate_id != candidate.candidate_id:
            raise ValueError("rejection must reference the candidate being frozen")
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT payload FROM candidate_rejections WHERE candidate_id = ?",
                (candidate.candidate_id,),
            ).fetchone()
            if existing:
                return CandidateRejected.model_validate_json(existing["payload"])
            count = self._connection.execute(
                "SELECT COUNT(*) AS value FROM candidate_rejections WHERE run_id = ?",
                (candidate.agent_run_id,),
            ).fetchone()["value"]
            if count >= 3:
                raise RepositoryConflict(
                    f"candidate submission limit reached: {candidate.agent_run_id}"
                )
            self._connection.execute(
                """INSERT INTO agent_candidates(candidate_id, workspace_id, run_id, payload)
                VALUES (?, ?, ?, ?)""",
                (
                    candidate.candidate_id,
                    candidate.workspace_id,
                    candidate.agent_run_id,
                    candidate.model_dump_json(),
                ),
            )
            self._connection.execute(
                """INSERT INTO candidate_rejections(rejection_id, candidate_id, run_id, payload)
                VALUES (?, ?, ?, ?)""",
                (
                    rejection.rejection_id,
                    rejection.candidate_id,
                    candidate.agent_run_id,
                    rejection.model_dump_json(),
                ),
            )
        return rejection

    def list_candidate_rejections(self, run_id: str) -> tuple[CandidateRejected, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM candidate_rejections WHERE run_id = ? ORDER BY rowid",
                (run_id,),
            ).fetchall()
        return tuple(CandidateRejected.model_validate_json(row["payload"]) for row in rows)

    def get_candidate(self, candidate_id: str) -> CandidateSnapshot | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM agent_candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        return CandidateSnapshot.model_validate_json(row["payload"]) if row else None

    def get_release_by_key(self, release_key: str) -> ReleaseRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM agent_releases WHERE release_key = ?", (release_key,)
            ).fetchone()
        return ReleaseRecord.model_validate_json(row["payload"]) if row else None

    def list_releases(self, workspace_id: str) -> tuple[ReleaseRecord, ...]:
        rows = self._connection.execute(
            "SELECT payload FROM agent_releases WHERE workspace_id = ? ORDER BY rowid",
            (workspace_id,),
        ).fetchall()
        return tuple(ReleaseRecord.model_validate_json(row["payload"]) for row in rows)

    def save_release_narrative(self, narrative: ReleaseNarrative) -> ReleaseNarrative:
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT payload FROM release_narratives WHERE release_id = ?",
                (narrative.release_id,),
            ).fetchone()
            if existing:
                return ReleaseNarrative.model_validate_json(existing["payload"])
            self._connection.execute(
                "INSERT INTO release_narratives(narrative_id, release_id, payload) VALUES (?, ?, ?)",
                (narrative.narrative_id, narrative.release_id, narrative.model_dump_json()),
            )
        return narrative

    def get_release_narrative(self, release_id: str) -> ReleaseNarrative | None:
        row = self._connection.execute(
            "SELECT payload FROM release_narratives WHERE release_id = ?",
            (release_id,),
        ).fetchone()
        return ReleaseNarrative.model_validate_json(row["payload"]) if row else None
