/**
 * useUpdater — auto-actualización de Itzel vía Tauri updater.
 *
 * Al montar, comprueba si hay una nueva versión en GitHub Releases. Si la hay,
 * notifica al usuario (notificación nativa). La descarga + instalación + reinicio
 * solo ocurren cuando el usuario lo confirma (installAndRestart), salvo que se
 * active autoDownload.
 *
 * Robustez:
 *   check() falla silenciosamente si el updater no está configurado (pubkey
 *   placeholder), si no hay backend de Tauri (modo dev/web) o si no hay release.
 *   En esos casos el hook queda en estado "none"/"error" sin molestar al usuario.
 *
 * English: Tauri-based auto-updater hook. Checks GitHub Releases on mount,
 * notifies the user, and installs on confirmation. Fails gracefully.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";

// ── Estado público ──────────────────────────────────────────────────────────

export type UpdaterStatus =
  | "idle"        // aún no se ha comprobado
  | "checking"    // comprobando updates
  | "none"        // no hay updates (estás al día)
  | "available"   // hay una nueva versión disponible
  | "downloading" // descargando + instalando
  | "ready"       // instalado, a punto de reiniciar
  | "error";      // fallo (silencioso por defecto)

export interface UpdaterState {
  status: UpdaterStatus;
  version: string | null;
  error: string | null;
  /** Comprueba manualmente si hay actualizaciones. */
  checkNow: () => Promise<void>;
  /** Descarga, instala y reinicia la app con la nueva versión. */
  installAndRestart: () => Promise<void>;
}

export interface UpdaterOptions {
  /** Comprobar updates automáticamente al montar (default: true). */
  autoCheck?: boolean;
  /** Descargar e instalar automáticamente al detectar update (default: false). */
  autoDownload?: boolean;
}

// ── Hook ────────────────────────────────────────────────────────────────────

export function useUpdater(options: UpdaterOptions = {}): UpdaterState {
  const { autoCheck = true, autoDownload = false } = options;

  const [status, setStatus] = useState<UpdaterStatus>("idle");
  const [version, setVersion] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // El objeto Update se guarda en una ref para instalarlo después.
  const updateRef = useRef<Update | null>(null);

  // ── Notificación nativa (mejor esfuerzo) ───────────────────────────
  const notify = useCallback(async (title: string, body: string) => {
    try {
      let granted = await isPermissionGranted();
      if (!granted) {
        granted = (await requestPermission()) === "granted";
      }
      if (granted) sendNotification({ title, body });
    } catch {
      // Notificaciones no disponibles — no es crítico.
    }
  }, []);

  // ── Instalar + reiniciar ───────────────────────────────────────────
  const installAndRestart = useCallback(async () => {
    const update = updateRef.current;
    if (!update) return;
    try {
      setStatus("downloading");
      await update.downloadAndInstall();
      setStatus("ready");
      await relaunch();
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // ── Comprobar updates ──────────────────────────────────────────────
  const checkNow = useCallback(async () => {
    setStatus("checking");
    setError(null);
    try {
      const update = await check();
      if (update) {
        updateRef.current = update;
        setVersion(update.version);
        setStatus("available");
        await notify(
          "Itzel — actualización disponible",
          `La versión ${update.version} está lista para instalar.`,
        );
        if (autoDownload) await installAndRestart();
      } else {
        setStatus("none");
      }
    } catch (e) {
      // Falla silenciosa: updater no configurado, sin Tauri, o sin release.
      setStatus("error");
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [notify, autoDownload, installAndRestart]);

  // ── Auto-check al montar ───────────────────────────────────────────
  useEffect(() => {
    if (autoCheck) void checkNow();
  }, [autoCheck, checkNow]);

  return { status, version, error, checkNow, installAndRestart };
}
