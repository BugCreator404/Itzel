"""Endpoints GET /api/v1/models y POST /api/v1/models/switch."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from ...config import config
from ...logger import log_api
from ...ws_manager import WsMessage, ws_manager

router = APIRouter(prefix="/models", tags=["models"])

# Directorio donde viven los GGUF descargados
_MODELS_DIR = Path.home() / ".itzel" / "models"

# Catálogo de modelos conocidos por Itzel
_KNOWN_MODELS: list[dict] = [
    {
        "id": "itzel-1b",
        "name": "Itzel-1B",
        "description": "Modelo principal — Qwen2.5-1.5B fine-tuned. Rápido en CPU.",
        "size_mb": 900,
        "backend": "llama.cpp",
        "filename": "itzel-1b-q4_k_m.gguf",
        "recommended": True,
    },
    {
        "id": "itzel-7b",
        "name": "Itzel-7B",
        "description": "Modelo avanzado — mayor razonamiento. Requiere ~5 GB RAM.",
        "size_mb": 4800,
        "backend": "llama.cpp",
        "filename": "itzel-7b-q4_k_m.gguf",
        "recommended": False,
    },
    {
        "id": "ollama/llama3.1",
        "name": "Llama 3.1 (Ollama)",
        "description": "Meta Llama 3.1 via Ollama. Requiere Ollama instalado.",
        "size_mb": 0,
        "backend": "ollama",
        "filename": None,
        "recommended": False,
    },
]


# ── Schemas ────────────────────────────────────────────────────────────────

class ModelInfo(BaseModel):
    id: str
    name: str
    description: str
    size_mb: int
    backend: str
    loaded: bool
    active: bool
    downloaded: bool


class ModelSwitchRequest(BaseModel):
    model_id: str


class ModelSwitchResponse(BaseModel):
    ok: bool
    model_id: str
    message: str


# ── Helpers ────────────────────────────────────────────────────────────────

def _is_downloaded(filename: Optional[str]) -> bool:
    if not filename:
        return False
    return (_MODELS_DIR / filename).exists()


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/", response_model=list[ModelInfo])
async def list_models() -> list[ModelInfo]:
    """Lista todos los modelos conocidos con su estado actual."""
    active_id = config.model.active
    return [
        ModelInfo(
            id=m["id"],
            name=m["name"],
            description=m["description"],
            size_mb=m["size_mb"],
            backend=m["backend"],
            loaded=False,                          # TODO(v2): leer de engine state
            active=(m["id"] == active_id),
            downloaded=_is_downloaded(m["filename"]),
        )
        for m in _KNOWN_MODELS
    ]


@router.post("/switch", response_model=ModelSwitchResponse)
async def switch_model(req: ModelSwitchRequest) -> ModelSwitchResponse:
    """Cambia el modelo activo en caliente (persiste en config)."""
    known_ids = {m["id"] for m in _KNOWN_MODELS}
    if req.model_id not in known_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Modelo '{req.model_id}' no reconocido. Modelos disponibles: {sorted(known_ids)}",
        )

    # Actualizar config en memoria
    config.model.active = req.model_id

    log_api.info(
        "Modelo cambiado a %s", req.model_id,
        extra={"component": "models", "model": req.model_id},
    )

    # Notificar a todos los clientes WS del cambio
    await ws_manager.broadcast(
        WsMessage.status("idle", model=req.model_id)
    )

    return ModelSwitchResponse(
        ok=True,
        model_id=req.model_id,
        message=f"Modelo activo cambiado a '{req.model_id}'. El cambio real de pesos ocurre en v2.",
    )
