from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.env import load_project_env

load_project_env()

from app.api.routes import router
from app.trip_agent.api import router as trip_agent_v2_router


def create_app() -> FastAPI:
    app = FastAPI(title="Travel Agent v0.2", version="0.2.0")
    origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in origins if origin.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(trip_agent_v2_router)
    return app


app = create_app()
