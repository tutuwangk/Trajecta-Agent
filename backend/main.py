from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.env import load_project_env

load_project_env()

from app.trip_agent_v3.api import router as trip_agent_v3_router


def create_app() -> FastAPI:
    app = FastAPI(title="Trajecta Travel Agent", version="3.0.0")
    origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in origins if origin.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(trip_agent_v3_router)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "stack": "v3"}

    return app


app = create_app()
