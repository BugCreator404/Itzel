/**
 * Tests para useUpdater — auto-actualización vía Tauri.
 *
 * Los plugins de Tauri no existen en jsdom → se mockean check/relaunch y
 * las notificaciones.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

// ─── mocks de Tauri ───────────────────────────────────────────────────────────

const checkMock = vi.fn();
const relaunchMock = vi.fn();
const downloadAndInstallMock = vi.fn();
const sendNotificationMock = vi.fn();

vi.mock("@tauri-apps/plugin-updater", () => ({
  check: () => checkMock(),
}));

vi.mock("@tauri-apps/plugin-process", () => ({
  relaunch: () => relaunchMock(),
}));

vi.mock("@tauri-apps/plugin-notification", () => ({
  isPermissionGranted: vi.fn(() => Promise.resolve(true)),
  requestPermission: vi.fn(() => Promise.resolve("granted")),
  sendNotification: (...args: unknown[]) => sendNotificationMock(...args),
}));

import { useUpdater } from "./useUpdater";

// Helper: construye un objeto Update falso.
function fakeUpdate(version: string) {
  return {
    version,
    downloadAndInstall: () => downloadAndInstallMock(),
  };
}

// ─── suite ────────────────────────────────────────────────────────────────────

describe("useUpdater", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("queda en 'none' cuando no hay actualización", async () => {
    checkMock.mockResolvedValue(null);
    const { result } = renderHook(() => useUpdater());
    await waitFor(() => expect(result.current.status).toBe("none"));
    expect(result.current.version).toBeNull();
  });

  it("detecta una actualización disponible y notifica", async () => {
    checkMock.mockResolvedValue(fakeUpdate("0.2.0"));
    const { result } = renderHook(() => useUpdater());
    await waitFor(() => expect(result.current.status).toBe("available"));
    expect(result.current.version).toBe("0.2.0");
    expect(sendNotificationMock).toHaveBeenCalledOnce();
  });

  it("falla en silencio si check() lanza (updater no configurado)", async () => {
    checkMock.mockRejectedValue(new Error("no pubkey configured"));
    const { result } = renderHook(() => useUpdater());
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toContain("pubkey");
    // No reinicia ni instala nada.
    expect(relaunchMock).not.toHaveBeenCalled();
  });

  it("no comprueba al montar si autoCheck es false", async () => {
    checkMock.mockResolvedValue(null);
    const { result } = renderHook(() => useUpdater({ autoCheck: false }));
    await act(async () => { await Promise.resolve(); });
    expect(checkMock).not.toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
  });

  it("installAndRestart descarga, instala y reinicia", async () => {
    checkMock.mockResolvedValue(fakeUpdate("0.2.0"));
    downloadAndInstallMock.mockResolvedValue(undefined);
    relaunchMock.mockResolvedValue(undefined);

    const { result } = renderHook(() => useUpdater());
    await waitFor(() => expect(result.current.status).toBe("available"));

    await act(async () => { await result.current.installAndRestart(); });

    expect(downloadAndInstallMock).toHaveBeenCalledOnce();
    expect(relaunchMock).toHaveBeenCalledOnce();
  });

  it("autoDownload instala automáticamente al detectar update", async () => {
    checkMock.mockResolvedValue(fakeUpdate("0.3.0"));
    downloadAndInstallMock.mockResolvedValue(undefined);
    relaunchMock.mockResolvedValue(undefined);

    renderHook(() => useUpdater({ autoDownload: true }));

    await waitFor(() => expect(downloadAndInstallMock).toHaveBeenCalledOnce());
    expect(relaunchMock).toHaveBeenCalledOnce();
  });

  it("checkNow puede llamarse manualmente", async () => {
    checkMock.mockResolvedValue(null);
    const { result } = renderHook(() => useUpdater({ autoCheck: false }));

    await act(async () => { await result.current.checkNow(); });
    expect(checkMock).toHaveBeenCalledOnce();
    expect(result.current.status).toBe("none");
  });
});
