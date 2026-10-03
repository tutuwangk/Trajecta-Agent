from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from main import create_app


LEGACY_APP_DIRECTORIES = (
    "adapters",
    "agents",
    "api",
    "domain",
    "orchestration",
    "planning",
    "schemas",
    "services",
    "trip_agent",
)


def test_only_v3_backend_stack_remains() -> None:
    app_root = Path(__file__).resolve().parents[3] / "app"

    assert [
        name for name in LEGACY_APP_DIRECTORIES if (app_root / name).exists()
    ] == []


def test_root_application_exposes_v3_without_legacy_routes() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/health" in paths
    assert any(path.startswith("/api/v3/") for path in paths)
    assert not any(path.startswith("/api/v2/") for path in paths)
    assert "/api/sessions" not in paths
    assert TestClient(app).get("/health").json() == {
        "status": "ok",
        "stack": "v3",
    }
