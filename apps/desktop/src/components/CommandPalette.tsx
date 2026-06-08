/**
 * CommandPalette — paleta de acciones estilo Spotlight / VS Code.
 * Se abre con Ctrl+/ desde ChatFloat.
 * Búsqueda fuzzy sobre label + keywords. Navegación con ↑↓ Enter Escape.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { invoke } from "@tauri-apps/api/core";

// ─── tipos ────────────────────────────────────────────────────────────────────

export interface PaletteAction {
  id:          string;
  icon:        string;
  label:       string;
  description: string;
  /** Palabras clave adicionales para la búsqueda. */
  keywords:    string[];
  run:         () => void | Promise<void>;
}

interface CommandPaletteProps {
  onClose: () => void;
}

// ─── catálogo de acciones ─────────────────────────────────────────────────────

function buildActions(onClose: () => void): PaletteAction[] {
  const close = (fn: () => void | Promise<void>) => async () => {
    await fn();
    onClose();
  };

  return [
    {
      id:          "model-itzel-1b",
      icon:        "🧠",
      label:       "Cambiar a Itzel-1B",
      description: "Modelo local ligero (Qwen2.5 1.5B Q4_K_M)",
      keywords:    ["modelo", "model", "1b", "qwen", "switch"],
      run: close(async () => {
        await invoke("invoke_backend", {
          method: "POST",
          path:   "/api/v1/models/switch",
          body:   { model_id: "itzel-1b" },
        }).catch(() => {});
      }),
    },
    {
      id:          "model-itzel-7b",
      icon:        "🧠",
      label:       "Cambiar a Itzel-7B",
      description: "Modelo local avanzado (Qwen2.5 7B Q4_K_M)",
      keywords:    ["modelo", "model", "7b", "qwen", "switch"],
      run: close(async () => {
        await invoke("invoke_backend", {
          method: "POST",
          path:   "/api/v1/models/switch",
          body:   { model_id: "itzel-7b" },
        }).catch(() => {});
      }),
    },
    {
      id:          "mood-idle",
      icon:        "😌",
      label:       "Estado: Idle",
      description: "Poner al ajolote en modo espera",
      keywords:    ["mood", "ajolote", "estado", "idle", "espera"],
      run: close(() => { /* WS broadcast handled by backend */ }),
    },
    {
      id:          "mood-sleep",
      icon:        "😴",
      label:       "Estado: Dormir",
      description: "Poner al ajolote a dormir",
      keywords:    ["mood", "ajolote", "estado", "sleep", "dormir", "zzz"],
      run: close(() => {}),
    },
    {
      id:          "export-chat",
      icon:        "💾",
      label:       "Exportar conversación",
      description: "Descarga el historial como archivo .txt",
      keywords:    ["export", "exportar", "guardar", "descargar", "txt", "log"],
      run: close(() => {
        // El historial se exporta desde el componente padre vía evento
        window.dispatchEvent(new CustomEvent("itzel:export-chat"));
      }),
    },
    {
      id:          "clear-memory",
      icon:        "🧹",
      label:       "Limpiar memoria",
      description: "Borra los recuerdos guardados de esta conversación",
      keywords:    ["memoria", "memory", "limpiar", "borrar", "clear"],
      run: close(() => {
        window.dispatchEvent(new CustomEvent("itzel:clear-memory"));
      }),
    },
    {
      id:          "view-memory",
      icon:        "📋",
      label:       "Ver memoria",
      description: "Muestra los recuerdos guardados",
      keywords:    ["memoria", "memory", "ver", "show", "recuerdos"],
      run: close(() => {
        window.dispatchEvent(new CustomEvent("itzel:view-memory"));
      }),
    },
    {
      id:          "install-skill",
      icon:        "🔌",
      label:       "Instalar skill",
      description: "Abre el gestor de skills y agentes",
      keywords:    ["skill", "plugin", "agente", "instalar", "install", "extensión"],
      run: close(() => {
        window.dispatchEvent(new CustomEvent("itzel:open-skills"));
      }),
    },
    {
      id:          "open-settings",
      icon:        "⚙️",
      label:       "Abrir ajustes",
      description: "Configuración de Itzel",
      keywords:    ["settings", "ajustes", "configuración", "config", "preferencias"],
      run: close(() => {
        window.dispatchEvent(new CustomEvent("itzel:open-settings"));
      }),
    },
    {
      id:          "open-main",
      icon:        "🖥️",
      label:       "Abrir ventana principal",
      description: "Trae al frente la ventana completa de Itzel (Ctrl+Shift+I)",
      keywords:    ["ventana", "window", "principal", "main", "abrir"],
      run: close(async () => {
        await invoke("toggle_chat_window").catch(() => {});
        // El shortcut Ctrl+Shift+I lo maneja Rust directamente
        window.dispatchEvent(new CustomEvent("itzel:open-main"));
      }),
    },
  ];
}

// ─── fuzzy match ──────────────────────────────────────────────────────────────

function fuzzyMatch(action: PaletteAction, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  const haystack = [action.label, action.description, ...action.keywords]
    .join(" ")
    .toLowerCase();
  // Búsqueda por substrings: cada palabra del query debe aparecer
  return q.split(/\s+/).every(word => haystack.includes(word));
}

// ─── componente ──────────────────────────────────────────────────────────────

export function CommandPalette({ onClose }: CommandPaletteProps) {
  const [query,     setQuery]     = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef    = useRef<HTMLInputElement>(null);
  const listRef     = useRef<HTMLUListElement>(null);

  const actions  = useMemo(() => buildActions(onClose), [onClose]);
  const filtered = useMemo(
    () => actions.filter(a => fuzzyMatch(a, query)),
    [actions, query]
  );

  // Enfocar input al abrir
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Resetear índice activo cuando cambia el filtro
  useEffect(() => {
    setActiveIdx(0);
  }, [query]);

  // Scroll al elemento activo
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const item = list.children[activeIdx] as HTMLElement | undefined;
    item?.scrollIntoView({ block: "nearest" });
  }, [activeIdx]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIdx(i => Math.min(i + 1, filtered.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIdx(i => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        filtered[activeIdx]?.run();
      }
    },
    [filtered, activeIdx, onClose]
  );

  return (
    /* Overlay: clic fuera cierra */
    <div
      className="cmd-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Paleta de comandos"
      onClick={onClose}
    >
      <div
        className="cmd-palette"
        onClick={e => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        {/* Input de búsqueda */}
        <div className="cmd-search">
          <span className="cmd-search-icon" aria-hidden="true">⌘</span>
          <input
            ref={inputRef}
            className="cmd-input"
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Busca una acción…"
            aria-label="Buscar acción"
            autoComplete="off"
            spellCheck={false}
          />
          {query && (
            <button
              className="cmd-clear"
              onClick={() => setQuery("")}
              aria-label="Limpiar búsqueda"
            >
              ×
            </button>
          )}
        </div>

        {/* Lista de resultados */}
        <ul
          ref={listRef}
          className="cmd-list"
          role="listbox"
          aria-label="Resultados"
        >
          {filtered.length === 0 && (
            <li className="cmd-empty" role="option" aria-selected="false">
              Sin resultados para "{query}"
            </li>
          )}
          {filtered.map((action, idx) => (
            <li
              key={action.id}
              className={`cmd-item${idx === activeIdx ? " cmd-item--active" : ""}`}
              role="option"
              aria-selected={idx === activeIdx}
              onClick={() => action.run()}
              onMouseEnter={() => setActiveIdx(idx)}
            >
              <span className="cmd-item-icon" aria-hidden="true">
                {action.icon}
              </span>
              <span className="cmd-item-text">
                <span className="cmd-item-label">{action.label}</span>
                <span className="cmd-item-desc">{action.description}</span>
              </span>
            </li>
          ))}
        </ul>

        {/* Pie con atajos de teclado */}
        <div className="cmd-footer" aria-hidden="true">
          <span><kbd>↑↓</kbd> navegar</span>
          <span><kbd>Enter</kbd> ejecutar</span>
          <span><kbd>Esc</kbd> cerrar</span>
        </div>
      </div>
    </div>
  );
}
