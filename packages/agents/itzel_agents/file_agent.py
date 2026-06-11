"""
FileAgent — operaciones de archivos para Itzel.

Expone al LLM un conjunto de tools para leer, escribir, mover, copiar, borrar,
listar y buscar archivos en el sistema del usuario. Todo 100% local.

Seguridad:
    - delete_file        → SIEMPRE pide confirmación (irreversible).
    - move_file/copy_file → confirman solo si el destino ya existe (sobrescritura).
    - write_file          → confirma solo si el archivo ya existe.
    - read_file           → límite de tamaño (no carga binarios gigantes).
    - search_files        → límite de resultados y de tamaño por archivo.

Cada acción se audita en ~/.itzel/logs/actions.log vía el ToolRegistry.

English summary:
    File operations agent. Destructive/overwriting actions require confirmation
    (delete always; move/copy/write only when the target already exists).
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from itzel_core.tools import Tool, tool

from .base_agent import BaseAgent

# ─── límites de seguridad ─────────────────────────────────────────────────────

_MAX_READ_BYTES     = 10 * 1024 * 1024   # 10 MB — no cargar archivos enormes
_MAX_WRITE_BYTES    = 50 * 1024 * 1024   # 50 MB — tope de escritura
_MAX_SEARCH_RESULTS = 100                # resultados máximos por búsqueda
_MAX_SCAN_BYTES     = 2 * 1024 * 1024    # 2 MB — tamaño máx. a escanear por contenido
_MAX_LIST_ENTRIES   = 1000               # entradas máximas al listar un directorio


def _resolve(path: str, *, must_exist: bool = False) -> Path:
    """Atajo al normalizador de rutas de BaseAgent."""
    return BaseAgent._resolve_path(path, must_exist=must_exist)


def _dst_exists(args: dict[str, Any]) -> bool:
    """Predicado de confirmación: True si el destino ya existe (sobrescritura)."""
    dst = args.get("dst") or args.get("dest")
    if not dst:
        return False
    try:
        return _resolve(str(dst)).exists()
    except Exception:
        return True  # ante la duda, confirmar


def _path_exists(args: dict[str, Any]) -> bool:
    """Predicado de confirmación: True si la ruta de 'path' ya existe."""
    p = args.get("path")
    if not p:
        return False
    try:
        return _resolve(str(p)).exists()
    except Exception:
        return True


# ─── tools (funciones de módulo, stateless) ───────────────────────────────────

@tool(
    "read_file",
    "Lee y devuelve el contenido de texto de un archivo.",
    param_docs={"path": "Ruta del archivo a leer (admite ~ y rutas relativas)."},
)
def read_file(path: str) -> str:
    """Lee un archivo de texto UTF-8. Falla si supera el límite de tamaño."""
    p = _resolve(path, must_exist=True)
    if p.is_dir():
        raise IsADirectoryError(f"'{p}' es un directorio, no un archivo.")
    size = p.stat().st_size
    if size > _MAX_READ_BYTES:
        raise ValueError(
            f"Archivo demasiado grande ({size} bytes). "
            f"Límite: {_MAX_READ_BYTES} bytes."
        )
    return p.read_text(encoding="utf-8", errors="replace")


@tool(
    "write_file",
    "Escribe contenido de texto a un archivo. Confirma si el archivo ya existe.",
    confirm_when=_path_exists,
    param_docs={
        "path":    "Ruta del archivo a escribir.",
        "content": "Contenido de texto a guardar.",
    },
)
def write_file(path: str, content: str) -> str:
    """Crea o sobrescribe un archivo de texto. Crea los directorios padre."""
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        raise ValueError(f"Contenido excede el límite de {_MAX_WRITE_BYTES} bytes.")
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Escrito {len(content)} caracteres en {p}"


@tool(
    "move_file",
    "Mueve o renombra un archivo. Confirma si el destino ya existe.",
    confirm_when=_dst_exists,
    param_docs={
        "src": "Ruta de origen.",
        "dst": "Ruta de destino.",
    },
)
def move_file(src: str, dst: str) -> str:
    """Mueve src a dst. Sobrescribe dst si existía (tras confirmación)."""
    src_p = _resolve(src, must_exist=True)
    dst_p = _resolve(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_p), str(dst_p))
    return f"Movido {src_p} → {dst_p}"


@tool(
    "copy_file",
    "Copia un archivo. Confirma si el destino ya existe.",
    confirm_when=_dst_exists,
    param_docs={
        "src": "Ruta de origen.",
        "dst": "Ruta de destino.",
    },
)
def copy_file(src: str, dst: str) -> str:
    """Copia src a dst preservando metadatos."""
    src_p = _resolve(src, must_exist=True)
    dst_p = _resolve(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    if src_p.is_dir():
        shutil.copytree(str(src_p), str(dst_p), dirs_exist_ok=True)
    else:
        shutil.copy2(str(src_p), str(dst_p))
    return f"Copiado {src_p} → {dst_p}"


@tool(
    "delete_file",
    "Borra un archivo o directorio. SIEMPRE pide confirmación (irreversible).",
    confirm_if_irreversible=True,
    param_docs={"path": "Ruta del archivo o directorio a borrar."},
)
def delete_file(path: str) -> str:
    """Elimina un archivo o un directorio completo. Acción irreversible."""
    p = _resolve(path, must_exist=True)
    if p.is_dir():
        shutil.rmtree(str(p))
        return f"Directorio borrado: {p}"
    p.unlink()
    return f"Archivo borrado: {p}"


@tool(
    "list_dir",
    "Lista el contenido de un directorio (archivos y subcarpetas).",
    param_docs={"path": "Ruta del directorio a listar (default: directorio actual)."},
)
def list_dir(path: str = ".") -> list[dict[str, Any]]:
    """Devuelve la lista de entradas de un directorio con metadata básica."""
    p = _resolve(path, must_exist=True)
    if not p.is_dir():
        raise NotADirectoryError(f"'{p}' no es un directorio.")
    entries: list[dict[str, Any]] = []
    for child in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
        try:
            st = child.stat()
            entries.append({
                "name":   child.name,
                "type":   "dir" if child.is_dir() else "file",
                "size":   st.st_size if child.is_file() else None,
                "path":   str(child),
            })
        except OSError:
            continue
        if len(entries) >= _MAX_LIST_ENTRIES:
            break
    return entries


@tool(
    "get_info",
    "Obtiene metadata de un archivo o directorio (tamaño, fechas, tipo).",
    param_docs={"path": "Ruta del archivo o directorio."},
)
def get_info(path: str) -> dict[str, Any]:
    """Devuelve metadata detallada de una ruta."""
    p = _resolve(path, must_exist=True)
    st = p.stat()
    return {
        "path":       str(p),
        "name":       p.name,
        "type":       "dir" if p.is_dir() else "file",
        "size":       st.st_size,
        "modified":   datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
        "created":    datetime.fromtimestamp(st.st_ctime, timezone.utc).isoformat(),
        "suffix":     p.suffix,
        "is_symlink": p.is_symlink(),
    }


@tool(
    "search_files",
    "Busca archivos por nombre, y opcionalmente por contenido, dentro de un directorio.",
    timeout=60,
    param_docs={
        "query":      "Texto a buscar en el nombre del archivo (o en el contenido).",
        "path":       "Directorio raíz de la búsqueda (default: directorio actual).",
        "in_content": "Si es True, busca el texto dentro del contenido de los archivos.",
    },
)
def search_files(query: str, path: str = ".", in_content: bool = False) -> list[dict[str, Any]]:
    """
    Busca recursivamente archivos cuyo nombre (o contenido) contenga `query`.
    Limita resultados y tamaño escaneado por seguridad y rendimiento.
    """
    root = _resolve(path, must_exist=True)
    if not root.is_dir():
        raise NotADirectoryError(f"'{root}' no es un directorio.")

    q = query.lower()
    results: list[dict[str, Any]] = []

    for child in root.rglob("*"):
        if len(results) >= _MAX_SEARCH_RESULTS:
            break
        if not child.is_file():
            continue

        name_match = q in child.name.lower()
        content_match = False

        if in_content and not name_match:
            try:
                if child.stat().st_size <= _MAX_SCAN_BYTES:
                    text = child.read_text(encoding="utf-8", errors="ignore")
                    content_match = q in text.lower()
            except (OSError, ValueError):
                continue

        if name_match or content_match:
            results.append({
                "path":  str(child),
                "name":  child.name,
                "match": "name" if name_match else "content",
            })

    return results


# ─── el agente ────────────────────────────────────────────────────────────────

class FileAgent(BaseAgent):
    """Agente de operaciones de archivos. Registra las 8 tools de archivo."""

    name = "file"

    # Lista de tools que expone este agente.
    _TOOLS: list[Tool] = [
        read_file,
        write_file,
        move_file,
        copy_file,
        delete_file,
        list_dir,
        get_info,
        search_files,
    ]

    def _register_tools(self) -> None:
        self.registry.register_all(self._TOOLS)
