"""
Manifiesto de una skill — validación de skill.json y permisos.

Cada skill vive en su propia carpeta:

    skills/community/mi-skill/
    ├── skill.json          # metadata (este módulo la valida)
    ├── handler.py          # debe exportar: run(query: str, context: dict) -> str
    ├── requirements.txt    # deps extra (opcional)
    └── README.md           # docs

Permisos:
    Una skill declara en skill.json qué capacidades usa. El loader verifica
    estáticamente (analizando los imports del handler con `ast`) que el código
    no use capacidades NO declaradas — si lo hace, la skill no se carga.

English summary:
    Pydantic model for skill.json plus a static (ast-based) permission checker
    that maps handler imports to declared permissions. Undeclared usage blocks
    the skill from loading (fail-safe).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ─── permisos ─────────────────────────────────────────────────────────────────

#: Permisos que una skill puede declarar.
ALLOWED_PERMISSIONS = {
    "filesystem",   # leer/escribir archivos del usuario
    "network",      # llamadas de red salientes
    "clipboard",    # leer/escribir el portapapeles
    "system",       # subprocesos / control del OS
}

#: Imports de Python → permiso que implican.
#: Si el handler importa uno de estos módulos sin declarar el permiso,
#: la skill se rechaza al cargar.
_IMPORT_PERMISSION_MAP: dict[str, str] = {
    # network
    "httpx":       "network",
    "requests":    "network",
    "urllib":      "network",
    "aiohttp":     "network",
    "socket":      "network",
    "http":        "network",
    "websockets":  "network",
    # filesystem
    "shutil":      "filesystem",
    "pathlib":     "filesystem",
    "tempfile":    "filesystem",
    "glob":        "filesystem",
    "fileinput":   "filesystem",
    # clipboard
    "pyperclip":   "clipboard",
    # system
    "subprocess":  "system",
    "ctypes":      "system",
    "winreg":      "system",
    "psutil":      "system",
}

_NAME_RE    = re.compile(r"^[a-z0-9][a-z0-9-]{1,49}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-.+][\w.]+)?$")


# ─── modelo del manifiesto ────────────────────────────────────────────────────

class SkillManifest(BaseModel):
    """Contenido validado de skill.json."""

    name:           str
    version:        str = "0.1.0"
    description:    str                      # ES-MX (principio #3: español primero)
    description_en: str = ""                 # fallback EN
    author:         str = ""
    triggers:       list[str] = Field(default_factory=list)
    permissions:    list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                f"Nombre de skill inválido: '{v}'. "
                "Usa kebab-case: minúsculas, dígitos y guiones (2-50 chars)."
            )
        return v

    @field_validator("version")
    @classmethod
    def _valid_version(cls, v: str) -> str:
        if not _VERSION_RE.match(v):
            raise ValueError(f"Versión inválida: '{v}'. Usa semver (p. ej. 1.0.0).")
        return v

    @field_validator("permissions")
    @classmethod
    def _valid_permissions(cls, v: list[str]) -> list[str]:
        unknown = set(v) - ALLOWED_PERMISSIONS
        if unknown:
            raise ValueError(
                f"Permisos desconocidos: {sorted(unknown)}. "
                f"Permitidos: {sorted(ALLOWED_PERMISSIONS)}."
            )
        return v


# ─── carga del manifiesto ─────────────────────────────────────────────────────

class ManifestError(ValueError):
    """skill.json inexistente, mal formado o inválido."""


def load_manifest(skill_dir: Path) -> SkillManifest:
    """
    Lee y valida el skill.json de una carpeta de skill.

    Raises:
        ManifestError: si falta el archivo, no es JSON o no pasa validación.
    """
    path = skill_dir / "skill.json"
    if not path.exists():
        raise ManifestError(f"No existe skill.json en {skill_dir}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"skill.json mal formado en {skill_dir}: {exc}") from exc
    try:
        return SkillManifest.model_validate(data)
    except Exception as exc:
        raise ManifestError(f"skill.json inválido en {skill_dir}: {exc}") from exc


# ─── verificación estática de permisos ────────────────────────────────────────

def infer_permissions(handler_source: str) -> set[str]:
    """
    Infiere qué permisos usa realmente un handler analizando sus imports.

    Análisis estático con `ast` — no ejecuta el código. También detecta el
    uso del builtin open() como uso de filesystem.

    Returns:
        Set de permisos implicados por el código.
    """
    try:
        tree = ast.parse(handler_source)
    except SyntaxError:
        # Si ni siquiera parsea, el loader lo rechazará después al importarlo.
        return set()

    used: set[str] = set()
    for node in ast.walk(tree):
        # import httpx / import urllib.request
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _IMPORT_PERMISSION_MAP:
                    used.add(_IMPORT_PERMISSION_MAP[root])
        # from httpx import get
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in _IMPORT_PERMISSION_MAP:
                    used.add(_IMPORT_PERMISSION_MAP[root])
        # open(...) → filesystem
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                used.add("filesystem")

    return used


def check_permissions(
    manifest:       SkillManifest,
    handler_source: str,
) -> tuple[set[str], set[str]]:
    """
    Compara permisos declarados vs usados.

    Returns:
        (undeclared, unused):
            undeclared — usados por el código pero NO declarados (bloquean carga).
            unused     — declarados pero sin uso detectado (solo warning).
    """
    used       = infer_permissions(handler_source)
    declared   = set(manifest.permissions)
    undeclared = used - declared
    unused     = declared - used
    return undeclared, unused
