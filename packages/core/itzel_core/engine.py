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
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1 import chat, health, models, rag, status, voice
from .api.v1 import websocket as ws_route
from .config import config
from .logger import log_engine
from .middleware import AgentTimeoutMiddleware, RequestLoggerMiddleware
from .models import get_adapter
from .models.base import ModelNotAvailableError
from .monitoring.dashboard import router as dashboard_router

_IS_DEV   = os.getenv("ITZEL_ENV", "development") != "production"
_START_TS = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    log_engine.info(
        "Itzel backend arrancando",
        extra={"component": "engine", "host": config.server.host, "port": config.server.port},
    )

    # ── Detección y carga del modelo ────────────────────────────────
    # get_adapter() detecta automáticamente qué está disponible:
    #   1. llama.cpp + .gguf   → carga el modelo en memoria (~2-10s)
    #   2. Ollama en :11434    → verifica conectividad
    #   3. NoModelAdapter      → responde con instrucciones de instalación
    try:
        adapter = await get_adapter()
        info    = await adapter.info()
        log_engine.info(
            "Modelo activo: %s (backend: %s, cargado: %s)",
            info.name, info.backend, info.loaded,
            extra={"component": "engine", "model": info.id},
        )
    except ModelNotAvailableError as exc:
        # No bloqueamos el arranque — el endpoint de chat manejará el error
        log_engine.warning(
            "No hay modelo disponible al arrancar: %s", exc,
            extra={"component": "engine"},
        )
    except Exception as exc:
        log_engine.error(
            "Error inesperado al inicializar el modelo: %s", exc,
            extra={"component": "engine"},
        )

    elapsed = time.time() - _START_TS
    log_engine.info(
        "Backend listo en %.2fs — escuchando en %s:%d",
        elapsed, config.server.host, config.server.port,
        extra={"component": "engine"},
    )

    yield

    # ── Teardown ────────────────────────────────────────────────────
    log_engine.info("Itzel backend detenido", extra={"component": "engine"})


def create_app() -> FastAPI:
    app = FastAPI(
        title="Itzel API",
        description="IA personal local — 100% on-device · MIT License",
        version="0.1.0",
        docs_url    ="/api/docs"         if _IS_DEV else None,
        redoc_url   ="/api/redoc"        if _IS_DEV else None,
        openapi_url ="/api/openapi.json" if _IS_DEV else None,
        lifespan=lifespan,
    )

    # ── Middleware (el primero registrado es el más externo) ─────────
    app.add_middleware(AgentTimeoutMiddleware, timeout_s=120.0)
    app.add_middleware(RequestLoggerMiddleware)

    # ── CORS: solo orígenes localhost ────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:1420",   # Tauri dev server
            "tauri://localhost",       # Tauri producción macOS/Linux
            "https://tauri.localhost", # Tauri producción Windows
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

    # ── Memoria semántica (RAG) /api/v1/rag ──────────────────────────
    # Se monta SIEMPRE: el router se auto-protege (503 si rag.enabled=False,
    # 501 si faltan las deps opcionales [rag]). Así la UI "Mis documentos"
    # puede consultar /rag/status y ofrecer activarlo, en vez de un 404.
    app.include_router(rag.router,     prefix="/api/v1")

    # ── WebSocket /ws (chat bridge) ──────────────────────────────────
    app.include_router(ws_route.router)

    # ── WebSocket /api/v1/voice/ws (pipeline de voz) ─────────────────
    app.include_router(voice.router,   prefix="/api/v1")

    # ── Dashboard de monitoreo local /dashboard ───────────────────────
    # Solo accesible desde localhost (el CORS ya lo garantiza).
    # Devuelve 503 automáticamente cuando monitoring.enabled=False.
    if config.monitoring.enabled:
        app.include_router(dashboard_router)
        log_engine.info(
            "Dashboard de monitoreo en /dashboard",
            extra={"component": "engine"},
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "itzel_core.engine:app",
        host      = config.server.host,
        port      = config.server.port,
        reload    = _IS_DEV,
        log_level = "debug" if _IS_DEV else "warning",
        workers   = 1,  # un solo worker — el modelo no es thread-safe
    )
