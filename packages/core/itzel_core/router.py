"""
Router de modelos para Itzel.

Responsabilidades:
  1. select_model()       — elige el ModelBackend según tarea/config (sin cambios)
  2. build_system_prompt() — carga el prompt correcto y sustituye placeholders
  3. build_messages()     — ensambla [system, ...history, user] para el adaptador
  4. get_adapter()        — re-exportado desde models para comodidad

El endpoint de chat solo necesita importar este módulo.
"""

from __future__ import annotations

import locale
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from .config import config
from .models import get_adapter, reset_adapter
from .models.base import BaseAdapter, GenerationParams, Message

# ─── enums (sin cambios — backward compat) ───────────────────────────────────

class TaskType(str, Enum):
    CHAT      = "chat"
    CODE      = "code"
    SUMMARIZE = "summarize"
    RESEARCH  = "research"
    SYSTEM    = "system"
    VOICE     = "voice"


class ModelBackend(str, Enum):
    ITZEL_1B  = "itzel-1b"
    ITZEL_7B  = "itzel-7b"
    OLLAMA    = "ollama"
    LM_STUDIO = "lm_studio"
    OPENAI    = "openai"       # nube — requiere permiso explícito
    ANTHROPIC = "anthropic"   # nube — requiere permiso explícito


TASK_MODEL_MAP: dict[TaskType, ModelBackend] = {
    TaskType.CHAT:      ModelBackend.ITZEL_1B,
    TaskType.VOICE:     ModelBackend.ITZEL_1B,
    TaskType.SUMMARIZE: ModelBackend.ITZEL_1B,
    TaskType.CODE:      ModelBackend.ITZEL_7B,
    TaskType.RESEARCH:  ModelBackend.ITZEL_7B,
    TaskType.SYSTEM:    ModelBackend.ITZEL_1B,
}


def select_model(task: TaskType, override: Optional[str] = None) -> ModelBackend:
    """
    Devuelve el backend preferido para una tarea.
    Siempre prefiere modelos locales; nube solo con config explícita.
    """
    if override:
        try:
            return ModelBackend(override)
        except ValueError:
            pass

    try:
        return ModelBackend(config.model.active)
    except ValueError:
        pass

    return TASK_MODEL_MAP.get(task, ModelBackend.ITZEL_1B)


# ─── system prompt ────────────────────────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Idiomas soportados; el primero es el fallback
_SUPPORTED_LANGS = ("es-MX", "en-US")

# Nombres de mes en ES-MX para el formato de fecha localizado
_MESES_ES = (
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
_DIAS_ES = (
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo",
)


def build_system_prompt(
    *,
    session_id: str,
    model_name: str,
    language:   str = "es-MX",
) -> str:
    """
    Carga el prompt del idioma solicitado y sustituye los placeholders.

    Args:
        session_id: UUID de la sesión activa.
        model_name: Nombre del adaptador activo (ej. "ollama/llama3.1:8b").
        language:   Código de idioma BCP-47 (ej. "es-MX", "en-US").

    Returns:
        String con el system prompt listo para enviarse al modelo.
    """
    lang  = _normalize_lang(language)
    path  = _PROMPTS_DIR / f"system_{lang}.txt"
    text  = path.read_text(encoding="utf-8")

    now   = datetime.now(timezone.utc)
    date_str = _format_date(now, lang)

    lang_label = "Español (México)" if lang == "es-MX" else "English (US)"

    return text.format(
        model      = model_name,
        date       = date_str,
        language   = lang_label,
        session_id = session_id,
    )


def build_messages(
    *,
    system_prompt: str,
    history:       list[Message],
    new_message:   str,
) -> list[Message]:
    """
    Ensambla la lista de mensajes para el adaptador:
        [system, msg1, msg2, ..., new_user_msg]

    El historial ya viene recortado desde memory.get_session().
    """
    messages: list[Message] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": new_message})
    return messages


# ─── helpers privados ─────────────────────────────────────────────────────────

def _normalize_lang(language: str) -> str:
    """Normaliza el idioma al más cercano soportado; fallback es-MX."""
    lang = language.strip()
    if lang in _SUPPORTED_LANGS:
        return lang
    # "es", "es_MX", "español" → "es-MX"
    if lang.startswith("es"):
        return "es-MX"
    # "en", "en_US", "english" → "en-US"
    if lang.startswith("en"):
        return "en-US"
    return "es-MX"


def _format_date(dt: datetime, lang: str) -> str:
    """Formatea la fecha de forma localizada sin depender de locale del SO."""
    if lang == "es-MX":
        dia_semana = _DIAS_ES[dt.weekday()]
        mes        = _MESES_ES[dt.month]
        return f"{dia_semana}, {dt.day} de {mes} de {dt.year}"
    # en-US — %-d no funciona en Windows; usar lstrip("0") para quitar cero inicial
    return dt.strftime("%A, %B %d, %Y").replace(" 0", " ")


# ─── re-exports convenientes ──────────────────────────────────────────────────

__all__ = [
    "TaskType",
    "ModelBackend",
    "TASK_MODEL_MAP",
    "select_model",
    "build_system_prompt",
    "build_messages",
    "get_adapter",
    "reset_adapter",
    "BaseAdapter",
    "GenerationParams",
    "Message",
]
