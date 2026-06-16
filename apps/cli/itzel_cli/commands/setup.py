"""Comando: itzel setup

Wizard de configuración inicial interactivo.
Guía al usuario en 5 pasos:

  Paso 1 — Verificar entorno (Python, directorios, dependencias)
  Paso 2 — Elegir idioma preferido
  Paso 3 — Elegir e iniciar backend (Ollama o llama.cpp)
  Paso 4 — Descargar modelo si es necesario
  Paso 5 — Verificar todo y mostrar resumen

Al terminar, Itzel está lista para usarse con `itzel chat`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import typer
from rich.prompt import Confirm, Prompt

from ..client import ItzelClient
from ..output import (
    console,
    err,
    hint,
    info,
    ok,
    print_header,
    warn,
)

# ─── directorios y archivos ───────────────────────────────────────────────────

_HOME_DIR    = Path.home() / ".itzel"
_CONFIG_FILE = _HOME_DIR / "itzel.config.json"
_MODELS_DIR  = _HOME_DIR / "models"
_LOGS_DIR    = _HOME_DIR / "logs"


# ─── punto de entrada ─────────────────────────────────────────────────────────

def run() -> None:
    """Wizard interactivo de configuración inicial."""
    print_header(
        "Itzel Setup 🦎",
        "Configura tu IA personal local en pocos pasos",
    )
    console.print()

    # ── Paso 1: Entorno ───────────────────────────────────────────────────────
    _step("1", "Verificando entorno")
    env_ok = _check_environment()
    if not env_ok:
        err("El entorno no cumple los requisitos mínimos.")
        raise typer.Exit(1)

    # ── Paso 2: Idioma ────────────────────────────────────────────────────────
    _step("2", "Idioma preferido")
    language = _choose_language()

    # ── Paso 3: Backend ───────────────────────────────────────────────────────
    _step("3", "Backend de modelo")
    backend = _choose_backend()

    # ── Paso 4: Modelo ────────────────────────────────────────────────────────
    _step("4", "Descargar modelo")
    _setup_model(backend)

    # ── Paso 5: Hotkey global ─────────────────────────────────────────────────
    _step("5", "Atajo de teclado global")
    hotkey = _choose_hotkey()

    # ── Paso 6: Voz ───────────────────────────────────────────────────────────
    _step("6", "Asistente de voz")
    voice_enabled = _setup_voice()

    # ── Paso 7: Escribir config y verificar ───────────────────────────────────
    _step("7", "Guardando configuración")
    _write_config(language=language, backend=backend, hotkey=hotkey,
                  voice_enabled=voice_enabled)
    _verify_setup()

    # ── Resumen final ─────────────────────────────────────────────────────────
    console.print()
    ok("¡Itzel está lista! 🦎")
    console.print()
    if voice_enabled:
        console.print(
            f"[bold #f9a8d4]Presiona {hotkey} para hablar conmigo.[/]"
        )
    else:
        console.print(
            f"[bold #f9a8d4]Presiona {hotkey} para abrir Itzel.[/]"
        )
    console.print()
    hint("Comandos para empezar:")
    hint("  itzel ask '¿Qué es un ajolote?'")
    hint("  itzel chat")
    hint("  itzel status")
    console.print()
    _launch_app()


# ─── pasos ────────────────────────────────────────────────────────────────────

def _check_environment() -> bool:
    """Verifica Python, crea directorios y comprueba dependencias clave."""
    all_ok = True

    # Python >= 3.11
    ver = sys.version_info
    if ver >= (3, 11):
        ok(f"Python {ver.major}.{ver.minor}.{ver.micro}")
    else:
        err(f"Python {ver.major}.{ver.minor} — se requiere 3.11+")
        all_ok = False

    # Crear directorios ~/.itzel/
    for d in [_HOME_DIR, _MODELS_DIR, _LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    ok(f"Directorio ~/.itzel/ listo")

    # httpx (requerido siempre)
    try:
        import httpx  # noqa: F401
        ok("httpx instalado")
    except ImportError:
        err("httpx no encontrado — instala con: pip install httpx")
        all_ok = False

    # rich (requerido siempre)
    try:
        import rich  # noqa: F401
        ok("rich instalado")
    except ImportError:
        err("rich no encontrado — instala con: pip install rich")
        all_ok = False

    # itzel-core (el backend Python)
    try:
        import itzel_core  # noqa: F401
        ok("itzel-core disponible")
    except ImportError:
        warn("itzel-core no instalado en este entorno")
        hint("  Instala con: pip install -e packages/core")

    return all_ok


def _choose_language() -> str:
    """Pregunta al usuario su idioma preferido."""
    console.print("[#9890b8]Elige el idioma de Itzel:[/]")
    console.print("  [bold #4ecdc4]1[/] Español (México)  [dim]← recomendado[/]")
    console.print("  [bold #4ecdc4]2[/] English (US)")
    console.print()

    choice = Prompt.ask(
        "[#f9a8d4]Idioma[/]",
        choices=["1", "2"],
        default="1",
    )
    lang = "es-MX" if choice == "1" else "en-US"
    label = "Español (México)" if lang == "es-MX" else "English (US)"
    ok(f"Idioma: {label}")
    return lang


def _choose_backend() -> str:
    """
    Detecta qué backends están disponibles y deja elegir al usuario.
    Devuelve: "ollama" | "llamacpp" | "none"
    """
    ollama_available  = _check_ollama()
    llamacpp_possible = _check_llamacpp()

    console.print()
    console.print("[#9890b8]Opciones de modelo:[/]")

    options = []
    if ollama_available:
        console.print(f"  [bold #4ecdc4]1[/] Ollama [dim](detectado, ya corriendo)[/]")
        options.append(("1", "ollama"))
    if llamacpp_possible:
        console.print(f"  [bold #4ecdc4]{len(options)+1}[/] llama.cpp  [dim](local puro, sin dependencias externas)[/]")
        options.append((str(len(options)+1), "llamacpp"))
    console.print(f"  [bold #4ecdc4]{len(options)+1}[/] Omitir por ahora  [dim](configurar después)[/]")
    options.append((str(len(options)+1), "none"))

    console.print()
    choice = Prompt.ask(
        "[#f9a8d4]Backend[/]",
        choices=[o[0] for o in options],
        default="1",
    )

    selected = next(v for k, v in options if k == choice)

    if selected == "ollama":
        ok("Backend: Ollama")
    elif selected == "llamacpp":
        ok("Backend: llama.cpp (local)")
    else:
        warn("Backend no configurado — usa 'itzel model pull itzel-1b' cuando quieras")

    return selected


def _setup_model(backend: str) -> None:
    """Guía la descarga del modelo según el backend elegido."""
    if backend == "ollama":
        _setup_ollama_model()
    elif backend == "llamacpp":
        _setup_llamacpp_model()
    else:
        info("Omitiendo descarga de modelo.")


def _setup_ollama_model() -> None:
    """Verifica qué modelos hay en Ollama y ofrece descargar uno."""
    from ..client import _list_ollama_models  # type: ignore[attr-defined]

    # Intentar importar el helper de model.py
    try:
        from .model import _list_ollama_models as _lom
        installed = _lom()
    except Exception:
        installed = []

    if installed:
        ok(f"Modelos Ollama instalados: {', '.join(installed[:3])}")
        return

    console.print()
    console.print("[#9890b8]No hay modelos en Ollama. ¿Descargar uno?[/]")
    console.print("  [bold #4ecdc4]1[/] llama3.2:3b   [dim](~2 GB — rápido)[/]")
    console.print("  [bold #4ecdc4]2[/] llama3.1:8b   [dim](~5 GB — mejor calidad)[/]")
    console.print("  [bold #4ecdc4]3[/] phi3:mini     [dim](~2.3 GB — muy rápido en CPU)[/]")
    console.print("  [bold #4ecdc4]4[/] Omitir        [dim](descargar después con: ollama pull)[/]")
    console.print()

    choice = Prompt.ask("[#f9a8d4]Modelo[/]", choices=["1", "2", "3", "4"], default="1")
    model_map = {"1": "llama3.2:3b", "2": "llama3.1:8b", "3": "phi3:mini", "4": None}
    model_id  = model_map.get(choice)

    if not model_id:
        info("Omitiendo — descarga después con: ollama pull llama3.1:8b")
        return

    console.print(f"\n[#9890b8]Descargando {model_id}…[/]")
    console.print("[dim](esto puede tardar varios minutos según tu conexión)[/]\n")

    try:
        result = subprocess.run(
            ["ollama", "pull", model_id],
            check=True,
        )
        ok(f"Modelo {model_id} descargado.")
    except subprocess.CalledProcessError:
        err(f"Error al descargar {model_id}.")
        hint(f"Intenta manualmente: ollama pull {model_id}")
    except FileNotFoundError:
        err("Ollama no encontrado en PATH.")
        hint("Instala Ollama desde: https://ollama.com/download")


def _setup_llamacpp_model() -> None:
    """Guía la descarga del modelo Itzel-1B para llama.cpp."""
    dest = _MODELS_DIR / "itzel-1b-q4_k_m.gguf"

    if dest.exists():
        ok(f"Modelo ya descargado: {dest}")
        return

    console.print()
    console.print(
        "[#9890b8]Se descargará Itzel-1B (~900 MB) desde HuggingFace.[/]"
    )
    do_it = Confirm.ask("[#f9a8d4]¿Descargar ahora?", default=True)

    if not do_it:
        info("Omitiendo — descarga después con: itzel model pull itzel-1b")
        return

    # Verificar que huggingface_hub esté disponible
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        warn("huggingface_hub no instalado.")
        do_install = Confirm.ask(
            "[#f9a8d4]¿Instalar huggingface_hub ahora?", default=True
        )
        if do_install:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "huggingface_hub", "-q"],
                check=True,
            )
            ok("huggingface_hub instalado.")
        else:
            hint("Instala con: pip install huggingface_hub")
            hint("Luego descarga: itzel model pull itzel-1b")
            return

    # Delegar al comando model pull
    from .model import pull as model_pull
    typer_ctx = typer.Context(typer.Command("pull"))
    # Invocar sin el runner para que imprima directamente
    try:
        from .model import _pull_from_hf, _CATALOG
        meta = _CATALOG["itzel-1b"]
        _pull_from_hf(meta, dest)
    except Exception as exc:
        err(f"Error en la descarga: {exc}")
        hint("Intenta de nuevo con: itzel model pull itzel-1b")


def _choose_hotkey() -> str:
    """Pregunta al usuario qué hotkey global usar para abrir Itzel."""
    console.print("[#9890b8]Elige el atajo de teclado global:[/]")
    console.print("  [bold #4ecdc4]1[/] Ctrl+Space   [dim]← recomendado[/]")
    console.print("  [bold #4ecdc4]2[/] Ctrl+Shift+I")
    console.print("  [bold #4ecdc4]3[/] Alt+Space")
    console.print("  [bold #4ecdc4]4[/] Personalizado")
    console.print()

    choice = Prompt.ask(
        "[#f9a8d4]Hotkey[/]", choices=["1", "2", "3", "4"], default="1"
    )
    hotkey_map = {
        "1": "ctrl+space",
        "2": "ctrl+shift+i",
        "3": "alt+space",
    }
    if choice == "4":
        hotkey = Prompt.ask(
            "[#f9a8d4]Escribe el atajo[/] [dim](ej. ctrl+shift+x)[/]",
            default="ctrl+space",
        ).lower().strip()
    else:
        hotkey = hotkey_map[choice]
    ok(f"Hotkey: {hotkey}")
    return hotkey


def _setup_voice() -> bool:
    """
    Pregunta si habilitar la voz y descarga los modelos necesarios.
    Devuelve True si el usuario eligió habilitar la voz.
    """
    console.print("[#9890b8]¿Habilitar asistente de voz?[/]")
    console.print("  Requiere ~400 MB extra (Whisper small + Kokoro-82M).")
    console.print()

    enabled = Confirm.ask("[#f9a8d4]¿Habilitar voz?[/]", default=False)
    if not enabled:
        info("Voz deshabilitada — actívala después con: itzel config voice.enabled true")
        return False

    ok("Voz habilitada")
    _download_voice_models()
    return True


def _download_voice_models() -> None:
    """Descarga Whisper small y Kokoro-82M con barra de progreso."""
    import urllib.request

    models = [
        {
            "name":  "Whisper small (STT)",
            "file":  "whisper-small.bin",
            "url":   "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
            "size":  "~466 MB",
        },
        {
            "name":  "Kokoro-82M (TTS)",
            "file":  "kokoro-v0_19.onnx",
            "url":   "https://huggingface.co/hexgrad/Kokoro-82M/resolve/main/kokoro-v0_19.onnx",
            "size":  "~83 MB",
        },
    ]

    from ..output import download_progress

    for m in models:
        dest = _MODELS_DIR / m["file"]
        if dest.exists():
            ok(f"{m['name']}: ya descargado")
            continue

        console.print(f"\n[#9890b8]Descargando {m['name']} {m['size']}…[/]")
        try:
            progress = download_progress()
            with progress:
                task = progress.add_task(m["name"], total=None)

                def _hook(block: int, block_size: int, total: int,
                          _p=progress, _t=task) -> None:
                    if total > 0:
                        _p.update(_t, total=total, completed=block * block_size)

                urllib.request.urlretrieve(m["url"], dest, reporthook=_hook)
            ok(f"{m['name']} descargado → {dest.name}")
        except Exception as exc:
            warn(f"No se pudo descargar {m['name']}: {exc}")
            hint(f"Descárgalo manualmente y colócalo en {_MODELS_DIR}")
            if dest.exists():
                dest.unlink()   # borrar descarga parcial


def _launch_app() -> None:
    """Intenta abrir la app de escritorio Tauri si está instalada."""
    import platform
    import subprocess as sp

    candidates: list[Path] = []
    system = platform.system()

    if system == "Windows":
        candidates = [
            Path(r"C:\Program Files\Itzel\itzel.exe"),
            Path.home() / "AppData" / "Local" / "Programs" / "Itzel" / "itzel.exe",
        ]
    elif system == "Darwin":
        candidates = [Path("/Applications/Itzel.app")]
    else:
        candidates = [
            Path.home() / ".local" / "bin" / "itzel-desktop",
            Path("/usr/local/bin/itzel-desktop"),
        ]

    app = next((p for p in candidates if p.exists()), None)
    if app is None:
        hint("App de escritorio no encontrada — descárgala en:")
        hint("  https://github.com/BugCreator404/itzel/releases/latest")
        return

    console.print()
    launch = Confirm.ask("[#f9a8d4]¿Abrir Itzel ahora?[/]", default=True)
    if not launch:
        return

    try:
        if system == "Darwin":
            sp.Popen(["open", str(app)], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        else:
            sp.Popen([str(app)], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        ok("¡Itzel abierta!")
    except Exception as exc:
        warn(f"No se pudo abrir la app: {exc}")


def _write_config(language: str, backend: str,
                  hotkey: str = "ctrl+space",
                  voice_enabled: bool = False) -> None:
    """Escribe la configuración inicial en ~/.itzel/itzel.config.json."""
    model_active = "itzel-1b" if backend == "llamacpp" else "ollama/llama3.1:8b"
    if backend == "none":
        model_active = "none"

    config: dict = {
        "version":   "0.1.0",
        "language":  language,
        "telemetry": False,
        "analytics": False,
        "model": {
            "active":         model_active,
            "context_length": 32768,
            "temperature":    0.7,
            "top_p":          0.95,
            "max_tokens":     2048,
        },
        "server": {
            "host": "127.0.0.1",
            "port": 7432,
        },
        "voice": {
            "enabled":   voice_enabled,
            "language":  language,
            "stt_model": "whisper-small",
            "tts_model": "kokoro",
        },
        "security": {
            "confirm_irreversible": True,
            "sandbox_enabled":      True,
        },
        "shortcuts": {
            "open": hotkey,
        },
    }

    # No sobreescribir si ya existe — preguntar
    if _CONFIG_FILE.exists():
        overwrite = Confirm.ask(
            "[#fbbf24]Ya existe itzel.config.json. ¿Sobreescribir?[/]",
            default=False,
        )
        if not overwrite:
            info("Configuración existente conservada.")
            return

    _CONFIG_FILE.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    ok(f"Configuración guardada en {_CONFIG_FILE}")


def _verify_setup() -> None:
    """Comprueba que todo esté en orden al finalizar."""
    console.print()
    info("Verificando instalación…")

    client = ItzelClient()
    if client.is_alive():
        ok("Backend FastAPI: online")
    else:
        warn("Backend FastAPI: offline")
        hint(
            "Inicia el backend cuando quieras con:\n"
            "  uvicorn itzel_core.engine:app --port 7432 --reload"
        )

    if _CONFIG_FILE.exists():
        ok(f"Config: {_CONFIG_FILE}")
    else:
        warn("Config no encontrada.")

    gguf_files = list(_MODELS_DIR.glob("*.gguf"))
    if gguf_files:
        ok(f"Modelos en disco: {len(gguf_files)} archivo(s)")
    else:
        info("Sin modelos .gguf descargados.")


# ─── helpers de detección ─────────────────────────────────────────────────────

def _check_ollama() -> bool:
    """True si Ollama responde en localhost:11434."""
    try:
        import httpx
        with httpx.Client(timeout=2.0) as c:
            r = c.get("http://127.0.0.1:11434/api/tags")
            return r.status_code == 200
    except Exception:
        return False


def _check_llamacpp() -> bool:
    """True si llama-cpp-python está instalado."""
    try:
        import importlib
        importlib.import_module("llama_cpp")
        return True
    except ImportError:
        return True   # Mostrar la opción aunque no esté — se puede instalar durante el setup


def _step(num: str, title: str) -> None:
    """Imprime el encabezado de cada paso del wizard."""
    console.print(f"\n[bold #f9a8d4]Paso {num}[/] [#e8e4f4]— {title}[/]")
    console.print("[dim]" + "─" * 40 + "[/]")
