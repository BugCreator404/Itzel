"""itzel_core.monitoring — Monitoreo local de Itzel.

ES: Todo el monitoreo es 100% local. Ningún dato sale del equipo (principio #1).
    Cuando monitoring.enabled=False, el colector es un no-op y el dashboard
    devuelve 503 — 0 bytes de datos, 0 archivos de log.

EN: All monitoring is 100% local. No data leaves the machine. When disabled,
    the collector is a no-op and the dashboard returns 503.
"""

from .metrics import MetricsCollector, get_collector
from .alerts import AlertManager, get_alert_manager

__all__ = [
    "MetricsCollector",
    "get_collector",
    "AlertManager",
    "get_alert_manager",
]
