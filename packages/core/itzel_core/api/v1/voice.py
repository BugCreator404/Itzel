"""
Endpoint WebSocket /api/v1/voice/ws — pipeline de voz en tiempo real.

El cliente envía audio PCM float32 mono 16 kHz como frames binarios.
El servidor devuelve transcripciones y eventos de estado como JSON.

Protocolo cliente → servidor:
  binary frame : bytes de audio PCM float32 mono 16 kHz
                 (chunks de cualquier tamaño; el VAD los procesa a 512 muestras)
  JSON text    :
    {"type": "config",    "mode": "always|hotkey",  "language": "es"}
    {"type": "set_tts",   "enabled": true}           activa TTS Kokoro (carga async)
    {"type": "set_tts",   "enabled": false}           desactiva TTS
    {"type": "ptt_start"}
    {"type": "ptt_stop"}
    {"type": "force_end"}
    {"type": "ping",      "id": "<optional>"}
    {"type": "stop"}

Protocolo servidor → cliente (JSON text):
  {"type": "ready",           "session_id": "...",                        "ts": 1234.5}
  {"type": "transcript",      "text": "...",  "language": "es",           "ts": 1234.5}
  {"type": "listening_start",                                              "ts": 1234.5}
  {"type": "listening_end",                                                "ts": 1234.5}
  {"type": "state",           "state": "idle|listening|processing|speaking","ts": 1234.5}
  {"type": "level",           "rms": 0.31,                                "ts": 1234.5}
  {"type": "speaking_start",                                               "ts": 1234.5}
  {"type": "speaking_end",                                                 "ts": 1234.5}
  {"type": "speaking_level",  "rms": 0.45,                                "ts": 1234.5}
  {"type": "interrupted",                                                  "ts": 1234.5}
  {"type": "tts_ready",                                                    "ts": 1234.5}
  {"type": "error",           "detail": "...",                            "ts": 1234.5}
  {"type": "pong",            "id": "<echo>",                             "ts": 1234.5}
  {"type": "configured",                                                   "ts": 1234.5}
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from uuid import uuid4

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("itzel.voice.ws")

router = APIRouter(prefix="/voice", tags=["voice"])

# Nivel de audio: enviar máximo una vez cada 100 ms para no saturar el WS
_LEVEL_THROTTLE_S: float = 0.1


# ─── helpers ──────────────────────────────────────────────────────────────────

def _ts() -> float:
    """Timestamp Unix con 3 decimales."""
    return round(time.time(), 3)


def _make_sender(ws: WebSocket, loop: asyncio.AbstractEventLoop):
    """
    Devuelve una función thread-safe que envía un dict JSON por el WebSocket.
    Se llama desde los callbacks del pipeline (hilos worker).
    """
    async def _async_send(data: dict) -> None:
        try:
            await ws.send_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass  # WebSocket ya cerrado — ignorar silenciosamente

    def _send(data: dict) -> None:
        asyncio.run_coroutine_threadsafe(_async_send(data), loop)

    return _send


# ─── control de la sesión ──────────────────────────────────────────────────────

async def _handle_control(
    msg:        dict,
    pipeline,
    ws:         WebSocket,
    session_id: str,
    send,
) -> None:
    """Procesa un mensaje de control JSON enviado por el cliente."""
    msg_type = msg.get("type", "")

    if msg_type == "ping":
        await ws.send_text(json.dumps({
            "type": "pong",
            "id":   msg.get("id"),
            "ts":   _ts(),
        }))

    elif msg_type == "ptt_start":
        pipeline.push_to_talk_start()

    elif msg_type == "ptt_stop":
        pipeline.push_to_talk_stop()

    elif msg_type == "force_end":
        # Útil para tests E2E y modos personalizados
        if pipeline.is_running:
            pipeline.vad.force_end()

    elif msg_type == "config":
        # Reconfigurar en caliente: solo cambia modo si el pipeline no está activo
        try:
            from itzel_voice.pipeline import PipelineConfig
            changes = {}
            if "mode" in msg:
                changes["mode"] = msg["mode"]
            if changes:
                pipeline.config = PipelineConfig(
                    **{**pipeline.config.__dict__, **changes}
                )
            await ws.send_text(json.dumps({
                "type": "configured",
                "mode": pipeline.config.mode,
                "ts":   _ts(),
            }))
        except Exception as exc:
            await ws.send_text(json.dumps({
                "type":   "error",
                "detail": f"Configuración inválida: {exc}",
                "ts":     _ts(),
            }))

    elif msg_type == "set_tts":
        enabled = msg.get("enabled", True)
        if not enabled:
            if pipeline.tts is not None:
                pipeline.tts.stop()
                pipeline.tts = None
            await ws.send_text(json.dumps({
                "type": "configured", "tts": False, "ts": _ts(),
            }))
        else:
            # Cargar TTS en background — puede tardar ~2s la primera vez
            def _load_tts() -> None:
                try:
                    from itzel_voice.tts import TextToSpeech, TTSConfig
                    tts = TextToSpeech(TTSConfig())
                    tts.load()
                    pipeline.tts = tts
                    pipeline._setup_tts_callbacks()
                    send({"type": "tts_ready", "ts": _ts()})
                except Exception as exc:
                    send({
                        "type":   "error",
                        "detail": f"Error al cargar TTS: {exc}",
                        "ts":     _ts(),
                    })

            threading.Thread(target=_load_tts, daemon=True).start()
            await ws.send_text(json.dumps({
                "type": "configured", "tts": "loading", "ts": _ts(),
            }))

    elif msg_type == "stop":
        if pipeline.is_running:
            pipeline.stop()

    else:
        log.debug("Mensaje de control no reconocido: %s", msg_type)


# ─── endpoint WebSocket ────────────────────────────────────────────────────────

@router.websocket("/ws")
async def voice_ws(ws: WebSocket) -> None:
    """
    WebSocket de voz por sesión.

    Cada conexión tiene su propio VoicePipeline: VAD + Whisper aislados.
    El pipeline se detiene al desconectarse el cliente.
    """
    await ws.accept()
    session_id = str(uuid4())
    loop       = asyncio.get_running_loop()
    pipeline   = None

    try:
        # Importación diferida: itzel-voice es una dependencia opcional del core
        try:
            from itzel_voice.pipeline import PipelineConfig, VoicePipeline
        except ImportError:
            await ws.send_text(json.dumps({
                "type":   "error",
                "detail": (
                    "El paquete itzel-voice no está instalado. "
                    "Ejecuta: pip install -e packages/voice[full]"
                ),
                "ts": _ts(),
            }))
            await ws.close(code=1011)
            return

        # Preparar sender thread-safe para los callbacks del pipeline
        _send = _make_sender(ws, loop)

        # Throttle para eventos de nivel de micrófono y TTS
        _level_ts:          list[float] = [0.0]
        _speaking_level_ts: list[float] = [0.0]

        def _on_level(rms: float) -> None:
            now = time.monotonic()
            if now - _level_ts[0] >= _LEVEL_THROTTLE_S:
                _level_ts[0] = now
                _send({"type": "level", "rms": round(rms, 3), "ts": _ts()})

        def _on_speaking_level(rms: float) -> None:
            now = time.monotonic()
            if now - _speaking_level_ts[0] >= _LEVEL_THROTTLE_S:
                _speaking_level_ts[0] = now
                _send({"type": "speaking_level", "rms": round(rms, 3), "ts": _ts()})

        # Crear y configurar pipeline
        pipeline = VoicePipeline(PipelineConfig(mode="always"))

        pipeline.on_transcript      = lambda text, lang: _send({
            "type": "transcript", "text": text, "language": lang, "ts": _ts(),
        })
        pipeline.on_listening_start = lambda: _send({
            "type": "listening_start", "ts": _ts(),
        })
        pipeline.on_listening_end   = lambda: _send({
            "type": "listening_end", "ts": _ts(),
        })
        pipeline.on_state_change    = lambda s: _send({
            "type": "state", "state": s, "ts": _ts(),
        })
        pipeline.on_error           = lambda exc: _send({
            "type": "error", "detail": str(exc), "ts": _ts(),
        })
        pipeline.on_level           = _on_level

        # Callbacks TTS (se activan cuando pipeline.tts está asignado)
        pipeline.on_speaking_start  = lambda: _send({
            "type": "speaking_start", "ts": _ts(),
        })
        pipeline.on_speaking_end    = lambda: _send({
            "type": "speaking_end", "ts": _ts(),
        })
        pipeline.on_speaking_level  = _on_speaking_level
        pipeline.on_interrupted     = lambda: _send({
            "type": "interrupted", "ts": _ts(),
        })

        # Arrancar sin micrófono: el audio llega como bytes del cliente
        pipeline.start(use_mic=False)

        await ws.send_text(json.dumps({
            "type":       "ready",
            "session_id": session_id,
            "ts":         _ts(),
        }))

        log.info("Sesión de voz iniciada: %s", session_id)

        # ── Bucle principal: recibir frames ───────────────────────────
        while True:
            try:
                frame = await ws.receive()
            except WebSocketDisconnect:
                break

            frame_type = frame.get("type", "")
            if frame_type == "websocket.disconnect":
                break

            raw_bytes = frame.get("bytes")
            raw_text  = frame.get("text")

            if raw_bytes:
                # Frame binario: PCM float32 mono 16 kHz desde el cliente
                try:
                    chunk = np.frombuffer(raw_bytes, dtype=np.float32)
                    if chunk.size > 0 and pipeline.is_running:
                        pipeline._audio_queue.put_nowait(chunk)
                except Exception as exc:
                    log.warning("Frame de audio inválido en sesión %s: %s", session_id, exc)

            elif raw_text:
                try:
                    msg = json.loads(raw_text)
                    await _handle_control(msg, pipeline, ws, session_id, _send)
                except json.JSONDecodeError:
                    log.warning(
                        "Texto no-JSON en sesión %s: %.80s", session_id, raw_text
                    )

    except WebSocketDisconnect:
        log.info("Cliente de voz desconectado normalmente: %s", session_id)

    except Exception as exc:
        log.error("Error inesperado en sesión de voz %s: %s", session_id, exc)

    finally:
        if pipeline is not None and pipeline.is_running:
            pipeline.stop()
        log.info("Sesión de voz cerrada: %s", session_id)
