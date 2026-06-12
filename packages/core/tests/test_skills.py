"""
Tests del sistema de skills (itzel_core.skills).

Cubre: validación del manifiesto, verificación estática de permisos, carga e
invocación, triggers, hot-reload, scaffold y las 3 skills oficiales reales.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from itzel_core.skills import (
    ALLOWED_PERMISSIONS,
    ManifestError,
    SkillLoader,
    SkillManifest,
    check_permissions,
    create_skill,
    infer_permissions,
    load_manifest,
)

# Carpeta real de skills oficiales del repo (tests de integración).
_OFFICIAL = Path(__file__).resolve().parents[3] / "skills" / "official"


# ─── helpers ──────────────────────────────────────────────────────────────────

def make_skill(
    base: Path,
    name: str,
    handler: str,
    *,
    permissions: list[str] | None = None,
    triggers: list[str] | None = None,
) -> Path:
    """Crea una skill mínima en disco para los tests."""
    d = base / name
    d.mkdir(parents=True)
    (d / "skill.json").write_text(
        json.dumps({
            "name": name,
            "description": f"skill de prueba {name}",
            "permissions": permissions or [],
            "triggers": triggers or [],
        }),
        encoding="utf-8",
    )
    (d / "handler.py").write_text(handler, encoding="utf-8")
    return d


ECHO_HANDLER = 'def run(query, context):\n    return f"eco: {query}"\n'


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    d = tmp_path / "community"
    d.mkdir()
    return d


# ─── manifiesto ───────────────────────────────────────────────────────────────

class TestManifest:
    def test_valid_manifest(self):
        m = SkillManifest(
            name="mi-skill", description="hace cosas",
            triggers=["haz"], permissions=["network"],
        )
        assert m.version == "0.1.0"

    def test_invalid_name_rejected(self):
        with pytest.raises(ValueError):
            SkillManifest(name="Mi Skill!", description="x")

    def test_invalid_version_rejected(self):
        with pytest.raises(ValueError):
            SkillManifest(name="ok-skill", description="x", version="banana")

    def test_unknown_permission_rejected(self):
        with pytest.raises(ValueError):
            SkillManifest(name="ok-skill", description="x", permissions=["root"])

    def test_load_manifest_missing_file(self, tmp_path):
        with pytest.raises(ManifestError):
            load_manifest(tmp_path)

    def test_load_manifest_bad_json(self, tmp_path):
        (tmp_path / "skill.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ManifestError):
            load_manifest(tmp_path)


# ─── permisos (verificación estática) ─────────────────────────────────────────

class TestPermissions:
    def test_infer_network(self):
        assert infer_permissions("import httpx") == {"network"}
        assert infer_permissions("from urllib.request import urlopen") == {"network"}

    def test_infer_filesystem(self):
        assert "filesystem" in infer_permissions("from pathlib import Path")
        assert "filesystem" in infer_permissions("def run(q, c):\n    open('/x')")

    def test_infer_system_and_clipboard(self):
        assert infer_permissions("import subprocess") == {"system"}
        assert infer_permissions("import pyperclip") == {"clipboard"}

    def test_no_permissions_for_pure_code(self):
        assert infer_permissions("import math\ndef run(q, c):\n    return 'x'") == set()

    def test_check_undeclared_and_unused(self):
        m = SkillManifest(name="t-skill", description="x", permissions=["clipboard"])
        undeclared, unused = check_permissions(m, "import httpx")
        assert undeclared == {"network"}
        assert unused == {"clipboard"}

    def test_undeclared_blocks_loading(self, skills_dir):
        """Integración: skill que usa red sin declararla NO carga."""
        make_skill(skills_dir, "espia", "import httpx\ndef run(q, c):\n    return 'x'")
        loader = SkillLoader(dirs=[skills_dir])
        loader.load_all()
        assert "espia" not in loader.skills
        assert "no declarados" in loader.errors["espia"]


# ─── loader ───────────────────────────────────────────────────────────────────

class TestLoader:
    def test_loads_valid_skill(self, skills_dir):
        make_skill(skills_dir, "eco", ECHO_HANDLER)
        loader = SkillLoader(dirs=[skills_dir])
        loader.load_all()
        assert len(loader) == 1
        assert loader.get("eco") is not None

    def test_tool_invocation(self, skills_dir):
        make_skill(skills_dir, "eco", ECHO_HANDLER)
        loader = SkillLoader(dirs=[skills_dir])
        loader.load_all()
        tool = loader.get("eco").tool
        assert tool.name == "skill-eco"
        assert tool("hola") == "eco: hola"

    def test_tool_schema_has_query(self, skills_dir):
        make_skill(skills_dir, "eco", ECHO_HANDLER)
        loader = SkillLoader(dirs=[skills_dir])
        loader.load_all()
        schema = loader.get("eco").tool.to_schema()
        assert "query" in schema["input_schema"]["properties"]

    def test_missing_run_rejected(self, skills_dir):
        make_skill(skills_dir, "rota", "X = 1\n")
        loader = SkillLoader(dirs=[skills_dir])
        loader.load_all()
        assert "rota" not in loader.skills
        assert "run" in loader.errors["rota"]

    def test_disabled_skill_not_loaded(self, skills_dir):
        make_skill(skills_dir, "eco", ECHO_HANDLER)
        loader = SkillLoader(dirs=[skills_dir], disabled={"eco"})
        loader.load_all()
        assert "eco" not in loader.skills
        assert "deshabilitada" in loader.errors["eco"]

    def test_trigger_matching(self, skills_dir):
        make_skill(skills_dir, "clima", ECHO_HANDLER, triggers=["clima", "weather"])
        make_skill(skills_dir, "otro", ECHO_HANDLER, triggers=["nada"])
        loader = SkillLoader(dirs=[skills_dir])
        loader.load_all()
        hits = loader.match_trigger("¿Cómo está el CLIMA hoy?")
        assert [s.name for s in hits] == ["clima"]

    def test_system_permission_requires_confirmation(self, skills_dir):
        make_skill(
            skills_dir, "peligro",
            "import subprocess\ndef run(q, c):\n    return 'x'",
            permissions=["system"],
        )
        loader = SkillLoader(dirs=[skills_dir])
        loader.load_all()
        assert loader.get("peligro").tool.confirm_if_irreversible is True


class TestHotReload:
    def test_rescan_detects_new_skill(self, skills_dir):
        loader = SkillLoader(dirs=[skills_dir])
        loader.load_all()
        assert len(loader) == 0
        make_skill(skills_dir, "nueva", ECHO_HANDLER)
        result = loader.rescan()
        assert result["added"] == ["nueva"]
        assert loader.get("nueva") is not None

    def test_rescan_detects_removed_skill(self, skills_dir):
        make_skill(skills_dir, "efimera", ECHO_HANDLER)
        loader = SkillLoader(dirs=[skills_dir])
        loader.load_all()
        import shutil
        shutil.rmtree(skills_dir / "efimera")
        result = loader.rescan()
        assert result["removed"] == ["efimera"]
        assert loader.get("efimera") is None

    def test_rescan_reloads_modified_handler(self, skills_dir):
        d = make_skill(skills_dir, "viva", ECHO_HANDLER)
        loader = SkillLoader(dirs=[skills_dir])
        loader.load_all()
        assert loader.get("viva").tool("x") == "eco: x"

        time.sleep(0.05)
        (d / "handler.py").write_text(
            'def run(query, context):\n    return "v2"\n', encoding="utf-8"
        )
        # Forzar mtime distinto (en FS con resolución gruesa)
        os.utime(d / "handler.py")
        result = loader.rescan()
        assert result["reloaded"] == ["viva"]
        assert loader.get("viva").tool("x") == "v2"


# ─── scaffold ─────────────────────────────────────────────────────────────────

class TestScaffold:
    def test_creates_complete_structure(self, tmp_path):
        d = create_skill("demo-skill", base_dir=tmp_path, author="tester")
        assert (d / "skill.json").exists()
        assert (d / "handler.py").exists()
        assert (d / "README.md").exists()

    def test_scaffold_loads_via_loader(self, tmp_path):
        """Round-trip: lo que genera el scaffold carga sin errores."""
        create_skill("demo-skill", base_dir=tmp_path)
        loader = SkillLoader(dirs=[tmp_path])
        loader.load_all()
        assert loader.errors == {}
        assert "recibió" in loader.get("demo-skill").tool("hola")

    def test_never_overwrites(self, tmp_path):
        create_skill("demo-skill", base_dir=tmp_path)
        with pytest.raises(FileExistsError):
            create_skill("demo-skill", base_dir=tmp_path)

    def test_invalid_name(self, tmp_path):
        with pytest.raises(ValueError):
            create_skill("Demo Skill", base_dir=tmp_path)


# ─── skills oficiales reales ──────────────────────────────────────────────────

@pytest.mark.skipif(not _OFFICIAL.is_dir(), reason="skills/official no encontrada")
class TestOfficialSkills:
    def test_all_official_skills_load(self):
        loader = SkillLoader(dirs=[_OFFICIAL])
        loader.load_all()
        assert loader.errors == {}
        assert set(loader.skills) >= {"calculator", "clipboard", "web-search"}

    def test_calculator_evaluates(self):
        loader = SkillLoader(dirs=[_OFFICIAL])
        loader.load_all()
        tool = loader.get("calculator").tool
        assert "= 22" in tool("calcula 2 + 2 * 10")
        assert "= 20" in tool("cuánto es sqrt(144) + 8")

    def test_calculator_blocks_injection(self):
        loader = SkillLoader(dirs=[_OFFICIAL])
        loader.load_all()
        tool = loader.get("calculator").tool
        for evil in (
            '__import__("os").system("dir")',
            'open("/etc/passwd")',
            "().__class__.__bases__",
            "9**9**9",
        ):
            out = tool(evil)
            assert "No pude evaluar" in out, f"inyección no bloqueada: {evil}"

    def test_official_permissions_are_minimal(self):
        """Las oficiales declaran solo lo permitido y conocido."""
        loader = SkillLoader(dirs=[_OFFICIAL])
        loader.load_all()
        for skill in loader.skills.values():
            assert set(skill.manifest.permissions) <= ALLOWED_PERMISSIONS
        assert loader.get("calculator").manifest.permissions == []
        assert loader.get("web-search").manifest.permissions == ["network"]
