/**
 * Setup global de Vitest para apps/desktop.
 * Mockea canvas 2D (jsdom no lo implementa) y ResizeObserver.
 */

import { vi } from "vitest";

// ── Canvas 2D ────────────────────────────────────────────────────────────────

const fakeCtx: Partial<CanvasRenderingContext2D> = {
  clearRect:             vi.fn(),
  fillRect:              vi.fn(),
  beginPath:             vi.fn(),
  closePath:             vi.fn(),
  fill:                  vi.fn(),
  stroke:                vi.fn(),
  save:                  vi.fn(),
  restore:               vi.fn(),
  translate:             vi.fn(),
  rotate:                vi.fn(),
  lineTo:                vi.fn(),
  arc:                   vi.fn(),
  fillText:              vi.fn(),
  setTransform:          vi.fn(),
  fillStyle:             "",
  strokeStyle:           "",
  lineWidth:             1,
  font:                  "",
  globalAlpha:           1,
  imageSmoothingEnabled: false,
};

HTMLCanvasElement.prototype.getContext = vi.fn(
  (id: string) => id === "2d" ? (fakeCtx as CanvasRenderingContext2D) : null
) as typeof HTMLCanvasElement.prototype.getContext;

// ── ResizeObserver ────────────────────────────────────────────────────────────

globalThis.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe:    vi.fn(),
  unobserve:  vi.fn(),
  disconnect: vi.fn(),
}));
