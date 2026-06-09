"""Comando: itzel model [list|pull|use|remove|info]

Gestiona los modelos de IA de Itzel:
  list   — modelos disponibles (backend + disco)
  pull   — descarga un modelo desde HuggingFace con barra de progreso
  use    — cambia el modelo activo en el backend
  remove — elimina un archivo .gguf del disco (con confirmación)
  info   — información detallada de un modelo
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import typer

from ..client import BackendError, BackendOfflineError, ItzelClient
from ..output import (
    confirm,
    console,
    download_progress,
    err,
    hint,
    info,
    make_table,
    ok,
    spinner_progress,
    warn,
)

app = typer.Typer(help="Gestiona modelos locales de Itzel")

# ─── directorio y catálogo ────────────────────────────────────────────────────

_MODELS_DIR = Path.home() / ".itzel" / "models"

# Catálogo local — fuente de verdad para pull/info sin backend
_CATALOG: dict[str, dict] = {
    "itzel-1b": {
        "name":        "Itzel-1B",
        "description": "Modelo principal — Qwen2.5-1.5B fine-tuned. Rápido en CPU.",
        "backend":     "llama.cpp",
        "filename":    "itzel-1b-q4_k_m.gguf",
        "size_mb":     900,
        "context_len": 32_768,
        "hf_repo":     "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "hf_filename": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    },
    "itzel-7b": {
        "name":        "Itzel-7B",
        "description": "Modelo avanzado — mayor razonamiento. Requiere ~5 GB RAM.",
        "backend":     "llama.cpp",
        "filename":    "itzel-7b-q4_k_m.gguf",
        "size_mb":     4_800,
        "context_len": 32_768,
        "hf_repo":     "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "hf_filename": "qwen2.5-7b-instruct-q4_k_m.gguf",
    },
}


# ─── list ─────────────────────────────────────────────────────────────────────

@app.command("list")
def list_models() -> None:
    """Lista todos los modelos conocidos con su estado actual."""
    client = ItzelClient()
    alive  = client.is_alive()

    # Obtener estado del backend si está disponible
    backend_models: dict[str, dict] = {}
    if alive:
        try:
            for m in client.list_models():
                backend_models[m.id] = {
                    "loaded": m.loaded,
                    "active": m.active,
                    "downloaded": m.downloaded,
                }
        except Exception:
            pass

    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    gguf_files = {p.name for p in _MODELS_DIR.glob("*.gguf")}

    table = make_table(
        "Modelos de Itzel",
        ("ID",          "#f9a8d4", 14),
        ("Nombre",      "#e8e4f4", 16),
        ("Estado",      "",        14),
        ("Backend",     "#9890b8", 10),
        ("Tamaño",      "#9890b8",  8),
        ("Descripción", "dim",     36),
    )

    for model_id, meta in _CATALOG.items():
        bk      = backend_models.get(model_id, {})
        on_disk = meta["filename"] in gguf_files or bk.get("downloaded", False)
        loaded  = bk.get("loaded", False)
        active  = bk.get("active", False)

        if loaded and active:
            status = "[bold #4ecdc4]activo[/]"
        elif loaded:
            status = "[#4ecdc4]en memoria[/]"
        elif on_disk:
            status = "[#fbbf24]en disco[/]"
        else:
            status = "[dim]no descargado[/]"

        size_label = f"~{meta['size_mb'] // 1024}GB" if meta["size_mb"] >= 1024 else f"~{meta['size_mb']}MB"

        table.add_row(
            model_id,
            meta["name"],
            status,
            meta["backend"],
            size_label,
            meta["description"],
        )

    # Modelos Ollama
    if alive:
        try:
            from ..client import OllamaAdapter  # type: ignore[attr-defined]
        except ImportError:
            pass
        try:
            ollama_models = _list_ollama_models()
            for om in ollama_models:
                table.add_row(
                    f"ollama/{om}",
                    om,
                    "[bold #4ecdc4]Ollama[/]",
                    "ollama",
                    "—",
                    "Modelo instalado en Ollama",
                )
        except Exception:
            pass

    console.print(table)

    if not alive:
        console.print()
        hint("Backend offline — estado de carga no disponible. Inicia con: itzel setup")


# ─── pull ─────────────────────────────────────────────────────────────────────

@app.command("pull")
def pull(
    name: str = typer.Argument(..., help="ID del modelo: itzel-1b | itzel-7b"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-descargar aunque ya exista"),
) -> None:
    """Descarga un modelo desde HuggingFace con barra de progreso."""
    meta = _CATALOG.get(name)
    if not meta:
        err(
            f"Modelo '{name}' no reconocido.",
            hint="Modelos disponibles: " + ", ".join(_CATALOG.keys()),
        )
        raise typer.Exit(1)

    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dest = _MODELS_DIR / meta["filename"]

    if dest.exists() and not force:
        ok(f"'{name}' ya está descargado en {dest}")
        hint("Usa --force para re-descargar.")
        return

    console.print(
        f"\n[bold #f9a8d4]Descargando[/] [#4ecdc4]{meta['name']}[/] "
        f"([#9890b8]~{meta['size_mb']} MB[/])\n"
    )

    # Intentar con huggingface_hub
    try:
        import huggingface_hub  # noqa: F401
        _pull_from_hf(meta, dest)
    except ImportError:
        _show_manual_download_instructions(name, meta, dest)


def _pull_from_hf(meta: dict, dest: Path) -> None:
    """Descarga usando huggingface_hub con barra de progreso Rich."""
    from huggingface_hub import hf_hub_download

    tmp_name = f"{dest.name}.tmp"
    tmp_path = dest.parent / tmp_name

    try:
        with spinner_progress("Conectando a HuggingFace…") as progress:
            progress.add_task("download")

        console.print(f"[dim]Repositorio: {meta['hf_repo']}[/]")
        console.print(f"[dim]Archivo:     {meta['hf_filename']}[/]\n")

        # hf_hub_download descarga a caché y devuelve la ruta
        cached = hf_hub_download(
            repo_id  = meta["hf_repo"],
            filename = meta["hf_filename"],
            local_dir= str(dest.parent),
        )

        # Mover al nombre final esperado por Itzel
        hf_downloaded = Path(cached)
        if hf_downloaded != dest:
            shutil.move(str(hf_downloaded), str(dest))

        ok(f"Modelo descargado en: {dest}")
        hint(f"Usa el modelo con: itzel model use {meta.get('id', dest.stem)}")

    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        err(f"Error al descargar: {exc}")
        _show_manual_download_instructions("", meta, dest)
        raise typer.Exit(1)


def _show_manual_download_instructions(name: str, meta: dict, dest: Path) -> None:
    """Muestra instrucciones para descarga manual cuando huggingface_hub no está."""
    console.print(
        f"\n[bold #fbbf24]huggingface_hub no instalado.[/] "
        "Descarga el modelo manualmente:\n"
    )
    console.print(f"  [#4ecdc4]pip install huggingface_hub[/]")
    console.print(
        f"  [#4ecdc4]huggingface-cli download {meta['hf_repo']} "
        f"{meta['hf_filename']} --local-dir ~/.itzel/models/[/]"
    )
    console.print(f"  [#4ecdc4]# Renombra el archivo a: {meta['filename']}[/]")
    console.print(
        f"\n[dim]Destino final esperado: {dest}[/]"
    )


# ─── use ──────────────────────────────────────────────────────────────────────

@app.command("use")
def use(
    name: str = typer.Argument(..., help="ID del modelo a activar"),
) -> None:
    """Cambia el modelo activo en el backend."""
    client = ItzelClient()
    try:
        result = client.switch_model(name)
        ok(f"Modelo activo: {result.get('model_id', name)}")
        hint("Los cambios son efectivos en el próximo mensaje.")
    except BackendOfflineError:
        err(
            "Backend offline.",
            hint="Inicia el backend primero con: itzel setup",
        )
        raise typer.Exit(1)
    except BackendError as exc:
        err(f"No se pudo cambiar el modelo: {exc.detail}")
        raise typer.Exit(1)


# ─── remove ───────────────────────────────────────────────────────────────────

@app.command("remove")
def remove(
    name: str   = typer.Argument(..., help="ID del modelo a eliminar"),
    yes:  bool  = typer.Option(False, "--yes", "-y", help="Sin confirmación"),
) -> None:
    """Elimina el archivo .gguf del modelo del disco."""
    meta = _CATALOG.get(name)
    if not meta:
        err(f"Modelo '{name}' no reconocido.")
        raise typer.Exit(1)

    gguf = _MODELS_DIR / meta["filename"]
    if not gguf.exists():
        warn(f"'{name}' no está descargado en {gguf}")
        return

    size_mb = gguf.stat().st_size // (1024 * 1024)
    if not yes:
        confirm(
            f"¿Eliminar '{gguf.name}' ({size_mb} MB)?",
            default=False,
        )

    gguf.unlink()
    ok(f"Modelo '{name}' eliminado ({size_mb} MB liberados).")


# ─── info ─────────────────────────────────────────────────────────────────────

@app.command("info")
def info_cmd(
    name: Optional[str] = typer.Argument(None, help="ID del modelo (default: activo)"),
) -> None:
    """Muestra información detallada de un modelo."""
    client = ItzelClient()

    # Si no se especifica, usar el activo del backend
    if not name:
        if client.is_alive():
            try:
                models = client.list_models()
                active = next((m for m in models if m.active), None)
                name = active.id if active else None
            except Exception:
                pass
        if not name:
            err("No se pudo determinar el modelo activo.")
            hint("Especifica un ID: itzel model info itzel-1b")
            raise typer.Exit(1)

    meta = _CATALOG.get(name) or _CATALOG.get(name.removeprefix("ollama/"), {})

    console.print(f"\n[bold #f9a8d4]{name}[/]\n")

    if meta:
        info(f"Nombre:       {meta.get('name', name)}")
        info(f"Backend:      {meta.get('backend', '—')}")
        info(f"Descripción:  {meta.get('description', '—')}")
        info(f"Tamaño:       ~{meta.get('size_mb', 0)} MB")
        info(f"Contexto:     {meta.get('context_len', 0):,} tokens")
        info(f"HuggingFace:  {meta.get('hf_repo', '—')}")

        gguf = _MODELS_DIR / meta.get("filename", "")
        if gguf.exists():
            size_real = gguf.stat().st_size // (1024 * 1024)
            info(f"En disco:     {gguf} ({size_real} MB)")
        else:
            info(f"En disco:     [dim]no descargado[/]")
    else:
        info(f"Modelo Ollama — gestiona con: ollama show {name}")

    if client.is_alive():
        try:
            models  = client.list_models()
            backend = next((m for m in models if m.id == name), None)
            if backend:
                info(f"Cargado:      {'sí' if backend.loaded else 'no'}")
                info(f"Activo:       {'sí' if backend.active else 'no'}")
        except Exception:
            pass

    console.print()


# ─── helpers ──────────────────────────────────────────────────────────────────

def _list_ollama_models() -> list[str]:
    """Obtiene los modelos instalados en Ollama (si está corriendo)."""
    import httpx
    try:
        with httpx.Client(timeout=2.0) as c:
            r = c.get("http://127.0.0.1:11434/api/tags")
            if r.status_code == 200:
                return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []
