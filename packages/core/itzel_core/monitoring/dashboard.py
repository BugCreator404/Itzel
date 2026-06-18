"""dashboard.py — Dashboard de monitoreo local de Itzel.

ES: Router FastAPI montado en /dashboard. Sirve una app web con:
      - HTML vanilla + JS puro (sin CDN, funciona sin internet — principio #1)
      - SSE (/dashboard/stream) para métricas en tiempo real
      - Gráfica canvas de tokens/s últimos 5 min
      - Alertas de RAM > 80%
      - Estado de componentes (modelo, voz, BD, MCP)
      - Log de acciones del sistema

    Accesible en: http://localhost:7432/dashboard
    Devuelve 503 cuando monitoring.enabled=False.

EN: FastAPI router at /dashboard. Serves a no-CDN, offline-capable monitoring
    UI with SSE-based real-time metrics. Returns 503 when monitoring is off.
"""

from __future__ import annotations

import asyncio
import json
import platform
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ..monitoring.alerts import _disk_free_gb, get_alert_manager
from ..monitoring.metrics import get_collector

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_ITZEL_PINK  = "#f9a8d4"
_ITZEL_TEAL  = "#4ecdc4"
_ITZEL_BG    = "#0a0514"
_ITZEL_CARD  = "#1a0828"
_ITZEL_MUTED = "#9890b8"


# ─── utilidades ───────────────────────────────────────────────────────────────

def _monitoring_enabled() -> bool:
    try:
        from ..config import config
        return config.monitoring.enabled
    except Exception:
        return True


def _503() -> Response:
    return Response(
        content='{"detail":"monitoring desactivado — itzel config monitoring.enabled true"}',
        status_code=503,
        media_type="application/json",
    )


# ─── HTML de la app ───────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Itzel — Monitor local</title>
<style>
  :root {
    --pink:  #f9a8d4;
    --teal:  #4ecdc4;
    --bg:    #0a0514;
    --card:  #1a0828;
    --muted: #9890b8;
    --text:  #e8e4f4;
    --border:#2d1a40;
    --ok:    #4ade80;
    --warn:  #facc15;
    --err:   #f87171;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: system-ui, sans-serif; font-size: 14px;
    min-height: 100vh; padding: 1.5rem;
  }
  h1 { color: var(--pink); font-size: 1.3rem; margin-bottom: 0.25rem; }
  .subtitle { color: var(--muted); font-size: 0.8rem; margin-bottom: 1.5rem; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 1rem;
    margin-bottom: 1.5rem;
  }
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 0.75rem; padding: 1rem 1.25rem;
  }
  .card-title {
    color: var(--muted); font-size: 0.75rem; text-transform: uppercase;
    letter-spacing: 0.08em; margin-bottom: 0.5rem;
  }
  .metric { font-size: 1.8rem; font-weight: 700; color: var(--pink); }
  .metric-unit { font-size: 0.85rem; color: var(--muted); margin-left: 0.3rem; }
  .sub { font-size: 0.78rem; color: var(--muted); margin-top: 0.2rem; }

  /* barra de RAM */
  .bar-bg {
    height: 8px; background: var(--border); border-radius: 4px;
    margin-top: 0.6rem; overflow: hidden;
  }
  .bar-fill {
    height: 100%; border-radius: 4px;
    background: var(--teal); transition: width 0.5s ease, background 0.3s;
  }
  .bar-fill.warn { background: var(--warn); }
  .bar-fill.err  { background: var(--err);  }

  /* gráfica canvas */
  .chart-card { grid-column: 1 / -1; }
  canvas { width: 100%; height: 160px; display: block; }

  /* estado de componentes */
  .comp-list { display: flex; flex-direction: column; gap: 0.4rem; }
  .comp-row {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 0.82rem;
  }
  .badge {
    padding: 0.15rem 0.55rem; border-radius: 9999px; font-size: 0.72rem;
    font-weight: 600;
  }
  .badge.ok   { background: #14532d; color: var(--ok);  }
  .badge.warn { background: #713f12; color: var(--warn); }
  .badge.err  { background: #7f1d1d; color: var(--err);  }

  /* log de acciones */
  .log-area {
    background: #0d0820; border: 1px solid var(--border);
    border-radius: 0.5rem; padding: 0.75rem;
    font-family: monospace; font-size: 0.76rem; color: var(--muted);
    max-height: 160px; overflow-y: auto; white-space: pre-wrap;
  }

  /* punto live */
  .live-dot {
    display: inline-block; width: 8px; height: 8px;
    background: var(--ok); border-radius: 50%;
    animation: pulse 1.4s ease-in-out infinite;
    margin-right: 0.4rem;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; } 50% { opacity: 0.3; }
  }
  .alert-banner {
    background: #7f1d1d; border: 1px solid var(--err);
    border-radius: 0.5rem; padding: 0.6rem 1rem;
    color: var(--err); font-size: 0.82rem; margin-bottom: 1rem;
    display: none;
  }
  .ts { color: var(--muted); font-size: 0.72rem; }
</style>
</head>
<body>
<h1>🦎 Itzel — Monitor local</h1>
<p class="subtitle"><span class="live-dot"></span>Actualización en tiempo real &middot; 100% local</p>

<div id="alert-banner" class="alert-banner"></div>

<div class="grid">
  <!-- tokens/s -->
  <div class="card">
    <div class="card-title">Tokens / segundo</div>
    <div><span class="metric" id="tps-now">—</span><span class="metric-unit">tok/s</span></div>
    <div class="sub">Media 5 min: <span id="tps-avg">—</span> tok/s</div>
  </div>

  <!-- latencia -->
  <div class="card">
    <div class="card-title">Latencia de respuesta</div>
    <div><span class="metric" id="lat-p50">—</span><span class="metric-unit">ms p50</span></div>
    <div class="sub">p95: <span id="lat-p95">—</span> ms &nbsp;·&nbsp; p99: <span id="lat-p99">—</span> ms</div>
  </div>

  <!-- RAM -->
  <div class="card">
    <div class="card-title">RAM del proceso</div>
    <div><span class="metric" id="ram-mb">—</span><span class="metric-unit">MB</span></div>
    <div class="bar-bg"><div class="bar-fill" id="ram-bar" style="width:0%"></div></div>
    <div class="sub" id="ram-sub">—</div>
  </div>

  <!-- cache -->
  <div class="card">
    <div class="card-title">Cache hits / misses</div>
    <div><span class="metric" id="cache-rate">—</span><span class="metric-unit">% hit rate</span></div>
    <div class="sub">Hits: <span id="cache-hits">—</span> &nbsp;·&nbsp; Misses: <span id="cache-misses">—</span></div>
  </div>

  <!-- conversaciones + acciones -->
  <div class="card">
    <div class="card-title">Actividad</div>
    <div><span class="metric" id="conv-active">—</span><span class="metric-unit">convs. activas</span></div>
    <div class="sub">Acciones del sistema: <span id="sys-actions">—</span></div>
  </div>

  <!-- disco -->
  <div class="card">
    <div class="card-title">Disco libre</div>
    <div><span class="metric" id="disk-free">—</span><span class="metric-unit">GB</span></div>
    <div class="sub" id="disk-sub">—</div>
  </div>
</div>

<!-- gráfica tokens/s -->
<div class="card chart-card" style="margin-bottom:1rem">
  <div class="card-title">Tokens/s — últimos 5 min</div>
  <canvas id="tps-chart"></canvas>
</div>

<!-- estado de componentes + log -->
<div class="grid">
  <div class="card">
    <div class="card-title">Estado de componentes</div>
    <div class="comp-list" id="comp-list">
      <div class="comp-row"><span>Cargando…</span></div>
    </div>
  </div>
  <div class="card" style="grid-column: span 2">
    <div class="card-title">Log de acciones <span class="ts" id="log-ts"></span></div>
    <div class="log-area" id="log-area">—</div>
  </div>
</div>

<script>
// ── mini chart (canvas puro, sin librerías externas) ──────────────────────────
const canvas = document.getElementById('tps-chart');
const ctx    = canvas.getContext('2d');
let tpsData  = [];   // [{ts, value}]

function drawChart() {
  const W = canvas.offsetWidth, H = 160;
  canvas.width = W * devicePixelRatio; canvas.height = H * devicePixelRatio;
  ctx.scale(devicePixelRatio, devicePixelRatio);
  ctx.clearRect(0, 0, W, H);
  if (tpsData.length < 2) return;

  const vals = tpsData.map(d => d.value);
  const maxV = Math.max(...vals) * 1.15 || 1;
  const pad  = {t:12, r:8, b:20, l:36};
  const cW   = W - pad.l - pad.r;
  const cH   = H - pad.t - pad.b;

  // grid lines
  ctx.strokeStyle = '#2d1a40'; ctx.lineWidth = 0.5;
  [0.25, 0.5, 0.75, 1].forEach(f => {
    const y = pad.t + cH * (1 - f);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + cW, y); ctx.stroke();
    ctx.fillStyle = '#9890b8'; ctx.font = '10px system-ui'; ctx.textAlign = 'right';
    ctx.fillText((maxV * f).toFixed(1), pad.l - 4, y + 3);
  });

  // área + línea
  const pts = tpsData.map((d, i) => ({
    x: pad.l + (i / (tpsData.length - 1)) * cW,
    y: pad.t + cH * (1 - d.value / maxV),
  }));
  ctx.beginPath();
  pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
  ctx.strokeStyle = '#f9a8d4'; ctx.lineWidth = 2; ctx.stroke();

  // relleno degradado
  const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + cH);
  grad.addColorStop(0, 'rgba(249,168,212,0.25)');
  grad.addColorStop(1, 'rgba(249,168,212,0)');
  ctx.lineTo(pts[pts.length-1].x, pad.t + cH);
  ctx.lineTo(pts[0].x, pad.t + cH);
  ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
}

// ── SSE ───────────────────────────────────────────────────────────────────────
const es = new EventSource('/dashboard/stream');

es.addEventListener('metrics', e => {
  const d = JSON.parse(e.data);

  // tokens/s
  setText('tps-now', d.model.tokens_per_second_last.toFixed(1));
  setText('tps-avg', d.model.tokens_per_second_avg.toFixed(1));

  // latencia
  setText('lat-p50', d.latency_ms.p50.toFixed(0));
  setText('lat-p95', d.latency_ms.p95.toFixed(0));
  setText('lat-p99', d.latency_ms.p99.toFixed(0));

  // RAM
  const pct = d.ram.pct;
  setText('ram-mb', d.ram.mb.toFixed(0));
  setText('ram-sub', `${pct.toFixed(1)}% de la RAM total`);
  const bar = document.getElementById('ram-bar');
  bar.style.width = pct + '%';
  bar.className = 'bar-fill' + (pct > 90 ? ' err' : pct > 80 ? ' warn' : '');

  // alerta RAM
  const banner = document.getElementById('alert-banner');
  if (pct > 80) {
    banner.style.display = 'block';
    banner.textContent = `⚠ RAM al ${pct.toFixed(0)}% — considera cerrar otras aplicaciones.`;
  } else { banner.style.display = 'none'; }

  // cache
  setText('cache-rate', (d.cache.hit_rate * 100).toFixed(1));
  setText('cache-hits',   d.cache.hits);
  setText('cache-misses', d.cache.misses);

  // actividad
  setText('conv-active', d.conversations.active);
  setText('sys-actions', d.system_actions.total);
});

es.addEventListener('history', e => {
  tpsData = JSON.parse(e.data);
  drawChart();
});

es.addEventListener('components', e => {
  const comps = JSON.parse(e.data);
  const list  = document.getElementById('comp-list');
  list.innerHTML = comps.map(c => `
    <div class="comp-row">
      <span>${c.name}</span>
      <span class="badge ${c.status}">${c.label}</span>
    </div>
  `).join('');
});

es.addEventListener('disk', e => {
  const d = JSON.parse(e.data);
  if (d.free_gb !== null) {
    setText('disk-free', d.free_gb.toFixed(1));
    setText('disk-sub', d.free_gb < 1 ? '⚠ Espacio bajo' : 'Espacio disponible');
  }
});

es.addEventListener('actions_log', e => {
  const lines = JSON.parse(e.data);
  document.getElementById('log-area').textContent = lines.join('\\n') || '(sin acciones registradas)';
  document.getElementById('log-ts').textContent = new Date().toLocaleTimeString('es-MX');
});

es.onerror = () => {
  document.querySelector('.live-dot').style.background = '#f87171';
};

// ── helpers ───────────────────────────────────────────────────────────────────
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

window.addEventListener('resize', drawChart);
</script>
</body>
</html>
"""


# ─── endpoints ────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_home() -> Response:
    if not _monitoring_enabled():
        return _503()
    return HTMLResponse(_HTML)


@router.get("/stream")
async def dashboard_stream() -> Response:
    """SSE: emite métricas, historial, componentes, disco y log cada 2 segundos."""
    if not _monitoring_enabled():
        return _503()

    async def _generator() -> AsyncGenerator[str, None]:
        while True:
            try:
                col = get_collector()
                snap = col.snapshot()

                # evento: métricas principales
                yield f"event: metrics\ndata: {json.dumps(snap.to_dict(), ensure_ascii=False)}\n\n"

                # evento: historial para la gráfica
                history = col.tps_history(points=60)
                yield f"event: history\ndata: {json.dumps(history)}\n\n"

                # evento: estado de componentes
                comps = await _component_status()
                yield f"event: components\ndata: {json.dumps(comps, ensure_ascii=False)}\n\n"

                # evento: disco (poco frecuente pero útil)
                free_gb = _disk_free_gb()
                yield f"event: disk\ndata: {json.dumps({'free_gb': round(free_gb, 2) if free_gb else None})}\n\n"

                # evento: log de acciones recientes
                log_lines = _recent_action_log()
                yield f"event: actions_log\ndata: {json.dumps(log_lines, ensure_ascii=False)}\n\n"

                # disparar alertas si aplica
                mgr = get_alert_manager()
                mgr.check_ram(snap.ram_pct)
                if free_gb is not None:
                    mgr.check_disk(free_gb)

            except Exception as exc:
                yield f"event: error\ndata: {json.dumps({'msg': str(exc)})}\n\n"

            await asyncio.sleep(2)

    return Response(
        content=_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/snapshot")
async def dashboard_snapshot() -> Response:
    """JSON: instantánea de métricas (sin SSE). Útil para scripts/CLI."""
    if not _monitoring_enabled():
        return _503()
    snap = get_collector().snapshot()
    return JSONResponse(snap.to_dict())


# ─── estado de componentes ────────────────────────────────────────────────────

async def _component_status() -> list[dict]:
    """Verifica el estado de cada componente del sistema."""
    components: list[dict] = []

    # Modelo IA
    try:
        from ..models import get_adapter
        adapter = await get_adapter()
        info    = await adapter.info()
        ok      = info.loaded
        components.append({
            "name":   f"Modelo ({info.name})",
            "status": "ok" if ok else "warn",
            "label":  "activo" if ok else "no cargado",
        })
    except Exception:
        components.append({"name": "Modelo IA", "status": "err", "label": "error"})

    # Base de datos
    try:
        from ..db import get_db
        db = get_db()
        db.execute("SELECT 1")
        components.append({"name": "BD SQLCipher", "status": "ok", "label": "cifrada"})
    except Exception:
        components.append({"name": "BD SQLCipher", "status": "err", "label": "error"})

    # Voz (whisper disponible)
    try:
        import faster_whisper  # noqa: F401
        components.append({"name": "Voz (Whisper)", "status": "ok", "label": "disponible"})
    except ImportError:
        components.append({"name": "Voz (Whisper)", "status": "warn", "label": "no instalado"})

    # MCP
    try:
        from ..config import config
        mcp_count = len([s for s in config.mcp_servers if s.enabled])
        components.append({
            "name":   "MCP",
            "status": "ok" if mcp_count else "warn",
            "label":  f"{mcp_count} servidor(es)" if mcp_count else "sin servidores",
        })
    except Exception:
        components.append({"name": "MCP", "status": "warn", "label": "—"})

    # Sistema operativo
    components.append({
        "name":   "Plataforma",
        "status": "ok",
        "label":  platform.system(),
    })

    return components


# ─── log de acciones recientes ────────────────────────────────────────────────

def _recent_action_log(n: int = 30) -> list[str]:
    """Lee las últimas N líneas del actions.log. Sin lanzar si no existe."""
    try:
        log_path = Path.home() / ".itzel" / "logs" / "actions.log"
        if not log_path.exists():
            return []
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []
