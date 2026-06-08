"""Endpoint GET /api/v1/health — estado rápido del backend."""

from __future__ import annotations

import time

from fastapi import APIRouter
from pydantic import BaseModel

from ...ws_manager import ws_manager

router = APIRouter(prefix="/health", tags=["system"])

# Momento en que arrancó el proceso (unix timestamp)
_START_TIME = time.monotonic()


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_s: float
    model_loaded: bool
    voice_loaded: bool
    ws_connections: int


@router.get("/", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        uptime_s=round(time.monotonic() - _START_TIME, 2),
        model_loaded=False,        # TODO(v2): leer de engine state
        voice_loaded=False,        # TODO(v3): leer de voice manager
        ws_connections=ws_manager.active_count,
    )
