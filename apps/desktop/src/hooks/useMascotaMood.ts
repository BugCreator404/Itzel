/**
 * useMascotaMood — traduce el estado del bridge WebSocket al mood del sprite.
 *
 * Reglas de transición:
 *   WS mascot=work   → mood 'work'
 *   WS mascot=think  → mood 'think'
 *   WS mascot=happy  → mood 'happy'
 *   WS mascot=wave   → mood 'wave'
 *   WS mascot=idle   → mood 'idle'  (reset del timer de inactividad)
 *   Sin actividad >5 min y mood='idle' → mood 'sleep'
 *   Cualquier cambio del WS despierta de 'sleep'
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useWsState, type MascotState } from "./useWsState";
import type { MoodKey } from "@itzel/mascota";

// ─── tipos públicos ───────────────────────────────────────────────────────────

export interface MascotaMoodState {
  mood: MoodKey;
  /** Conectado al backend WebSocket. */
  connected: boolean;
  /** Error de conexión, si lo hay. */
  wsError: string | null;
  /**
   * Resetea el timer de inactividad manualmente.
   * Llamar en cualquier interacción del usuario (clic, input, etc.)
   * para evitar que el ajolote se duerma mientras el usuario está activo.
   */
  resetInactivity: () => void;
}

// ─── constantes ───────────────────────────────────────────────────────────────

/** 5 minutos en ms — sin actividad WS ni interacción → sleep */
const INACTIVITY_MS = 5 * 60 * 1_000;

/** MascotState y MoodKey son la misma unión — conversión de identidad. */
function toMoodKey(s: MascotState): MoodKey {
  return s as MoodKey;
}

// ─── hook ─────────────────────────────────────────────────────────────────────

export function useMascotaMood(): MascotaMoodState {
  const { mascot, connected, error: wsError } = useWsState();

  // mood activo — puede diferir de mascot cuando hay inactividad (sleep)
  const [mood, setMood] = useState<MoodKey>(() => toMoodKey(mascot));

  // timestamp de la última actividad (cambio de mascot o interacción explícita)
  const lastActivityRef = useRef(Date.now());
  // ref del timer de inactividad para limpieza correcta
  const inactivityTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── reset del timer ────────────────────────────────────────────────────────

  const resetInactivity = useCallback(() => {
    lastActivityRef.current = Date.now();

    // Cancelar el timer anterior si existe
    if (inactivityTimerRef.current !== null) {
      clearTimeout(inactivityTimerRef.current);
    }

    // Programar sleep solo cuando estemos en idle
    inactivityTimerRef.current = setTimeout(() => {
      setMood(prev => (prev === 'idle' ? 'sleep' : prev));
    }, INACTIVITY_MS);
  }, []);

  // ── reaccionar a cambios del WS ────────────────────────────────────────────

  useEffect(() => {
    // Cualquier cambio del backend despierta el ajolote y resetea inactividad
    setMood(toMoodKey(mascot));
    resetInactivity();
  }, [mascot, resetInactivity]);

  // ── arrancar timer de inactividad al montar ────────────────────────────────

  useEffect(() => {
    resetInactivity();
    return () => {
      if (inactivityTimerRef.current !== null) {
        clearTimeout(inactivityTimerRef.current);
      }
    };
  // Solo al montar — resetInactivity es estable (useCallback sin deps)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { mood, connected, wsError, resetInactivity };
}
