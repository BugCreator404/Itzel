/**
 * Tests del componente <Mascota />.
 * SpriteEngine se mockea completamente — estos tests verifican el
 * comportamiento React (lifecycle, props, interacción), no el canvas.
 */

import { render, screen, fireEvent, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Mascota } from "./Mascota";

// ─── mock de @itzel/mascota ───────────────────────────────────────────────────

// Instancia mock accesible en cada test
const mockEngine = {
  setMood:       vi.fn(),
  getMood:       vi.fn().mockReturnValue("idle"),
  pet:           vi.fn(),
  setMouseNorm:  vi.fn(),
  start:         vi.fn(),
  stop:          vi.fn(),
  dispose:       vi.fn(),
  resizeFx:      vi.fn(),
};

// Capturamos el callback onBubble para poder dispararlo manualmente
let capturedOnBubble: ((text: string | null) => void) | undefined;

vi.mock("@itzel/mascota", () => ({
  SpriteEngine: vi.fn().mockImplementation((opts: { onBubble?: (t: string | null) => void }) => {
    capturedOnBubble = opts.onBubble;
    return mockEngine;
  }),
}));

// ─── helpers ──────────────────────────────────────────────────────────────────

function renderMascota(props: Partial<React.ComponentProps<typeof Mascota>> = {}) {
  return render(<Mascota {...props} />);
}

// ─── suite ────────────────────────────────────────────────────────────────────

describe("<Mascota /> — montaje y desmontaje", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedOnBubble = undefined;
  });

  it("monta sin errores y llama start()", () => {
    renderMascota();
    expect(mockEngine.start).toHaveBeenCalledTimes(1);
  });

  it("llama dispose() al desmontar", () => {
    const { unmount } = renderMascota();
    unmount();
    expect(mockEngine.dispose).toHaveBeenCalledTimes(1);
  });

  it("aplica el mood inicial al motor", () => {
    renderMascota({ mood: "work" });
    expect(mockEngine.setMood).toHaveBeenCalledWith("work");
  });
});

describe("<Mascota /> — cambio de props mood", () => {
  beforeEach(() => vi.clearAllMocks());

  it("llama setMood cuando el prop mood cambia", () => {
    const { rerender } = renderMascota({ mood: "idle" });
    expect(mockEngine.setMood).toHaveBeenCalledWith("idle");

    rerender(<Mascota mood="think" />);
    expect(mockEngine.setMood).toHaveBeenCalledWith("think");
  });

  it("no llama setMood extra si el mood no cambia", () => {
    const { rerender } = renderMascota({ mood: "happy" });
    const callsBefore = mockEngine.setMood.mock.calls.length;

    rerender(<Mascota mood="happy" />);
    // React puede llamar useEffect una vez más en StrictMode, pero el valor
    // sigue siendo el mismo — verificamos que no aumenta
    expect(mockEngine.setMood.mock.calls.length).toBe(callsBefore);
  });
});

describe("<Mascota /> — bubble de diálogo", () => {
  beforeEach(() => vi.clearAllMocks());

  it("muestra el bubble cuando onBubble recibe texto", () => {
    renderMascota();
    expect(screen.queryByRole("status")).toBeNull(); // bubble oculto

    act(() => capturedOnBubble?.("Trabajando"));

    const bubble = screen.getByText("Trabajando");
    expect(bubble).toBeDefined();
  });

  it("oculta el bubble cuando onBubble recibe null", () => {
    renderMascota();
    act(() => capturedOnBubble?.("Pensando"));
    act(() => capturedOnBubble?.(null));

    // El texto anterior ya no está visible (bubble vacío)
    expect(screen.queryByText("Pensando")).toBeNull();
  });

  it("llama onBubbleChange cuando el bubble cambia", () => {
    const onBubbleChange = vi.fn();
    renderMascota({ onBubbleChange });

    act(() => capturedOnBubble?.("¡Yay! ✨"));
    expect(onBubbleChange).toHaveBeenCalledWith("¡Yay! ✨");

    act(() => capturedOnBubble?.(null));
    expect(onBubbleChange).toHaveBeenCalledWith(null);
  });
});

describe("<Mascota /> — interactividad", () => {
  beforeEach(() => vi.clearAllMocks());

  it("llama pet() al hacer pointerdown cuando interactive=true", () => {
    renderMascota({ interactive: true });
    const stage = document.querySelector(".mascota-stage") as HTMLElement;
    fireEvent.pointerDown(stage);
    expect(mockEngine.pet).toHaveBeenCalledTimes(1);
  });

  it("NO llama pet() cuando interactive=false", () => {
    renderMascota({ interactive: false });
    const stage = document.querySelector(".mascota-stage") as HTMLElement;
    fireEvent.pointerDown(stage);
    expect(mockEngine.pet).not.toHaveBeenCalled();
  });

  it("llama setMouseNorm en pointerMove cuando interactive=true", () => {
    renderMascota({ interactive: true });
    const stage = document.querySelector(".mascota-stage") as HTMLElement;
    fireEvent.pointerMove(stage, { clientX: 100, clientY: 100 });
    expect(mockEngine.setMouseNorm).toHaveBeenCalled();
  });

  it("resetea cursor a (0,0) en pointerLeave", () => {
    renderMascota({ interactive: true });
    const stage = document.querySelector(".mascota-stage") as HTMLElement;
    fireEvent.pointerLeave(stage);
    expect(mockEngine.setMouseNorm).toHaveBeenCalledWith(0, 0);
  });

  it("NO llama setMouseNorm en pointerMove cuando interactive=false", () => {
    renderMascota({ interactive: false });
    const stage = document.querySelector(".mascota-stage") as HTMLElement;
    fireEvent.pointerMove(stage, { clientX: 50, clientY: 50 });
    expect(mockEngine.setMouseNorm).not.toHaveBeenCalled();
  });
});

describe("<Mascota /> — tamaño CSS", () => {
  beforeEach(() => vi.clearAllMocks());

  it("aplica el tamaño correcto con size=1 (280×340 px)", () => {
    renderMascota({ size: 1 });
    const stage = document.querySelector(".mascota-stage") as HTMLElement;
    expect(stage.style.width).toBe("280px");
    expect(stage.style.height).toBe("340px");
  });

  it("aplica el tamaño correcto con size=2 (560×680 px)", () => {
    renderMascota({ size: 2 });
    const stage = document.querySelector(".mascota-stage") as HTMLElement;
    expect(stage.style.width).toBe("560px");
    expect(stage.style.height).toBe("680px");
  });
});
