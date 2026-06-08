"""Endpoint /api/v1/health — estado del sistema."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["system"])


class HealthResponse(BaseModel):
    status: str
    version: str
    model_loaded: bool
    voice_loaded: bool


@router.get("/", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        model_loaded=False,
        voice_loaded=False,
    )
