"""
Cargador de skills — escaneo, validación, hot-reload.

El loader recorre las carpetas de skills (official/ y community/), valida cada
skill.json, verifica los permisos contra el código real del handler (análisis
estático) y envuelve cada skill como una Tool del sistema de itzel_core.tools —
así el LLM las invoca con la misma confirmación y auditoría que las tools de
los agentes.

Contrato del handler (handler.py):
    def run(query: str, context: dict) -> str:
        ...

Seguridad:
    - Permiso usado sin declarar → la skill NO carga (fail-safe).
    - Permiso "system" declarado → la tool pide confirmación al usuario.
    - Las skills deshabilitadas en config no se cargan.

Hot-reload:
    rescan() detecta skills nuevas, eliminadas o modificadas (mtime de
    skill.json y handler.py) sin reiniciar el proceso.

English summary:
    Skill loader with manifest validation, static permission enforcement,
    Tool wrapping (reusing the Session 9 tool system) and mtime-based
    hot-reload via rescan().
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..tools import Tool
from .manifest import ManifestError, SkillManifest, check_permissions, load_manifest

log = logging.getLogger("itzel.skills")

#: Timeout por defecto para la ejecución de una skill (segundos).
_SKILL_TIMEOUT = 30


def default_skill_dirs() -> list[Path]:
    """
    Carpetas de skills por defecto, en orden de prioridad:
        1. ./skills/official y ./skills/community (repo / desarrollo)
        2. ~/.itzel/skills (instalación de usuario)
    Solo devuelve las que existen.
    """
    candidates = [
        Path.cwd() / "skills" / "official",
        Path.cwd() / "skills" / "community",
        Path.home() / ".itzel" / "skills",
    ]
    return [p for p in candidates if p.is_dir()]


# ─── skill cargada ────────────────────────────────────────────────────────────

@dataclass
class LoadedSkill:
    """Una skill validada, importada y lista para invocar."""
    manifest: SkillManifest
    dir:      Path
    tool:     Tool
    module:   Any
    #: mtimes de skill.json y handler.py — para detectar cambios en rescan()
    mtimes:   dict[str, float] = field(default_factory=dict)
    #: permisos declarados pero sin uso detectado (informativo)
    unused_permissions: set[str] = field(default_factory=set)

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def tool_name(self) -> str:
        return self.tool.name


# ─── loader ───────────────────────────────────────────────────────────────────

class SkillLoader:
    """
    Escanea, valida y carga skills como Tools.

    Uso:
        loader = SkillLoader()           # usa default_skill_dirs()
        loader.load_all()
        tools  = loader.all_tools()      # para el ToolRegistry / LLM
        hits   = loader.match_trigger("busca el clima en Tijuana")
        loader.rescan()                  # hot-reload
    """

    def __init__(
        self,
        dirs:     list[Path] | None = None,
        *,
        disabled: set[str] | None = None,
    ) -> None:
        self.dirs     = dirs if dirs is not None else default_skill_dirs()
        self.disabled = disabled or set()
        self.skills:  dict[str, LoadedSkill] = {}
        #: nombre o carpeta → mensaje de error (para `itzel skills list`)
        self.errors:  dict[str, str] = {}
        self._lock = threading.Lock()

    # ── carga ─────────────────────────────────────────────────────────────────

    def load_all(self) -> None:
        """Escanea todas las carpetas y carga cada skill válida."""
        with self._lock:
            self.skills.clear()
            self.errors.clear()
            for skill_dir in self._discover_dirs():
                self._load_one(skill_dir)
        log.info(
            "Skills cargadas: %d ok, %d con error.",
            len(self.skills), len(self.errors),
        )

    def rescan(self) -> dict[str, list[str]]:
        """
        Hot-reload: detecta skills nuevas, eliminadas y modificadas.

        Returns:
            {"added": [...], "removed": [...], "reloaded": [...]}
        """
        with self._lock:
            current = {d.name: d for d in self._discover_dirs()}
            added, removed, reloaded = [], [], []

            # Eliminadas
            for name in list(self.skills):
                if self.skills[name].dir.name not in current:
                    del self.skills[name]
                    removed.append(name)

            for dir_name, skill_dir in current.items():
                existing = next(
                    (s for s in self.skills.values() if s.dir == skill_dir), None
                )
                if existing is None:
                    # Nueva (o antes fallida — reintenta)
                    if self._load_one(skill_dir):
                        added.append(dir_name)
                elif self._mtimes(skill_dir) != existing.mtimes:
                    # Modificada → recargar
                    del self.skills[existing.name]
                    if self._load_one(skill_dir):
                        reloaded.append(dir_name)

            return {"added": added, "removed": removed, "reloaded": reloaded}

    # ── consultas ─────────────────────────────────────────────────────────────

    def all_tools(self) -> list[Tool]:
        """Tools de todas las skills cargadas — para registrar en el engine."""
        return [s.tool for s in self.skills.values()]

    def get(self, name: str) -> LoadedSkill | None:
        return self.skills.get(name)

    def match_trigger(self, text: str) -> list[LoadedSkill]:
        """Skills cuyos triggers aparecen en el texto (ruteo por keyword)."""
        lowered = text.lower()
        return [
            s for s in self.skills.values()
            if any(t.lower() in lowered for t in s.manifest.triggers)
        ]

    def __len__(self) -> int:
        return len(self.skills)

    # ── internals ─────────────────────────────────────────────────────────────

    def _discover_dirs(self) -> list[Path]:
        """Subcarpetas que contienen un skill.json."""
        found: list[Path] = []
        for base in self.dirs:
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                if child.is_dir() and (child / "skill.json").exists():
                    found.append(child)
        return found

    def _load_one(self, skill_dir: Path) -> bool:
        """Carga una skill. Registra el error si falla. True si cargó."""
        label = skill_dir.name
        try:
            manifest = load_manifest(skill_dir)
        except ManifestError as exc:
            self.errors[label] = str(exc)
            log.warning("Skill '%s' rechazada: %s", label, exc)
            return False

        if manifest.name in self.disabled:
            self.errors[manifest.name] = "deshabilitada en itzel.config.json"
            log.info("Skill '%s' deshabilitada por config.", manifest.name)
            return False

        if manifest.name in self.skills:
            self.errors[label] = (
                f"nombre duplicado: '{manifest.name}' ya cargada desde "
                f"{self.skills[manifest.name].dir}"
            )
            return False

        handler_path = skill_dir / "handler.py"
        if not handler_path.exists():
            self.errors[manifest.name] = "falta handler.py"
            return False

        # Verificación estática de permisos (fail-safe)
        source = handler_path.read_text(encoding="utf-8")
        undeclared, unused = check_permissions(manifest, source)
        if undeclared:
            self.errors[manifest.name] = (
                f"usa permisos no declarados: {sorted(undeclared)} — "
                "decláralos en skill.json o elimina su uso"
            )
            log.warning(
                "Skill '%s' bloqueada — permisos sin declarar: %s",
                manifest.name, sorted(undeclared),
            )
            return False
        if unused:
            log.info(
                "Skill '%s': permisos declarados sin uso detectado: %s",
                manifest.name, sorted(unused),
            )

        # Importar el handler como módulo aislado
        try:
            module = self._import_handler(manifest.name, handler_path)
        except Exception as exc:
            self.errors[manifest.name] = f"error al importar handler.py: {exc}"
            log.warning("Skill '%s' no importó: %s", manifest.name, exc)
            return False

        run_fn = getattr(module, "run", None)
        if not callable(run_fn):
            self.errors[manifest.name] = "handler.py no exporta run(query, context)"
            return False
        try:
            params = inspect.signature(run_fn).parameters
            if len(params) < 2:
                self.errors[manifest.name] = (
                    "run() debe aceptar (query: str, context: dict)"
                )
                return False
        except (TypeError, ValueError):
            pass  # builtins/extensiones sin firma inspectable — permitir

        skill = LoadedSkill(
            manifest = manifest,
            dir      = skill_dir,
            tool     = self._make_tool(manifest, run_fn, skill_dir),
            module   = module,
            mtimes   = self._mtimes(skill_dir),
            unused_permissions = unused,
        )
        self.skills[manifest.name] = skill
        self.errors.pop(manifest.name, None)
        log.info("Skill cargada: %s v%s", manifest.name, manifest.version)
        return True

    @staticmethod
    def _import_handler(name: str, handler_path: Path) -> Any:
        """Importa handler.py como módulo con nombre único (permite recarga)."""
        module_name = f"itzel_skill_{name.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, handler_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"No se pudo crear el spec para {handler_path}")
        module = importlib.util.module_from_spec(spec)
        # Registrar (y reemplazar si existía) para que dataclasses/typing funcionen
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _make_tool(
        manifest:  SkillManifest,
        run_fn:    Callable[[str, dict], str],
        skill_dir: Path,
    ) -> Tool:
        """
        Envuelve run() como Tool del sistema de tools (Sesión 9).

        - Nombre: "skill-<name>" (compatible con tool-calling: [a-zA-Z0-9_-]).
        - Permiso "system" → confirmación obligatoria antes de ejecutar.
        """
        context = {"skill": manifest.name, "dir": str(skill_dir)}

        def skill_runner(query: str) -> str:
            return run_fn(query, dict(context))

        description = manifest.description
        if manifest.description_en:
            description = f"{manifest.description} / {manifest.description_en}"

        return Tool(
            skill_runner,
            name        = f"skill-{manifest.name}",
            description = description,
            confirm_if_irreversible = "system" in manifest.permissions,
            timeout     = _SKILL_TIMEOUT,
            param_docs  = {"query": "Petición del usuario para esta skill."},
        )

    @staticmethod
    def _mtimes(skill_dir: Path) -> dict[str, float]:
        """mtimes de los archivos que disparan recarga al cambiar."""
        out: dict[str, float] = {}
        for fname in ("skill.json", "handler.py"):
            f = skill_dir / fname
            if f.exists():
                out[fname] = f.stat().st_mtime
        return out
