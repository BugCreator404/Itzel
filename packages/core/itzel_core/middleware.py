"""Middleware de Itzel: logging estructurado + timeout de agentes.

Orden de ejecución (primero registrado = más externo):
  1. RequestLoggerMiddleware  → loguea toda request/response
  2. AgentTimeoutMiddleware   → cancela requests > 120 s
"""

from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from .logger import log_api

_AGENT_TIMEOUT_S = 120.0   # máximo global por request


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """Loguea método, path, status y latencia de cada request en JSON."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = str(uuid.uuid4())[:8]
        t0 = time.monotonic()

        # WS handshakes no tienen response body — solo loguear entrada
        if request.url.path == "/ws":
            return await call_next(request)

        response = await call_next(request)

        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        log_api.info(
            "%s %s → %d",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "req_id":     req_id,
                "method":     request.method,
                "path":       request.url.path,
                "status":     response.status_code,
                "latency_ms": latency_ms,
                "component":  "http",
            },
        )
        # Inyectar req_id en la respuesta para facilitar debugging
        response.headers["X-Request-Id"] = req_id
        return response


class AgentTimeoutMiddleware(BaseHTTPMiddleware):
    """Cancela cualquier request que supere AGENT_TIMEOUT_S segundos.

    Devuelve 504 Gateway Timeout con mensaje en ES-MX.
    El SSE de /chat tiene su propio manejo de CancelledError,
    así que el cliente recibe [ERROR:...] antes del 504.
    """

    def __init__(self, app: ASGIApp, timeout_s: float = _AGENT_TIMEOUT_S) -> None:
        super().__init__(app)
        self._timeout = timeout_s

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await asyncio.wait_for(
                call_next(request),
                timeout=self._timeout,
            )
        except TimeoutError:
            log_api.warning(
                "Timeout en %s después de %.0f s",
                request.url.path,
                self._timeout,
                extra={"path": request.url.path, "component": "timeout"},
            )
            return Response(
                content='{"detail":"La operación tardó demasiado. Intenta con un mensaje más corto."}',
                status_code=504,
                media_type="application/json",
            )
