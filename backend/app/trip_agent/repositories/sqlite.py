from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import sqlite3
from threading import RLock

from pydantic import TypeAdapter

from app.trip_agent.domain import (
    AgentRun,
    CandidateRejected,
    CandidateSnapshot,
    ClarificationAnswers,
    ClarificationBatch,
    ReleaseRecord,
    ReleaseNarrative,
    KnowledgeClaim,
    SourceRecord,
    RunStatus,
    TripWorkspace,
)


_KNOWLEDGE_CLAIM_ADAPTER = TypeAdapter(KnowledgeClaim)


class RepositoryConflict(RuntimeError):
    pass


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
                CREATE TABLE IF NOT EXISTS source_records (
                    source_record_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (workspace_id) REFERENCES trip_workspaces(workspace_id)
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

    def save_workspace(self, workspace: TripWorkspace, *, expected_version: int) -> TripWorkspace:
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
        return workspace

    def save_workspace_with_sources(
        self,
        workspace: TripWorkspace,
        *,
        expected_version: int,
        sources: tuple[SourceRecord, ...],
    ) -> TripWorkspace:
        if workspace.version != expected_version + 1:
            raise RepositoryConflict("workspace save must advance exactly one version")
        payload = workspace.model_dump_json()
        with self._lock, self._connection:
            for source in sources:
                self._connection.execute(
                    "INSERT INTO source_records(source_record_id, workspace_id, payload) VALUES (?, ?, ?)",
                    (source.source_record_id, workspace.workspace_id, source.model_dump_json()),
                )
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

    def append_event(self, run_id: str, event: dict[str, object]) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO agent_events(run_id, payload, created_at) VALUES (?, ?, ?)",
                (run_id, json.dumps(event, ensure_ascii=False), datetime.now().astimezone().isoformat()),
            )
        return int(cursor.lastrowid)

    def get_tool_effect(
        self, tool_call_id: str, *, tool_name: str
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT tool_name, payload FROM agent_tool_effects WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
        if row is None:
            return None
        if row["tool_name"] != tool_name:
            raise RepositoryConflict(
                f"tool call id {tool_call_id} was already used by {row['tool_name']}"
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
                """INSERT INTO agent_tool_effects(tool_call_id, run_id, tool_name, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tool_call_id) DO NOTHING""",
                (
                    tool_call_id,
                    run_id,
                    tool_name,
                    payload,
                    datetime.now().astimezone().isoformat(),
                ),
            )
        existing = self.get_tool_effect(tool_call_id, tool_name=tool_name)
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
        updated = workspace.with_fact_revision()
        with self._lock, self._connection:
            for source in sources:
                self._connection.execute(
                    "INSERT INTO source_records(source_record_id, workspace_id, payload) VALUES (?, ?, ?)",
                    (source.source_record_id, workspace_id, source.model_dump_json()),
                )
            for claim in claims:
                self._connection.execute(
                    """INSERT INTO knowledge_claims(
                        claim_id, workspace_id, kind, entity_id, field_name, payload
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        claim.claim_id,
                        workspace_id,
                        claim.kind,
                        claim.entity_id,
                        claim.field,
                        _KNOWLEDGE_CLAIM_ADAPTER.dump_json(claim).decode(),
                    ),
                )
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
        return updated

    def list_sources(self, workspace_id: str) -> tuple[SourceRecord, ...]:
        rows = self._connection.execute(
            "SELECT payload FROM source_records WHERE workspace_id = ? ORDER BY source_record_id",
            (workspace_id,),
        ).fetchall()
        return tuple(SourceRecord.model_validate_json(row["payload"]) for row in rows)

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

    def publish_candidate(
        self,
        candidate: CandidateSnapshot,
        release: ReleaseRecord,
        narrative: ReleaseNarrative,
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
