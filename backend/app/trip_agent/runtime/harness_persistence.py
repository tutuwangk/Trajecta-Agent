from __future__ import annotations

from pathlib import Path
import sqlite3

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai_harness.step_persistence import StepPersistence, StepStore, continue_run


class HarnessPersistenceAdapter:
    """Keep all pydantic-ai-harness types behind the domain persistence port."""

    def __init__(
        self,
        store: StepStore,
        *,
        deferred_database: str | Path | None = None,
    ) -> None:
        self._store = store
        self._deferred_messages: dict[str, bytes] = {}
        self._deferred_connection = (
            sqlite3.connect(str(deferred_database), check_same_thread=False)
            if deferred_database is not None
            else None
        )
        if self._deferred_connection is not None:
            self._deferred_connection.execute(
                """CREATE TABLE IF NOT EXISTS deferred_provider_transcripts(
                    provider_run_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL
                )"""
            )
            self._deferred_connection.commit()

    def capability(self, *, agent_name: str, run_id: str | None = None) -> StepPersistence:
        return StepPersistence(store=self._store, agent_name=agent_name, run_id=run_id)

    async def continuable_messages(self, provider_run_id: str) -> tuple[ModelMessage, ...]:
        return tuple(await continue_run(self._store, run_id=provider_run_id))

    async def unresolved_effect_ids(self, provider_run_id: str) -> tuple[str, ...]:
        records = await self._store.list_unresolved_tool_effects(run_id=provider_run_id)
        return tuple(record.tool_call_id for record in records)

    async def latest_recovery_state(
        self, conversation_id: str
    ) -> tuple[str, tuple[ModelMessage, ...], tuple[tuple[str, str], ...]]:
        runs = await self._store.list_runs(conversation_id=conversation_id)
        for record in sorted(runs, key=lambda item: item.started_at, reverse=True):
            snapshot = await self._store.latest_snapshot(run_id=record.run_id)
            if snapshot is None:
                continue
            unresolved = await self._store.list_unresolved_tool_effects(run_id=record.run_id)
            return (
                record.run_id,
                tuple(snapshot.messages),
                tuple((item.tool_call_id, item.tool_name) for item in unresolved),
            )
        raise LookupError(f"no continuable provider snapshot for conversation {conversation_id!r}")

    def save_deferred_messages(
        self, provider_run_id: str, messages: tuple[ModelMessage, ...] | list[ModelMessage]
    ) -> None:
        payload = ModelMessagesTypeAdapter.dump_json(messages)
        if self._deferred_connection is None:
            self._deferred_messages[provider_run_id] = payload
            return
        with self._deferred_connection:
            self._deferred_connection.execute(
                """INSERT INTO deferred_provider_transcripts(provider_run_id, payload)
                VALUES (?, ?)
                ON CONFLICT(provider_run_id) DO UPDATE SET payload = excluded.payload""",
                (provider_run_id, payload),
            )

    def deferred_messages(self, provider_run_id: str) -> tuple[ModelMessage, ...]:
        if self._deferred_connection is None:
            payload = self._deferred_messages.get(provider_run_id)
        else:
            row = self._deferred_connection.execute(
                "SELECT payload FROM deferred_provider_transcripts WHERE provider_run_id = ?",
                (provider_run_id,),
            ).fetchone()
            payload = row[0] if row else None
        if payload is None:
            raise LookupError(f"no deferred provider transcript for run_id {provider_run_id!r}")
        return tuple(ModelMessagesTypeAdapter.validate_json(payload))
