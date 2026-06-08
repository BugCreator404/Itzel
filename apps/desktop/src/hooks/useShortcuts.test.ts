/**
 * Tests para useShortcuts — suscripción a eventos Tauri de hotkeys.
 *
 * Tauri no existe en jsdom → mockeamos @tauri-apps/api/event.
 */

import {
  describe, it, expect, vi, beforeEach, afterEach
} from "vitest";
import { renderHook, act } from "@testing-library/react";

// ─── mock de Tauri ────────────────────────────────────────────────────────────

type TauriHandler = (event: { payload: string }) => void;

// Mapa interno: eventName → lista de handlers activos
const tauriHandlers = new Map<string, TauriHandler[]>();

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn((eventName: string, handler: TauriHandler) => {
    const list = tauriHandlers.get(eventName) ?? [];
    list.push(handler);
    tauriHandlers.set(eventName, list);
    // Devuelve promesa que resuelve con la función de cleanup
    const unlisten = vi.fn(() => {
      const current = tauriHandlers.get(eventName) ?? [];
      tauriHandlers.set(
        eventName,
        current.filter(h => h !== handler)
      );
    });
    return Promise.resolve(unlisten);
  }),
}));

/** Dispara un evento "shortcut" con el payload dado. */
function emitShortcut(action: string): void {
  const handlers = tauriHandlers.get("shortcut") ?? [];
  handlers.forEach(h => h({ payload: action }));
}

// ─── import del hook (después del mock) ──────────────────────────────────────

import { useShortcuts } from "./useShortcuts";

// ─── suite ────────────────────────────────────────────────────────────────────

describe("useShortcuts", () => {
  beforeEach(() => {
    tauriHandlers.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("se suscribe al evento 'shortcut' de Tauri al montar", async () => {
    const { listen } = await import("@tauri-apps/api/event");
    renderHook(() => useShortcuts({}));
    // Dar tiempo a que resuelva la promesa de listen
    await act(async () => { await Promise.resolve(); });
    expect(listen).toHaveBeenCalledWith("shortcut", expect.any(Function));
  });

  it("llama al handler correcto cuando llega el evento", async () => {
    const stopAgent = vi.fn();
    renderHook(() => useShortcuts({ "stop-agent": stopAgent }));
    await act(async () => { await Promise.resolve(); });

    emitShortcut("stop-agent");
    expect(stopAgent).toHaveBeenCalledOnce();
  });

  it("llama al handler command-palette", async () => {
    const openPalette = vi.fn();
    renderHook(() => useShortcuts({ "command-palette": openPalette }));
    await act(async () => { await Promise.resolve(); });

    emitShortcut("command-palette");
    expect(openPalette).toHaveBeenCalledOnce();
  });

  it("ignora acciones sin handler registrado", async () => {
    const stopAgent = vi.fn();
    renderHook(() => useShortcuts({ "stop-agent": stopAgent }));
    await act(async () => { await Promise.resolve(); });

    // "command-palette" no tiene handler → no debe lanzar error
    expect(() => emitShortcut("command-palette")).not.toThrow();
    expect(stopAgent).not.toHaveBeenCalled();
  });

  it("puede llamar a múltiples handlers en el mismo evento", async () => {
    const stopAgent      = vi.fn();
    const cmdPalette     = vi.fn();
    renderHook(() =>
      useShortcuts({ "stop-agent": stopAgent, "command-palette": cmdPalette })
    );
    await act(async () => { await Promise.resolve(); });

    emitShortcut("stop-agent");
    emitShortcut("command-palette");
    expect(stopAgent).toHaveBeenCalledOnce();
    expect(cmdPalette).toHaveBeenCalledOnce();
  });

  it("usa siempre la versión más reciente del handler (closure fresca via ref)", async () => {
    let callCount = 0;
    const handler1 = vi.fn(() => { callCount++; });
    const handler2 = vi.fn(() => { callCount += 10; });

    // Empieza con handler1
    const { rerender } = renderHook(
      ({ h }) => useShortcuts({ "stop-agent": h }),
      { initialProps: { h: handler1 } }
    );
    await act(async () => { await Promise.resolve(); });

    // Cambia a handler2 sin re-suscribir
    act(() => { rerender({ h: handler2 }); });

    emitShortcut("stop-agent");

    // Debe llamar handler2 (el más reciente), no handler1
    expect(callCount).toBe(10);
    expect(handler1).not.toHaveBeenCalled();
    expect(handler2).toHaveBeenCalledOnce();
  });

  it("llama a unlisten al desmontar el componente", async () => {
    const { listen } = await import("@tauri-apps/api/event");
    const unlistenMock = vi.fn();
    vi.mocked(listen).mockResolvedValueOnce(unlistenMock);

    const { unmount } = renderHook(() => useShortcuts({}));
    await act(async () => { await Promise.resolve(); });

    unmount();
    expect(unlistenMock).toHaveBeenCalledOnce();
  });
});
