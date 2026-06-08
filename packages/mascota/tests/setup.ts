/**
 * Setup de Vitest: mockea HTMLCanvasElement.getContext('2d') en jsdom.
 * jsdom no implementa Canvas2D, pero el motor solo llama a métodos de dibujo
 * que son seguros de ignorar en tests.
 */

import { vi } from "vitest";

// Contexto 2D mínimo — todos los métodos de dibujo son no-op
const fakeCtx: Partial<CanvasRenderingContext2D> = {
  clearRect:        vi.fn(),
  fillRect:         vi.fn(),
  beginPath:        vi.fn(),
  closePath:        vi.fn(),
  fill:             vi.fn(),
  stroke:           vi.fn(),
  save:             vi.fn(),
  restore:          vi.fn(),
  translate:        vi.fn(),
  rotate:           vi.fn(),
  lineTo:           vi.fn(),
  arc:              vi.fn(),
  fillText:         vi.fn(),
  setTransform:     vi.fn(),
  // Propiedades escriturables
  fillStyle:        "",
  strokeStyle:      "",
  lineWidth:        1,
  font:             "",
  globalAlpha:      1,
  imageSmoothingEnabled: false,
};

// Parchear el prototipo una vez antes de todos los tests
HTMLCanvasElement.prototype.getContext = vi.fn(
  (contextId: string) => contextId === "2d" ? (fakeCtx as CanvasRenderingContext2D) : null
) as typeof HTMLCanvasElement.prototype.getContext;
