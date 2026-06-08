"""Orquestador principal — FastAPI app factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1 import chat, health
from .config import config
from .rate_limiter import RateLimiter

# Rate limiter global
rate_limiter = RateLimiter(
    model_rps=config.rate_limit.model_rps,
    voice_rps=config.rate_limit.voice_rps,
    web_rps=config.rate_limit.web_rps,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    # TODO(v2): cargar modelo llama.cpp, Whisper, Kokoro
    yield
    # Shutdown
    # TODO(v2): liberar recursos del modelo


def create_app() -> FastAPI:
    app = FastAPI(
        title="Itzel API",
        description="IA personal local — 100% on-device · MIT License",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # CORS: solo localhost — ningún origen externo
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:1420",   # Tauri dev
            "tauri://localhost",       # Tauri producción
            "https://tauri.localhost",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rutas versionadas
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "itzel_core.engine:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,
        log_level="info",
    )
