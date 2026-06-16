"""alerts.py — Alertas nativas del OS para Itzel.

ES: Envía notificaciones del sistema operativo cuando se superan los umbrales
    configurados. 100% local — nada sale del equipo (principio #1).

    Disparadores configurados en MonitoringConfig:
      - RAM del proceso > alert_ram_pct % de la RAM total
      - Agente sin respuesta > alert_agent_timeout_s segundos
      - Errores consecutivos del modelo ≥ alert_consecutive_errors
      - Disco libre < alert_disk_free_gb GB

    Backend de notificación (en orden de preferencia):
      1. plyer  — multiplataforma (Win/Mac/Linux), requiere instalación
      2. subprocess OS-nativo:
           Windows → PowerShell Toast (no requiere deps)
           macOS   → osascript
           Linux   → notify-send
      3. Solo log — si ninguno de los anteriores funciona

EN: Sends OS desktop notifications when configured thresholds are exceeded.
    Graceful fallback: plyer → native subprocess → log-only.

Uso / Usage:
    mgr = get_alert_manager()
    mgr.check_ram(ram_pct=85.0)          # dispara si > 80%
    mgr.check_disk(free_gb=0.8)          # dispara si < 1 GB
    mgr.record_model_error()             # cuenta errores consecutivos
    mgr.record_model_success()           # resetea el contador
    mgr.check_agent_timeout(started_at=t0)
"""

from __future__ import annotations

import logging
import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("itzel.alerts")

# ─── nivel de alerta ──────────────────────────────────────────────────────────

_ALERT_TITLE = "Itzel"

# Evitar spam: no repetir la misma alerta en menos de N segundos.
_COOLDOWN_S = 300   # 5 minutos entre alertas del mismo tipo


# ─── backend de notificación ──────────────────────────────────────────────────

def _notify_plyer(title: str, message: str) -> bool:
    """Intenta enviar notificación con plyer. Devuelve True si tuvo éxito."""
    try:
        from plyer import notification
        notification.notify(title=title, message=message, app_name="Itzel",
                            timeout=8)
        return True
    except Exception:
        return False


def _notify_native(title: str, message: str) -> bool:
    """Notificación nativa via subprocess según el OS. Sin deps externas."""
    system = platform.system()
    try:
        if system == "Windows":
            # PowerShell Toast — funciona en Windows 10+ sin admin.
            ps_script = (
                f"[Windows.UI.Notifications.ToastNotificationManager, "
                f"Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; "
                f"$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02; "
                f"$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template); "
                f"$xml.GetElementsByTagName('text')[0].AppendChild($xml.CreateTextNode('{title}')) | Out-Null; "
                f"$xml.GetElementsByTagName('text')[1].AppendChild($xml.CreateTextNode('{message}')) | Out-Null; "
                f"$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
                f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Itzel').Show($toast)"
            )
            subprocess.run(
                ["powershell", "-NonInteractive", "-Command", ps_script],
                capture_output=True, timeout=5,
            )
            return True

        elif system == "Darwin":
            # macOS — osascript siempre disponible.
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{message}" with title "{title}"'],
                capture_output=True, timeout=5,
            )
            return True

        elif system == "Linux":
            # Linux — notify-send (libnotify), disponible en la mayoría de distros.
            subprocess.run(
                ["notify-send", "--app-name=Itzel", title, message],
                capture_output=True, timeout=5,
            )
            return True

    except Exception:
        pass
    return False


def _send_notification(title: str, message: str) -> None:
    """Envía la notificación usando el mejor backend disponible."""
    if not _notify_plyer(title, message):
        if not _notify_native(title, message):
            # Fallback final: solo log — nunca falla.
            logger.warning("[ALERTA] %s — %s", title, message)


# ─── manager de alertas ───────────────────────────────────────────────────────

@dataclass
class _AlertState:
    """Estado interno para evitar spam de alertas."""
    last_sent: float = 0.0              # epoch del último envío


class AlertManager:
    """Gestiona los 4 disparadores de alerta. Thread-safe."""

    def __init__(
        self,
        enabled: bool = True,
        ram_pct_threshold: float = 80.0,
        agent_timeout_s: int = 120,
        consecutive_errors_threshold: int = 3,
        disk_free_gb_threshold: float = 1.0,
    ) -> None:
        self._enabled = enabled
        self._ram_pct   = ram_pct_threshold
        self._timeout_s = agent_timeout_s
        self._err_limit = consecutive_errors_threshold
        self._disk_gb   = disk_free_gb_threshold

        self._lock = threading.Lock()
        self._states: dict[str, _AlertState] = {}
        self._consecutive_errors: int = 0

    # ── helpers de cooldown ───────────────────────────────────────────────────

    def _cooldown_ok(self, key: str) -> bool:
        """True si podemos enviar la alerta (pasó el cooldown o es la primera)."""
        state = self._states.setdefault(key, _AlertState())
        now = time.time()
        if now - state.last_sent >= _COOLDOWN_S:
            state.last_sent = now
            return True
        return False

    def _fire(self, key: str, message: str) -> None:
        """Dispara la alerta si no está en cooldown. Async en thread daemon."""
        if not self._enabled:
            logger.debug("[ALERTA silenciada] %s — %s", key, message)
            return
        with self._lock:
            if not self._cooldown_ok(key):
                return
        # Enviar en thread daemon para no bloquear el event loop de FastAPI.
        t = threading.Thread(
            target=_send_notification,
            args=(_ALERT_TITLE, message),
            daemon=True,
        )
        t.start()
        logger.info("Alerta enviada: %s — %s", key, message)

    # ── disparadores ─────────────────────────────────────────────────────────

    def check_ram(self, ram_pct: float) -> bool:
        """Dispara alerta si ram_pct supera el umbral. Devuelve True si disparó."""
        if ram_pct >= self._ram_pct:
            msg = (
                f"Uso de RAM al {ram_pct:.0f}% "
                f"(umbral: {self._ram_pct:.0f}%). "
                "Considera cerrar otras aplicaciones."
            )
            self._fire("ram_high", msg)
            return True
        return False

    def check_agent_timeout(self, started_at: float) -> bool:
        """Dispara alerta si el agente lleva más de timeout_s segundos sin responder."""
        elapsed = time.time() - started_at
        if elapsed >= self._timeout_s:
            msg = (
                f"Un agente lleva {elapsed:.0f}s sin responder "
                f"(umbral: {self._timeout_s}s). "
                "Puede estar procesando una tarea compleja."
            )
            self._fire("agent_timeout", msg)
            return True
        return False

    def record_model_error(self) -> bool:
        """Cuenta un error del modelo. Dispara alerta al llegar al umbral."""
        with self._lock:
            self._consecutive_errors += 1
            count = self._consecutive_errors
        if count >= self._err_limit:
            msg = (
                f"{count} errores consecutivos del modelo. "
                "Verifica que el backend esté corriendo: itzel status"
            )
            self._fire("model_errors", msg)
            return True
        return False

    def record_model_success(self) -> None:
        """Resetea el contador de errores consecutivos tras una respuesta exitosa."""
        with self._lock:
            self._consecutive_errors = 0

    def check_disk(self, free_gb: Optional[float] = None) -> bool:
        """Dispara alerta si el disco libre cae bajo el umbral.

        Si free_gb es None, lo mide con shutil (sin psutil).
        """
        if free_gb is None:
            free_gb = _disk_free_gb()
        if free_gb is not None and free_gb < self._disk_gb:
            msg = (
                f"Espacio libre en disco: {free_gb:.1f} GB "
                f"(umbral: {self._disk_gb:.1f} GB). "
                "Considera liberar espacio o borrar modelos antiguos."
            )
            self._fire("disk_low", msg)
            return True
        return False

    def check_all(self) -> dict[str, bool]:
        """Comprueba RAM y disco usando psutil/shutil. Útil para el ticker periódico."""
        try:
            import psutil
            proc    = psutil.Process()
            ram_mb  = proc.memory_info().rss / (1024 * 1024)
            total   = psutil.virtual_memory().total / (1024 * 1024)
            ram_pct = (ram_mb / total * 100) if total > 0 else 0.0
        except Exception:
            ram_pct = 0.0

        return {
            "ram":  self.check_ram(ram_pct),
            "disk": self.check_disk(),
        }

    @property
    def consecutive_errors(self) -> int:
        with self._lock:
            return self._consecutive_errors


# ─── helpers ──────────────────────────────────────────────────────────────────

def _disk_free_gb() -> Optional[float]:
    """Espacio libre en el home del usuario (GB). Sin psutil."""
    try:
        import shutil
        from pathlib import Path
        usage = shutil.disk_usage(Path.home())
        return usage.free / (1024 ** 3)
    except Exception:
        return None


# ─── singleton global ─────────────────────────────────────────────────────────

_manager: Optional[AlertManager] = None
_manager_lock = threading.Lock()


def get_alert_manager() -> AlertManager:
    """Devuelve el singleton global del manager de alertas. Thread-safe."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                try:
                    from ..config import config
                    mon = config.monitoring
                    _manager = AlertManager(
                        enabled=mon.enabled and mon.alerts_enabled,
                        ram_pct_threshold=mon.alert_ram_pct,
                        agent_timeout_s=mon.alert_agent_timeout_s,
                        consecutive_errors_threshold=mon.alert_consecutive_errors,
                        disk_free_gb_threshold=mon.alert_disk_free_gb,
                    )
                except Exception:
                    _manager = AlertManager()
    return _manager
