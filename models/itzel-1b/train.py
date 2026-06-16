#!/usr/bin/env python3
"""train.py — Fine-tuning de Itzel-1B con LoRA.

ES: Entrena adaptadores LoRA sobre Qwen2.5-1.5B-Instruct usando los datos
    preparados por prepare_data.py. Intenta usar Unsloth (ruta rápida, GPU
    NVIDIA); si no está disponible, cae a TRL SFTTrainer + PEFT (funciona en
    cualquier GPU/CPU). Ambas rutas leen el mismo config.yaml y producen el
    mismo adapter en outputs/.

EN: Trains LoRA adapters on Qwen2.5-1.5B-Instruct. Tries Unsloth (fast path,
    NVIDIA GPU); falls back to TRL SFTTrainer + PEFT (any GPU/CPU). Both read
    the same config.yaml and produce the same adapter under outputs/.

Uso / Usage:
    python prepare_data.py      # primero prepara los datos
    python train.py             # entrena con config.yaml
    python train.py --config otra.yaml --resume
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

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
    if not config_path.exists():
        sys.exit(f"[ERROR] No se encontró config: {config_path}")
    with config_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ─── detección de hardware ────────────────────────────────────────────────────

def _detect_precision() -> tuple[bool, bool]:
    """Devuelve (bf16, fp16) según la GPU. bf16 en Ampere+; fp16 en GPUs viejas."""
    try:
        import torch
        if not torch.cuda.is_available():
            return False, False                 # CPU: sin half-precision
        if torch.cuda.is_bf16_supported():
            return True, False
        return False, True
    except ImportError:
        return False, False


def _has_unsloth() -> bool:
    """True si Unsloth está instalado (ruta rápida)."""
    try:
        import unsloth  # noqa: F401
        return True
    except Exception:
        return False


# ─── carga del dataset ────────────────────────────────────────────────────────

def load_prepared_dataset(cfg: dict[str, Any]):
    """Carga el train.jsonl preparado. Falla con instrucciones si no existe."""
    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit("[ERROR] Falta 'datasets':  pip install datasets")

    data_cfg = cfg["data"]
    dataset_dir = (_REPO_ROOT / data_cfg["dataset_dir"]).resolve()
    train_path = dataset_dir / data_cfg["prepared_train"]

    if not train_path.exists():
        sys.exit(
            f"[ERROR] No existe {train_path}\n"
            "        Ejecuta primero:  python prepare_data.py"
        )

    ds = load_dataset("json", data_files=str(train_path), split="train")
    print(f"  [OK] Dataset cargado: {len(ds)} ejemplos")
    return ds


def _formatting_func_factory(tokenizer):
    """Crea la función que convierte 'messages' a texto con la plantilla de chat."""
    def _format(example: dict[str, Any]) -> str:
        return tokenizer.apply_chat_template(
            example["messages"], tokenize=False, add_generation_prompt=False
        )
    return _format


# ─── ruta Unsloth (rápida) ────────────────────────────────────────────────────

def _train_unsloth(cfg: dict[str, Any], bf16: bool, fp16: bool, resume: bool) -> Path:
    """Entrena con Unsloth. Más rápido y menos VRAM en GPUs NVIDIA."""
    from unsloth import FastLanguageModel
    from trl import SFTTrainer, SFTConfig

    m, l, t = cfg["model"], cfg["lora"], cfg["training"]
    print("  [OK] Ruta: Unsloth (rápida)")

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=m["base"],
        max_seq_length=m["max_seq_length"],
        dtype=m["dtype"],
        load_in_4bit=m["load_in_4bit"],
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=l["r"],
        lora_alpha=l["alpha"],
        lora_dropout=l["dropout"],
        bias=l["bias"],
        target_modules=l["target_modules"],
        use_gradient_checkpointing=l["use_gradient_checkpointing"],
        random_state=l["random_state"],
    )

    dataset = load_prepared_dataset(cfg)
    out_dir = _THIS_DIR / t["output_dir"]

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        formatting_func=_formatting_func_factory(tokenizer),
        args=SFTConfig(
            output_dir=str(out_dir),
            max_seq_length=m["max_seq_length"],
            num_train_epochs=t["num_train_epochs"],
            per_device_train_batch_size=t["per_device_train_batch_size"],
            gradient_accumulation_steps=t["gradient_accumulation_steps"],
            learning_rate=t["learning_rate"],
            lr_scheduler_type=t["lr_scheduler_type"],
            warmup_ratio=t["warmup_ratio"],
            weight_decay=t["weight_decay"],
            optim=t["optim"],
            logging_steps=t["logging_steps"],
            save_steps=t["save_steps"],
            save_total_limit=t["save_total_limit"],
            seed=t["seed"],
            bf16=bf16,
            fp16=fp16,
            report_to="none",          # Sin telemetría externa (principio #2)
        ),
    )
    trainer.train(resume_from_checkpoint=resume)
    return _save_adapter(model, tokenizer, out_dir)


# ─── ruta TRL + PEFT (compatible) ─────────────────────────────────────────────

def _train_trl(cfg: dict[str, Any], bf16: bool, fp16: bool, resume: bool) -> Path:
    """Entrena con transformers + peft + trl. Funciona en cualquier GPU/CPU."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import LoraConfig
    from trl import SFTTrainer, SFTConfig

    m, l, t = cfg["model"], cfg["lora"], cfg["training"]
    print("  [OK] Ruta: TRL + PEFT (compatible)")
    if not torch.cuda.is_available():
        print("  [AVISO] Sin GPU CUDA — el entrenamiento en CPU será lento. "
              "Usa un dataset pequeño para pruebas.")

    tokenizer = AutoTokenizer.from_pretrained(m["base"])
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Cuantización 4-bit si hay GPU y bitsandbytes; si no, precisión completa.
    quant_config = None
    if m["load_in_4bit"] and torch.cuda.is_available():
        try:
            from transformers import BitsAndBytesConfig
            quant_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16 if bf16 else torch.float16,
                bnb_4bit_use_double_quant=True,
            )
        except Exception:
            print("  [AVISO] bitsandbytes no disponible — sin cuantización 4-bit.")

    model = AutoModelForCausalLM.from_pretrained(
        m["base"],
        quantization_config=quant_config,
        torch_dtype="auto",
        device_map="auto" if torch.cuda.is_available() else None,
    )

    lora_config = LoraConfig(
        r=l["r"],
        lora_alpha=l["alpha"],
        lora_dropout=l["dropout"],
        bias=l["bias"],
        target_modules=l["target_modules"],
        task_type="CAUSAL_LM",
    )

    dataset = load_prepared_dataset(cfg)
    out_dir = _THIS_DIR / t["output_dir"]

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        peft_config=lora_config,
        formatting_func=_formatting_func_factory(tokenizer),
        args=SFTConfig(
            output_dir=str(out_dir),
            max_seq_length=m["max_seq_length"],
            num_train_epochs=t["num_train_epochs"],
            per_device_train_batch_size=t["per_device_train_batch_size"],
            gradient_accumulation_steps=t["gradient_accumulation_steps"],
            learning_rate=t["learning_rate"],
            lr_scheduler_type=t["lr_scheduler_type"],
            warmup_ratio=t["warmup_ratio"],
            weight_decay=t["weight_decay"],
            optim=t["optim"] if torch.cuda.is_available() else "adamw_torch",
            logging_steps=t["logging_steps"],
            save_steps=t["save_steps"],
            save_total_limit=t["save_total_limit"],
            seed=t["seed"],
            bf16=bf16,
            fp16=fp16,
            report_to="none",          # Sin telemetría externa (principio #2)
        ),
    )
    trainer.train(resume_from_checkpoint=resume)
    return _save_adapter(model, tokenizer, out_dir)


# ─── guardado ─────────────────────────────────────────────────────────────────

def _save_adapter(model, tokenizer, out_dir: Path) -> Path:
    """Guarda el adapter LoRA final en outputs/adapter/."""
    adapter_dir = out_dir / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    print(f"\n[OK] Adapter LoRA guardado en: {adapter_dir.relative_to(_REPO_ROOT)}")
    return adapter_dir


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Fine-tuning de Itzel-1B con LoRA.")
    parser.add_argument("--config", default=str(_THIS_DIR / "config.yaml"))
    parser.add_argument("--resume", action="store_true",
                        help="Reanuda desde el último checkpoint")
    parser.add_argument("--force-trl", action="store_true",
                        help="Fuerza la ruta TRL+PEFT aunque Unsloth esté instalado")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))

    print("\nItzel-1B — entrenamiento")
    print("─" * 44)
    bf16, fp16 = _detect_precision()
    precision = "bf16" if bf16 else "fp16" if fp16 else "fp32 (CPU)"
    print(f"  Modelo base: {cfg['model']['base']}")
    print(f"  Precisión:   {precision}")
    print(f"  LoRA:        r={cfg['lora']['r']} alpha={cfg['lora']['alpha']}")
    print(f"  Epochs:      {cfg['training']['num_train_epochs']}")
    print()

    use_unsloth = _has_unsloth() and not args.force_trl
    try:
        if use_unsloth:
            _train_unsloth(cfg, bf16, fp16, args.resume)
        else:
            _train_trl(cfg, bf16, fp16, args.resume)
    except ImportError as exc:
        sys.exit(
            f"[ERROR] Falta una dependencia de entrenamiento: {exc}\n"
            "        Instala con:  pip install -r requirements-train.txt"
        )

    print("\nSiguiente paso: python evaluate.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
