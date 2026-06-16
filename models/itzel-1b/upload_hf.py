#!/usr/bin/env python3
"""upload_hf.py — Publica Itzel-1B en Hugging Face Hub.

ES: Sube el GGUF cuantizado (y opcionalmente el adapter LoRA) al repo
    configurado en config.yaml (huggingface.repo_id), junto con una model card
    bilingüe (ES-MX / EN) generada automáticamente. El token NUNCA va en el
    código ni en config: se lee de la variable de entorno HF_TOKEN o del
    keychain del OS (principio #8).

EN: Uploads the quantized GGUF (and optionally the LoRA adapter) to the
    configured HF repo with an auto-generated bilingual model card. The token
    is read from HF_TOKEN env var or the OS keychain — never hardcoded.

Uso / Usage:
    export HF_TOKEN=hf_xxx        # o guárdalo en el keychain (servicio 'itzel')
    python upload_hf.py
    python upload_hf.py --include-adapter --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Optional

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent

# Forzar UTF-8 en la salida (Windows usa cp1252 por defecto).
# Force UTF-8 output on Windows consoles.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ─── configuración ────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        sys.exit("[ERROR] Falta PyYAML:  pip install pyyaml")
    with config_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ─── token (env > keychain) ───────────────────────────────────────────────────

def resolve_token() -> Optional[str]:
    """Obtiene el token de HF sin exponerlo: primero HF_TOKEN, luego keychain."""
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if token:
        return token.strip()
    try:
        import keyring
        stored = keyring.get_password("itzel", "hf-token")
        if stored:
            return stored.strip()
    except Exception:
        pass
    return None


# ─── model card bilingüe ──────────────────────────────────────────────────────

def build_model_card(cfg: dict[str, Any], sha256: str = "") -> str:
    """Genera la model card (README.md del repo HF) en ES-MX e inglés."""
    hf = cfg["huggingface"]
    base = cfg["model"]["base"]
    quant = cfg["export"]["quantization"]
    repo = hf["repo_id"]
    sha_line = f"\n- **SHA256 (GGUF):** `{sha256}`" if sha256 else ""

    # El front-matter YAML es el que Hugging Face usa para indexar el modelo.
    return f"""---
license: {hf['license']}
language:
- es
- en
base_model: {base}
tags:
- itzel
- local-ai
- assistant
- spanish
- mexico
- gguf
- lora
library_name: gguf
pipeline_tag: text-generation
---

# Itzel-1B 🦎

> **ES:** Modelo de IA personal local, hecho en Tijuana, México 🇲🇽
> **EN:** Local personal AI model, made in Tijuana, Mexico 🇲🇽

Itzel-1B es un fine-tune de [{base}](https://huggingface.co/{base})
optimizado para ser una asistente personal **100% local**: corre en tu máquina,
sin nube, sin telemetría, con español mexicano nativo.

Itzel-1B is a fine-tune of `{base}` optimized to be a **100% local** personal
assistant: runs on your machine, no cloud, no telemetry, native Mexican Spanish.

---

## Uso / Usage

### Con llama.cpp / With llama.cpp

```bash
# Descarga el GGUF / Download the GGUF
huggingface-cli download {repo} {cfg['export']['out_name']}-{quant}.gguf --local-dir .

# Ejecuta / Run
./llama-cli -m {cfg['export']['out_name']}-{quant}.gguf -p "Hola Itzel, ¿qué puedes hacer?"
```

### Con la app de Itzel / With the Itzel app

```bash
itzel model use {repo}
itzel chat
```

---

## Detalles / Details

- **Base:** `{base}`
- **Cuantización / Quantization:** {quant}
- **Idiomas / Languages:** Español (MX) · English
- **Método / Method:** LoRA (r={cfg['lora']['r']}, alpha={cfg['lora']['alpha']})
- **Licencia / License:** {hf['license']}{sha_line}

### Verificación de integridad / Integrity check

```bash
sha256sum -c {cfg['export']['out_name']}-{quant}.gguf.sha256
```

---

## Privacidad / Privacy

ES: Itzel-1B está pensado para correr localmente. El modelo **no** envía datos a
ningún servidor. Cualquier conexión externa (p.ej. búsqueda web) es una acción
explícita del usuario.

EN: Itzel-1B is designed to run locally. The model does **not** send data to any
server. Any external connection (e.g. web search) is an explicit user action.

## Reproducir / Reproduce

Todo el pipeline de entrenamiento es abierto:
The full training pipeline is open:

[github.com/BugCreator404/itzel](https://github.com/BugCreator404/itzel) →
`models/itzel-1b/`

```bash
python prepare_data.py && python train.py && python export_gguf.py
```

---

*Hecho con cariño en México · Made with care in Mexico* 🦎
"""


# ─── localizar artefactos ─────────────────────────────────────────────────────

def find_gguf(cfg: dict[str, Any]) -> tuple[Path, str]:
    """Devuelve (ruta_gguf, sha256) o termina si no existe."""
    exp = cfg["export"]
    gguf = _THIS_DIR / exp["out_dir"] / f"{exp['out_name']}-{exp['quantization']}.gguf"
    if not gguf.exists():
        sys.exit(f"[ERROR] No existe el GGUF: {gguf}\n        Ejecuta:  python export_gguf.py")
    sha = ""
    sha_file = gguf.with_suffix(gguf.suffix + ".sha256")
    if sha_file.exists():
        sha = sha_file.read_text(encoding="utf-8").split()[0]
    return gguf, sha


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Sube Itzel-1B a Hugging Face Hub.")
    parser.add_argument("--config", default=str(_THIS_DIR / "config.yaml"))
    parser.add_argument("--include-adapter", action="store_true",
                        help="Sube también el adapter LoRA (outputs/adapter)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Genera la model card y valida, pero NO sube nada")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    hf = cfg["huggingface"]
    repo_id = hf["repo_id"]

    print("\nItzel-1B — publicación en Hugging Face")
    print("─" * 44)
    print(f"  Repo: {repo_id}")

    gguf, sha = find_gguf(cfg)
    card = build_model_card(cfg, sha)
    print(f"  GGUF: {gguf.relative_to(_REPO_ROOT)}")

    # Guardar siempre una copia local de la model card (auditable).
    card_path = _THIS_DIR / "MODEL_CARD.md"
    card_path.write_text(card, encoding="utf-8")
    print(f"  Model card: {card_path.relative_to(_REPO_ROOT)}")

    if args.dry_run:
        print("\n[dry-run] Model card generada. No se subió nada.")
        return 0

    token = resolve_token()
    if not token:
        sys.exit(
            "[ERROR] No hay token de Hugging Face.\n"
            "        Exporta HF_TOKEN o guárdalo en el keychain:\n"
            "          export HF_TOKEN=hf_xxx\n"
            "        Obtén uno en: https://huggingface.co/settings/tokens"
        )

    try:
        from huggingface_hub import HfApi, create_repo
    except ImportError:
        sys.exit("[ERROR] Falta huggingface_hub:  pip install huggingface_hub")

    api = HfApi(token=token)
    print("\n  Creando/verificando repo…")
    create_repo(repo_id, token=token, private=hf["private"], exist_ok=True)

    print("  Subiendo model card…")
    api.upload_file(path_or_fileobj=card.encode("utf-8"), path_in_repo="README.md",
                    repo_id=repo_id, commit_message="docs: model card bilingüe")

    print(f"  Subiendo GGUF ({gguf.stat().st_size / 1024 / 1024:.0f} MB)…")
    api.upload_file(path_or_fileobj=str(gguf), path_in_repo=gguf.name,
                    repo_id=repo_id, commit_message=f"model: {gguf.name}")

    sha_file = gguf.with_suffix(gguf.suffix + ".sha256")
    if sha_file.exists():
        api.upload_file(path_or_fileobj=str(sha_file), path_in_repo=sha_file.name,
                        repo_id=repo_id, commit_message="chore: sha256")

    if args.include_adapter:
        adapter_dir = _THIS_DIR / cfg["training"]["output_dir"] / "adapter"
        if adapter_dir.exists():
            print("  Subiendo adapter LoRA…")
            api.upload_folder(folder_path=str(adapter_dir), path_in_repo="adapter",
                              repo_id=repo_id, commit_message="model: adapter LoRA")
        else:
            print("  [AVISO] No hay adapter para subir — omitido.")

    print(f"\n[OK] Publicado: https://huggingface.co/{repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
