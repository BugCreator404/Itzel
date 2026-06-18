"""Carga y valida itzel.config.json usando pydantic-settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# Ruta por orden de prioridad: proyecto local → home del usuario
_CONFIG_PATHS = [
    Path.cwd() / "itzel.config.json",
    Path.home() / ".itzel" / "itzel.config.json",
]


class VoiceConfig(BaseModel):
    enabled: bool = True
    stt_model: str = "whisper-small"
    tts_model: str = "kokoro"
    language: str = "es-MX"


class ModelConfig(BaseModel):
    active: str = "itzel-1b"
    context_length: int = 32768
    temperature: float = 0.7
    top_p: float = 0.95
    max_tokens: int = 2048


class SecurityConfig(BaseModel):
    sandbox_enabled: bool = True
    sandbox_cpu_limit: float = 0.5
    sandbox_ram_mb: int = 512
    sandbox_timeout_s: int = 30
    confirm_irreversible: bool = True


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7432
    dashboard_port: int = 7433
    workers: int = 1


class RateLimitConfig(BaseModel):
    model_rps: int = 10
    voice_rps: int = 5
    web_rps: int = 2


class DBConfig(BaseModel):
    # Ruta de la BD cifrada (None = ~/.itzel/memory.db por defecto en db.py).
    path: str | None = None
    wal: bool = True                       # WAL mode para concurrencia
    backup_dir: str = "data/backups"       # destino de los backups cifrados
    backup_weekly: bool = True             # backup automático semanal


class CacheConfig(BaseModel):
    enabled: bool = True
    l1_max: int = 100                      # entradas máximas del LRU en memoria
    l2_ttl_s: int = 86_400                 # TTL del cache persistente (24 h)
    summary_threshold_tokens: int = 4_000  # umbral para generar resumen episódico


class SkillsConfig(BaseModel):
    enabled: bool = True                   # sistema de skills on/off
    autoload: bool = True                  # cargar skills al arrancar el engine
    disabled: list[str] = Field(default_factory=list)  # skills bloqueadas por nombre


class MCPServerConfig(BaseModel):
    """Un servidor MCP externo. La API key NUNCA va aquí — vive en el keychain
    del OS bajo el servicio 'itzel', usuario 'mcp-<name>' (principio #8)."""
    name: str
    transport: Literal["sse", "stdio"] = "sse"
    url: str | None = None              # requerido para transport="sse"
    command: str | None = None          # requerido para transport="stdio"
    args: list[str] = Field(default_factory=list)
    auth: Literal["none", "api_key"] = "none"
    enabled: bool = True


class MonitoringConfig(BaseModel):
    """Configuración del monitoreo local — todo queda en el equipo (principio #1).

    Cuando enabled=False: 0 bytes de logs, dashboard devuelve 503, alertas
    silenciadas. El usuario puede cambiar esto con:
        itzel config monitoring.enabled false
    """
    enabled: bool = True
    # Dashboard accesible en http://localhost:<port>/dashboard
    # (montado en el mismo proceso del engine, no en dashboard_port separado)
    dashboard_port: int = 7433              # reservado para modo standalone futuro

    # Logs estructurados — rotación
    log_max_bytes: int = 100 * 1024 * 1024   # 100 MB por archivo
    log_retention_days: int = 7              # máx días antes de rotar

    # Alertas del OS (notificaciones nativas)
    alerts_enabled: bool = True
    alert_ram_pct: float = 80.0              # % de RAM que dispara alerta
    alert_agent_timeout_s: int = 120         # agente sin respuesta → alerta
    alert_consecutive_errors: int = 3        # errores del modelo → alerta
    alert_disk_free_gb: float = 1.0          # GB libres mínimos

    # Métricas en memoria (ventana deslizante)
    metrics_window_s: int = 300             # 5 min de historial en RAM


class ItzelConfig(BaseModel):
    version: str = "0.1.0"
    language: str = "es-MX"
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    db: DBConfig = Field(default_factory=DBConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    telemetry: bool = False  # siempre False — principio no negociable
    analytics: bool = False  # siempre False


def load_config() -> ItzelConfig:
    """Carga la config del primer archivo encontrado; crea defaults si no existe ninguno."""
    for path in _CONFIG_PATHS:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            # telemetry y analytics siempre False, sin importar lo que diga el archivo
            data["telemetry"] = False
            data["analytics"] = False
            return ItzelConfig.model_validate(data)

    # Sin config: usar defaults y escribir en home
    cfg = ItzelConfig()
    _save_default_config(cfg)
    return cfg


def _save_default_config(cfg: ItzelConfig) -> None:
    dest = Path.home() / ".itzel" / "itzel.config.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as f:
        json.dump(cfg.model_dump(), f, indent=2, ensure_ascii=False)


# Instancia global para importar en otros módulos
config = load_config()
