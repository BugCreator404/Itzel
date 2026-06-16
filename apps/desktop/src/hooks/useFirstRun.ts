/**
 * useFirstRun — detecta si es la primera vez que se abre Itzel.
 *
 * La bandera de primer arranque se persiste en ~/.itzel/first_run_done via el
 * plugin shell de Tauri (comando `touch`/`New-Item`). En navegador/test, donde
 * Tauri no está disponible, siempre devuelve isFirstRun=false (modo seguro).
 *
 * Tour de onboarding: 3 pasos (chat, shortcuts, skills). El step se avanza
 * con nextStep(); al superar el último, completeFirstRun() marca la bandera.
 *
 * English: First-run detection via a sentinel file in ~/.itzel/. Graceful
 * degradation when Tauri is not available (browser/test).
 */

import { useCallback, useEffect, useState } from "react";

// ─── constantes ───────────────────────────────────────────────────────────────

const TOTAL_STEPS = 3;

// ─── tipos públicos ───────────────────────────────────────────────────────────

export interface FirstRunState {
  /** true mientras no se haya completado el onboarding. */
  isFirstRun: boolean;
  /** Paso actual del tour (1-3). */
  step: number;
  /** Avanza al siguiente paso o completa el onboarding si es el último. */
  nextStep: () => void;
  /** Salta directamente al final y marca la bandera. */
  completeFirstRun: () => void;
  /** Username del OS (para el saludo personalizado). */
  username: string;
}

// ─── helpers Tauri (lazy — no rompen si Tauri no está disponible) ─────────────

// Usamos plugin-shell (ya instalado) en lugar de plugin-fs para evitar
// añadir otra dependencia. La bandera es un archivo vacío en ~/.itzel/.

async function _sentinelPath(): Promise<string> {
  try {
    const { Command } = await import("@tauri-apps/plugin-shell");
    const isWin = navigator.userAgent.toLowerCase().includes("windows");
    const cmd   = isWin
      ? Command.create("cmd", ["/c", "echo %USERPROFILE%"])
      : Command.create("sh",  ["-c", "echo $HOME"]);
    const out   = await cmd.execute();
    const home  = out.stdout.trim();
    return isWin
      ? `${home}\\.itzel\\first_run_done`
      : `${home}/.itzel/first_run_done`;
  } catch {
    return "";
  }
}

async function _readSentinel(): Promise<boolean> {
  try {
    const { Command } = await import("@tauri-apps/plugin-shell");
    const path = await _sentinelPath();
    if (!path) return false;
    const isWin = navigator.userAgent.toLowerCase().includes("windows");
    const cmd   = isWin
      ? Command.create("cmd", ["/c", `if exist "${path}" echo 1`])
      : Command.create("sh",  ["-c",  `test -f "${path}" && echo 1`]);
    const out = await cmd.execute();
    return out.stdout.trim() === "1";
  } catch {
    return false;
  }
}

async function _writeSentinel(): Promise<void> {
  try {
    const { Command } = await import("@tauri-apps/plugin-shell");
    const path = await _sentinelPath();
    if (!path) return;
    const isWin = navigator.userAgent.toLowerCase().includes("windows");
    const cmd   = isWin
      ? Command.create("cmd", ["/c", `echo.>"${path}"`])
      : Command.create("sh",  ["-c",  `touch "${path}"`]);
    await cmd.execute();
  } catch {
    // Si no podemos escribir (sin Tauri, permisos…) no es crítico.
  }
}

async function _getUsername(): Promise<string> {
  try {
    const { Command } = await import("@tauri-apps/plugin-shell");
    const isWin = navigator.userAgent.toLowerCase().includes("windows");
    const cmd   = isWin
      ? Command.create("cmd", ["/c", "echo %USERNAME%"])
      : Command.create("sh",  ["-c", "echo $USER"]);
    const out  = await cmd.execute();
    const name = out.stdout.trim();
    return name || "usuario";
  } catch {
    return "usuario";
  }
}

// ─── hook ─────────────────────────────────────────────────────────────────────

export function useFirstRun(): FirstRunState {
  const [isFirstRun, setIsFirstRun] = useState(false);
  const [step,       setStep]       = useState(1);
  const [username,   setUsername]   = useState("usuario");
  const [checked,    setChecked]    = useState(false);

  // ── comprobar bandera al montar ───────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [done, name] = await Promise.all([
        _readSentinel(),
        _getUsername(),
      ]);
      if (!cancelled) {
        setIsFirstRun(!done);
        setUsername(name);
        setChecked(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ── completar onboarding ──────────────────────────────────────────────────

  const completeFirstRun = useCallback(() => {
    setIsFirstRun(false);
    void _writeSentinel();
  }, []);

  // ── avanzar paso ─────────────────────────────────────────────────────────

  const nextStep = useCallback(() => {
    setStep(prev => {
      const next = prev + 1;
      if (next > TOTAL_STEPS) {
        completeFirstRun();
        return prev;
      }
      return next;
    });
  }, [completeFirstRun]);

  // Antes de terminar la comprobación inicial no mostramos nada
  // (evita el flash del onboarding en usuarios que ya lo completaron).
  return {
    isFirstRun: checked && isFirstRun,
    step,
    nextStep,
    completeFirstRun,
    username,
  };
}
