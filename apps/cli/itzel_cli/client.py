"""
Cliente HTTP síncrono para el backend de Itzel.

Todos los comandos del CLI usan esta clase como única frontera con el
backend FastAPI. Ventajas:
  - Un solo lugar para manejar errores de conexión.
  - Typer es síncrono → httpx síncrono, sin event loops en CLI.
  - SSE streaming con httpx.Client.stream() nativo.

Uso rápido:
    client = ItzelClient()
    if not client.is_alive():
        sys.exit("Backend offline — ejecuta: itzel setup")

    for token in client.stream_chat("Hola Itzel"):
        print(token, end="", flush=True)
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

# ─── constantes ───────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL    = "http://127.0.0.1:7432"
_CONNECT_TIMEOUT_S   = 3.0     # tiempo máximo para establecer conexión
_READ_TIMEOUT_S      = 90.0    # tiempo máximo entre tokens en stream
_HEALTH_TIMEOUT_S    = 2.0     # timeout rápido para health check

_SESSION_FILE = Path.home() / ".itzel" / "cli_session.json"


# ─── dataclasses de respuesta ─────────────────────────────────────────────────

@dataclass
class ModelInfo:
    id:         str
    name:       str
    backend:    str
    loaded:     bool
    active:     bool
    downloaded: bool
    size_mb:    int = 0
    description:str = ""


@dataclass
class BackendStatus:
    alive:       bool
    version:     str         = "?"
    model:       str         = "none"
    model_loaded:bool        = False
    uptime_s:    float       = 0.0
    extra:       dict        = field(default_factory=dict)


@dataclass
class MemoryEntry:
    id:         str
    role:       str
    content:    str
    session_id: str
    created_at: str


# ─── excepciones ──────────────────────────────────────────────────────────────

class BackendOfflineError(RuntimeError):
    """El backend no está disponible en este momento."""
    def __init__(self, url: str) -> None:
        super().__init__(
            f"No se puede conectar al backend en {url}.\n"
            "  → Inicia el backend con: itzel setup\n"
            "  → O manualmente:         uvicorn itzel_core.engine:app --port 7432"
        )
        self.url = url


class BackendError(RuntimeError):
    """El backend respondió con un error HTTP."""
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Error del backend [{status_code}]: {detail}")
        self.status_code = status_code
        self.detail      = detail


# ─── cliente principal ────────────────────────────────────────────────────────

class ItzelClient:
    """
    Cliente HTTP síncrono para el backend de Itzel (FastAPI en localhost).

    No lanza excepciones de red en métodos `try_*` — devuelven None/False.
    Los métodos normales lanzan BackendOfflineError / BackendError.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (
            base_url
            or os.getenv("ITZEL_BACKEND_URL")
            or _DEFAULT_BASE_URL
        ).rstrip("/")

        self._timeout = httpx.Timeout(
            connect = _CONNECT_TIMEOUT_S,
            read    = _READ_TIMEOUT_S,
            write   = 10.0,
            pool    = 5.0,
        )

    # ── health ────────────────────────────────────────────────────────────────

    def is_alive(self) -> bool:
        """Devuelve True si el backend responde. Nunca lanza excepciones."""
        try:
            with httpx.Client(timeout=_HEALTH_TIMEOUT_S) as c:
                r = c.get(f"{self.base_url}/api/v1/health/")
                return r.status_code == 200
        except Exception:
            return False

    def require_alive(self) -> None:
        """Lanza BackendOfflineError si el backend no responde."""
        if not self.is_alive():
            raise BackendOfflineError(self.base_url)

    # ── status ────────────────────────────────────────────────────────────────

    def get_status(self) -> BackendStatus:
        """Obtiene el estado completo del backend."""
        self.require_alive()
        try:
            with httpx.Client(timeout=self._timeout) as c:
                r = c.get(f"{self.base_url}/api/v1/status/")
                r.raise_for_status()
                data = r.json()
                return BackendStatus(
                    alive        = True,
                    version      = data.get("version", "?"),
                    model        = data.get("model", "none"),
                    model_loaded = data.get("model_loaded", False),
                    uptime_s     = data.get("uptime_s", 0.0),
                    extra        = data,
                )
        except httpx.HTTPStatusError as exc:
            raise BackendError(exc.response.status_code, exc.response.text) from exc

    # ── chat / SSE ────────────────────────────────────────────────────────────

    def stream_chat(
        self,
        message:    str,
        session_id: str | None = None,
        language:   str | None = None,
    ) -> Iterator[str]:
        """
        Envía un mensaje al endpoint /api/v1/chat/ y devuelve un iterador
        de tokens SSE conforme llegan.

        Yields:
            Tokens de texto. Termina cuando recibe [DONE] o [ERROR].

        Raises:
            BackendOfflineError: si el backend no está disponible.
            BackendError:        si el backend responde con código >= 400.
        """
        self.require_alive()

        payload: dict[str, Any] = {
            "message":    message,
            "session_id": session_id,
            "language":   language,
            "stream":     True,
        }

        try:
            with httpx.Client(timeout=self._timeout) as c:
                with c.stream(
                    "POST",
                    f"{self.base_url}/api/v1/chat/",
                    json=payload,
                ) as resp:
                    if resp.status_code >= 400:
                        body = resp.read().decode()
                        raise BackendError(resp.status_code, body)

                    for raw_line in resp.iter_lines():
                        if not raw_line.startswith("data: "):
                            continue
                        data = raw_line[6:]
                        if data in ("[DONE]", "[CANCELLED]"):
                            break
                        if data == "[ERROR]":
                            break
                        if data.startswith("[SOURCES]"):
                            continue   # frame de control RAG, no es texto
                        yield data

        except (BackendOfflineError, BackendError):
            raise
        except httpx.ConnectError as exc:
            raise BackendOfflineError(self.base_url) from exc
        except httpx.TimeoutException as exc:
            raise BackendError(0, f"Timeout esperando respuesta: {exc}") from exc

    def chat_sync(
        self,
        message:    str,
        session_id: str | None = None,
        language:   str | None = None,
    ) -> str:
        """
        Versión no-streaming de chat. Acumula todos los tokens y devuelve
        el string completo. Útil para --json output.
        """
        return "".join(self.stream_chat(message, session_id, language))

    # ── modelos ───────────────────────────────────────────────────────────────

    def list_models(self) -> list[ModelInfo]:
        """Lista los modelos conocidos con su estado."""
        self.require_alive()
        with httpx.Client(timeout=self._timeout) as c:
            r = c.get(f"{self.base_url}/api/v1/models/")
            r.raise_for_status()
            return [
                ModelInfo(
                    id          = m["id"],
                    name        = m["name"],
                    backend     = m["backend"],
                    loaded      = m["loaded"],
                    active      = m["active"],
                    downloaded  = m["downloaded"],
                    size_mb     = m.get("size_mb", 0),
                    description = m.get("description", ""),
                )
                for m in r.json()
            ]

    def switch_model(self, model_id: str) -> dict[str, Any]:
        """Cambia el modelo activo en el backend."""
        self.require_alive()
        with httpx.Client(timeout=self._timeout) as c:
            r = c.post(
                f"{self.base_url}/api/v1/models/switch",
                json={"model_id": model_id},
            )
            r.raise_for_status()
            return r.json()  # type: ignore[no-any-return]

    # ── memoria ───────────────────────────────────────────────────────────────

    def search_memory(self, query: str, limit: int = 20) -> list[MemoryEntry]:
        """Busca en la memoria episódica del backend."""
        # Acceso directo a SQLite (misma máquina — más rápido que HTTP)
        try:
            from itzel_core.memory import MemoryStore
            store   = MemoryStore()
            entries = store.search(query, limit=limit)
            return [
                MemoryEntry(
                    id         = e.id,
                    role       = e.role,
                    content    = e.content,
                    session_id = e.session_id,
                    created_at = e.created_at,
                )
                for e in entries
            ]
        except ImportError:
            return []

    def export_memory(self) -> list[dict]:
        """Exporta toda la memoria a una lista de dicts."""
        try:
            from itzel_core.memory import MemoryStore
            store = MemoryStore()
            rows  = store._db.query(
                "SELECT id,role,content,conversation_id,created_at,metadata "
                "FROM messages ORDER BY created_at"
            )
            return [
                {
                    "id": r[0], "role": r[1], "content": r[2],
                    "session_id": r[3], "created_at": r[4],
                    "metadata": json.loads(r[5]),
                }
                for r in rows
            ]
        except ImportError:
            return []

    def clear_memory(self) -> int:
        """Borra toda la memoria. Devuelve el número de entradas eliminadas."""
        try:
            from itzel_core.memory import MemoryStore
            store = MemoryStore()
            row = store._db.query_one("SELECT COUNT(*) FROM messages")
            store.clear()
            return int(row[0]) if row else 0
        except ImportError:
            return 0

    # ── sesión CLI ────────────────────────────────────────────────────────────

    @staticmethod
    def load_session() -> str:
        """Carga o crea el session_id de la sesión CLI activa."""
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _SESSION_FILE.exists():
            try:
                data = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
                sid  = data.get("session_id", "")
                if sid:
                    return sid
            except (json.JSONDecodeError, KeyError):
                pass
        # Crear nueva sesión
        sid = str(uuid4())
        _SESSION_FILE.write_text(
            json.dumps({"session_id": sid}, indent=2),
            encoding="utf-8",
        )
        return sid

    @staticmethod
    def new_session() -> str:
        """Fuerza una nueva sesión y la persiste."""
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        sid = str(uuid4())
        _SESSION_FILE.write_text(
            json.dumps({"session_id": sid}, indent=2),
            encoding="utf-8",
        )
        return sid
