"""Endpoint GET /api/v1/status — estado detallado de todos los componentes."""

from __future__ import annotations

import time

from fastapi import APIRouter
from pydantic import BaseModel

from ...ws_manager import ws_manager

router = APIRouter(prefix="/status", tags=["system"])

_START_TIME = time.monotonic()


class ComponentStatus(BaseModel):
    ok: bool
    detail: str


class SystemStatus(BaseModel):
    backend: ComponentStatus
    model: ComponentStatus
    voice_stt: ComponentStatus
    voice_tts: ComponentStatus
    memory: ComponentStatus
    mcp: ComponentStatus
    ws_connections: int
    uptime_s: float


@router.get("/", response_model=SystemStatus)
async def system_status() -> SystemStatus:
    return SystemStatus(
        backend=ComponentStatus(ok=True, detail="running"),
        model=ComponentStatus(ok=False, detail="not_loaded — ejecuta itzel setup"),
        voice_stt=ComponentStatus(ok=False, detail="not_loaded — sesión 3"),
        voice_tts=ComponentStatus(ok=False, detail="not_loaded — sesión 3"),
        memory=ComponentStatus(ok=True,  detail="sqlite — ~/.itzel/memory.db"),
        mcp=ComponentStatus(ok=False, detail="no servers configured"),
        ws_connections=ws_manager.active_count,
        uptime_s=round(time.monotonic() - _START_TIME, 2),
    )
