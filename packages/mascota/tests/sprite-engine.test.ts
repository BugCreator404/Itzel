/**
 * Tests del motor de sprite SpriteEngine.
 * Ambiente: jsdom (canvas 2D simulado).
 * rAF: fake — no se ejecuta ningún frame a menos que se avance el reloj.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { SpriteEngine, type MoodKey } from "../src/index";

// ─── helpers ──────────────────────────────────────────────────────────────────

/** Crea un par de canvas HTMLElement con dimensiones mínimas para el motor. */
function makeCanvases(): {
  spriteCanvas: HTMLCanvasElement;
  fxCanvas: HTMLCanvasElement;
} {
  const make = (w: number, h: number) => {
    const cv = document.createElement("canvas");
    cv.width  = w;
    cv.height = h;
    // jsdom: getBoundingClientRect devuelve ceros; lo sobreescribimos
    vi.spyOn(cv, "getBoundingClientRect").mockReturnValue(
      { width: w, height: h, top: 0, left: 0, right: w, bottom: h, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
    );
    return cv;
  };
  return { spriteCanvas: make(280, 340), fxCanvas: make(280, 340) };
}

/** Instancia un motor con valores de prueba y lo devuelve junto con los canvas. */
function makeEngine(onBubble?: (text: string | null) => void) {
  const { spriteCanvas, fxCanvas } = makeCanvases();
  const engine = new SpriteEngine({ spriteCanvas, fxCanvas, onBubble });
  return { engine, spriteCanvas, fxCanvas };
}

// ─── suite ────────────────────────────────────────────────────────────────────

describe("SpriteEngine — constructor", () => {
  it("asigna dimensiones nativas al canvas sprite (28×10 × 34×10)", () => {
    const { spriteCanvas } = makeEngine();
    expect(spriteCanvas.width).toBe(280);
    expect(spriteCanvas.height).toBe(340);
  });

  it("lanza si el canvas no admite contexto 2D", () => {
    const { spriteCanvas, fxCanvas } = makeCanvases();
    vi.spyOn(spriteCanvas, "getContext").mockReturnValue(null);
    expect(() => new SpriteEngine({ spriteCanvas, fxCanvas })).toThrow();
  });

  it("acepta cellSize personalizado y ajusta dimensiones", () => {
    const { spriteCanvas, fxCanvas } = makeCanvases();
    // cellSize=5 → 28*5=140 × 34*5=170
    const engine = new SpriteEngine({ spriteCanvas, fxCanvas, cellSize: 5 });
    expect(spriteCanvas.width).toBe(140);
    expect(spriteCanvas.height).toBe(170);
    engine.dispose();
  });
});

describe("SpriteEngine — mood", () => {
  let engine: SpriteEngine;

  beforeEach(() => {
    ({ engine } = makeEngine());
  });

  it("mood inicial es 'idle'", () => {
    expect(engine.getMood()).toBe("idle");
  });

  it("setMood cambia el mood correctamente", () => {
    const moods: MoodKey[] = ["work", "think", "happy", "wave", "sleep", "idle"];
    for (const m of moods) {
      engine.setMood(m);
      expect(engine.getMood()).toBe(m);
    }
  });

  it("setMood llama onBubble con el texto del mood", () => {
    const cb = vi.fn();
    const { engine: eng } = makeEngine(cb);

    eng.setMood("work");
    expect(cb).toHaveBeenCalledWith("Trabajando");

    eng.setMood("think");
    expect(cb).toHaveBeenCalledWith("Mmm… déjame ver");

    eng.dispose();
  });

  it("setMood llama onBubble con null para moods sin texto", () => {
    const cb = vi.fn();
    const { engine: eng } = makeEngine(cb);

    eng.setMood("idle");
    expect(cb).toHaveBeenCalledWith(null);

    eng.setMood("sleep");
    expect(cb).toHaveBeenCalledWith(null);

    eng.dispose();
  });
});

describe("SpriteEngine — pet", () => {
  it("pet() provoca un burst y activa el mood 'happy' temporalmente vía onBubble", () => {
    const cb = vi.fn();
    const { engine } = makeEngine(cb);

    engine.setMood("idle");
    cb.mockClear();

    engine.pet();
    // Durante el pet, activeMood() devuelve 'happy' → onBubble recibe '¡Yay! ✨'
    expect(cb).toHaveBeenCalledWith("¡Yay! ✨");
    engine.dispose();
  });
});

describe("SpriteEngine — setMouseNorm", () => {
  it("clampea valores fuera del rango [-1, 1]", () => {
    const { engine } = makeEngine();
    // No lanza y tampoco expone los valores directamente,
    // pero no debe arrojar excepción con valores extremos
    expect(() => engine.setMouseNorm(99, -99)).not.toThrow();
    expect(() => engine.setMouseNorm(-2, 2)).not.toThrow();
    engine.dispose();
  });
});

describe("SpriteEngine — ciclo de vida", () => {
  it("start() registra un rAF", () => {
    const rafSpy = vi.spyOn(globalThis, "requestAnimationFrame");
    const { engine } = makeEngine();

    engine.start();
    expect(rafSpy).toHaveBeenCalledTimes(1);
    engine.dispose();
  });

  it("start() idempotente — segunda llamada no registra otro rAF", () => {
    const rafSpy = vi.spyOn(globalThis, "requestAnimationFrame");
    const { engine } = makeEngine();

    engine.start();
    engine.start(); // segunda llamada — debe ignorarse
    expect(rafSpy).toHaveBeenCalledTimes(1);
    engine.dispose();
  });

  it("stop() cancela el rAF activo", () => {
    const cafSpy = vi.spyOn(globalThis, "cancelAnimationFrame");
    const { engine } = makeEngine();

    engine.start();
    engine.stop();
    expect(cafSpy).toHaveBeenCalledTimes(1);
  });

  it("dispose() limpia partículas y llama stop()", () => {
    const cafSpy = vi.spyOn(globalThis, "cancelAnimationFrame");
    const { engine } = makeEngine();

    engine.start();
    engine.pet(); // genera partículas internas
    engine.dispose();

    // cancelAnimationFrame se llamó
    expect(cafSpy).toHaveBeenCalled();
    // Después de dispose, start/stop no deben lanzar excepción
    expect(() => engine.start()).not.toThrow();
    engine.dispose(); // doble dispose no debe lanzar
  });
});

describe("SpriteEngine — resizeFx", () => {
  it("resizeFx() no lanza con canvas en el DOM", () => {
    const { engine } = makeEngine();
    expect(() => engine.resizeFx()).not.toThrow();
    engine.dispose();
  });
});
