import {
  useRef,
  useEffect,
  useState,
  useCallback,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { SpriteEngine, type MoodKey } from '@itzel/mascota';

// ─── tipos ────────────────────────────────────────────────────────────────────

export interface MascotaProps {
  /** Estado de ánimo del ajolote. Default: 'idle'. */
  mood?: MoodKey;
  /**
   * Factor de escala visual. El canvas nativo es 280×340 px (28×34 celdas × 10 px).
   * CSS width = 280 * size. Default: 1.5 → 420 px de ancho.
   */
  size?: number;
  /** Habilita pet() al hacer clic y el seguimiento ocular del cursor. Default: true. */
  interactive?: boolean;
  /** Callback cuando el bubble debe mostrar/ocultar texto (opcional). */
  onBubbleChange?: (text: string | null) => void;
}

// ─── constantes ───────────────────────────────────────────────────────────────

const CELL_SIZE = 10;
const SPRITE_W  = 28 * CELL_SIZE; // 280 px nativos
const SPRITE_H  = 34 * CELL_SIZE; // 340 px nativos

// Textos del bubble por mood (para los puntos animados)
const BUBBLE_HAS_DOTS: ReadonlySet<MoodKey> = new Set(['work', 'think']);

// ─── componente ──────────────────────────────────────────────────────────────

export function Mascota({
  mood       = 'idle',
  size       = 1.5,
  interactive = true,
  onBubbleChange,
}: MascotaProps) {
  const spriteRef = useRef<HTMLCanvasElement>(null);
  const fxRef     = useRef<HTMLCanvasElement>(null);
  const stageRef  = useRef<HTMLDivElement>(null);
  const engineRef = useRef<SpriteEngine | null>(null);

  const [bubble, setBubble] = useState<string | null>(null);
  // dots animados: '.', '..', '...' — tick cada 350 ms
  const [dotCount, setDotCount] = useState(0);

  const cssW = SPRITE_W * size;
  const cssH = SPRITE_H * size;

  // ── instanciar motor ───────────────────────────────────────────────────────
  useEffect(() => {
    const cv = spriteRef.current;
    const fx = fxRef.current;
    if (!cv || !fx) return;

    const engine = new SpriteEngine({
      spriteCanvas: cv,
      fxCanvas:     fx,
      cellSize:     CELL_SIZE,
      onBubble: (text) => {
        setBubble(text);
        onBubbleChange?.(text);
      },
    });

    engine.setMood(mood);
    engine.start();
    engineRef.current = engine;

    // Recalcular canvas FX cuando el contenedor cambia de tamaño
    const ro = new ResizeObserver(() => engine.resizeFx());
    ro.observe(fx);

    return () => {
      ro.disconnect();
      engine.dispose();
      engineRef.current = null;
    };
  // Solo se monta/desmonta una vez — mood se sincroniza por separado
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── sincronizar mood con el motor ─────────────────────────────────────────
  useEffect(() => {
    engineRef.current?.setMood(mood);
  }, [mood]);

  // ── ticker de puntos animados ─────────────────────────────────────────────
  useEffect(() => {
    if (!bubble || !BUBBLE_HAS_DOTS.has(mood)) {
      setDotCount(0);
      return;
    }
    const id = setInterval(() => setDotCount(n => (n + 1) % 4), 350);
    return () => clearInterval(id);
  }, [bubble, mood]);

  // ── mouse tracking ────────────────────────────────────────────────────────
  const handlePointerMove = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (!interactive) return;
    const r = e.currentTarget.getBoundingClientRect();
    const nx = ((e.clientX - r.left)  / r.width  - 0.5) * 2;
    const ny = ((e.clientY - r.top)   / r.height - 0.5) * 2;
    engineRef.current?.setMouseNorm(nx, ny);
  }, [interactive]);

  const handlePointerLeave = useCallback(() => {
    engineRef.current?.setMouseNorm(0, 0);
  }, []);

  // ── pet al hacer clic ─────────────────────────────────────────────────────
  const handlePointerDown = useCallback(() => {
    if (!interactive) return;
    engineRef.current?.pet();
  }, [interactive]);

  // ── render ────────────────────────────────────────────────────────────────

  const bubbleVisible = bubble !== null;
  const dotsStr = bubbleVisible && BUBBLE_HAS_DOTS.has(mood)
    ? '.'.repeat(dotCount)
    : '';

  return (
    <div
      className="mascota-wrap"
      style={{ width: cssW, height: cssH + 56 /* 56 px para el bubble */ }}
    >
      {/* Bubble de diálogo */}
      <div
        className={`mascota-bubble${bubbleVisible ? ' mascota-bubble--show' : ''}`}
        aria-live="polite"
        aria-atomic="true"
      >
        {bubble}
        {dotsStr && <span className="mascota-dots" aria-hidden="true">{dotsStr}</span>}
      </div>

      {/* Stage: sprite + fx overlay */}
      <div
        ref={stageRef}
        className="mascota-stage"
        style={{ width: cssW, height: cssH }}
        onPointerMove={handlePointerMove}
        onPointerLeave={handlePointerLeave}
        onPointerDown={handlePointerDown}
      >
        {/* Glow de fondo */}
        <div className="mascota-glow" aria-hidden="true" />

        {/* Sombra proyectada */}
        <div className="mascota-shadow" aria-hidden="true" />

        {/* Canvas del sprite — CSS escala el canvas nativo */}
        <canvas
          ref={spriteRef}
          className="mascota-sprite"
          style={{ width: cssW, height: cssH }}
          title="¡Acaríciame!"
          aria-label="Itzel, ajolote asistente"
        />

        {/* Canvas de partículas FX (overlay absoluto) */}
        <canvas
          ref={fxRef}
          className="mascota-fx"
          aria-hidden="true"
        />
      </div>
    </div>
  );
}
