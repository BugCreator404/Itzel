"""Comando: itzel uninstall

Desinstala Itzel limpiamente: CLI, datos, keychain y (opcionalmente) modelos.

Opciones:
  --data-only     Borra solo ~/.itzel/ (historial, config, logs). Conserva el CLI.
  --keep-models   No borra los modelos descargados (~1 GB).
  --yes / -y      Sin confirmación interactiva.

El comando muestra un resumen de lo que va a borrar ANTES de hacer nada
(principio #5: confirmar antes de acciones irreversibles).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from ..output import confirm, console, err, hint, info, ok, warn

# ─── rutas ────────────────────────────────────────────────────────────────────

_ITZEL_DIR  = Path.home() / ".itzel"
_MODELS_DIR = _ITZEL_DIR / "models"


# ─── punto de entrada ─────────────────────────────────────────────────────────

def run(
    data_only:   bool = False,
    keep_models: bool = False,
    yes:         bool = False,
) -> None:
    """Desinstala Itzel con confirmación explícita."""
    console.print()
    console.print("[bold #f9a8d4]Itzel — desinstalación[/]")
    console.print("[dim]" + "─" * 44 + "[/]")
    console.print()

    # ── resumen de lo que se va a borrar ──────────────────────────────────────
    plan = _build_plan(data_only=data_only, keep_models=keep_models)
    _print_plan(plan)

    if not any(plan.values()):
        info("Nada que desinstalar.")
        return

    # ── confirmación (principio #5) ───────────────────────────────────────────
    if not yes:
        confirm("¿Desinstalar? Esta acción no se puede deshacer.")

    console.print()

    # ── ejecutar ──────────────────────────────────────────────────────────────
    _remove_keychain()

    if not keep_models and plan["models"]:
        _remove_dir(_MODELS_DIR, "Modelos de IA")

    if plan["data"]:
        _remove_dir(_ITZEL_DIR, "Directorio ~/.itzel")

    if not data_only and plan["cli"]:
        _remove_cli()

    console.print()
    ok("Itzel desinstalada.")
    if data_only:
        hint("El CLI sigue instalado — ejecuta 'itzel setup' para comenzar de nuevo.")
    else:
        hint("¡Hasta pronto! Puedes volver a instalar en cualquier momento:")
        hint("  curl -fsSL https://raw.githubusercontent.com/BugCreator404/itzel/main/install.sh | bash")


# ─── planificación ────────────────────────────────────────────────────────────

def _build_plan(*, data_only: bool, keep_models: bool) -> dict[str, bool]:
    """Determina qué se borrará según las opciones."""
    return {
        "cli":     not data_only and _cli_installed(),
        "data":    _ITZEL_DIR.exists(),
        "models":  not keep_models and _MODELS_DIR.exists() and any(_MODELS_DIR.iterdir()),
        "keychain": _has_keychain_entries(),
    }


def _print_plan(plan: dict[str, bool]) -> None:
    """Muestra el resumen visual de lo que se va a borrar."""
    console.print("[#9890b8]Se eliminarán:[/]")
    console.print()

    if plan["cli"]:
        cli_path = _find_cli_path()
        console.print(f"  [#f87171]✗[/] CLI de Itzel  [dim]{cli_path or 'itzel'}[/]")

    if plan["models"]:
        size = _dir_size_mb(_MODELS_DIR)
        console.print(f"  [#f87171]✗[/] Modelos descargados  [dim]{_MODELS_DIR} ({size} MB)[/]")

    if plan["data"]:
        size = _dir_size_mb(_ITZEL_DIR)
        console.print(
            f"  [#f87171]✗[/] Directorio de datos  [dim]{_ITZEL_DIR} ({size} MB)[/]\n"
            f"     [dim]historial cifrado, configuración, logs, skills de usuario[/]"
        )

    if plan["keychain"]:
        console.print("  [#f87171]✗[/] Credenciales del keychain  [dim](API keys de MCP)[/]")

    if not any(plan.values()):
        return

    console.print()
    if not plan["models"] and _MODELS_DIR.exists():
        hint("Modelos conservados (--keep-models). Ocupa espacio en disco.")
    if not plan["cli"]:
        info("Solo se borran los datos (--data-only).")

    console.print()


# ─── acciones ─────────────────────────────────────────────────────────────────

def _remove_dir(path: Path, label: str) -> None:
    """Borra un directorio recursivamente con reporte de progreso."""
    try:
        shutil.rmtree(path)
        ok(f"{label} eliminado.")
    except PermissionError:
        err(f"No se pudo borrar {path} — sin permisos.")
        hint(f"Bórralo manualmente:  rm -rf '{path}'")
    except Exception as exc:
        err(f"Error al borrar {path}: {exc}")


def _remove_cli() -> None:
    """Desinstala el CLI con pipx (si disponible) o pip."""
    method = _detect_install_method()

    if method == "pipx":
        info("Desinstalando con pipx…")
        _run_cmd(["pipx", "uninstall", "itzel-cli"], "pipx uninstall")
    elif method == "pip":
        info("Desinstalando con pip…")
        _run_cmd(
            [sys.executable, "-m", "pip", "uninstall", "-y", "itzel-cli"],
            "pip uninstall",
        )
    else:
        # Fallback: borrar el binario directamente
        cli_path = _find_cli_path()
        if cli_path:
            try:
                Path(cli_path).unlink()
                ok(f"Binario eliminado: {cli_path}")
            except Exception as exc:
                err(f"No se pudo borrar {cli_path}: {exc}")
                hint(f"Bórralo manualmente:  rm '{cli_path}'")
        else:
            warn("No se encontró el binario — puede que ya estuviera eliminado.")


def _remove_keychain() -> None:
    """Borra todas las entradas del keychain bajo el servicio 'itzel'."""
    try:
        import keyring
        import keyring.errors

        # Entradas conocidas: MCP API keys, DB key de SQLCipher
        known_users = ["db-key"]

        # Intentar también limpiar las keys de MCP desde la config
        try:
            import json
            cfg_file = _ITZEL_DIR / "itzel.config.json"
            if cfg_file.exists():
                cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
                for srv in cfg.get("mcp_servers", []):
                    known_users.append(f"mcp-{srv.get('name', '')}")
        except Exception:
            pass

        deleted = 0
        for user in known_users:
            try:
                existing = keyring.get_password("itzel", user)
                if existing:
                    keyring.delete_password("itzel", user)
                    deleted += 1
            except (keyring.errors.PasswordDeleteError, Exception):
                pass

        if deleted:
            ok(f"Keychain: {deleted} entrada(s) eliminada(s).")
        else:
            info("Keychain: sin entradas que eliminar.")

    except ImportError:
        info("keyring no disponible — keychain no modificado.")
    except Exception as exc:
        warn(f"No se pudo limpiar el keychain: {exc}")


def _run_cmd(cmd: list[str], label: str) -> None:
    """Ejecuta un comando y reporta el resultado."""
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        ok(f"{label} completado.")
    except subprocess.CalledProcessError as exc:
        warn(f"{label} falló: {exc.stderr.strip()[:120]}")
        hint(f"Intenta manualmente:  {' '.join(cmd)}")
    except FileNotFoundError:
        warn(f"'{cmd[0]}' no encontrado — omitido.")


# ─── helpers de detección ─────────────────────────────────────────────────────

def _cli_installed() -> bool:
    """True si el binario 'itzel' existe en el PATH o en ubicaciones conocidas."""
    return _find_cli_path() is not None


def _find_cli_path() -> str | None:
    """Devuelve la ruta al binario de itzel, o None si no se encuentra."""
    import shutil as _shutil
    return _shutil.which("itzel")


def _detect_install_method() -> str:
    """Detecta si se instaló con pipx, pip u otro método. Devuelve 'pipx'|'pip'|'other'."""
    cli_path = _find_cli_path()
    if not cli_path:
        return "other"
    if "pipx" in cli_path.lower() or ".local/pipx" in cli_path:
        return "pipx"
    # Si el ejecutable vive cerca del Python activo → pip
    python_dir = Path(sys.executable).parent
    if Path(cli_path).parent == python_dir:
        return "pip"
    return "other"


def _has_keychain_entries() -> bool:
    """True si hay al menos una entrada de Itzel en el keychain."""
    try:
        import keyring
        return keyring.get_password("itzel", "db-key") is not None
    except Exception:
        return False


def _dir_size_mb(path: Path) -> int:
    """Tamaño aproximado de un directorio en MB."""
    try:
        total = sum(
            f.stat().st_size for f in path.rglob("*") if f.is_file()
        )
        return total // (1024 * 1024)
    except Exception:
        return 0
