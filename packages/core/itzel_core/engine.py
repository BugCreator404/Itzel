"""Orquestador principal — FastAPI app factory.

Registra en orden:
  1. Middleware (AgentTimeout → RequestLogger)
  2. CORS (solo orígenes localhost)
  3. Routers versionados /api/v1/
  4. Endpoint WebSocket /ws
  5. OpenAPI solo en desarrollo
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1 import chat, health, models, status
from .api.v1 import websocket as ws_route
from .config import config
from .logger import log_engine
from .middleware import AgentTimeoutMiddleware, RequestLoggerMiddleware
from .rate_limiter import RateLimiter
from .ws_manager import ws_manager

_IS_DEV = os.getenv("ITZEL_ENV", "development") != "production"
_START_TS = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    log_engine.info(
        "Itzel backend arrancando",
        extra={"component": "engine", "host": config.server.host, "port": config.server.port},
    )
    # TODO(v2): cargar modelo llama.cpp / Ollama
    # TODO(v3): cargar Whisper STT + Kokoro TTS
    yield
    log_engine.info("Itzel backend detenido", extra={"component": "engine"})


def create_app() -> FastAPI:
    app = FastAPI(
        title="Itzel API",
        description="IA personal local — 100% on-device · MIT License",
        version="0.1.0",
        # OpenAPI solo en desarrollo — sin docs en producción
        docs_url="/api/docs"    if _IS_DEV else None,
        redoc_url="/api/redoc"  if _IS_DEV else None,
        openapi_url="/api/openapi.json" if _IS_DEV else None,
        lifespan=lifespan,
    )

    # ── Middleware (orden: el primero registrado es el más externo) ──
    app.add_middleware(AgentTimeoutMiddleware, timeout_s=120.0)
    app.add_middleware(RequestLoggerMiddleware)

    # ── CORS: solo orígenes localhost ────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:1420",    # Tauri dev server
            "tauri://localhost",        # Tauri producción macOS/Linux
            "https://tauri.localhost",  # Tauri producción Windows
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # ── Routers REST /api/v1/ ────────────────────────────────────────
    app.include_router(health.router,  prefix="/api/v1")
    app.include_router(chat.router,    prefix="/api/v1")
    app.include_router(status.router,  prefix="/api/v1")
    app.include_router(models.router,  prefix="/api/v1")

    # ── WebSocket /ws ────────────────────────────────────────────────
    app.include_router(ws_route.router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "itzel_core.engine:app",
        host=config.server.host,
        port=config.server.port,
        reload=_IS_DEV,
        log_level="debug" if _IS_DEV else "warning",
        # Un solo worker — el modelo no es thread-safe
        workers=1,
    )
