/**
 * useWsState — conexión WebSocket con reconexión automática.
 *
 * Protocolo entrante: { type: "status|error|pong", id, payload }
 * Protocolo saliente: { type: "ping|status_request",  id, payload }
 *
 * Reconexión backoff: 1 s → 2 s → 4 s (3 intentos máximo).
 * Después de 3 fallos deja de intentar y expone `error`.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { v4 as uuidv4 } from "uuid";

// ── Tipos del protocolo ────────────────────────────────────────────────────

export type MascotState = "idle" | "work" | "think" | "happy" | "wave" | "sleep";

export interface WsStatusPayload {
  mascot: MascotState;
  model: string;
}

export interface WsErrorPayload {
  code: string;
  message: string;
}

export interface WsMessage {
  type: "status" | "error" | "pong" | "ping" | string;
  id: string;
  payload: WsStatusPayload | WsErrorPayload | Record<string, unknown>;
}

// ── Estado público del hook ────────────────────────────────────────────────

export interface WsState {
  connected: boolean;
  mascot: MascotState;
  model: string;
  error: string | null;
  /** Forzar reconexión manual */
  reconnect: () => void;
}

// ── Constantes ─────────────────────────────────────────────────────────────

const WS_URL          = "ws://localhost:7432/ws";
const MAX_RETRIES     = 3;
const PING_INTERVAL   = 30_000;   // ms
const BACKOFF_BASE_MS = 1_000;    // 1 s → 2 s → 4 s

// ── Hook ───────────────────────────────────────────────────────────────────

export function useWsState(): WsState {
  const [connected, setConnected] = useState(false);
  const [mascot,    setMascot]    = useState<MascotState>("idle");
  const [model,     setModel]     = useState("itzel-1b");
  const [error,     setError]     = useState<string | null>(null);

  const wsRef        = useRef<WebSocket | null>(null);
  const retriesRef   = useRef(0);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Ref para el reconnect estable (evita re-renders en el efecto)
  const connectRef   = useRef<() => void>(() => {});

  // ── Enviar mensaje JSON al backend ───────────────────────────────
  const send = useCallback((type: string, payload: Record<string, unknown> = {}) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type, id: uuidv4(), payload }));
    }
  }, []);

  // ── Arrancar ping periódico ──────────────────────────────────────
  const startPing = useCallback(() => {
    pingTimerRef.current = setInterval(() => send("ping"), PING_INTERVAL);
  }, [send]);

  const stopPing = useCallback(() => {
    if (pingTimerRef.current) {
      clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
  }, []);

  // ── Conectar / reconectar ────────────────────────────────────────
  const connect = useCallback(() => {
    // Limpiar conexión anterior si existe
    if (wsRef.current) {
      wsRef.current.onclose = null;   // evitar re-trigger del handler
      wsRef.current.close();
    }
    stopPing();

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setError(null);
      retriesRef.current = 0;
      startPing();
      // Pedir estado inicial
      send("status_request");
    };

    ws.onmessage = (ev: MessageEvent<string>) => {
      let msg: WsMessage;
      try {
        msg = JSON.parse(ev.data) as WsMessage;
      } catch {
        return;
      }

      if (msg.type === "status") {
        const p = msg.payload as WsStatusPayload;
        setMascot(p.mascot);
        setModel(p.model);
      } else if (msg.type === "error") {
        const p = msg.payload as WsErrorPayload;
        setError(p.message);
      }
      // pong: ignorar — solo sirve para mantener viva la conexión
    };

    ws.onerror = () => {
      // onerror siempre precede a onclose; la lógica de retry va en onclose
    };

    ws.onclose = () => {
      setConnected(false);
      stopPing();

      if (retriesRef.current >= MAX_RETRIES) {
        setError(
          "No se pudo conectar con el backend de Itzel. " +
          "Ejecuta `itzel setup` y vuelve a abrir la app."
        );
        return;
      }

      // Backoff exponencial: 1 s, 2 s, 4 s
      const delay = BACKOFF_BASE_MS * Math.pow(2, retriesRef.current);
      retriesRef.current += 1;
      setTimeout(() => connectRef.current(), delay);
    };
  }, [send, startPing, stopPing]);

  // Guardar referencia estable para el closure de onclose
  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  // ── Montar / desmontar ───────────────────────────────────────────
  useEffect(() => {
    connect();
    return () => {
      stopPing();
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  // Solo al montar — connect es estable gracias a useCallback
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    connected,
    mascot,
    model,
    error,
    reconnect: connect,
  };
}
