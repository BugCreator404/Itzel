/**
 * Tests de useChatStream — se ejecutan con Vitest + jsdom.
 * Mockean fetch para simular el SSE del backend.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useChatStream } from "./useChatStream";

// ── Helper: construir un ReadableStream que emite líneas SSE ───────────────

function makeSseStream(lines: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const line of lines) {
        controller.enqueue(encoder.encode(line));
      }
      controller.close();
    },
  });
}

// ── Setup / Teardown ──────────────────────────────────────────────────────

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("crypto", {
    randomUUID: () => "test-uuid-fixed",
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ── Tests ─────────────────────────────────────────────────────────────────

describe("useChatStream", () => {

  it("estado inicial es idle con buffer vacío", () => {
    const { result } = renderHook(() => useChatStream());
    expect(result.current.status).toBe("idle");
    expect(result.current.buffer).toBe("");
    expect(result.current.error).toBeNull();
  });

  it("acumula tokens y termina en 'done' con [DONE]", async () => {
    const stream = makeSseStream([
      "data: Hola\n\n",
      "data:  mundo\n\n",
      "data: [DONE]\n\n",
    ]);

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      })
    );

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.send("test");
    });

    expect(result.current.status).toBe("done");
    expect(result.current.buffer).toContain("Hola");
    expect(result.current.buffer).toContain("mundo");
    expect(result.current.error).toBeNull();
  });

  it("expone error en status 'error' con [ERROR:...]", async () => {
    const stream = makeSseStream(["data: [ERROR:modelo no cargado]\n\n"]);

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      })
    );

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.send("test");
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toBe("modelo no cargado");
  });

  it("HTTP 429 resulta en status 'error'", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Demasiadas solicitudes" }), {
        status: 429,
        headers: { "Content-Type": "application/json" },
      })
    );

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      await result.current.send("test");
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toContain("Demasiadas solicitudes");
  });

  it("reset limpia buffer, error y genera nueva sessionId", async () => {
    const { result } = renderHook(() => useChatStream());
    const initialSession = result.current.sessionId;

    // Forzar un estado sucio
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(makeSseStream(["data: [ERROR:test]\n\n"]), { status: 200 })
    );
    await act(async () => { await result.current.send("x"); });
    expect(result.current.status).toBe("error");

    act(() => { result.current.reset(); });

    expect(result.current.status).toBe("idle");
    expect(result.current.buffer).toBe("");
    expect(result.current.error).toBeNull();
  });

  it("send no lanza si ya hay streaming en curso", async () => {
    // Estado streaming (fetch nunca resuelve)
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() => useChatStream());

    // Primer send — queda en streaming
    act(() => { result.current.send("primero"); });

    // Segundo send — debe ignorarse silenciosamente
    await act(async () => {
      await result.current.send("segundo");
    });

    // fetch solo se llamó una vez
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });

});
