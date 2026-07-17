from concurrent.futures import ThreadPoolExecutor

from app.services.database import SQLiteStore


def test_planning_run_persists_typed_checkpoints_and_is_idempotent(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("成都一日游", "", {"destination": "成都", "days": 1})

    run_id = store.start_planning_run(
        session_id,
        idempotency_key="request-1",
        input_snapshot={"user_profile": {"destination": "成都", "days": 1}},
    )
    duplicate_run_id = store.start_planning_run(session_id, idempotency_key="request-1")
    store.update_planning_run(
        run_id,
        status="running",
        stage="fact_snapshot",
        checkpoint="fact_snapshot",
        fact_snapshot={"version": "facts-1"},
        metrics={"model_call_count": 1},
    )
    store.update_planning_run(
        run_id,
        status="completed",
        stage="completed",
        checkpoint="completed",
        result_status="degraded",
        release_decision={"status": "degraded"},
        result_snapshot={"itinerary": {"destination": "成都"}},
    )
    store.update_planning_run(run_id, status="running", stage="blueprint", checkpoint="blueprint")
    store.update_planning_run(
        run_id,
        status="completed",
        stage="completed",
        checkpoint="completed",
        result_snapshot={"itinerary": {"destination": "错误覆盖"}},
    )

    run = store.get_planning_run(run_id)

    assert duplicate_run_id == run_id
    assert run["status"] == "completed"
    assert run["checkpoint"] == "completed"
    assert run["result_status"] == "degraded"
    assert run["fact_snapshot"]["version"] == "facts-1"
    assert run["metrics"]["model_call_count"] == 1
    assert run["result_snapshot"]["itinerary"]["destination"] == "成都"


def test_planning_run_idempotency_is_atomic_for_concurrent_requests(tmp_path):
    store = SQLiteStore(str(tmp_path / "travel.sqlite3"))
    session_id = store.create_session("成都一日游", "", {"destination": "成都", "days": 1})

    with ThreadPoolExecutor(max_workers=6) as pool:
        run_ids = list(
            pool.map(
                lambda _: store.start_planning_run(session_id, idempotency_key="same-request"),
                range(12),
            )
        )

    assert len(set(run_ids)) == 1
