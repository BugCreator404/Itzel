"""Selecciona el modelo apropiado según la tarea y la configuración activa."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .config import config


class TaskType(str, Enum):
    CHAT = "chat"
    CODE = "code"
    SUMMARIZE = "summarize"
    RESEARCH = "research"
    SYSTEM = "system"
    VOICE = "voice"


class ModelBackend(str, Enum):
    ITZEL_1B = "itzel-1b"
    ITZEL_7B = "itzel-7b"
    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    OPENAI = "openai"          # nube — requiere permiso explícito
    ANTHROPIC = "anthropic"    # nube — requiere permiso explícito


TASK_MODEL_MAP: dict[TaskType, ModelBackend] = {
    TaskType.CHAT: ModelBackend.ITZEL_1B,
    TaskType.VOICE: ModelBackend.ITZEL_1B,
    TaskType.SUMMARIZE: ModelBackend.ITZEL_1B,
    TaskType.CODE: ModelBackend.ITZEL_7B,
    TaskType.RESEARCH: ModelBackend.ITZEL_7B,
    TaskType.SYSTEM: ModelBackend.ITZEL_1B,
}


def select_model(task: TaskType, override: Optional[str] = None) -> ModelBackend:
    """
    Devuelve el backend a usar.
    Siempre prefiere modelos locales; solo usa nube si el usuario configura
    explícitamente un backend externo.
    """
    if override:
        try:
            return ModelBackend(override)
        except ValueError:
            pass

    active = config.model.active
    try:
        return ModelBackend(active)
    except ValueError:
        pass

    return TASK_MODEL_MAP.get(task, ModelBackend.ITZEL_1B)
