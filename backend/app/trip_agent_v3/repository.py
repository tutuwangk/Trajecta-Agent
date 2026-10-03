from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.trip_agent_v3.domain.delivery import (
    CandidateSnapshot,
    DeliveryAssessment,
    ReleaseRecord,
    RunRecord,
    RunStatus,
)
from app.trip_agent_v3.domain.execution import TripWorkspaceRecord


class V3RepositoryConflict(RuntimeError):
    pass


_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset(
        {RunStatus.ACTIVE, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.ACTIVE: frozenset(
        {
            RunStatus.WAITING_USER,
            RunStatus.NEEDS_RESUME,
            RunStatus.SUCCEEDED,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.WAITING_USER: frozenset(
        {RunStatus.NEEDS_RESUME, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.NEEDS_RESUME: frozenset(
        {RunStatus.ACTIVE, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.FAILED: frozenset(),
}


class SqliteTripAgentV3Repository:
    """V3-owned artifact store with run-bound candidate and release reads."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.database, check_same_thread=False
        )
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
                CREATE TABLE IF NOT EXISTS v3_workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v3_runs (
                    run_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v3_candidates (
                    candidate_snapshot_id TEXT PRIMARY KEY,
                    producing_run_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (producing_run_id) REFERENCES v3_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS v3_candidates_by_run
                ON v3_candidates(producing_run_id);
                CREATE TABLE IF NOT EXISTS v3_delivery_assessments (
                    candidate_snapshot_id TEXT PRIMARY KEY,
                    producing_run_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (candidate_snapshot_id)
                        REFERENCES v3_candidates(candidate_snapshot_id),
                    FOREIGN KEY (producing_run_id) REFERENCES v3_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS v3_releases (
                    release_id TEXT PRIMARY KEY,
                    producing_run_id TEXT NOT NULL UNIQUE,
                    workspace_id TEXT NOT NULL,
                    candidate_snapshot_id TEXT NOT NULL UNIQUE,
                    published_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (producing_run_id) REFERENCES v3_runs(run_id),
                    FOREIGN KEY (candidate_snapshot_id)
                        REFERENCES v3_candidates(candidate_snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS v3_run_requests (
                    workspace_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY (workspace_id, idempotency_key),
                    FOREIGN KEY (workspace_id)
                        REFERENCES v3_workspaces(workspace_id),
                    FOREIGN KEY (run_id) REFERENCES v3_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS v3_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES v3_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS v3_checkpoints (
                    run_id TEXT PRIMARY KEY,
                    input_fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES v3_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS v3_provider_transcripts (
                    run_id TEXT PRIMARY KEY,
                    input_fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES v3_runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS v3_run_metrics (
                    run_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES v3_runs(run_id)
                );
                """
            )

    def save_workspace(self, workspace: TripWorkspaceRecord) -> None:
        payload = workspace.model_dump_json()
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT payload FROM v3_workspaces WHERE workspace_id = ?",
                (workspace.workspace_id,),
            ).fetchone()
            if existing is not None and existing["payload"] != payload:
                raise V3RepositoryConflict(
                    f"workspace {workspace.workspace_id} already exists"
                )
            self._connection.execute(
                """INSERT OR IGNORE INTO v3_workspaces(
                    workspace_id, version, payload
                ) VALUES (?, ?, ?)""",
                (
                    workspace.workspace_id,
                    workspace.version,
                    payload,
                ),
            )

    def get_workspace(
        self, workspace_id: str
    ) -> TripWorkspaceRecord | None:
        row = self._connection.execute(
            "SELECT payload FROM v3_workspaces WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        return (
            TripWorkspaceRecord.model_validate_json(row["payload"])
            if row
            else None
        )

    def update_workspace(
        self,
        workspace: TripWorkspaceRecord,
        *,
        expected_version: int,
    ) -> None:
        if workspace.version != expected_version + 1:
            raise V3RepositoryConflict(
                "workspace update must increment version by one"
            )
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE v3_workspaces SET version = ?, payload = ?
                WHERE workspace_id = ? AND version = ?""",
                (
                    workspace.version,
                    workspace.model_dump_json(),
                    workspace.workspace_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise V3RepositoryConflict(
                    "workspace version changed concurrently"
                )

    def get_run_by_idempotency_key(
        self, *, workspace_id: str, idempotency_key: str
    ) -> RunRecord | None:
        row = self._connection.execute(
            """SELECT run_id FROM v3_run_requests
            WHERE workspace_id = ? AND idempotency_key = ?""",
            (workspace_id, idempotency_key),
        ).fetchone()
        return self.get_run(row["run_id"]) if row else None

    def get_active_run(self, workspace_id: str) -> RunRecord | None:
        row = self._connection.execute(
            """SELECT run_id FROM v3_runs
            WHERE workspace_id = ? AND status IN (
                'created', 'active', 'waiting_user', 'needs_resume'
            ) ORDER BY rowid DESC LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        return self.get_run(row["run_id"]) if row else None

    def create_run(
        self, *, workspace_id: str, idempotency_key: str
    ) -> RunRecord:
        workspace = self.get_workspace(workspace_id)
        if workspace is None:
            raise KeyError(workspace_id)
        with self._lock, self._connection:
            existing = self._connection.execute(
                """SELECT run_id FROM v3_run_requests
                WHERE workspace_id = ? AND idempotency_key = ?""",
                (workspace_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                run = self.get_run(existing["run_id"])
                if run is None:
                    raise V3RepositoryConflict(
                        "idempotent run reference is missing"
                    )
                return run
            active = self._connection.execute(
                """SELECT run_id FROM v3_runs
                WHERE workspace_id = ? AND status IN (
                    'created', 'active', 'waiting_user', 'needs_resume'
                ) LIMIT 1""",
                (workspace_id,),
            ).fetchone()
            if active is not None:
                raise V3RepositoryConflict(
                    f"workspace already has active run {active['run_id']}"
                )
            run = RunRecord(
                run_id=f"run-{uuid4()}",
                workspace_id=workspace_id,
                goal_revision_id=workspace.goal.goal_revision_id,
                status=RunStatus.CREATED,
            )
            self._connection.execute(
                """INSERT INTO v3_runs(
                    run_id, workspace_id, status, payload
                ) VALUES (?, ?, ?, ?)""",
                (
                    run.run_id,
                    run.workspace_id,
                    run.status.value,
                    run.model_dump_json(),
                ),
            )
            self._connection.execute(
                """INSERT INTO v3_run_requests(
                    workspace_id, idempotency_key, run_id
                ) VALUES (?, ?, ?)""",
                (workspace_id, idempotency_key, run.run_id),
            )
            return run

    def save_run(self, run: RunRecord) -> None:
        payload = run.model_dump_json()
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT payload FROM v3_runs WHERE run_id = ?",
                (run.run_id,),
            ).fetchone()
            if existing is not None and existing["payload"] != payload:
                raise V3RepositoryConflict(
                    f"run {run.run_id} already exists with different payload"
                )
            self._connection.execute(
                """INSERT OR IGNORE INTO v3_runs(
                    run_id, workspace_id, status, payload
                ) VALUES (?, ?, ?, ?)""",
                (
                    run.run_id,
                    run.workspace_id,
                    run.status.value,
                    payload,
                ),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self._connection.execute(
            "SELECT payload FROM v3_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return RunRecord.model_validate_json(row["payload"]) if row else None

    def transition_run(
        self, run_id: str, status: RunStatus
    ) -> RunRecord:
        with self._lock, self._connection:
            current = self.get_run(run_id)
            if current is None:
                raise KeyError(run_id)
            if status is current.status:
                return current
            if status not in _RUN_TRANSITIONS[current.status]:
                raise V3RepositoryConflict(
                    f"illegal run transition "
                    f"{current.status.value} -> {status.value}"
                )
            updated = RunRecord(
                run_id=current.run_id,
                workspace_id=current.workspace_id,
                goal_revision_id=current.goal_revision_id,
                status=status,
            )
            self._connection.execute(
                "UPDATE v3_runs SET status = ?, payload = ? WHERE run_id = ?",
                (status.value, updated.model_dump_json(), run_id),
            )
            return updated

    def claim_run_for_execution(self, run_id: str) -> RunRecord | None:
        """Atomically acquire a created/resumable run for one executor."""
        with self._lock, self._connection:
            current = self.get_run(run_id)
            if current is None:
                raise KeyError(run_id)
            if current.status not in {
                RunStatus.CREATED,
                RunStatus.NEEDS_RESUME,
            }:
                return None
            updated = RunRecord(
                run_id=current.run_id,
                workspace_id=current.workspace_id,
                goal_revision_id=current.goal_revision_id,
                status=RunStatus.ACTIVE,
            )
            cursor = self._connection.execute(
                """UPDATE v3_runs SET status = ?, payload = ?
                WHERE run_id = ? AND status = ?""",
                (
                    RunStatus.ACTIVE.value,
                    updated.model_dump_json(),
                    run_id,
                    current.status.value,
                ),
            )
            return updated if cursor.rowcount == 1 else None

    def append_event(
        self, run_id: str, event: dict[str, object]
    ) -> int:
        import json

        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT INTO v3_events(run_id, payload) VALUES (?, ?)",
                (
                    run_id,
                    json.dumps(event, ensure_ascii=False, default=str),
                ),
            )
            return int(cursor.lastrowid)

    def list_events(
        self, run_id: str, *, after_event_id: int = 0
    ) -> tuple[dict[str, object], ...]:
        import json

        rows = self._connection.execute(
            """SELECT event_id, payload, created_at FROM v3_events
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

    def save_checkpoint(
        self,
        *,
        run_id: str,
        input_fingerprint: str,
        payload: dict[str, object],
    ) -> None:
        import json

        if self.get_run(run_id) is None:
            raise KeyError(run_id)
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO v3_checkpoints(
                    run_id, input_fingerprint, payload, updated_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(run_id) DO UPDATE SET
                    input_fingerprint = excluded.input_fingerprint,
                    payload = excluded.payload,
                    updated_at = CURRENT_TIMESTAMP""",
                (run_id, input_fingerprint, serialized),
            )

    def get_checkpoint(
        self, run_id: str
    ) -> tuple[str, dict[str, object]] | None:
        import json

        row = self._connection.execute(
            """SELECT input_fingerprint, payload
            FROM v3_checkpoints WHERE run_id = ?""",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return row["input_fingerprint"], json.loads(row["payload"])

    def save_provider_transcript(
        self,
        *,
        run_id: str,
        input_fingerprint: str,
        payload: bytes,
    ) -> None:
        if self.get_run(run_id) is None:
            raise KeyError(run_id)
        serialized = payload.decode("utf-8")
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO v3_provider_transcripts(
                    run_id, input_fingerprint, payload, updated_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(run_id) DO UPDATE SET
                    input_fingerprint = excluded.input_fingerprint,
                    payload = excluded.payload,
                    updated_at = CURRENT_TIMESTAMP""",
                (run_id, input_fingerprint, serialized),
            )

    def get_provider_transcript(
        self, run_id: str
    ) -> tuple[str, bytes] | None:
        row = self._connection.execute(
            """SELECT input_fingerprint, payload
            FROM v3_provider_transcripts WHERE run_id = ?""",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return row["input_fingerprint"], row["payload"].encode("utf-8")

    def save_run_metrics(
        self, *, run_id: str, payload: dict[str, object]
    ) -> None:
        import json

        if self.get_run(run_id) is None:
            raise KeyError(run_id)
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO v3_run_metrics(
                    run_id, payload, updated_at
                ) VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(run_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = CURRENT_TIMESTAMP""",
                (run_id, serialized),
            )

    def get_run_metrics(
        self, run_id: str
    ) -> dict[str, object] | None:
        import json

        row = self._connection.execute(
            "SELECT payload FROM v3_run_metrics WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_candidate(
        self,
        candidate: CandidateSnapshot,
        assessment: DeliveryAssessment,
    ) -> None:
        run = self.get_run(candidate.producing_run_id)
        if run is None:
            raise V3RepositoryConflict("candidate references missing run")
        if (
            run.workspace_id != candidate.workspace_id
            or run.goal_revision_id != candidate.goal_revision_id
            or assessment.candidate_snapshot_id
            != candidate.candidate_snapshot_id
            or assessment.producing_run_id != candidate.producing_run_id
        ):
            raise V3RepositoryConflict(
                "candidate, assessment, and run lineage do not match"
            )
        with self._lock, self._connection:
            existing_candidate = self._connection.execute(
                """SELECT payload FROM v3_candidates
                WHERE candidate_snapshot_id = ?""",
                (candidate.candidate_snapshot_id,),
            ).fetchone()
            existing_assessment = self._connection.execute(
                """SELECT payload FROM v3_delivery_assessments
                WHERE candidate_snapshot_id = ?""",
                (candidate.candidate_snapshot_id,),
            ).fetchone()
            if existing_candidate is not None or existing_assessment is not None:
                if (
                    existing_candidate is not None
                    and existing_assessment is not None
                    and existing_candidate["payload"]
                    == candidate.model_dump_json()
                    and existing_assessment["payload"]
                    == assessment.model_dump_json()
                ):
                    return
                raise V3RepositoryConflict(
                    "candidate snapshot id already has different content"
                )
            self._connection.execute(
                """INSERT INTO v3_candidates(
                    candidate_snapshot_id, producing_run_id,
                    workspace_id, payload
                ) VALUES (?, ?, ?, ?)""",
                (
                    candidate.candidate_snapshot_id,
                    candidate.producing_run_id,
                    candidate.workspace_id,
                    candidate.model_dump_json(),
                ),
            )
            self._connection.execute(
                """INSERT INTO v3_delivery_assessments(
                    candidate_snapshot_id, producing_run_id, state, payload
                ) VALUES (?, ?, ?, ?)""",
                (
                    assessment.candidate_snapshot_id,
                    assessment.producing_run_id,
                    assessment.state.value,
                    assessment.model_dump_json(),
                ),
            )

    def latest_candidate_for_run(
        self, run_id: str
    ) -> CandidateSnapshot | None:
        row = self._connection.execute(
            """SELECT payload FROM v3_candidates
            WHERE producing_run_id = ? ORDER BY rowid DESC LIMIT 1""",
            (run_id,),
        ).fetchone()
        return (
            CandidateSnapshot.model_validate_json(row["payload"])
            if row
            else None
        )

    def get_candidate(self, candidate_snapshot_id: str) -> CandidateSnapshot | None:
        row = self._connection.execute(
            "SELECT payload FROM v3_candidates WHERE candidate_snapshot_id = ?",
            (candidate_snapshot_id,),
        ).fetchone()
        return CandidateSnapshot.model_validate_json(row["payload"]) if row else None

    def assessment_for_candidate(
        self, candidate_snapshot_id: str
    ) -> DeliveryAssessment | None:
        row = self._connection.execute(
            """SELECT payload FROM v3_delivery_assessments
            WHERE candidate_snapshot_id = ?""",
            (candidate_snapshot_id,),
        ).fetchone()
        return (
            DeliveryAssessment.model_validate_json(row["payload"])
            if row
            else None
        )

    def _validate_release_lineage(self, release: ReleaseRecord) -> None:
        run = self.get_run(release.producing_run_id)
        candidate = self.get_candidate(release.candidate_snapshot_id)
        if run is None or candidate is None:
            raise V3RepositoryConflict("release references missing run or candidate")
        if (
            release.workspace_id != run.workspace_id
            or release.workspace_id != candidate.workspace_id
            or candidate.producing_run_id != run.run_id
            or release.goal_revision_id != run.goal_revision_id
        ):
            raise V3RepositoryConflict("release, candidate, and run lineage do not match")

    def _insert_release(self, release: ReleaseRecord) -> None:
        existing = self.release_for_run(release.producing_run_id)
        if existing is not None:
            if existing == release:
                return
            raise V3RepositoryConflict("run already has an immutable release")
        self._connection.execute(
            """INSERT INTO v3_releases(
                release_id, producing_run_id, workspace_id,
                candidate_snapshot_id, published_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (release.release_id, release.producing_run_id, release.workspace_id,
             release.candidate_snapshot_id, release.published_at.isoformat(), release.model_dump_json()),
        )

    def save_release(self, release: ReleaseRecord) -> None:
        with self._lock, self._connection:
            self._validate_release_lineage(release)
            self._insert_release(release)

    def commit_release(self, release: ReleaseRecord) -> ReleaseRecord:
        with self._lock, self._connection:
            self._validate_release_lineage(release)
            existing = self.release_for_run(release.producing_run_id)
            if existing is not None:
                if existing.candidate_snapshot_id == release.candidate_snapshot_id:
                    return existing
                raise V3RepositoryConflict("run already published a different candidate")
            run = self.get_run(release.producing_run_id)
            if run.status is not RunStatus.ACTIVE:
                raise V3RepositoryConflict("publication requires an active run")
            succeeded = run.model_copy(update={"status": RunStatus.SUCCEEDED})
            updated = self._connection.execute(
                "UPDATE v3_runs SET status = ?, payload = ? WHERE run_id = ? AND status = ?",
                (RunStatus.SUCCEEDED.value, succeeded.model_dump_json(), run.run_id, RunStatus.ACTIVE.value),
            )
            if updated.rowcount != 1:
                raise V3RepositoryConflict("run changed during publication")
            self._insert_release(release)
            return release

    def release_for_run(self, run_id: str) -> ReleaseRecord | None:
        row = self._connection.execute(
            "SELECT payload FROM v3_releases WHERE producing_run_id = ?",
            (run_id,),
        ).fetchone()
        return (
            ReleaseRecord.model_validate_json(row["payload"]) if row else None
        )
