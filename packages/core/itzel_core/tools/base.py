"""
Sistema de herramientas (tools) para los agentes de Itzel.

Una "tool" es una función Python que el modelo Itzel-1B puede invocar
(tool calling). El decorador @tool envuelve la función, infiere su esquema
de parámetros desde la firma y la registra para que el LLM la descubra.

Flujo de invocación (ToolRegistry.invoke):
    1. ¿La tool es irreversible?  →  pedir confirmación al usuario.
    2. Ejecutar con límite de tiempo (timeout).
    3. Registrar la acción en el log de auditoría.
    4. Devolver un ToolResult uniforme (ok / value / error).

Principios de seguridad (no negociables):
    - Confirmar antes de acciones irreversibles (delete, overwrite…).
    - Fail-safe: sin confirm_handler, las irreversibles SE RECHAZAN.
    - Cada acción se loggea localmente; nada sale de la máquina.

English summary:
    @tool turns a plain Python function into an LLM-callable tool with an
    auto-generated JSON schema (Anthropic / OpenAI tool-use compatible).
    ToolRegistry.invoke handles confirmation, timeout and audit logging.
"""

from __future__ import annotations

import inspect
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, get_args, get_origin, get_type_hints

# ─── mapeo de tipos Python → JSON Schema ──────────────────────────────────────

_JSON_TYPES: dict[type, str] = {
    str:   "string",
    int:   "integer",
    float: "number",
    bool:  "boolean",
    list:  "array",
    dict:  "object",
}


def _python_type_to_json(py_type: Any) -> str:
    """
    Traduce una anotación de tipo Python a un tipo de JSON Schema.
    Tipos desconocidos caen a "string" (el más permisivo para el LLM).
    """
    # Optional[X] / X | None → desempaquetar el primer tipo no-None
    origin = get_origin(py_type)
    if origin is not None:
        args = [a for a in get_args(py_type) if a is not type(None)]
        if args:
            return _python_type_to_json(args[0])
    return _JSON_TYPES.get(py_type, "string")


# ─── metadatos y resultado ────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolParam:
    """Un parámetro de una tool, ya normalizado para el esquema JSON."""
    name:        str
    json_type:   str
    description: str = ""
    required:    bool = True
    default:     Any = None


@dataclass
class ActionRequest:
    """
    Descripción legible de una acción a punto de ejecutarse.
    Se pasa al confirm_handler y al logger de auditoría.
    """
    tool_name:     str
    summary:       str               # texto humano: "Eliminar archivo X"
    args:          dict[str, Any]
    irreversible:  bool = False
    sandboxed:     bool = False
    agent:         str = ""          # agente que originó la acción (para auditoría)


@dataclass
class ToolResult:
    """
    Resultado uniforme de ejecutar cualquier tool.

    Attributes:
        ok:     True si la tool terminó correctamente.
        value:  Valor de retorno de la tool (None si hubo error).
        error:  Mensaje de error legible (None si ok).
        denied: True si la acción se rechazó por falta de confirmación.
    """
    ok:     bool
    value:  Any = None
    error:  Optional[str] = None
    denied: bool = False

    @classmethod
    def success(cls, value: Any = None) -> "ToolResult":
        return cls(ok=True, value=value)

    @classmethod
    def failure(cls, error: str) -> "ToolResult":
        return cls(ok=False, error=error)

    @classmethod
    def rejected(cls, error: str = "Acción no confirmada por el usuario.") -> "ToolResult":
        return cls(ok=False, error=error, denied=True)


# ─── excepciones del dominio ──────────────────────────────────────────────────

class ToolError(RuntimeError):
    """Error base de cualquier tool."""


class ConfirmationDenied(ToolError):
    """La acción irreversible no fue confirmada por el usuario."""


class SandboxViolation(ToolError):
    """La ejecución violó los límites del sandbox (RAM, red, tiempo…)."""


# ─── la tool ──────────────────────────────────────────────────────────────────

class Tool:
    """
    Envuelve una función Python como herramienta invocable por el LLM.

    No se instancia directamente — se construye vía el decorador @tool.
    """

    def __init__(
        self,
        func:                   Callable[..., Any],
        name:                   str,
        description:            str,
        *,
        confirm_if_irreversible: bool = False,
        confirm_when:            Optional[Callable[[dict[str, Any]], bool]] = None,
        sandbox:                 bool = False,
        timeout:                 int = 30,
        param_docs:              Optional[dict[str, str]] = None,
    ) -> None:
        self.func                    = func
        self.name                    = name
        self.description             = description.strip()
        self.confirm_if_irreversible = confirm_if_irreversible
        # Predicado opcional de confirmación dinámica sobre los args
        # (p. ej. confirmar move_file solo si el destino ya existe).
        self.confirm_when            = confirm_when
        self.sandbox                 = sandbox
        self.timeout                 = timeout
        self._param_docs             = param_docs or {}
        self.params                  = self._infer_params()

    # ── confirmación ──────────────────────────────────────────────────────────

    def needs_confirmation(self, args: dict[str, Any]) -> bool:
        """
        Decide si esta invocación requiere confirmación del usuario.

        - confirm_if_irreversible=True  → siempre confirma.
        - confirm_when(args)=True        → confirma condicionalmente.
        - Si confirm_when lanza error    → confirma (fail-safe).
        """
        if self.confirm_if_irreversible:
            return True
        if self.confirm_when is not None:
            try:
                return bool(self.confirm_when(args))
            except Exception:
                return True  # ante la duda, confirmar
        return False

    # ── introspección de parámetros ───────────────────────────────────────────

    def _infer_params(self) -> list[ToolParam]:
        """Infiere los parámetros desde la firma + type hints de la función."""
        sig = inspect.signature(self.func)
        try:
            hints = get_type_hints(self.func)
        except Exception:
            hints = {}

        params: list[ToolParam] = []
        for pname, p in sig.parameters.items():
            # Ignorar *args, **kwargs y el self/cls de métodos
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            if pname in ("self", "cls"):
                continue

            py_type   = hints.get(pname, str)
            has_default = p.default is not inspect.Parameter.empty
            params.append(ToolParam(
                name        = pname,
                json_type   = _python_type_to_json(py_type),
                description = self._param_docs.get(pname, ""),
                required    = not has_default,
                default     = None if not has_default else p.default,
            ))
        return params

    # ── esquema para tool-calling ─────────────────────────────────────────────

    def to_schema(self) -> dict[str, Any]:
        """
        Genera el esquema JSON de la tool — compatible con el formato
        de tool-use de Anthropic / OpenAI.

        {
          "name": "read_file",
          "description": "...",
          "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "..."}},
            "required": ["path"]
          }
        }
        """
        properties: dict[str, Any] = {}
        required:   list[str]      = []
        for p in self.params:
            prop: dict[str, Any] = {"type": p.json_type}
            if p.description:
                prop["description"] = p.description
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        return {
            "name":        self.name,
            "description": self.description,
            "input_schema": {
                "type":       "object",
                "properties": properties,
                "required":   required,
            },
        }

    # ── ejecución directa (sin orquestación) ──────────────────────────────────

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Permite llamar la tool como una función normal (uso en Python)."""
        return self.func(*args, **kwargs)

    def __repr__(self) -> str:
        flags = []
        if self.confirm_if_irreversible:
            flags.append("confirm")
        if self.sandbox:
            flags.append("sandbox")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        return f"<Tool {self.name}(timeout={self.timeout}s){suffix}>"


# ─── decorador @tool ──────────────────────────────────────────────────────────

def tool(
    name:                    Optional[str] = None,
    description:             str = "",
    *,
    confirm_if_irreversible: bool = False,
    confirm_when:            Optional[Callable[[dict[str, Any]], bool]] = None,
    sandbox:                 bool = False,
    timeout:                 int = 30,
    param_docs:              Optional[dict[str, str]] = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """
    Convierte una función en una Tool invocable por el LLM.

    Uso:
        @tool(
            "read_file",
            "Lee el contenido de un archivo de texto.",
            param_docs={"path": "Ruta absoluta del archivo a leer."},
        )
        def read_file(path: str) -> str:
            return Path(path).read_text(encoding="utf-8")

    Args:
        name:        Nombre de la tool (default: nombre de la función).
        description: Qué hace la tool — lo lee el LLM para decidir usarla.
        confirm_if_irreversible: Si True, pide confirmación antes de ejecutar.
        sandbox:     Si True, marca la tool como ejecutable solo en sandbox.
        timeout:     Tiempo máximo de ejecución en segundos.
        param_docs:  Descripciones por parámetro para el esquema JSON.
    """
    def _decorate(func: Callable[..., Any]) -> Tool:
        return Tool(
            func,
            name        = name or func.__name__,
            description = description or (func.__doc__ or "").strip(),
            confirm_if_irreversible = confirm_if_irreversible,
            confirm_when = confirm_when,
            sandbox     = sandbox,
            timeout     = timeout,
            param_docs  = param_docs,
        )
    return _decorate


# ─── registro de tools ────────────────────────────────────────────────────────

# Tipo del callback de confirmación: recibe la acción, devuelve True para permitir.
ConfirmHandler = Callable[[ActionRequest], bool]

# Tipo del callback de logging de acciones.
ActionSink = Callable[["ActionRequest", "ToolResult"], None]


class ToolRegistry:
    """
    Catálogo de tools disponibles + orquestador de su ejecución.

    Un agente registra sus tools aquí; el endpoint de chat expone
    `all_schemas()` al LLM, y cuando el modelo pide invocar una tool
    se llama `invoke()`, que aplica confirmación, timeout y logging.
    """

    def __init__(
        self,
        *,
        agent_name:      str = "",
        confirm_handler: Optional[ConfirmHandler] = None,
        action_sink:     Optional[ActionSink] = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        # Nombre del agente dueño de este registry (para auditoría).
        self.agent_name = agent_name
        # Handler de confirmación: si es None, las irreversibles se rechazan.
        self.confirm_handler = confirm_handler
        # Sink de auditoría: se llama tras cada invoke (ok o error).
        self.action_sink = action_sink
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="itzel-tool")
        self._lock = threading.Lock()

    # ── registro ──────────────────────────────────────────────────────────────

    def register(self, t: Tool) -> Tool:
        """Registra una tool. Lanza ValueError si el nombre ya existe."""
        with self._lock:
            if t.name in self._tools:
                raise ValueError(f"Ya existe una tool llamada '{t.name}'.")
            self._tools[t.name] = t
        return t

    def register_all(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def all_schemas(self) -> list[dict[str, Any]]:
        """Esquemas de todas las tools — para entregar al LLM."""
        return [t.to_schema() for t in self._tools.values()]

    # ── invocación orquestada ─────────────────────────────────────────────────

    def invoke(self, name: str, args: Optional[dict[str, Any]] = None) -> ToolResult:
        """
        Ejecuta una tool por nombre con sus argumentos.

        Aplica, en orden:
          1. Confirmación si la tool es irreversible (fail-safe).
          2. Ejecución con timeout.
          3. Logging de auditoría vía action_sink.

        Nunca lanza excepción: todo error se devuelve como ToolResult.failure.
        """
        args = args or {}
        t = self._tools.get(name)
        if t is None:
            return ToolResult.failure(f"Tool desconocida: '{name}'.")

        needs_confirm = t.needs_confirmation(args)
        request = ActionRequest(
            tool_name    = t.name,
            summary      = self._summarize(t, args),
            args         = args,
            irreversible = needs_confirm,
            sandboxed    = t.sandbox,
            agent        = self.agent_name,
        )

        # 1. Confirmación de acciones irreversibles (estática o condicional)
        if needs_confirm:
            allowed = self._ask_confirmation(request)
            if not allowed:
                result = ToolResult.rejected(
                    f"Acción '{t.name}' no confirmada — cancelada por seguridad."
                )
                self._emit(request, result)
                return result

        # 2. Ejecución con timeout
        result = self._run_with_timeout(t, args)

        # 3. Auditoría
        self._emit(request, result)
        return result

    # ── internals ─────────────────────────────────────────────────────────────

    def _ask_confirmation(self, request: ActionRequest) -> bool:
        """
        Pide confirmación al usuario vía el handler inyectado.
        Sin handler → False (fail-safe: rechaza la acción irreversible).
        """
        if self.confirm_handler is None:
            return False
        try:
            return bool(self.confirm_handler(request))
        except Exception:
            # Cualquier fallo en el handler se trata como denegación.
            return False

    def _run_with_timeout(self, t: Tool, args: dict[str, Any]) -> ToolResult:
        """Ejecuta la función de la tool en un worker thread con timeout."""
        future = self._executor.submit(t.func, **args)
        try:
            value = future.result(timeout=t.timeout)
            return ToolResult.success(value)
        except FutureTimeout:
            future.cancel()
            return ToolResult.failure(
                f"La tool '{t.name}' excedió el límite de {t.timeout}s."
            )
        except TypeError as exc:
            # Argumentos inválidos (faltantes o de más)
            return ToolResult.failure(f"Argumentos inválidos para '{t.name}': {exc}")
        except Exception as exc:
            return ToolResult.failure(f"Error en '{t.name}': {exc}")

    def _emit(self, request: ActionRequest, result: ToolResult) -> None:
        """Envía la acción al sink de auditoría, si hay uno."""
        if self.action_sink is None:
            return
        try:
            self.action_sink(request, result)
        except Exception:
            pass  # el logging nunca debe romper la ejecución

    @staticmethod
    def _summarize(t: Tool, args: dict[str, Any]) -> str:
        """Genera un resumen humano de la acción para confirmación/log."""
        if not args:
            return t.name
        parts = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"{t.name}({parts})"

    def shutdown(self) -> None:
        """Cierra el executor de threads (llamar al apagar el servidor)."""
        self._executor.shutdown(wait=False)
