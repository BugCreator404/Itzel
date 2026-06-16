/**
 * MisDocumentos — panel de la memoria semántica (RAG).
 *
 * Muestra y gestiona el índice de documentos local (deliverable "UI de índice"):
 *   • Carpetas indexadas, con nº de documentos por carpeta y botón para quitarlas.
 *   • Añadir carpeta (selector nativo de Tauri; prompt como fallback en navegador).
 *   • Botón "Re-indexar" con barra de progreso en vivo ("Indexando N archivos…").
 *   • Botón "Limpiar índice" con confirmación (principio #5 — acción irreversible).
 *   • Resumen: nº de documentos/fragmentos, modelo de embeddings, telemetría OFF.
 *
 * Estados especiales:
 *   - RAG desactivado (503) → explica cómo activarlo.
 *   - Faltan dependencias (501) → muestra el pip install 'itzel-core[rag]'.
 *   - Backend caído → mensaje de error de red.
 *
 * Privacidad (principio #2): se recuerda al usuario que los vectores se generan
 * localmente y nunca salen del equipo.
 *
 * English: "My documents" panel — manage the local semantic index (folders,
 * reindex with live progress, clear). Privacy-first, fully on-device.
 */

import { useCallback, useState } from "react";
import { useDocuments, type DirEntry } from "../hooks/useDocuments";

interface MisDocumentosProps {
  onClose: () => void;
}

// ─── selector de carpeta ───────────────────────────────────────────────────────
// Pide la ruta por teclado. Un selector nativo (plugin-dialog de Tauri) sería más
// cómodo, pero requiere el plugin de Rust + permiso 'dialog:allow-open'; queda
// como mejora de seguimiento para no acoplar este componente al backend nativo.

async function pickFolder(): Promise<string | null> {
  const typed = window.prompt("Ruta de la carpeta a indexar:");
  return typed?.trim() || null;
}

// ─── componente ────────────────────────────────────────────────────────────────

export function MisDocumentos({ onClose }: MisDocumentosProps) {
  const {
    status,
    dirs,
    progress,
    indexing,
    loading,
    error,
    reindex,
    clearIndex,
    addDir,
    removeDir,
  } = useDocuments();

  const [confirmClear, setConfirmClear] = useState(false);

  const handleAdd = useCallback(async () => {
    const path = await pickFolder();
    if (path) await addDir(path);
  }, [addDir]);

  const handleClear = useCallback(async () => {
    await clearIndex();
    setConfirmClear(false);
  }, [clearIndex]);

  // ── cuerpo según estado ──────────────────────────────────────────────────────

  let body: React.ReactNode;

  if (loading) {
    body = <p className="docs-muted">Cargando…</p>;
  } else if (error) {
    body = <p className="docs-error">{error}</p>;
  } else if (status && !status.enabled) {
    body = (
      <div className="docs-notice">
        <p>La memoria semántica está <strong>desactivada</strong>.</p>
        <code className="docs-code">itzel config rag.enabled true</code>
      </div>
    );
  } else if (status && !status.available) {
    body = (
      <div className="docs-notice">
        <p>
          Faltan dependencias{status.missing_deps.length ? ` (${status.missing_deps.join(", ")})` : ""}.
        </p>
        <code className="docs-code">pip install 'itzel-core[rag]'</code>
      </div>
    );
  } else if (status) {
    body = (
      <>
        {/* Resumen del índice */}
        <div className="docs-stats">
          <span><strong>{status.documents}</strong> documentos</span>
          <span><strong>{status.chunks}</strong> fragmentos</span>
          {status.embed_model && (
            <span className="docs-muted">{status.embed_model}</span>
          )}
          <span className="docs-badge-priv" title="Los vectores nunca salen de tu equipo">
            🔒 local · telemetría {status.telemetry}
          </span>
        </div>

        {/* Barra de progreso del indexado */}
        {indexing && progress && (
          <div className="docs-progress" role="status" aria-live="polite">
            <div className="docs-progress-bar">
              <div
                className="docs-progress-fill"
                style={{ width: `${progress.percent ?? 0}%` }}
              />
            </div>
            <span className="docs-progress-label">
              {progress.label || "Indexando…"}
              {progress.current_file ? ` — ${progress.current_file}` : ""}
            </span>
          </div>
        )}

        {/* Lista de carpetas indexadas */}
        {dirs.length === 0 ? (
          <p className="docs-empty">
            Aún no has añadido carpetas. Añade una para empezar a indexar tus documentos.
          </p>
        ) : (
          <ul className="docs-dirs">
            {dirs.map((d: DirEntry) => (
              <li key={d.path} className="docs-dir">
                <span className="docs-dir-info">
                  <span className={`docs-dir-path${d.exists ? "" : " docs-dir-path--missing"}`}>
                    {d.path}
                  </span>
                  <span className="docs-dir-count">
                    {d.exists ? `${d.documents} doc(s)` : "no encontrada"}
                  </span>
                </span>
                <button
                  className="docs-dir-remove"
                  onClick={() => removeDir(d.path)}
                  aria-label={`Quitar ${d.path}`}
                  title="Quitar del índice"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </>
    );
  }

  const ready = !!status && status.enabled && status.available && !loading && !error;

  // ── render ───────────────────────────────────────────────────────────────────

  return (
    <div
      className="docs-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Mis documentos"
      onClick={onClose}
    >
      <div className="docs-panel" onClick={e => e.stopPropagation()}>
        {/* Cabecera */}
        <div className="docs-header">
          <h2 className="docs-title">📂 Mis documentos</h2>
          <button className="docs-close" onClick={onClose} aria-label="Cerrar">
            ×
          </button>
        </div>

        <p className="docs-subtitle">
          Búsqueda semántica sobre tus archivos — 100% en tu equipo.
        </p>

        {/* Cuerpo */}
        <div className="docs-body">{body}</div>

        {/* Pie con acciones (solo cuando el RAG está listo) */}
        {ready && (
          <div className="docs-actions">
            <button className="docs-btn" onClick={handleAdd} disabled={indexing}>
              + Añadir carpeta
            </button>
            <div className="docs-actions-right">
              {confirmClear ? (
                <>
                  <span className="docs-confirm-q">¿Borrar el índice?</span>
                  <button className="docs-btn docs-btn--danger" onClick={handleClear}>
                    Sí, borrar
                  </button>
                  <button className="docs-btn docs-btn--ghost" onClick={() => setConfirmClear(false)}>
                    Cancelar
                  </button>
                </>
              ) : (
                <button
                  className="docs-btn docs-btn--ghost"
                  onClick={() => setConfirmClear(true)}
                  disabled={indexing || status.chunks === 0}
                >
                  Limpiar índice
                </button>
              )}
              <button
                className="docs-btn docs-btn--primary"
                onClick={() => reindex(false)}
                disabled={indexing || dirs.length === 0}
              >
                {indexing ? "Indexando…" : "Re-indexar"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
