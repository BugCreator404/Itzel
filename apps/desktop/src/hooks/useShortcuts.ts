/**
 * useShortcuts — escucha eventos Tauri de atajos globales y los mapea
 * a callbacks en React.
 *
 * Rust emite `emit_to("chat", "shortcut", "<action>")` cada vez que
 * se activa un hotkey global. Este hook se suscribe a esos eventos y
 * llama al callback correspondiente.
 *
 * Acciones disponibles (definidas en lib.rs):
 *   "stop-agent"      → cancela el stream activo (Ctrl+Shift+S / Esc global)
 *   "command-palette" → abre / cierra la paleta (Ctrl+/)
 *   "toggle"          → oculta / muestra la ventana (Ctrl+Space) — manejado en Rust
 *   "open-main"       → abre la ventana principal (Ctrl+Shift+I) — manejado en Rust
 *
 * Uso:
 *   useShortcuts({
 *     "stop-agent":      () => chat.cancel(),
 *     "command-palette": () => setPaletteOpen(p => !p),
 *   });
 */

import { useEffect, useRef } from "react";
import { listen } from "@tauri-apps/api/event";

// ─── tipos ────────────────────────────────────────────────────────────────────

/** Mapa de acción → callback. Solo se deben declarar las acciones que el
 *  componente quiere manejar; el resto se ignoran silenciosamente. */
export type ShortcutHandlers = Partial<Record<ShortcutAction, () => void>>;

/** Acciones emitidas por Rust. */
export type ShortcutAction =
  | "stop-agent"
  | "command-palette"
  | "toggle"
  | "open-main"
  | "snapshot-context"
  | "clear-chat";

// ─── hook ─────────────────────────────────────────────────────────────────────

/**
 * @param handlers - Objeto estable de callbacks por acción.
 *   Para evitar re-suscripciones innecesarias, pasa el objeto con
 *   `useMemo` / constante de módulo cuando sea posible.
 *   De todas formas, el hook usa una ref interna para que los
 *   callbacks siempre estén frescos sin re-suscribir el listener.
 */
export function useShortcuts(handlers: ShortcutHandlers): void {
  // Ref para acceder al último `handlers` sin re-crear el listener
  const handlersRef = useRef<ShortcutHandlers>(handlers);
  useEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    let unlisten: (() => void) | undefined;

    // `listen` es async → guardamos la promesa y limpiamos en el cleanup
    const subscription = listen<string>("shortcut", event => {
      const action = event.payload as ShortcutAction;
      const handler = handlersRef.current[action];
      handler?.();
    });

    subscription
      .then(fn => { unlisten = fn; })
      .catch(err => {
        // En entorno de tests (jsdom) no existe Tauri — ignorar silenciosamente
        console.warn("[itzel] useShortcuts: no se pudo suscribir al evento:", err);
      });

    return () => {
      unlisten?.();
    };
    // Solo se suscribe una vez — los handlers se actualizan vía ref
  }, []);
}
