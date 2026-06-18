/**
 * useChatStream — consume el SSE de POST /api/v1/chat.
 *
 * Señales de control en el stream:
 *   data: <token>     → acumular en buffer
 *   data: [DONE]      → marcar como completado
 *   data: [ERROR:msg] → exponer error y detener
 *
 * El hook NO gestiona el historial de mensajes — eso es responsabilidad
 * del componente padre. Solo gestiona el estado de la request en curso.
 */

import { useCallback, useRef, useState } from "react";
import { v4 as uuidv4 } from "uuid";

const API_BASE = "http://localhost:7432/api/v1";

// ── Tipos ──────────────────────────────────────────────────────────────────

export type StreamStatus = "idle" | "streaming" | "done" | "error";

/** Una fuente citada por el RAG; cada [n] del texto mapea a un archivo. */
export interface ChatSource {
  n:          number;
  source:     string;     // ruta completa del documento
  filename:   string;     // nombre para mostrar
  file_type?: string;
  modified?:  string;
}

export interface ChatStreamState {
  status:     StreamStatus;
  buffer:     string;       // tokens acumulados de la respuesta en curso
  error:      string | null;
  sources:    ChatSource[]; // fuentes citadas por el RAG ([] si no se usó)
  sessionId:  string;
  /** Envía un mensaje y arranca el stream */
  send:       (message: string, model?: string) => Promise<void>;
  /** Cancela el stream en curso */
  cancel:     () => void;
  /** Limpia buffer y error para empezar una nueva conversación */
  reset:      () => void;
}

// ── Hook ───────────────────────────────────────────────────────────────────

export function useChatStream(): ChatStreamState {
  const [status,    setStatus]    = useState<StreamStatus>("idle");
  const [buffer,    setBuffer]    = useState("");
  const [error,     setError]     = useState<string | null>(null);
  const [sources,   setSources]   = useState<ChatSource[]>([]);
  const [sessionId, setSessionId] = useState(() => uuidv4());

  // AbortController para cancelar el fetch en curso
  const abortRef = useRef<AbortController | null>(null);

  // ── cancel ───────────────────────────────────────────────────────
  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setStatus("idle");
  }, []);

  // ── reset ────────────────────────────────────────────────────────
  const reset = useCallback(() => {
    cancel();
    setBuffer("");
    setError(null);
    setSources([]);
    setStatus("idle");
    setSessionId(uuidv4());   // nueva sesión = nuevo historial
  }, [cancel]);

  // ── send ─────────────────────────────────────────────────────────
  const send = useCallback(async (message: string, model = "itzel-1b") => {
    // No permitir requests concurrentes
    if (status === "streaming") return;

    // Limpiar estado anterior
    setBuffer("");
    setError(null);
    setSources([]);
    setStatus("streaming");

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      const response = await fetch(`${API_BASE}/chat/`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ message, session_id: sessionId, model, stream: true }),
        signal:  ctrl.signal,
      });

      if (!response.ok) {
        const detail = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
        throw new Error(detail.detail ?? `HTTP ${response.status}`);
      }

      if (!response.body) throw new Error("El backend no devolvió un stream.");

      const reader  = response.body.getReader();
      const decoder = new TextDecoder();
      let   partial = "";   // acumula fragmentos de línea incompletos

      // ── Leer el stream SSE ────────────────────────────────────────
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        partial += decoder.decode(value, { stream: true });

        // Procesar todas las líneas SSE completas (terminan en \n\n)
        const lines = partial.split("\n\n");
        partial = lines.pop() ?? "";   // el último fragmento puede estar incompleto

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const token = line.slice(6);   // quitar "data: "

          if (token === "[DONE]") {
            setStatus("done");
            return;
          }

          if (token.startsWith("[ERROR:")) {
            const msg = token.slice(7, -1);   // quitar "[ERROR:" y "]"
            setError(msg);
            setStatus("error");
            return;
          }

          // Frame de control RAG: data: [SOURCES]{"sources":[...]}
          if (token.startsWith("[SOURCES]")) {
            try {
              const data = JSON.parse(token.slice("[SOURCES]".length));
              if (Array.isArray(data.sources)) {
                setSources(data.sources as ChatSource[]);
              }
            } catch {
              // Frame malformado — ignorar, nunca romper el chat.
            }
            continue;
          }

          // Token normal — acumular en buffer
          setBuffer(prev => prev + token);
        }
      }

      // Si el reader termina sin [DONE] (backend cerró el stream abruptamente)
      setStatus("done");

    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // Cancelación manual — no es un error real
        setStatus("idle");
        return;
      }
      const msg = err instanceof Error ? err.message : "Error desconocido";
      setError(msg);
      setStatus("error");
    }
  }, [status, sessionId]);

  return { status, buffer, error, sources, sessionId, send, cancel, reset };
}
