"""Comando: itzel doctor

Verifica el estado de todos los componentes del sistema y muestra un diagnóstico
claro con indicadores ✓/✗ y acciones correctivas cuando algo falla.

Checks que realiza:
  1. Python 3.11+
  2. Modelo Itzel-1B disponible en disco (~900 MB)
  3. Ollama corriendo en localhost:11434
  4. Whisper STT descargado (~460 MB)
  5. BD SQLite cifrada accesible
  6. Espacio libre en disco (>= 1 GB recomendado)
  7. RAM disponible
  8. Backend FastAPI respondiendo en localhost:7432
  9. Dashboard de monitoreo accesible
 10. Logs estructurados activos
"""

from __future__ import annotations

import shutil
import socket
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..output import console

# ─── tipos ────────────────────────────────────────────────────────────────────

class _Status(StrEnum):
    OK   = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class _Check:
    name:   str
    status: _Status
    detail: str
    fix:    str = ""        # instrucción correctiva si status != OK


# ─── punto de entrada ─────────────────────────────────────────────────────────

def run(json_output: bool = False, quiet: bool = False) -> None:
    """Ejecuta todos los checks y muestra el diagnóstico."""
    console.print()
    console.print("[bold #f9a8d4]Itzel — diagnóstico del sistema[/]")
    console.print("[dim]" + "─" * 44 + "[/]")
    console.print()

    checks = _run_all_checks()

    if json_output:
        import json
        out = [
            {"name": c.name, "status": c.status.value, "detail": c.detail, "fix": c.fix}
            for c in checks
        ]
        console.print_json(json.dumps(out, ensure_ascii=False, indent=2))
        return

    _print_checks(checks, quiet)

    # Resumen
    n_ok   = sum(1 for c in checks if c.status == _Status.OK)
    n_warn = sum(1 for c in checks if c.status == _Status.WARN)
    n_fail = sum(1 for c in checks if c.status == _Status.FAIL)
    total  = len([c for c in checks if c.status != _Status.SKIP])

    console.print()
    if n_fail == 0 and n_warn == 0:
        console.print(f"[bold #4ecdc4]Todo en orden — {n_ok}/{total} checks pasaron.[/]")
    elif n_fail == 0:
        console.print(f"[bold #fbbf24]{n_ok}/{total} OK · {n_warn} advertencia(s).[/]")
    else:
        console.print(f"[bold #f87171]{n_fail} error(es) · {n_warn} advertencia(s) · {n_ok} OK.[/]")
        console.print("[dim]Revisa las instrucciones ↑ para resolver los fallos.[/]")
    console.print()


# ─── checks individuales ──────────────────────────────────────────────────────

def _run_all_checks() -> list[_Check]:
    return [
        _check_python(),
        _check_model_gguf(),
        _check_ollama(),
        _check_whisper(),
        _check_database(),
        _check_disk(),
        _check_ram(),
        _check_backend(),
        _check_dashboard(),
        _check_logs(),
    ]


def _check_python() -> _Check:
    v = sys.version_info
    ver_str = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 11):
        return _Check("Python 3.11+", _Status.OK, f"Python {ver_str}")
    return _Check(
        "Python 3.11+", _Status.FAIL,
        f"Python {ver_str} — versión insuficiente",
        fix="Instala Python 3.11+ desde https://python.org/downloads",
    )


def _check_model_gguf() -> _Check:
    models_dir = Path.home() / ".itzel" / "models"
    gguf_files = list(models_dir.glob("*.gguf")) if models_dir.exists() else []

    if not gguf_files:
        # Buscar también en el repo local (desarrollo)
        repo_gguf = Path(__file__).resolve().parents[5] / "models" / "itzel-1b" / "gguf"
        if repo_gguf.exists():
            gguf_files = list(repo_gguf.glob("*.gguf"))

    if gguf_files:
        total_mb = sum(f.stat().st_size for f in gguf_files) // (1024 * 1024)
        names    = ", ".join(f.name for f in gguf_files[:2])
        return _Check("Modelo Itzel-1B", _Status.OK, f"{names} ({total_mb} MB)")

    # Revisar si Ollama tiene modelos disponibles como alternativa
    if _port_open("localhost", 11434):
        return _Check(
            "Modelo Itzel-1B", _Status.WARN,
            "GGUF no encontrado — Ollama disponible como alternativa",
        )

    return _Check(
        "Modelo Itzel-1B", _Status.FAIL,
        "No hay modelo disponible (~900 MB requeridos)",
        fix="itzel model pull itzel-1b",
    )


def _check_ollama() -> _Check:
    if _port_open("localhost", 11434):
        try:
            import httpx
            r = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
            models = r.json().get("models", [])
            if models:
                names = ", ".join(m.get("name", "?") for m in models[:3])
                return _Check("Ollama", _Status.OK, f"corriendo · {len(models)} modelo(s): {names}")
            return _Check("Ollama", _Status.WARN, "corriendo pero sin modelos descargados",
                          fix="ollama pull llama3.2:3b")
        except Exception:
            return _Check("Ollama", _Status.WARN, "puerto 11434 abierto — API no responde")
    return _Check(
        "Ollama", _Status.WARN,
        "no detectado en localhost:11434",
        fix="Descarga desde https://ollama.ai — opcional si usas el modelo GGUF local",
    )


def _check_whisper() -> _Check:
    whisper_dir = Path.home() / ".itzel" / "models" / "whisper"
    has_model   = whisper_dir.exists() and any(whisper_dir.glob("*.bin"))

    if has_model:
        mb = sum(f.stat().st_size for f in whisper_dir.glob("*.bin")) // (1024 * 1024)
        return _Check("Whisper STT", _Status.OK, f"modelo descargado ({mb} MB)")

    try:
        import faster_whisper  # noqa: F401
        return _Check(
            "Whisper STT", _Status.WARN,
            "faster-whisper instalado pero modelo no descargado (~460 MB)",
            fix="itzel voice setup",
        )
    except ImportError:
        return _Check(
            "Whisper STT", _Status.WARN,
            "no instalado — la voz no estará disponible",
            fix="itzel voice setup",
        )


def _check_database() -> _Check:
    db_path = Path.home() / ".itzel" / "memory.db"
    if not db_path.exists():
        return _Check(
            "BD SQLite cifrada", _Status.WARN,
            "no existe aún — se crea al primer uso",
        )
    try:
        # Verificar que es un archivo SQLite/SQLCipher válido (magia en primeros 16 bytes)
        with db_path.open("rb") as fh:
            header = fh.read(16)
        # SQLCipher cifrado: los primeros bytes NO son "SQLite format 3\x00"
        is_cipher = header[:6] != b"SQLite"
        size_kb   = db_path.stat().st_size // 1024
        return _Check(
            "BD SQLite cifrada", _Status.OK,
            f"{'cifrada (SQLCipher)' if is_cipher else 'sin cifrado — ejecuta itzel setup'} · {size_kb} KB",
        )
    except Exception as exc:
        return _Check(
            "BD SQLite cifrada", _Status.FAIL,
            f"no se pudo leer: {exc}",
            fix="Borra ~/.itzel/memory.db y ejecuta itzel setup",
        )


def _check_disk() -> _Check:
    try:
        usage    = shutil.disk_usage(Path.home())
        free_gb  = usage.free  / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        used_pct = (usage.used / usage.total) * 100

        if free_gb < 1.0:
            return _Check(
                "Espacio en disco", _Status.FAIL,
                f"{free_gb:.1f} GB libres de {total_gb:.0f} GB — por debajo del mínimo",
                fix="Libera espacio o borra modelos con: itzel model list",
            )
        if free_gb < 5.0:
            return _Check(
                "Espacio en disco", _Status.WARN,
                f"{free_gb:.1f} GB libres de {total_gb:.0f} GB ({used_pct:.0f}% usado)",
            )
        return _Check(
            "Espacio en disco", _Status.OK,
            f"{free_gb:.1f} GB libres de {total_gb:.0f} GB",
        )
    except Exception:
        return _Check("Espacio en disco", _Status.SKIP, "no se pudo medir")


def _check_ram() -> _Check:
    try:
        import psutil
        vm       = psutil.virtual_memory()
        total_gb = vm.total    / (1024 ** 3)
        avail_gb = vm.available / (1024 ** 3)
        used_pct = vm.percent

        if avail_gb < 1.0:
            return _Check(
                "RAM disponible", _Status.FAIL,
                f"{avail_gb:.1f} GB libres de {total_gb:.1f} GB — RAM crítica",
                fix="Cierra aplicaciones para liberar memoria",
            )
        if avail_gb < 3.0:
            return _Check(
                "RAM disponible", _Status.WARN,
                f"{avail_gb:.1f} GB libres de {total_gb:.1f} GB ({used_pct:.0f}% en uso)",
            )
        return _Check(
            "RAM disponible", _Status.OK,
            f"{avail_gb:.1f} GB libres de {total_gb:.1f} GB",
        )
    except ImportError:
        # Fallback sin psutil
        try:
            import resource
            soft, _ = resource.getrlimit(resource.RLIMIT_AS)
            return _Check("RAM disponible", _Status.SKIP,
                          "psutil no instalado — pip install psutil para métricas de RAM")
        except Exception:
            return _Check("RAM disponible", _Status.SKIP,
                          "psutil no instalado — pip install psutil")


def _check_backend() -> _Check:
    if not _port_open("localhost", 7432):
        return _Check(
            "Backend FastAPI", _Status.WARN,
            "no está corriendo en localhost:7432",
            fix="itzel run   ó   uvicorn itzel_core.engine:app --port 7432",
        )
    try:
        import httpx
        r = httpx.get("http://localhost:7432/api/v1/health/", timeout=2.0)
        if r.status_code == 200:
            data = r.json()
            uptime = data.get("uptime_s", 0)
            return _Check(
                "Backend FastAPI", _Status.OK,
                f"online · uptime {_fmt_uptime(uptime)} · localhost:7432",
            )
        return _Check(
            "Backend FastAPI", _Status.WARN,
            f"responde con HTTP {r.status_code}",
        )
    except Exception as exc:
        return _Check(
            "Backend FastAPI", _Status.WARN,
            f"puerto abierto pero no responde: {exc}",
        )


def _check_dashboard() -> _Check:
    if not _port_open("localhost", 7432):
        return _Check(
            "Dashboard local", _Status.SKIP,
            "backend no disponible",
        )
    try:
        import httpx
        r = httpx.get("http://localhost:7432/dashboard/", timeout=2.0)
        if r.status_code == 200:
            return _Check(
                "Dashboard local", _Status.OK,
                "accesible en http://localhost:7432/dashboard",
            )
        if r.status_code == 503:
            return _Check(
                "Dashboard local", _Status.WARN,
                "monitoreo desactivado",
                fix="itzel config monitoring.enabled true",
            )
        return _Check("Dashboard local", _Status.WARN, f"HTTP {r.status_code}")
    except Exception:
        return _Check("Dashboard local", _Status.WARN, "no accesible")


def _check_logs() -> _Check:
    log_dir  = Path.home() / ".itzel" / "logs"
    itzel_log = log_dir / "itzel.log"
    if not log_dir.exists():
        return _Check(
            "Logs estructurados", _Status.WARN,
            "directorio de logs no existe aún — se crea al iniciar el backend",
        )
    if not itzel_log.exists():
        return _Check(
            "Logs estructurados", _Status.WARN,
            f"{log_dir} existe pero itzel.log aún no",
        )
    size_kb = itzel_log.stat().st_size // 1024
    files   = list(log_dir.glob("*.log"))
    return _Check(
        "Logs estructurados", _Status.OK,
        f"{len(files)} archivo(s) · itzel.log {size_kb} KB · {log_dir}",
    )


# ─── impresión ────────────────────────────────────────────────────────────────

_ICONS = {
    _Status.OK:   "[bold #4ecdc4]✓[/]",
    _Status.WARN: "[bold #fbbf24]![/]",
    _Status.FAIL: "[bold #f87171]✗[/]",
    _Status.SKIP: "[dim]·[/]",
}


def _print_checks(checks: list[_Check], quiet: bool) -> None:
    for c in checks:
        icon   = _ICONS[c.status]
        name   = f"[#e8e4f4]{c.name:<22}[/]"
        detail = f"[#9890b8]{c.detail}[/]" if c.status == _Status.SKIP else c.detail
        console.print(f"  {icon} {name} {detail}")
        if c.fix and not quiet:
            console.print(f"       [dim]→ {c.fix}[/]")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """True si el puerto está abierto (TCP connect)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m}m"
