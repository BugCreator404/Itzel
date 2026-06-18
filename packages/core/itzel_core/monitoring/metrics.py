"""metrics.py — Colector de métricas local de Itzel.

ES: Singleton thread-safe que recopila las 6 métricas del sistema en una
    ventana deslizante de N segundos (default: 5 min). No usa OpenTelemetry
    SDK para evitar dependencias de backends externos (principio #1). La API
    es compatible con OTel (counter/gauge/histogram) por si se migra en el futuro.

    Métricas recopiladas:
      - tokens_per_second   → velocidad de inferencia del modelo
      - response_latency_ms → latencia de respuesta (histograma → p50/p95/p99)
      - ram_usage_mb        → uso de RAM del proceso Python
      - active_conversations → conversaciones activas en este momento
      - cache_hits / cache_misses → hits y misses del cache L1+L2
      - system_actions      → acciones del sistema ejecutadas (counter)

EN: Thread-safe singleton collecting 6 system metrics in a sliding window.
    OTel-compatible API without the external backend requirement.

Uso / Usage:
    from itzel_core.monitoring import get_collector

    col = get_collector()
    col.record_tokens(tokens=150, elapsed_s=1.2)
    col.record_latency(latency_ms=840)
    col.cache_hit()
    snapshot = col.snapshot()
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

# Importación diferida de psutil — opcional, no bloquea si no está instalado.
# Deferred import of psutil — optional, does not block if not installed.


# ─── tipos de datos ───────────────────────────────────────────────────────────

@dataclass
class _Sample:
    """Un punto de dato con timestamp (epoch seconds)."""
    ts: float
    value: float


@dataclass
class PercentileResult:
    p50: float
    p95: float
    p99: float
    count: int
    mean: float


@dataclass
class MetricsSnapshot:
    """Instantánea de todas las métricas en un momento dado."""
    timestamp: float                    # epoch UTC
    window_s: int                       # segundos del historial incluido

    # Velocidad del modelo
    tokens_per_second_avg: float        # media en la ventana
    tokens_per_second_last: float       # último valor registrado

    # Latencia de respuesta
    latency: PercentileResult           # p50/p95/p99 en ms

    # RAM del proceso Python
    ram_mb: float                       # MB actuales del proceso
    ram_pct: float                      # % sobre la RAM total del sistema

    # Conversaciones activas
    active_conversations: int

    # Cache
    cache_hits: int
    cache_misses: int
    cache_hit_rate: float               # 0.0 – 1.0

    # Acciones del sistema
    system_actions_total: int           # acumulado desde que arrancó el proceso

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "window_s": self.window_s,
            "model": {
                "tokens_per_second_avg": round(self.tokens_per_second_avg, 2),
                "tokens_per_second_last": round(self.tokens_per_second_last, 2),
            },
            "latency_ms": {
                "p50": round(self.latency.p50, 1),
                "p95": round(self.latency.p95, 1),
                "p99": round(self.latency.p99, 1),
                "mean": round(self.latency.mean, 1),
                "count": self.latency.count,
            },
            "ram": {
                "mb": round(self.ram_mb, 1),
                "pct": round(self.ram_pct, 1),
            },
            "conversations": {
                "active": self.active_conversations,
            },
            "cache": {
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "hit_rate": round(self.cache_hit_rate, 3),
            },
            "system_actions": {
                "total": self.system_actions_total,
            },
        }


# ─── ventana deslizante ───────────────────────────────────────────────────────

class _SlidingWindow:
    """Colección de muestras con expiración automática por timestamp."""

    def __init__(self, window_s: int) -> None:
        self._window_s = window_s
        self._samples: deque[_Sample] = deque()

    def add(self, value: float) -> None:
        self._samples.append(_Sample(ts=time.time(), value=value))
        self._evict()

    def values(self) -> list[float]:
        self._evict()
        return [s.value for s in self._samples]

    def _evict(self) -> None:
        cutoff = time.time() - self._window_s
        while self._samples and self._samples[0].ts < cutoff:
            self._samples.popleft()

    def last(self) -> float:
        self._evict()
        return self._samples[-1].value if self._samples else 0.0

    def mean(self) -> float:
        vals = self.values()
        return sum(vals) / len(vals) if vals else 0.0


# ─── cálculo de percentiles ───────────────────────────────────────────────────

def _percentiles(values: list[float]) -> PercentileResult:
    if not values:
        return PercentileResult(p50=0.0, p95=0.0, p99=0.0, count=0, mean=0.0)
    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def _pct(p: float) -> float:
        idx = (p / 100.0) * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        frac = idx - lo
        return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

    return PercentileResult(
        p50=_pct(50),
        p95=_pct(95),
        p99=_pct(99),
        count=n,
        mean=sum(sorted_vals) / n,
    )


# ─── colector principal ───────────────────────────────────────────────────────

class MetricsCollector:
    """Singleton thread-safe. Obtenerlo siempre con get_collector()."""

    def __init__(self, window_s: int = 300, enabled: bool = True) -> None:
        self._enabled = enabled
        self._window_s = window_s
        self._lock = threading.Lock()

        # Ventanas deslizantes por métrica.
        self._tps:     _SlidingWindow = _SlidingWindow(window_s)
        self._latency: _SlidingWindow = _SlidingWindow(window_s)

        # Contadores simples (no necesitan ventana deslizante).
        self._cache_hits:   int = 0
        self._cache_misses: int = 0
        self._sys_actions:  int = 0

        # Gauge: valor puntual.
        self._active_conversations: int = 0

    # ── API de registro ───────────────────────────────────────────────────────

    def record_tokens(self, tokens: int, elapsed_s: float) -> None:
        """Registra una respuesta: tokens generados en elapsed_s segundos."""
        if not self._enabled or elapsed_s <= 0:
            return
        tps = tokens / elapsed_s
        with self._lock:
            self._tps.add(tps)

    def record_latency(self, latency_ms: float) -> None:
        """Registra la latencia de una respuesta en milisegundos."""
        if not self._enabled:
            return
        with self._lock:
            self._latency.add(latency_ms)

    def cache_hit(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._cache_hits += 1

    def cache_miss(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._cache_misses += 1

    def set_active_conversations(self, n: int) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._active_conversations = max(0, n)

    def increment_system_actions(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._sys_actions += 1

    # ── RAM (psutil, diferido) ────────────────────────────────────────────────

    def _ram_stats(self) -> tuple[float, float]:
        """Devuelve (ram_mb_proceso, ram_pct_sistema). Tolerante a falta de psutil."""
        try:
            import psutil
            proc = psutil.Process()
            ram_mb = proc.memory_info().rss / (1024 * 1024)
            total_mb = psutil.virtual_memory().total / (1024 * 1024)
            ram_pct = (ram_mb / total_mb * 100) if total_mb > 0 else 0.0
            return ram_mb, ram_pct
        except Exception:
            return 0.0, 0.0

    # ── snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> MetricsSnapshot:
        """Instantánea thread-safe de todas las métricas actuales."""
        with self._lock:
            tps_vals  = self._tps.values()
            lat_vals  = self._latency.values()
            tps_last  = self._tps.last()
            tps_mean  = sum(tps_vals) / len(tps_vals) if tps_vals else 0.0
            hits      = self._cache_hits
            misses    = self._cache_misses
            sys_act   = self._sys_actions
            active    = self._active_conversations

        total_cache = hits + misses
        hit_rate = hits / total_cache if total_cache > 0 else 0.0
        ram_mb, ram_pct = self._ram_stats()

        return MetricsSnapshot(
            timestamp=time.time(),
            window_s=self._window_s,
            tokens_per_second_avg=tps_mean,
            tokens_per_second_last=tps_last,
            latency=_percentiles(lat_vals),
            ram_mb=ram_mb,
            ram_pct=ram_pct,
            active_conversations=active,
            cache_hits=hits,
            cache_misses=misses,
            cache_hit_rate=hit_rate,
            system_actions_total=sys_act,
        )

    # ── historial para gráficas ────────────────────────────────────────────────

    def tps_history(self, points: int = 60) -> list[dict[str, float]]:
        """Lista de {ts, value} de tokens/s para la gráfica de tiempo real.

        Devuelve hasta `points` muestras de la ventana activa. El dashboard
        las usa para dibujar la gráfica de los últimos 5 minutos.
        """
        with self._lock:
            self._tps._evict()
            samples = list(self._tps._samples)

        if not samples:
            return []

        # Submuestrear uniformemente si hay más puntos que `points`.
        if len(samples) > points:
            step = len(samples) / points
            samples = [samples[int(i * step)] for i in range(points)]

        return [{"ts": s.ts, "value": round(s.value, 2)} for s in samples]


# ─── singleton global ─────────────────────────────────────────────────────────

_collector: MetricsCollector | None = None
_collector_lock = threading.Lock()


def get_collector() -> MetricsCollector:
    """Devuelve el singleton global del colector. Thread-safe.

    La primera llamada lo inicializa con la config activa.
    Subsequent calls return the same instance.
    """
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:           # double-checked locking
                try:
                    from ..config import config
                    mon = config.monitoring
                    _collector = MetricsCollector(
                        window_s=mon.metrics_window_s,
                        enabled=mon.enabled,
                    )
                except Exception:
                    # Fallback si config no está disponible (tests, CLI parcial).
                    _collector = MetricsCollector(window_s=300, enabled=True)
    return _collector
