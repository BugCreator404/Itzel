/**
 * Tests para useChatHistory — navegación de historial estilo terminal.
 */

import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useChatHistory } from "./useChatHistory";

// ─── helpers ──────────────────────────────────────────────────────────────────

/** Crea un hook fresco con un historial inicial dado. */
function setup(initial: string[] = []) {
  return renderHook(() => useChatHistory(initial));
}

// ─── suite ────────────────────────────────────────────────────────────────────

describe("useChatHistory", () => {
  describe("estado inicial", () => {
    it("empieza con historial vacío si no se pasa semilla", () => {
      const { result } = setup();
      expect(result.current.size()).toBe(0);
    });

    it("acepta semilla inicial", () => {
      const { result } = setup(["hola", "mundo"]);
      expect(result.current.size()).toBe(2);
    });
  });

  describe("pushHistory", () => {
    it("agrega mensajes al historial", () => {
      const { result } = setup();
      act(() => result.current.pushHistory("primer mensaje"));
      expect(result.current.size()).toBe(1);
    });

    it("ignora strings vacíos", () => {
      const { result } = setup();
      act(() => result.current.pushHistory(""));
      act(() => result.current.pushHistory("  "));
      // Solo el segundo es non-empty string (spaces not empty string)
      // wait — "" es falsy, pero "  " no lo es, se guarda
      expect(result.current.size()).toBe(1);
    });

    it("no guarda duplicados consecutivos", () => {
      const { result } = setup();
      act(() => result.current.pushHistory("hola"));
      act(() => result.current.pushHistory("hola"));
      expect(result.current.size()).toBe(1);
    });

    it("guarda mensajes distintos en orden", () => {
      const { result } = setup();
      act(() => result.current.pushHistory("primero"));
      act(() => result.current.pushHistory("segundo"));
      expect(result.current.size()).toBe(2);
    });

    it("permite el mismo mensaje no-consecutivo", () => {
      const { result } = setup();
      act(() => result.current.pushHistory("hola"));
      act(() => result.current.pushHistory("mundo"));
      act(() => result.current.pushHistory("hola"));
      expect(result.current.size()).toBe(3);
    });
  });

  describe("navigateUp", () => {
    it("devuelve null si el historial está vacío", () => {
      const { result } = setup();
      let val: string | null = null;
      act(() => { val = result.current.navigateUp(); });
      expect(val).toBeNull();
    });

    it("devuelve el último mensaje al primer ↑", () => {
      const { result } = setup(["a", "b", "c"]);
      let val: string | null = null;
      act(() => { val = result.current.navigateUp(); });
      expect(val).toBe("c");
    });

    it("sigue retrocediendo con más ↑", () => {
      const { result } = setup(["a", "b", "c"]);
      let v1: string | null = null;
      let v2: string | null = null;
      let v3: string | null = null;
      act(() => { v1 = result.current.navigateUp(); });
      act(() => { v2 = result.current.navigateUp(); });
      act(() => { v3 = result.current.navigateUp(); });
      expect(v1).toBe("c");
      expect(v2).toBe("b");
      expect(v3).toBe("a");
    });

    it("no pasa del primer elemento (clamp)", () => {
      const { result } = setup(["a"]);
      let v1: string | null = null;
      let v2: string | null = null;
      act(() => { v1 = result.current.navigateUp(); });
      act(() => { v2 = result.current.navigateUp(); });
      expect(v1).toBe("a");
      expect(v2).toBe("a");
    });
  });

  describe("navigateDown", () => {
    it("devuelve null si el cursor ya está al final", () => {
      const { result } = setup(["a", "b"]);
      let val: string | null = null;
      act(() => { val = result.current.navigateDown(); });
      expect(val).toBeNull();
    });

    it("avanza hacia adelante tras navegar atrás", () => {
      const { result } = setup(["a", "b", "c"]);
      // Subir dos veces: cursor en "b"
      act(() => { result.current.navigateUp(); }); // "c"
      act(() => { result.current.navigateUp(); }); // "b"
      let val: string | null = null;
      act(() => { val = result.current.navigateDown(); }); // "c"
      expect(val).toBe("c");
    });

    it("devuelve null al llegar al final (señal de input vacío)", () => {
      const { result } = setup(["a", "b"]);
      act(() => { result.current.navigateUp(); }); // "b"
      act(() => { result.current.navigateUp(); }); // "a"
      act(() => { result.current.navigateDown(); }); // "b"
      let val: string | null = null;
      act(() => { val = result.current.navigateDown(); }); // fin → null
      expect(val).toBeNull();
    });
  });

  describe("resetHistory", () => {
    it("resetea el cursor al final", () => {
      const { result } = setup(["a", "b", "c"]);
      // Navegar hasta el inicio
      act(() => { result.current.navigateUp(); });
      act(() => { result.current.navigateUp(); });
      act(() => { result.current.navigateUp(); });
      // Resetear
      act(() => { result.current.resetHistory(); });
      // ↓ desde el final debe devolver null (ya estamos al final)
      let val: string | null = null;
      act(() => { val = result.current.navigateDown(); });
      expect(val).toBeNull();
    });

    it("después de reset, ↑ devuelve el último elemento", () => {
      const { result } = setup(["a", "b", "c"]);
      act(() => { result.current.navigateUp(); }); // "c"
      act(() => { result.current.navigateUp(); }); // "b"
      act(() => { result.current.resetHistory(); });
      let val: string | null = null;
      act(() => { val = result.current.navigateUp(); }); // "c" de nuevo
      expect(val).toBe("c");
    });
  });

  describe("flujo completo (simula envío de mensajes)", () => {
    it("push → reset → navega historial correctamente", () => {
      const { result } = setup();

      // Usuario envía tres mensajes
      act(() => {
        result.current.pushHistory("primero");
        result.current.resetHistory();
      });
      act(() => {
        result.current.pushHistory("segundo");
        result.current.resetHistory();
      });
      act(() => {
        result.current.pushHistory("tercero");
        result.current.resetHistory();
      });

      expect(result.current.size()).toBe(3);

      // Navega hacia atrás
      const values: Array<string | null> = [];
      act(() => { values.push(result.current.navigateUp()); });   // "tercero"
      act(() => { values.push(result.current.navigateUp()); });   // "segundo"
      act(() => { values.push(result.current.navigateUp()); });   // "primero"
      act(() => { values.push(result.current.navigateUp()); });   // "primero" (clamp)

      expect(values).toEqual(["tercero", "segundo", "primero", "primero"]);

      // Navega hacia adelante
      const fwd: Array<string | null> = [];
      act(() => { fwd.push(result.current.navigateDown()); }); // "segundo"
      act(() => { fwd.push(result.current.navigateDown()); }); // "tercero"
      act(() => { fwd.push(result.current.navigateDown()); }); // null (fin)

      expect(fwd).toEqual(["segundo", "tercero", null]);
    });
  });
});
