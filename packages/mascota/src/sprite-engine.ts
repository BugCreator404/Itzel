/* ============================================================
   ITZEL — Motor de sprite pixel art (puro TS, sin React).
   Grid de 28×34 celdas, celda S px.  Cada frame redibuja las
   tres capas (branquias / cola / cabeza) con offsets enteros
   para lograr movimiento 8-bit clásico.

   Uso:
     const eng = new SpriteEngine({ spriteCanvas, fxCanvas });
     eng.setMood('idle');
     eng.start();
     // ... más tarde:
     eng.dispose();
   ============================================================ */

// ─── tipos públicos ───────────────────────────────────────────────────────────

export type MoodKey = 'idle' | 'work' | 'think' | 'happy' | 'wave' | 'sleep';

export interface SpriteEngineOptions {
  /** Canvas donde se dibuja el sprite (28×34 celdas). */
  spriteCanvas: HTMLCanvasElement;
  /** Canvas overlay para partículas FX. */
  fxCanvas: HTMLCanvasElement;
  /** Tamaño en px de cada celda. Default 10. */
  cellSize?: number;
  /** Llamado cuando el bubble debe mostrar/ocultar texto. */
  onBubble?: (text: string | null) => void;
}

// ─── constantes del sprite ────────────────────────────────────────────────────

const Wc = 28; // celdas de ancho
const Hc = 34; // celdas de alto

const HEAD_ROWS: readonly string[] = [
  "............................",
  "............................",
  "............................",
  "............................",
  "........XXXXXXXXXXXX........",
  ".......XPPPPPPPPPPPPX.......",
  ".......XPPPPPPPPPPPPX.......",
  ".......XPPPPPPPPPPPPX.......",
  ".......XPPPPPPPPPPPPX.......",
  ".......XPPPPPPPPPPPPX.......",
  ".......XPPPPPPPPPPPPX.......",
  ".......XPPPPPPPPPPPPX.......",
  ".......XPPPPPPPPPPPPX.......",
  ".......XPPPPPPPPPPPPX.......",
  ".......XPPPPPPPPPPPPX.......",
  "........XXXXXXXXXXXX........",
];

// Branquia izquierda — el motor espeja al lado derecho (x → 27-x)
const GILL_L: ReadonlyArray<{ x: number; y: number; c: string }> = [
  { x: 4, y: 5,  c: 'M' }, { x: 5, y: 5,  c: 'M' },
  { x: 4, y: 6,  c: 'M' }, { x: 5, y: 6,  c: 'M' },
  { x: 3, y: 5,  c: 'X' }, { x: 3, y: 6,  c: 'X' },
  { x: 4, y: 4,  c: 'X' }, { x: 5, y: 4,  c: 'X' }, { x: 6, y: 6,  c: 'X' },
  { x: 2, y: 9,  c: 'M' }, { x: 3, y: 9,  c: 'M' },
  { x: 2, y: 10, c: 'M' }, { x: 3, y: 10, c: 'M' },
  { x: 1, y: 9,  c: 'X' }, { x: 1, y: 10, c: 'X' },
  { x: 2, y: 8,  c: 'X' }, { x: 3, y: 8,  c: 'X' },
  { x: 4, y: 10, c: 'X' }, { x: 5, y: 10, c: 'X' }, { x: 6, y: 10, c: 'X' },
  { x: 3, y: 12, c: 'M' }, { x: 4, y: 12, c: 'M' },
  { x: 3, y: 13, c: 'M' }, { x: 4, y: 13, c: 'M' },
  { x: 2, y: 12, c: 'X' }, { x: 2, y: 13, c: 'X' },
  { x: 3, y: 14, c: 'X' }, { x: 4, y: 14, c: 'X' },
  { x: 5, y: 12, c: 'X' }, { x: 6, y: 12, c: 'X' },
];

function buildTail(): ReadonlyArray<{ x: number; y: number; c: string }> {
  const out: { x: number; y: number; c: string }[] = [];
  const rows: ReadonlyArray<readonly [number, number]> = [
    [11, 16], [11, 16], [11, 16], [12, 15],
    [12, 15], [13, 14], [13, 14], [13, 14],
  ];
  let y = 16;
  for (const row of rows) {
    const a = row[0];
    const b = row[1];
    out.push({ x: a - 1, y, c: 'X' });
    for (let x = a; x <= b; x++) out.push({ x, y, c: 'P' });
    out.push({ x: b + 1, y, c: 'X' });
    y++;
  }
  out.push({ x: 13, y, c: 'X' });
  out.push({ x: 14, y, c: 'X' });
  return out;
}

const TAIL = buildTail();

function buildHeadCells(): ReadonlyArray<{ x: number; y: number; c: string }> {
  const cells: { x: number; y: number; c: string }[] = [];
  for (let ry = 0; ry < HEAD_ROWS.length; ry++) {
    const row = HEAD_ROWS[ry] ?? '';
    for (let x = 0; x < Wc; x++) {
      const ch = row[x];
      if (ch && ch !== '.') cells.push({ x, y: ry, c: ch });
    }
  }
  return cells;
}

const HEAD_CELLS = buildHeadCells();

// Paleta de colores oficial
const PAL: Readonly<Record<string, string>> = {
  P: '#e7b3e6',
  M: '#c389c0',
  X: '#241019',
  B: '#f49ec4',
  T: '#b06a9a',
};

// ─── definición de moods ──────────────────────────────────────────────────────

interface MoodDef {
  label: string;
  say: string;
  bobA: number; bobS: number;
  tailA: number; tailS: number;
  gillA: number; gillS: number;
  perk: number;
  blink: boolean;
  closed?: boolean;
  eyes?: 'scan' | 'up' | 'happy' | 'look';
  hop?: boolean;
  blush?: boolean;
  rock?: boolean;
  dots?: boolean;
  particles?: 'work' | 'think' | 'happy' | 'wave' | 'sleep';
}

const MOODS: Readonly<Record<MoodKey, MoodDef>> = {
  idle:  { label: 'Idle',        say: '',                bobA: 1, bobS: 1.6, tailA: 1.3, tailS: 2.2, gillA: 1, gillS: 1.8, perk:  0, blink: true },
  work:  { label: 'Trabajando',  say: 'Trabajando',      bobA: 1, bobS: 3.4, tailA: 2.2, tailS: 3.8, gillA: 1, gillS: 2.8, perk:  0, blink: true, eyes: 'scan', dots: true,  particles: 'work'  },
  think: { label: 'Pensando',    say: 'Mmm… déjame ver', bobA: 0, bobS: 1.0, tailA: 0.6, tailS: 1.0, gillA: 1, gillS: 1.2, perk:  2, blink: true, eyes: 'up',   dots: true,  particles: 'think' },
  happy: { label: '¡Feliz!',     say: '¡Yay! ✨',         bobA: 0, bobS: 0,   tailA: 2.4, tailS: 4.6, gillA: 2, gillS: 5.0, perk:  1, blink: true, eyes: 'happy', hop: true, blush: true, particles: 'happy' },
  wave:  { label: '¡Hola!',      say: '¡Holaaa! 👋',      bobA: 1, bobS: 2.2, tailA: 1.4, tailS: 3.0, gillA: 2, gillS: 4.4, perk:  1, blink: true, eyes: 'look', rock: true, blush: true, particles: 'wave'  },
  sleep: { label: 'Dormir',      say: '',                bobA: 1, bobS: 0.7, tailA: 0.3, tailS: 0.6, gillA: 1, gillS: 0.7, perk: -1, blink: false, closed: true, particles: 'sleep' },
};

// ─── partículas ───────────────────────────────────────────────────────────────

interface Particle {
  t: 'star' | 'sq' | 'dot' | 'ring' | 'z';
  x: number; y: number;
  vx: number; vy: number;
  g: number;
  life: number; dec: number;
  sz: number; c: string;
  rot: number; spin?: number;
  drift?: number;
  txt?: string;
}

const PINKS: readonly string[] = [
  '#f9a8d4', '#ffd9ec', '#c4b5fd', '#a7f3d0', '#ffb3c1', '#fde68a',
];
function randPink(): string {
  return PINKS[Math.floor(Math.random() * PINKS.length)] ?? '#f9a8d4';
}

// ─── clase principal ──────────────────────────────────────────────────────────

export class SpriteEngine {
  private readonly cv: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly fxCv: HTMLCanvasElement;
  private readonly fctx: CanvasRenderingContext2D;
  private readonly S: number;
  // No usamos `?:` para evitar problemas con exactOptionalPropertyTypes
  private readonly onBubble: ((text: string | null) => void) | undefined;

  private mood: MoodKey = 'idle';
  private petUntil = 0;
  private squash = 0;
  private mxN = 0;
  private myN = 0;
  private rafId: number | null = null;
  private t0 = 0;

  private nextBlink = 0;
  private blinkEnd = 0;

  private parts: Particle[] = [];
  private zc = 0;
  private cw = 0;
  private ch = 0;
  private dpr = 1;

  constructor(opts: SpriteEngineOptions) {
    this.S = opts.cellSize ?? 10;
    this.onBubble = opts.onBubble;

    this.cv = opts.spriteCanvas;
    this.cv.width  = Wc * this.S;
    this.cv.height = Hc * this.S;
    const ctx = this.cv.getContext('2d');
    if (!ctx) throw new Error('No se pudo obtener contexto 2D del canvas sprite');
    this.ctx = ctx;
    this.ctx.imageSmoothingEnabled = false;

    this.fxCv = opts.fxCanvas;
    const fctx = this.fxCv.getContext('2d');
    if (!fctx) throw new Error('No se pudo obtener contexto 2D del canvas FX');
    this.fctx = fctx;
    this.fctx.imageSmoothingEnabled = false;

    this.resizeFx();
  }

  // ── API pública ─────────────────────────────────────────────────────────────

  setMood(mood: MoodKey): void {
    this.mood = mood;
    this.petUntil = 0;
    this._emitBubble();
  }

  getMood(): MoodKey { return this.mood; }

  pet(): void {
    this.petUntil = performance.now() + 1600;
    this._emitBubble();
    this._burst(20);
    this.squash = 1;
  }

  setMouseNorm(x: number, y: number): void {
    this.mxN = Math.min(1, Math.max(-1, x));
    this.myN = Math.min(1, Math.max(-1, y));
  }

  start(): void {
    if (this.rafId !== null) return;
    this.t0 = performance.now();
    this.nextBlink = this.t0 + 1200;
    this.rafId = requestAnimationFrame(this._frame);
  }

  stop(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }

  dispose(): void {
    this.stop();
    this.parts = [];
    this.ctx.clearRect(0, 0, this.cv.width, this.cv.height);
    this.fctx.clearRect(0, 0, this.fxCv.width, this.fxCv.height);
  }

  resizeFx(): void {
    const r = this.fxCv.getBoundingClientRect();
    this.cw  = r.width  || this.cv.width;
    this.ch  = r.height || this.cv.height;
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.fxCv.width  = this.cw  * this.dpr;
    this.fxCv.height = this.ch * this.dpr;
    this.fctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.fctx.imageSmoothingEnabled = false;
  }

  // ── helpers internos ────────────────────────────────────────────────────────

  private _activeMood(): MoodKey {
    return performance.now() < this.petUntil ? 'happy' : this.mood;
  }

  private _emitBubble(): void {
    if (!this.onBubble) return;
    const m = MOODS[this._activeMood()];
    this.onBubble(m.say || null);
  }

  private _cell(x: number, y: number, c: string): void {
    const col = PAL[c];
    if (!col) return;
    this.ctx.fillStyle = col;
    this.ctx.fillRect(Math.round(x) * this.S, Math.round(y) * this.S, this.S, this.S);
  }

  // ── parpadeo ────────────────────────────────────────────────────────────────

  private _isBlinking(now: number, m: MoodDef): boolean {
    if (!m.blink) return false;
    if (now > this.nextBlink && this.blinkEnd === 0) this.blinkEnd = now + 120;
    if (this.blinkEnd) {
      if (now > this.blinkEnd) {
        this.blinkEnd = 0;
        this.nextBlink = now + 1700 + Math.random() * 3000;
        if (Math.random() < 0.22) this.nextBlink = now + 240;
        return false;
      }
      return true;
    }
    return false;
  }

  // ── cara ────────────────────────────────────────────────────────────────────

  private _drawEyes(oy: number, m: MoodDef, now: number, t: number): void {
    const closed = m.closed === true || this._isBlinking(now, m);

    if (closed) {
      this._cell(10, 10 + oy, 'X'); this._cell(11, 10 + oy, 'X');
      this._cell(16, 10 + oy, 'X'); this._cell(17, 10 + oy, 'X');
      return;
    }

    if (m.eyes === 'happy') {
      this._cell(10, 10 + oy, 'X'); this._cell(11,  9 + oy, 'X');
      this._cell(16,  9 + oy, 'X'); this._cell(17, 10 + oy, 'X');
      return;
    }

    let lx = 0, ly = 0;
    if      (m.eyes === 'scan') lx = Math.round(Math.sin(t * 3));
    else if (m.eyes === 'up')   ly = -1;
    else if (m.eyes === 'look') lx = -1;
    else {
      lx = Math.round(Math.sin(t * 0.6) * 0.7);
      ly = Math.round(Math.sin(t * 0.5) * 0.4);
    }
    lx = Math.min(1, Math.max(-1, lx + Math.round(this.mxN)));
    ly = Math.min(1, Math.max(-1, ly + Math.round(this.myN * 0.8)));

    // ojo izquierdo
    this._cell(10 + lx,  9 + ly + oy, 'X');
    this._cell(11 + lx,  9 + ly + oy, 'X');
    this._cell(10 + lx, 10 + ly + oy, 'X');
    this._cell(11 + lx, 10 + ly + oy, 'X');
    // ojo derecho
    this._cell(16 + lx,  9 + ly + oy, 'X');
    this._cell(17 + lx,  9 + ly + oy, 'X');
    this._cell(16 + lx, 10 + ly + oy, 'X');
    this._cell(17 + lx, 10 + ly + oy, 'X');
  }

  private _drawMouth(oy: number, m: MoodDef): void {
    if (m.eyes === 'happy') {
      this._cell(11, 12 + oy, 'X'); this._cell(16, 12 + oy, 'X');
      this._cell(12, 13 + oy, 'X'); this._cell(15, 13 + oy, 'X');
      this._cell(13, 14 + oy, 'X'); this._cell(14, 14 + oy, 'X');
      this._cell(13, 13 + oy, 'T'); this._cell(14, 13 + oy, 'T');
    } else if (m.closed === true) {
      this._cell(13, 13 + oy, 'X'); this._cell(14, 13 + oy, 'X');
    } else {
      this._cell(12, 12 + oy, 'X'); this._cell(15, 12 + oy, 'X');
      this._cell(13, 13 + oy, 'X'); this._cell(14, 13 + oy, 'X');
    }
  }

  private _drawBlush(oy: number): void {
    this._cell(9,  12 + oy, 'B'); this._cell(9,  13 + oy, 'B');
    this._cell(18, 12 + oy, 'B'); this._cell(18, 13 + oy, 'B');
  }

  // ── partículas ───────────────────────────────────────────────────────────────

  private _burst(n: number): void {
    const ox = this.cw * 0.5;
    const oy = this.ch * 0.28;
    for (let i = 0; i < n; i++) {
      const a  = -Math.PI / 2 + (Math.random() - 0.5) * 2.4;
      const sp = 1.4 + Math.random() * 3.2;
      this.parts.push({
        t: Math.random() < 0.5 ? 'star' : 'sq',
        x: ox + (Math.random() - 0.5) * 70, y: oy,
        vx: Math.cos(a) * sp, vy: Math.sin(a) * sp - 1,
        g: 0.05, life: 1, dec: 0.012 + Math.random() * 0.01,
        sz: 4 + Math.random() * 6, c: randPink(),
        spin: (Math.random() - 0.5) * 0.3, rot: Math.random() * Math.PI * 2,
      });
    }
  }

  private _emit(kind: NonNullable<MoodDef['particles']>): void {
    const TAU = Math.PI * 2;
    if (kind === 'happy') {
      if (Math.random() < 0.26) this._burst(2);
    } else if (kind === 'sleep') {
      if (Math.random() < 0.018) {
        this.zc++;
        this.parts.push({
          t: 'z', txt: 'Z',
          x: this.cw * 0.62, y: this.ch * 0.22,
          vx: 0.25, vy: -0.5, g: 0, life: 1, dec: 0.006,
          sz: 13 + (this.zc % 3) * 7, c: '#c4b5fd', rot: 0,
          drift: Math.random() * TAU,
        });
      }
    } else if (kind === 'work') {
      if (Math.random() < 0.12) {
        this.parts.push({
          t: 'dot',
          x: this.cw * (0.35 + Math.random() * 0.3), y: this.ch * 0.42,
          vx: (Math.random() - 0.5) * 0.4, vy: -0.8 - Math.random() * 0.5,
          g: 0, life: 1, dec: 0.02,
          sz: 4 + Math.random() * 2,
          c: Math.random() < 0.5 ? '#c4b5fd' : '#a7f3d0',
          rot: 0,
        });
      }
    } else if (kind === 'wave') {
      if (Math.random() < 0.18) {
        this.parts.push({
          t: 'star',
          x: this.cw * 0.76, y: this.ch * (0.24 + Math.random() * 0.22),
          vx: 0.6 + Math.random() * 0.8, vy: -0.3 - Math.random() * 0.6,
          g: 0.01, life: 1, dec: 0.02,
          sz: 4 + Math.random() * 3, c: randPink(),
          rot: Math.random() * TAU, spin: 0.1,
        });
      }
    } else if (kind === 'think') {
      if (Math.random() < 0.045) {
        this.parts.push({
          t: 'ring',
          x: this.cw * (0.6 + Math.random() * 0.05), y: this.ch * 0.18,
          vx: 0.2, vy: -0.5, g: 0, life: 1, dec: 0.016,
          sz: 5 + Math.random() * 7, c: '#c4b5fd', rot: 0,
        });
      }
    }
  }

  private _pstar(x: number, y: number, r: number, rot: number, c: string): void {
    const TAU = Math.PI * 2;
    this.fctx.save();
    this.fctx.translate(x, y);
    this.fctx.rotate(rot);
    this.fctx.fillStyle = c;
    this.fctx.beginPath();
    for (let i = 0; i < 5; i++) {
      const a = -Math.PI / 2 + i * TAU / 5;
      this.fctx.lineTo(Math.cos(a) * r, Math.sin(a) * r);
      const a2 = a + TAU / 10;
      this.fctx.lineTo(Math.cos(a2) * r * 0.45, Math.sin(a2) * r * 0.45);
    }
    this.fctx.closePath();
    this.fctx.fill();
    this.fctx.restore();
  }

  private _drawParts(): void {
    this.fctx.clearRect(0, 0, this.cw, this.ch);
    for (let i = this.parts.length - 1; i >= 0; i--) {
      const p = this.parts[i];
      // noUncheckedIndexedAccess: verificar que p existe
      if (!p) continue;

      p.vy += p.g;
      p.x  += p.vx;
      p.y  += p.vy;
      p.life -= p.dec;
      p.rot  += p.spin ?? 0;
      if (p.drift !== undefined) { p.drift += 0.05; p.x += Math.sin(p.drift) * 0.4; }

      if (p.life <= 0) { this.parts.splice(i, 1); continue; }

      this.fctx.globalAlpha = Math.min(1, Math.max(0, p.life));

      if (p.t === 'star') {
        this._pstar(p.x, p.y, p.sz, p.rot, p.c);
      } else if (p.t === 'sq') {
        this.fctx.save();
        this.fctx.translate(p.x, p.y);
        this.fctx.rotate(p.rot);
        this.fctx.fillStyle = p.c;
        this.fctx.fillRect(-p.sz / 2, -p.sz / 2, p.sz, p.sz);
        this.fctx.restore();
      } else if (p.t === 'dot') {
        this.fctx.fillStyle = p.c;
        this.fctx.fillRect(p.x - p.sz / 2, p.y - p.sz / 2, p.sz, p.sz);
      } else if (p.t === 'ring') {
        this.fctx.strokeStyle = p.c;
        this.fctx.lineWidth = 2.5;
        this.fctx.beginPath();
        this.fctx.arc(p.x, p.y, p.sz, 0, Math.PI * 2);
        this.fctx.stroke();
      } else if (p.t === 'z' && p.txt) {
        this.fctx.fillStyle = p.c;
        this.fctx.font = `900 ${p.sz}px "Press Start 2P",monospace`;
        this.fctx.fillText(p.txt, p.x, p.y);
      }
    }
    this.fctx.globalAlpha = 1;
  }

  // ── loop principal ───────────────────────────────────────────────────────────

  private _frame = (now: number): void => {
    const t = (now - this.t0) / 1000;
    const m = MOODS[this._activeMood()];

    if (this.squash > 0) this.squash = Math.max(0, this.squash - 0.05);

    let oy = Math.round(Math.sin(t * m.bobS) * m.bobA);
    if (m.hop === true) oy += -Math.round(Math.abs(Math.sin(t * 5.0)) * 2);
    if (this.squash > 0.5) oy += 1;

    this.ctx.clearRect(0, 0, this.cv.width, this.cv.height);

    // branquias (detrás)
    const gdy  = Math.round(Math.sin(t * m.gillS)       * m.gillA) - m.perk;
    const gdy2 = Math.round(Math.sin(t * m.gillS + 1.6) * m.gillA) - m.perk;
    for (const p of GILL_L) {
      this._cell(p.x,      p.y + gdy  + oy, p.c);
      this._cell(27 - p.x, p.y + gdy2 + oy, p.c);
    }

    // cola
    for (const p of TAIL) {
      const ry  = p.y - 16;
      const amp = m.tailA * Math.min(1.4, Math.max(0, ry / 3));
      const dx  = Math.round(Math.sin(t * m.tailS + ry * 0.5) * amp);
      this._cell(p.x + dx, p.y + oy, p.c);
    }

    // cabeza
    for (const p of HEAD_CELLS) this._cell(p.x, p.y + oy, p.c);

    // cara
    if (m.blush === true) this._drawBlush(oy);
    this._drawEyes(oy, m, now, t);
    this._drawMouth(oy, m);

    // inclinación suave en wave
    this.cv.style.transform = m.rock === true
      ? `rotate(${(Math.sin(t * 3) * 4).toFixed(2)}deg)`
      : 'rotate(0deg)';

    if (m.particles) this._emit(m.particles);
    this._drawParts();

    this.rafId = requestAnimationFrame(this._frame);
  };
}
