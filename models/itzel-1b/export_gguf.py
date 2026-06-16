#!/usr/bin/env python3
"""export_gguf.py — Exporta Itzel-1B a GGUF cuantizado.

ES: Pipeline de exportación para correr Itzel-1B con llama.cpp:
      1. Fusiona los pesos LoRA (outputs/adapter) con el modelo base.
      2. Convierte el modelo fusionado a GGUF f16 (convert_hf_to_gguf.py).
      3. Cuantiza f16 → Q4_K_M (binario llama-quantize).
      4. Verifica que el GGUF es válido (cabecera mágica 'GGUF').
      5. Genera un archivo .sha256 para verificación de integridad.

EN: Export pipeline to run Itzel-1B with llama.cpp: merge LoRA → GGUF f16 →
    quantize Q4_K_M → verify magic header → write SHA256.

Requisitos / Requirements:
    - transformers + peft + torch (para fusionar)
    - llama.cpp clonado y compilado (convert_hf_to_gguf.py + llama-quantize)
      Indica su ruta en config.yaml (export.llamacpp_dir) o se autodetecta.

Uso / Usage:
    python export_gguf.py
    python export_gguf.py --llamacpp ~/llama.cpp --keep-merged
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
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

# Magia de archivo GGUF: los 4 primeros bytes son 'GGUF'.
_GGUF_MAGIC = b"GGUF"


# ─── configuración ────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        sys.exit("[ERROR] Falta PyYAML:  pip install pyyaml")
    with config_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ─── localizar llama.cpp ──────────────────────────────────────────────────────

def find_llamacpp(explicit: Optional[str]) -> Path:
    """Localiza el directorio de llama.cpp. Orden: --llamacpp > config > comunes."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("LLAMACPP_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    candidates += [
        Path.home() / "llama.cpp",
        Path.home() / "src" / "llama.cpp",
        _REPO_ROOT / "llama.cpp",
        _REPO_ROOT.parent / "llama.cpp",
    ]
    for cand in candidates:
        if cand.exists() and (cand / "convert_hf_to_gguf.py").exists():
            return cand
    sys.exit(
        "[ERROR] No se encontró llama.cpp con convert_hf_to_gguf.py.\n"
        "        Clónalo y compílalo:\n"
        "          git clone https://github.com/ggerganov/llama.cpp\n"
        "          cd llama.cpp && cmake -B build && cmake --build build -j\n"
        "        Luego indica su ruta: python export_gguf.py --llamacpp /ruta/llama.cpp"
    )


def find_quantize_binary(llamacpp_dir: Path) -> Path:
    """Busca el binario llama-quantize (nombres y ubicaciones varían por versión/OS)."""
    names = ["llama-quantize", "llama-quantize.exe", "quantize", "quantize.exe"]
    search_dirs = [
        llamacpp_dir / "build" / "bin",
        llamacpp_dir / "build" / "bin" / "Release",
        llamacpp_dir / "build",
        llamacpp_dir,
    ]
    for d in search_dirs:
        for name in names:
            candidate = d / name
            if candidate.exists():
                return candidate
    sys.exit(
        f"[ERROR] No se encontró el binario de cuantización en {llamacpp_dir}.\n"
        "        Compila llama.cpp:  cmake -B build && cmake --build build -j"
    )


# ─── paso 1: fusionar LoRA ────────────────────────────────────────────────────

def merge_adapter(base: str, adapter_dir: Path, merged_dir: Path) -> None:
    """Fusiona el adapter LoRA con el modelo base y guarda en formato HF."""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import PeftModel
    except ImportError as exc:
        sys.exit(f"[ERROR] Falta dependencia: {exc}\n        pip install transformers peft torch")

    if not adapter_dir.exists():
        sys.exit(f"[ERROR] No existe el adapter: {adapter_dir}\n        Ejecuta:  python train.py")

    print(f"  [1/5] Fusionando adapter con {base}…")
    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.float16)
    model = PeftModel.from_pretrained(model, str(adapter_dir))
    model = model.merge_and_unload()           # funde LoRA en los pesos base

    merged_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(merged_dir), safe_serialization=True)
    # El tokenizer puede vivir en el adapter o en el base.
    tok_src = adapter_dir if (adapter_dir / "tokenizer_config.json").exists() else base
    AutoTokenizer.from_pretrained(str(tok_src)).save_pretrained(str(merged_dir))
    print(f"        Modelo fusionado en: {merged_dir.relative_to(_REPO_ROOT)}")


# ─── paso 2: convertir a GGUF f16 ─────────────────────────────────────────────

def convert_to_gguf(llamacpp_dir: Path, merged_dir: Path, f16_path: Path) -> None:
    """Convierte el modelo HF fusionado a GGUF f16."""
    print("  [2/5] Convirtiendo a GGUF f16…")
    script = llamacpp_dir / "convert_hf_to_gguf.py"
    f16_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(script), str(merged_dir),
        "--outfile", str(f16_path), "--outtype", "f16",
    ]
    _run(cmd, "Conversión a GGUF")


# ─── paso 3: cuantizar ────────────────────────────────────────────────────────

def quantize(quant_bin: Path, f16_path: Path, out_path: Path, quant_type: str) -> None:
    """Cuantiza el GGUF f16 al tipo indicado (p.ej. Q4_K_M)."""
    print(f"  [3/5] Cuantizando a {quant_type}…")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run([str(quant_bin), str(f16_path), str(out_path), quant_type], "Cuantización")


# ─── paso 4: verificar ────────────────────────────────────────────────────────

def verify_gguf(path: Path) -> None:
    """Verifica que el archivo existe, no está vacío y tiene la magia GGUF."""
    print("  [4/5] Verificando integridad del GGUF…")
    if not path.exists() or path.stat().st_size == 0:
        sys.exit(f"[ERROR] El GGUF no se generó o está vacío: {path}")
    with path.open("rb") as fh:
        magic = fh.read(4)
    if magic != _GGUF_MAGIC:
        sys.exit(f"[ERROR] Cabecera inválida: se esperaba 'GGUF', se obtuvo {magic!r}")
    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"        Cabecera GGUF OK · {size_mb:.1f} MB")


# ─── paso 5: SHA256 ───────────────────────────────────────────────────────────

def write_sha256(path: Path) -> str:
    """Calcula el SHA256 del GGUF y lo escribe en <archivo>.sha256."""
    print("  [5/5] Generando SHA256…")
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            sha.update(chunk)
    digest = sha.hexdigest()
    sha_path = path.with_suffix(path.suffix + ".sha256")
    # Formato estándar: "<hash>  <nombre>" (compatible con sha256sum -c).
    sha_path.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    print(f"        {digest}")
    print(f"        Escrito en: {sha_path.relative_to(_REPO_ROOT)}")
    return digest


# ─── utilidad ─────────────────────────────────────────────────────────────────

def _run(cmd: list[str], label: str) -> None:
    """Ejecuta un subproceso mostrando un error claro si falla."""
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        sys.exit(f"[ERROR] {label}: comando no encontrado — {cmd[0]}")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"[ERROR] {label} falló (código {exc.returncode}).")


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta Itzel-1B a GGUF.")
    parser.add_argument("--config", default=str(_THIS_DIR / "config.yaml"))
    parser.add_argument("--adapter", default=None, help="Ruta al adapter LoRA")
    parser.add_argument("--llamacpp", default=None, help="Ruta a llama.cpp")
    parser.add_argument("--keep-merged", action="store_true",
                        help="Conserva el modelo HF fusionado (no borra el temporal)")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    exp = cfg["export"]
    base = cfg["model"]["base"]

    adapter_dir = (Path(args.adapter) if args.adapter
                   else _THIS_DIR / cfg["training"]["output_dir"] / "adapter")
    out_dir = _THIS_DIR / exp["out_dir"]
    merged_dir = _THIS_DIR / cfg["training"]["output_dir"] / "merged"
    f16_path = out_dir / f"{exp['out_name']}-f16.gguf"
    final_path = out_dir / f"{exp['out_name']}-{exp['quantization']}.gguf"

    print("\nItzel-1B — exportación a GGUF")
    print("─" * 44)
    llamacpp_dir = find_llamacpp(args.llamacpp or exp.get("llamacpp_dir"))
    quant_bin = find_quantize_binary(llamacpp_dir)
    print(f"  llama.cpp: {llamacpp_dir}\n")

    merge_adapter(base, adapter_dir, merged_dir)
    convert_to_gguf(llamacpp_dir, merged_dir, f16_path)
    quantize(quant_bin, f16_path, final_path, exp["quantization"])
    verify_gguf(final_path)
    write_sha256(final_path)

    # Limpieza de temporales (el f16 y el merged son grandes).
    if not args.keep_merged:
        shutil.rmtree(merged_dir, ignore_errors=True)
        f16_path.unlink(missing_ok=True)
        print("\n  [limpieza] Temporales eliminados (usa --keep-merged para conservarlos).")

    print(f"\n[OK] GGUF listo: {final_path.relative_to(_REPO_ROOT)}")
    print("Siguiente paso: python upload_hf.py  (o usa el .gguf localmente con llama.cpp)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
