"""
itzel_core.skills — Sistema de skills (plugins) de Itzel.

Una skill es una carpeta con skill.json + handler.py que extiende a Itzel sin
tocar el core. El loader las valida, verifica sus permisos contra el código
real y las expone como Tools del engine (mismo registry que los agentes).

Componentes:
    manifest  — SkillManifest (valida skill.json) + verificación de permisos
    loader    — SkillLoader: escaneo, carga, hot-reload (rescan)
    scaffold  — create_skill(): genera el esqueleto de una skill nueva

Uso típico:
    from itzel_core.skills import SkillLoader

    loader = SkillLoader()           # skills/official + skills/community
    loader.load_all()
    registry.register_all(loader.all_tools())
"""

from __future__ import annotations

from .loader import LoadedSkill, SkillLoader, default_skill_dirs
from .manifest import (
    ALLOWED_PERMISSIONS,
    ManifestError,
    SkillManifest,
    check_permissions,
    infer_permissions,
    load_manifest,
)
from .scaffold import create_skill

__all__ = [
    "SkillLoader",
    "LoadedSkill",
    "default_skill_dirs",
    "SkillManifest",
    "ManifestError",
    "ALLOWED_PERMISSIONS",
    "load_manifest",
    "check_permissions",
    "infer_permissions",
    "create_skill",
]
