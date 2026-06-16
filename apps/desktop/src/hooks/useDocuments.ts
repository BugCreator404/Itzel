/**
 * useDocuments — estado de la memoria semántica (RAG) para la UI "Mis documentos".
 *
 * Habla con el backend local en /api/v1/rag/* (todo on-device, principio #1):
 *   GET  /rag/status     → disponibilidad + stats del índice + progreso
 *   GET  /rag/dirs       → carpetas indexadas + nº de docs por carpeta
 *   POST /rag/dirs       → añadir / quitar una carpeta (persistente)
 *   GET  /rag/progress   → progreso del indexado en vivo
 *   POST /rag/reindex    → re-indexar (en segundo plano)
 *   POST /rag/clear      → limpiar el índice vectorial
 *
 * Distingue los tres "no disponible":
 *   - backend caído        → error de red
 *   - RAG desactivado (503) → status.enabled = false
 *   - faltan deps  (501)    → status.available = false + missingDeps
 *
 * Mientras hay un indexado en curso, hace polling de /rag/progress para que la
 * barra "Indexando N archivos…" avance en vivo.
 *
 * English: React hook wrapping the local RAG REST API for the "My documents"
 * panel. Graceful handling of disabled (503) / missing-deps (501) / backend-down.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE = "http://localhost:7432/api/v1";
const POLL_MS = 800;

// ─── tipos (espejo de los schemas de api/v1/rag.py) ───────────────────────────

export interface IndexProgress {
  running: boolean;
  total: number;
  done: number;
  percent: number;
  current_file: string;
  indexed: number;
  skipped: number;
  removed: number;
  errors: number;
  label: string;
  started_at: string;
  finished_at: string;
}

export interface IndexStatus {
  enabled: boolean;
  available: boolean;
  missing_deps: string[];
  embed_model: string | null;
  embed_dim: number | null;
  documents: number;
  chunks: number;
  store_path: string | null;
  telemetry: string;
  progress: Partial<IndexProgress>;
}

export interface DirEntry {
  path: string;
  exists: boolean;
  documents: number;
}

export interface DocumentsState {
  /** null mientras carga por primera vez. */
  status: IndexStatus | null;
  dirs: DirEntry[];
  progress: IndexProgress | null;
  /** true si hay un indexado corriendo (deriva de progress.running). */
  indexing: boolean;
  loading: boolean;
  /** Mensaje de error de red (backend caído), o null. */
  error: string | null;
  refresh: () => Promise<void>;
  reindex: (force?: boolean) => Promise<void>;
  clearIndex: () => Promise<void>;
  addDir: (path: string) => Promise<void>;
  removeDir: (path: string) => Promise<void>;
}

// ─── helpers de red ────────────────────────────────────────────────────────────

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    // 503/501 los maneja el caller leyendo el cuerpo; otros → error.
    const err = new Error(`HTTP ${res.status}`);
    (err as Error & { status?: number }).status = res.status;
    throw err;
  }
  return (await res.json()) as T;
}

async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok && res.status >= 500 && res.status !== 503) {
    throw new Error(`HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

// ─── hook ──────────────────────────────────────────────────────────────────────

export function useDocuments(): DocumentsState {
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [dirs, setDirs] = useState<DirEntry[]>([]);
  const [progress, setProgress] = useState<IndexProgress | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  // ── carga de estado + carpetas ──────────────────────────────────────────────

  const refresh = useCallback(async () => {
    try {
      const st = await apiGet<IndexStatus>("/rag/status");
      if (!mountedRef.current) return;
      setStatus(st);
      setError(null);

      // dirs solo tiene sentido si el RAG está activo.
      if (st.enabled) {
        try {
          const d = await apiGet<DirEntry[]>("/rag/dirs");
          if (mountedRef.current) setDirs(d);
        } catch {
          // 501 (faltan deps): no hay carpetas que mostrar todavía.
          if (mountedRef.current) setDirs([]);
        }
      } else {
        setDirs([]);
      }
    } catch (e) {
      // 503 = desactivado → status.enabled=false ya viene en el cuerpo; pero si
      // el fetch falló por red, marcamos error de backend caído.
      if (!mountedRef.current) return;
      const status503 = (e as Error & { status?: number }).status === 503;
      if (status503) {
        setStatus(prev => prev ?? null);
        setError(null);
      } else {
        setError(
          "No se pudo contactar con Itzel. ¿El backend está corriendo? (itzel setup)"
        );
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  // ── polling del progreso mientras indexa ─────────────────────────────────────

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const p = await apiGet<IndexProgress>("/rag/progress");
        if (!mountedRef.current) return;
        setProgress(p);
        if (!p.running) {
          stopPolling();
          void refresh(); // refrescar conteos al terminar
        }
      } catch {
        stopPolling();
      }
    }, POLL_MS);
  }, [refresh, stopPolling]);

  // ── acciones ─────────────────────────────────────────────────────────────────

  const reindex = useCallback(
    async (force = false) => {
      setError(null);
      try {
        await apiPost(`/rag/reindex?force=${force ? "true" : "false"}`);
        // Arrancar polling inmediato para ver "Indexando…".
        setProgress(p => (p ? { ...p, running: true } : null));
        startPolling();
      } catch {
        setError("No se pudo iniciar el indexado.");
      }
    },
    [startPolling]
  );

  const clearIndex = useCallback(async () => {
    setError(null);
    try {
      await apiPost("/rag/clear");
      await refresh();
    } catch {
      setError("No se pudo limpiar el índice.");
    }
  }, [refresh]);

  const addDir = useCallback(
    async (path: string) => {
      setError(null);
      try {
        const d = await apiPost<DirEntry[]>("/rag/dirs", { path, action: "add" });
        if (mountedRef.current) setDirs(d);
      } catch {
        setError("No se pudo añadir la carpeta.");
      }
    },
    []
  );

  const removeDir = useCallback(
    async (path: string) => {
      setError(null);
      try {
        const d = await apiPost<DirEntry[]>("/rag/dirs", { path, action: "remove" });
        if (mountedRef.current) setDirs(d);
      } catch {
        setError("No se pudo quitar la carpeta.");
      }
    },
    []
  );

  // ── montar / desmontar ───────────────────────────────────────────────────────

  useEffect(() => {
    mountedRef.current = true;
    void refresh();
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, [refresh, stopPolling]);

  // Si al cargar ya había un indexado en curso (otra ventana), seguir el progreso.
  useEffect(() => {
    if (status?.progress?.running && !pollRef.current) {
      startPolling();
    }
  }, [status, startPolling]);

  const indexing = !!progress?.running || !!status?.progress?.running;

  return {
    status,
    dirs,
    progress,
    indexing,
    loading,
    error,
    refresh,
    reindex,
    clearIndex,
    addDir,
    removeDir,
  };
}
