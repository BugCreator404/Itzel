/**
 * useChatHistory — navegación de historial de mensajes enviados.
 *
 * Funciona como la historia de una terminal:
 *   ↑ → mensaje anterior
 *   ↓ → mensaje siguiente (o string vacío si estamos al final)
 *
 * El historial se gestiona internamente con useRef, por lo que nunca
 * dispara re-renders en el componente padre.
 *
 * Uso:
 *   const { navigateUp, navigateDown, pushHistory, resetHistory } = useChatHistory();
 *
 *   // Al enviar un mensaje:
 *   pushHistory(msg);   // guarda en historial
 *   resetHistory();     // cursor al final
 *
 *   // En onKeyDown del input:
 *   if (e.key === "ArrowUp")   { const prev = navigateUp();   if (prev) setInput(prev); }
 *   if (e.key === "ArrowDown") { setInput(navigateDown() ?? ""); }
 */

import { useCallback, useRef } from "react";

// ─── tipos públicos ───────────────────────────────────────────────────────────

export interface ChatHistoryHandle {
  /**
   * Mueve el cursor al mensaje anterior.
   * Devuelve el mensaje o `null` si ya estamos en el inicio.
   */
  navigateUp:    () => string | null;
  /**
   * Mueve el cursor al mensaje siguiente.
   * Devuelve el mensaje o `null` si el cursor llegó al final
   * (señal de que el input debe volver a mostrar el texto actual).
   */
  navigateDown:  () => string | null;
  /** Añade un mensaje al historial. Ignora duplicados consecutivos. */
  pushHistory:   (msg: string) => void;
  /** Resetea el cursor al final del historial (tras enviar un mensaje). */
  resetHistory:  () => void;
  /** Devuelve el número de mensajes guardados en el historial. */
  size:          () => number;
}

// ─── hook ─────────────────────────────────────────────────────────────────────

/**
 * @param initialHistory - Semilla inicial (solo se usa en el primer render).
 *   En la práctica suele estar vacía porque el historial se construye
 *   dinámicamente con pushHistory.
 */
export function useChatHistory(
  initialHistory: readonly string[] = []
): ChatHistoryHandle {
  // Historial de mensajes enviados. useRef → no dispara re-renders.
  const historyRef = useRef<string[]>([...initialHistory]);

  // Cursor de navegación.
  // historyRef.current.length = "más allá del último" (posición normal del input)
  const cursorRef = useRef<number>(historyRef.current.length);

  const navigateUp = useCallback((): string | null => {
    const history = historyRef.current;
    if (cursorRef.current <= 0) {
      // Ya estamos en el primer elemento — no moverse
      return history[0] ?? null;
    }
    cursorRef.current -= 1;
    return history[cursorRef.current] ?? null;
  }, []);

  const navigateDown = useCallback((): string | null => {
    const history = historyRef.current;
    if (cursorRef.current >= history.length) {
      // Ya estamos al final — no hay nada más nuevo
      return null;
    }
    cursorRef.current += 1;
    if (cursorRef.current === history.length) {
      // Llegamos al final → señal para restaurar el input vacío
      return null;
    }
    return history[cursorRef.current] ?? null;
  }, []);

  const pushHistory = useCallback((msg: string): void => {
    if (!msg) return;
    const history = historyRef.current;
    // No guardar duplicados consecutivos (igual que bash/zsh)
    if (history.length > 0 && history[history.length - 1] === msg) return;
    historyRef.current = [...history, msg];
  }, []);

  const resetHistory = useCallback((): void => {
    cursorRef.current = historyRef.current.length;
  }, []);

  const size = useCallback((): number => {
    return historyRef.current.length;
  }, []);

  return { navigateUp, navigateDown, pushHistory, resetHistory, size };
}
