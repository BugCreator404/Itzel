#!/usr/bin/env python3
"""prepare_data.py — Limpieza y preparación del dataset de Itzel-1B.

ES: Carga los ejemplos crudos de cada categoría (JSONL), valida el esquema,
    deduplica, baraja con semilla fija, divide en train/val y los formatea
    como ChatML de Qwen2.5. El resultado se escribe en datasets/_prepared/.

EN: Loads raw examples from each category (JSONL), validates the schema,
    deduplicates, shuffles deterministically, splits train/val and formats
    them as Qwen2.5 ChatML. Output goes to datasets/_prepared/.

Esquema de cada ejemplo de entrada / Input example schema:
    {
      "instruction": "Organiza los archivos de Downloads por tipo",
      "input": "",
      "output": "Voy a revisar tu carpeta Downloads...",
      "language": "es-MX",
      "category": "file_management"
    }

Uso / Usage:
    python prepare_data.py                  # usa config.yaml
    python prepare_data.py --dry-run        # solo valida y reporta, no escribe
    python prepare_data.py --config otra.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

# Raíz del repo = dos niveles arriba de este archivo (models/itzel-1b/).
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent

# Forzar UTF-8 en la salida: la consola de Windows usa cp1252 y rompe con
# acentos y caracteres de caja. Force UTF-8 output (Windows consoles use cp1252).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

# Campos obligatorios y opcionales del esquema de ejemplos.
_REQUIRED_FIELDS = ("instruction", "output")
_OPTIONAL_FIELDS = {"input": "", "language": "es-MX", "category": "general"}


# ─── carga de configuración ───────────────────────────────────────────────────

def load_config(config_path: Path) -> dict[str, Any]:
    """Carga config.yaml. Falla con mensaje claro si falta PyYAML."""
    try:
        import yaml
    except ImportError:
        sys.exit(
            "[ERROR] Falta PyYAML. Instala con:  pip install pyyaml\n"
            "        Missing PyYAML. Install with: pip install pyyaml"
        )
    if not config_path.exists():
        sys.exit(f"[ERROR] No se encontró config: {config_path}")
    with config_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ─── carga y validación de ejemplos ───────────────────────────────────────────

def _iter_jsonl_files(category_dir: Path):
    """Devuelve todos los .jsonl de una categoría (recursivo)."""
    if not category_dir.exists():
        return
    yield from sorted(category_dir.rglob("*.jsonl"))


def _validate_example(obj: dict[str, Any], source: str, lineno: int) -> dict[str, Any] | None:
    """Valida y normaliza un ejemplo. Devuelve None si es inválido (con aviso)."""
    # Campos obligatorios presentes y no vacíos.
    for field in _REQUIRED_FIELDS:
        if not isinstance(obj.get(field), str) or not obj[field].strip():
            print(f"  [AVISO] {source}:{lineno} — falta o vacío '{field}', se omite.")
            return None
    # Rellenar opcionales con defaults.
    clean: dict[str, Any] = {}
    for field in _REQUIRED_FIELDS:
        clean[field] = obj[field].strip()
    for field, default in _OPTIONAL_FIELDS.items():
        value = obj.get(field, default)
        clean[field] = value.strip() if isinstance(value, str) else default
    return clean


def load_examples(dataset_dir: Path, categories: list[str]) -> list[dict[str, Any]]:
    """Carga todos los ejemplos válidos de las categorías indicadas."""
    examples: list[dict[str, Any]] = []
    for category in categories:
        cat_dir = dataset_dir / category
        count_before = len(examples)
        for jsonl_file in _iter_jsonl_files(cat_dir):
            rel = jsonl_file.relative_to(dataset_dir)
            with jsonl_file.open(encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line or line.startswith("//"):
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as exc:
                        print(f"  [AVISO] {rel}:{lineno} — JSON inválido ({exc.msg}), se omite.")
                        continue
                    clean = _validate_example(obj, str(rel), lineno)
                    if clean is not None:
                        # Si la categoría no viene en el ejemplo, usar la carpeta.
                        if clean["category"] == "general":
                            clean["category"] = category
                        examples.append(clean)
        loaded = len(examples) - count_before
        marker = "OK " if loaded else "·· "
        print(f"  [{marker}] {category}: {loaded} ejemplos")
    return examples


# ─── deduplicación ────────────────────────────────────────────────────────────

def _example_hash(ex: dict[str, Any]) -> str:
    """Hash estable de (instruction + input + output) para detectar duplicados."""
    raw = f"{ex['instruction']}\x00{ex['input']}\x00{ex['output']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def deduplicate(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Elimina duplicados exactos conservando el primer aparecido."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for ex in examples:
        h = _example_hash(ex)
        if h not in seen:
            seen.add(h)
            unique.append(ex)
    removed = len(examples) - len(unique)
    if removed:
        print(f"  [OK ] Deduplicación: {removed} duplicados eliminados.")
    return unique


# ─── formato ChatML (Qwen2.5) ─────────────────────────────────────────────────

def to_chatml(ex: dict[str, Any], system_es: str, system_en: str) -> dict[str, Any]:
    """Convierte un ejemplo Alpaca a un registro de mensajes estilo ChatML.

    El system prompt se elige según el idioma del ejemplo. El campo 'input'
    (contexto) se concatena al final de la instrucción si existe.
    """
    system = system_en if ex["language"].lower().startswith("en") else system_es
    user_content = ex["instruction"]
    if ex["input"]:
        user_content = f"{user_content}\n\n{ex['input']}"

    return {
        "messages": [
            {"role": "system", "content": system.strip()},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": ex["output"]},
        ],
        # Metadatos conservados para análisis posterior (no se entrenan).
        "language": ex["language"],
        "category": ex["category"],
    }


# ─── split train/val ──────────────────────────────────────────────────────────

def split_train_val(
    records: list[dict[str, Any]], train_split: float, seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Baraja de forma determinista y divide en train/val."""
    rng = random.Random(seed)
    shuffled = records[:]
    rng.shuffle(shuffled)
    cut = int(len(shuffled) * train_split)
    return shuffled[:cut], shuffled[cut:]


# ─── escritura ────────────────────────────────────────────────────────────────

def write_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    """Escribe registros como JSONL (una línea por ejemplo)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ─── reporte ──────────────────────────────────────────────────────────────────

def _report_distribution(records: list[dict[str, Any]]) -> None:
    """Imprime la distribución por idioma y categoría (sanidad del dataset)."""
    by_lang: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    for rec in records:
        by_lang[rec["language"]] = by_lang.get(rec["language"], 0) + 1
        by_cat[rec["category"]] = by_cat.get(rec["category"], 0) + 1

    print("\n  Distribución por idioma / By language:")
    for lang, n in sorted(by_lang.items(), key=lambda kv: -kv[1]):
        print(f"    {lang:10s} {n}")
    print("  Distribución por categoría / By category:")
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"    {cat:18s} {n}")


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Prepara el dataset de Itzel-1B.")
    parser.add_argument("--config", default=str(_THIS_DIR / "config.yaml"),
                        help="Ruta a config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo valida y reporta, no escribe archivos")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    data_cfg = cfg["data"]
    chat_cfg = cfg["chat"]

    dataset_dir = (_REPO_ROOT / data_cfg["dataset_dir"]).resolve()
    print(f"\nItzel-1B — preparación de datos")
    print(f"{'─' * 44}")
    print(f"Dataset: {dataset_dir}\n")

    # 1. Cargar + validar
    examples = load_examples(dataset_dir, data_cfg["categories"])
    if not examples:
        print("\n[ERROR] No se cargó ningún ejemplo válido. "
              "Agrega datos en models/datasets/<categoria>/*.jsonl")
        return 1

    # 2. Deduplicar
    examples = deduplicate(examples)

    # 3. Convertir a ChatML
    records = [
        to_chatml(ex, chat_cfg["system_prompt_es"], chat_cfg["system_prompt_en"])
        for ex in examples
    ]

    # 4. Split
    train, val = split_train_val(records, data_cfg["train_split"], data_cfg["shuffle_seed"])

    print(f"\n  Total: {len(records)}  →  train: {len(train)}  ·  val: {len(val)}")
    _report_distribution(records)

    # 5. Escribir
    if args.dry_run:
        print("\n[dry-run] No se escribió nada. Quita --dry-run para generar los archivos.")
        return 0

    train_path = dataset_dir / data_cfg["prepared_train"]
    val_path = dataset_dir / data_cfg["prepared_val"]
    write_jsonl(train, train_path)
    write_jsonl(val, val_path)

    print(f"\n[OK] Escrito:")
    print(f"     {train_path.relative_to(_REPO_ROOT)}")
    print(f"     {val_path.relative_to(_REPO_ROOT)}")
    print("\nSiguiente paso: python train.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
