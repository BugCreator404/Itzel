"""Comando: itzel skills [list|install|remove|create]

Gestiona las skills (plugins) de Itzel — operación local pura, sin backend.

Subcomandos:
  list               — tabla con nombre, versión, permisos y estado
  create <nombre>    — genera el scaffold en skills/community/
  install <origen>   — instala desde una ruta local o un repo de GitHub
  remove <nombre>    — desinstala una skill de community (con confirmación)
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import typer

from ..output import (
    confirm,
    console,
    err,
    hint,
    info,
    make_table,
    ok,
    warn,
)

app = typer.Typer(help="Gestiona skills de Itzel")

_COMMUNITY = Path.cwd() / "skills" / "community"
_OFFICIAL   = Path.cwd() / "skills" / "official"


# ─── helpers ──────────────────────────────────────────────────────────────────

def _core():
    """Importa itzel_core.skills o explica cómo instalarlo."""
    try:
        from itzel_core import skills as sk
        return sk
    except ImportError:
        err(
            "itzel-core no está instalado en este entorno.",
            hint="Instálalo con: pip install -e packages/core",
        )
        return None


def _make_loader(sk):
    """SkillLoader con las skills deshabilitadas de la config."""
    try:
        from itzel_core.config import config
        disabled = set(config.skills.disabled)
    except Exception:
        disabled = set()
    return sk.SkillLoader(disabled=disabled)


# ─── list ─────────────────────────────────────────────────────────────────────

@app.command("list")
def list_skills() -> None:
    """Lista las skills instaladas con su estado."""
    sk = _core()
    if sk is None:
        return

    loader = _make_loader(sk)
    loader.load_all()

    if not loader.skills and not loader.errors:
        warn("No hay skills instaladas.")
        hint("Crea una con:  itzel skills create mi-skill")
        return

    table = make_table(
        "Skills de Itzel",
        ("Nombre",      "#f9a8d4", 16),
        ("Versión",     "#9890b8",  8),
        ("Estado",      "",        14),
        ("Permisos",    "#9890b8", 20),
        ("Descripción", "",        44),
    )

    for skill in sorted(loader.skills.values(), key=lambda s: s.name):
        perms = ", ".join(skill.manifest.permissions) or "—"
        table.add_row(
            skill.name,
            skill.manifest.version,
            "[#4ecdc4]● ok[/]",
            perms,
            skill.manifest.description[:80],
        )

    for name, error in sorted(loader.errors.items()):
        estado = (
            "[#fbbf24]◌ deshabilitada[/]" if "deshabilitada" in error
            else "[#f87171]✗ error[/]"
        )
        table.add_row(name, "—", estado, "—", f"[dim]{error[:80]}[/]")

    console.print(table)
    console.print(
        f"\n[dim]{len(loader.skills)} activa(s) · {len(loader.errors)} con problema(s)[/]"
    )


# ─── create ───────────────────────────────────────────────────────────────────

@app.command("create")
def create(
    name:   str = typer.Argument(..., help="Nombre de la skill (kebab-case)"),
    author: str = typer.Option("", "--author", "-a", help="Autor para skill.json"),
) -> None:
    """Crea el scaffold de una skill nueva en skills/community/."""
    sk = _core()
    if sk is None:
        return

    try:
        skill_dir = sk.create_skill(name, base_dir=_COMMUNITY, author=author)
    except (ValueError, FileExistsError) as exc:
        err(str(exc))
        raise typer.Exit(1) from None

    ok(f"Skill creada en {skill_dir}")
    info("Archivos: skill.json · handler.py · README.md")
    hint(
        "Siguientes pasos:\n"
        "  1. Edita handler.py — implementa run(query, context)\n"
        "  2. Declara permisos en skill.json si usas red/archivos/portapapeles\n"
        "  3. Verifica con:  itzel skills list"
    )


# ─── remove ───────────────────────────────────────────────────────────────────

@app.command("remove")
def remove(
    name: str  = typer.Argument(..., help="Skill a desinstalar"),
    yes:  bool = typer.Option(False, "--yes", "-y", help="Sin confirmación"),
) -> None:
    """Desinstala una skill de community (las oficiales se deshabilitan, no se borran)."""
    community_dir = _COMMUNITY / name
    official_dir  = _OFFICIAL / name

    if official_dir.is_dir():
        warn(f"'{name}' es una skill oficial — no se borra del repo.")
        hint(
            'Deshabilítala en itzel.config.json:\n'
            f'  "skills": {{ "disabled": ["{name}"] }}'
        )
        return

    if not community_dir.is_dir():
        err(f"Skill '{name}' no encontrada en {_COMMUNITY}")
        raise typer.Exit(1)

    if not yes:
        confirm(f"¿Eliminar la skill '{name}'? Se borra {community_dir} por completo.")

    shutil.rmtree(community_dir)
    ok(f"Skill '{name}' eliminada.")


# ─── install ──────────────────────────────────────────────────────────────────

@app.command("install")
def install(
    source: str  = typer.Argument(
        ..., help="Ruta local de la skill o URL/owner-repo de GitHub"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Sin confirmación"),
) -> None:
    """Instala una skill desde una carpeta local o un repositorio de GitHub."""
    sk = _core()
    if sk is None:
        return

    local = Path(source).expanduser()
    cloned_tmp: Path | None = None

    try:
        # ── origen: GitHub ────────────────────────────────────────────
        if not local.is_dir():
            url = _normalize_github(source)
            if url is None:
                err(f"Origen no válido: '{source}' no es carpeta ni repo de GitHub.")
                raise typer.Exit(1)
            if shutil.which("git") is None:
                err("git no está instalado — necesario para instalar desde GitHub.")
                raise typer.Exit(1)
            info(f"Clonando {url}…")
            cloned_tmp = Path(tempfile.mkdtemp(prefix="itzel-skill-"))
            result = subprocess.run(
                ["git", "clone", "--depth", "1", url, str(cloned_tmp / "repo")],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                err(f"No se pudo clonar: {result.stderr.strip()[:200]}")
                raise typer.Exit(1)
            local = cloned_tmp / "repo"

        # ── validar el manifiesto ANTES de copiar ─────────────────────
        try:
            manifest = sk.load_manifest(local)
        except sk.ManifestError as exc:
            err(f"La carpeta no es una skill válida: {exc}")
            raise typer.Exit(1) from None

        dest = _COMMUNITY / manifest.name
        if dest.exists():
            err(f"Ya existe una skill llamada '{manifest.name}' en {dest}")
            hint(f"Elimínala primero con:  itzel skills remove {manifest.name}")
            raise typer.Exit(1)

        # ── confirmación con permisos a la vista (principio #5) ───────
        perms = ", ".join(manifest.permissions) or "ninguno"
        info(f"Skill: {manifest.name} v{manifest.version} · autor: {manifest.author or '?'}")
        info(f"Permisos que pide: {perms}")
        if not yes:
            confirm(f"¿Instalar '{manifest.name}' en skills/community/?")

        # ── copiar e inmediatamente validar con el loader real ────────
        _COMMUNITY.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            local, dest,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )

        loader = sk.SkillLoader(dirs=[_COMMUNITY])
        loader.load_all()
        if manifest.name not in loader.skills:
            reason = loader.errors.get(manifest.name, "validación fallida")
            shutil.rmtree(dest, ignore_errors=True)   # rollback
            err(f"Skill rechazada y revertida: {reason}")
            raise typer.Exit(1)

        ok(f"Skill '{manifest.name}' instalada y validada.")
        reqs = dest / "requirements.txt"
        if reqs.exists():
            hint(f"Tiene dependencias extra:  pip install -r {reqs}")

    finally:
        if cloned_tmp is not None:
            shutil.rmtree(cloned_tmp, ignore_errors=True)


def _normalize_github(source: str) -> str | None:
    """'owner/repo' o URL de GitHub → URL clonable; None si no aplica."""
    s = source.strip().rstrip("/")
    if s.startswith(("https://github.com/", "git@github.com:")):
        return s if s.endswith(".git") else s + ".git"
    # forma corta owner/repo
    parts = s.split("/")
    if len(parts) == 2 and all(p and "/" not in p and " " not in p for p in parts):
        return f"https://github.com/{s}.git"
    return None
