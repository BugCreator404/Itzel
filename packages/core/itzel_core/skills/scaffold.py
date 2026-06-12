"""
Generador de scaffolds de skills — usado por `itzel skills create`.

Crea la estructura completa de una skill nueva en skills/community/ (o la
carpeta que se indique), con un handler funcional de ejemplo y documentación
bilingüe. Nunca sobrescribe una carpeta existente (principio #5).

English summary:
    Skill scaffold generator: writes skill.json, a working handler.py example
    and a bilingual README. Refuses to overwrite existing directories.
"""

from __future__ import annotations

from pathlib import Path

from .manifest import _NAME_RE

# ─── plantillas ───────────────────────────────────────────────────────────────

_SKILL_JSON = """\
{{
  "name": "{name}",
  "version": "0.1.0",
  "description": "Describe qué hace tu skill (en español).",
  "description_en": "Describe what your skill does (in English).",
  "author": "{author}",
  "triggers": [],
  "permissions": []
}}
"""

_HANDLER_PY = '''\
"""
Handler de la skill "{name}".

Contrato:
    run(query: str, context: dict) -> str

    query    — texto de la petición del usuario.
    context  — metadata de ejecución: {{"skill": "<nombre>", "dir": "<ruta>"}}.

Permisos:
    Si tu código usa red (httpx, requests…), archivos (pathlib, open…),
    portapapeles (pyperclip) o subprocesos, DEBES declararlo en skill.json
    → "permissions". Si no lo declaras, Itzel bloquea la skill al cargarla.

English: implement run(query, context) -> str. Declare any filesystem /
network / clipboard / system usage in skill.json permissions.
"""

from __future__ import annotations


def run(query: str, context: dict) -> str:
    """Punto de entrada de la skill. Devuelve la respuesta como texto."""
    return f"La skill {name} recibió: {{query}}"
'''

_README_MD = """\
# {name}

> Skill de [Itzel](https://github.com/BugCreator404/itzel) · creada por {author}

## ¿Qué hace? / What does it do?

**ES:** Describe aquí qué hace tu skill.

**EN:** Describe here what your skill does.

## Uso / Usage

```
(ejemplos de frases que activan la skill)
```

## Permisos / Permissions

Esta skill no requiere permisos. Si los necesita, decláralos en `skill.json`:
`filesystem`, `network`, `clipboard`, `system`.

## Desarrollo / Development

```bash
# Itzel detecta la skill automáticamente (hot-reload).
itzel skills list
```
"""


# ─── generador ────────────────────────────────────────────────────────────────

def create_skill(
    name:   str,
    *,
    base_dir: Path,
    author:   str = "",
) -> Path:
    """
    Crea el scaffold completo de una skill nueva.

    Args:
        name:     Nombre en kebab-case (mismo formato que valida el manifest).
        base_dir: Carpeta contenedora (típicamente skills/community/).
        author:   Autor para skill.json y README.

    Returns:
        La ruta de la carpeta creada.

    Raises:
        ValueError:        nombre inválido.
        FileExistsError:   la carpeta ya existe (nunca se sobrescribe).
    """
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Nombre inválido: '{name}'. "
            "Usa kebab-case: minúsculas, dígitos y guiones (2-50 chars)."
        )

    skill_dir = base_dir / name
    if skill_dir.exists():
        raise FileExistsError(f"Ya existe una skill en {skill_dir}")

    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.json").write_text(
        _SKILL_JSON.format(name=name, author=author), encoding="utf-8"
    )
    (skill_dir / "handler.py").write_text(
        _HANDLER_PY.format(name=name), encoding="utf-8"
    )
    (skill_dir / "README.md").write_text(
        _README_MD.format(name=name, author=author or "anónimo"), encoding="utf-8"
    )
    return skill_dir
