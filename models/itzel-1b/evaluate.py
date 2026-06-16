#!/usr/bin/env python3
"""evaluate.py — Evaluación de Itzel-1B.

ES: Carga el modelo base + adapter LoRA y mide sobre el set de validación
    (datasets/_prepared/val.jsonl) las métricas configuradas en config.yaml:

      - perplexity    Perplejidad en validación ES-MX (qué tan bien predice).
      - bleu          BLEU vs respuestas de referencia (similitud léxica).
      - task_success  Tasa de éxito en comandos del sistema (categoría
                      system_control): la respuesta menciona la acción esperada.
      - cpu_latency   Tiempo medio de respuesta forzando ejecución en CPU.

EN: Loads base model + LoRA adapter and measures the configured metrics over
    the validation set. Each metric degrades gracefully if its optional
    dependency is missing.

Uso / Usage:
    python evaluate.py                       # evalúa outputs/adapter
    python evaluate.py --adapter ruta/al/adapter --json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
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


# ─── configuración y datos ────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        sys.exit("[ERROR] Falta PyYAML:  pip install pyyaml")
    with config_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_val_records(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Lee val.jsonl (registros con 'messages', 'language', 'category')."""
    data_cfg = cfg["data"]
    val_path = (_REPO_ROOT / data_cfg["dataset_dir"] / data_cfg["prepared_val"]).resolve()
    if not val_path.exists():
        sys.exit(f"[ERROR] No existe {val_path}\n        Ejecuta:  python prepare_data.py")
    records: list[dict[str, Any]] = []
    with val_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ─── carga del modelo ─────────────────────────────────────────────────────────

def load_model(base: str, adapter_dir: Path, device: str):
    """Carga base + adapter LoRA. device: 'cuda' | 'cpu'."""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import PeftModel
    except ImportError as exc:
        sys.exit(f"[ERROR] Falta dependencia: {exc}\n        pip install transformers peft torch")

    tokenizer = AutoTokenizer.from_pretrained(str(adapter_dir) if adapter_dir.exists() else base)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    dtype = torch.float32 if device == "cpu" else "auto"
    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=dtype)
    if adapter_dir.exists():
        model = PeftModel.from_pretrained(model, str(adapter_dir))
        print(f"  [OK] Adapter cargado: {adapter_dir.relative_to(_REPO_ROOT)}")
    else:
        print(f"  [AVISO] No hay adapter en {adapter_dir} — evaluando el modelo base.")
    model.to(device)
    model.eval()
    return model, tokenizer


def _split_prompt_reference(record: dict[str, Any], tokenizer) -> tuple[str, str]:
    """Devuelve (prompt_con_chat_template, respuesta_de_referencia)."""
    messages = record["messages"]
    reference = messages[-1]["content"]            # mensaje assistant
    prompt_messages = messages[:-1]                # system + user
    prompt = tokenizer.apply_chat_template(
        prompt_messages, tokenize=False, add_generation_prompt=True
    )
    return prompt, reference


# ─── métrica: perplejidad ─────────────────────────────────────────────────────

def metric_perplexity(model, tokenizer, records, device) -> float:
    """Perplejidad media sobre los textos completos de validación."""
    import torch

    total_loss, total_tokens = 0.0, 0
    for rec in records:
        text = tokenizer.apply_chat_template(
            rec["messages"], tokenize=False, add_generation_prompt=False
        )
        enc = tokenizer(text, return_tensors="pt", truncation=True,
                        max_length=model.config.max_position_embeddings).to(device)
        with torch.no_grad():
            out = model(**enc, labels=enc["input_ids"])
        n = enc["input_ids"].shape[1]
        total_loss += out.loss.item() * n
        total_tokens += n
    return math.exp(total_loss / max(total_tokens, 1))


# ─── métrica: BLEU ────────────────────────────────────────────────────────────

def _simple_bleu(reference: str, hypothesis: str) -> float:
    """BLEU-1..4 simplificado con suavizado (sin dependencias externas).

    Si sacrebleu está disponible se prefiere; esto es un fallback honesto para
    no obligar a instalar otra librería solo para una métrica.
    """
    ref_tokens = reference.lower().split()
    hyp_tokens = hypothesis.lower().split()
    if not hyp_tokens:
        return 0.0

    precisions = []
    for n in range(1, 5):
        ref_ngrams = _ngrams(ref_tokens, n)
        hyp_ngrams = _ngrams(hyp_tokens, n)
        if not hyp_ngrams:
            precisions.append(0.0)
            continue
        overlap = sum((hyp_ngrams & ref_ngrams).values())
        # Suavizado +1 para evitar log(0).
        precisions.append((overlap + 1) / (sum(hyp_ngrams.values()) + 1))

    # Penalización por brevedad.
    bp = 1.0 if len(hyp_tokens) >= len(ref_tokens) else math.exp(
        1 - len(ref_tokens) / max(len(hyp_tokens), 1))
    geo_mean = math.exp(sum(math.log(p) for p in precisions) / 4)
    return bp * geo_mean


def _ngrams(tokens: list[str], n: int):
    from collections import Counter
    return Counter(tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1))


def metric_bleu(model, tokenizer, records, device, max_new_tokens: int) -> float:
    """BLEU medio de las respuestas generadas vs referencia."""
    try:
        import sacrebleu
        use_sacre = True
    except ImportError:
        use_sacre = False

    scores = []
    for rec in records:
        prompt, reference = _split_prompt_reference(rec, tokenizer)
        hypothesis = _generate(model, tokenizer, prompt, device, max_new_tokens)
        if use_sacre:
            scores.append(sacrebleu.sentence_bleu(hypothesis, [reference]).score / 100.0)
        else:
            scores.append(_simple_bleu(reference, hypothesis))
    return sum(scores) / max(len(scores), 1)


# ─── métrica: tasa de éxito en tareas del sistema ─────────────────────────────

def metric_task_success(model, tokenizer, records, device, max_new_tokens: int) -> float:
    """Fracción de tareas system_control cuya respuesta menciona la acción esperada.

    Heurística: la respuesta de referencia contiene un verbo/acción clave que
    la respuesta generada también debería mencionar. Es una señal, no una
    ejecución real (eso lo cubren los tests de integración del engine).
    """
    system_records = [r for r in records if r.get("category") == "system_control"]
    if not system_records:
        return float("nan")            # no aplica → se reporta como N/A

    hits = 0
    for rec in system_records:
        prompt, reference = _split_prompt_reference(rec, tokenizer)
        hypothesis = _generate(model, tokenizer, prompt, device, max_new_tokens).lower()
        # Palabras clave de la referencia (>3 chars), debe acertar al menos una.
        keywords = [w for w in reference.lower().split() if len(w) > 3][:5]
        if any(kw.strip(".,;:") in hypothesis for kw in keywords):
            hits += 1
    return hits / len(system_records)


# ─── métrica: latencia en CPU ─────────────────────────────────────────────────

def metric_cpu_latency(base: str, adapter_dir: Path, records, max_new_tokens: int,
                       samples: int) -> float:
    """Tiempo medio (segundos) de generar una respuesta forzando CPU."""
    model, tokenizer = load_model(base, adapter_dir, device="cpu")
    sample_records = records[:samples]
    times = []
    for rec in sample_records:
        prompt, _ = _split_prompt_reference(rec, tokenizer)
        t0 = time.perf_counter()
        _generate(model, tokenizer, prompt, device="cpu", max_new_tokens=max_new_tokens)
        times.append(time.perf_counter() - t0)
    return sum(times) / max(len(times), 1)


# ─── generación ───────────────────────────────────────────────────────────────

def _generate(model, tokenizer, prompt: str, device: str, max_new_tokens: int) -> str:
    import torch
    enc = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.generate(
            **enc, max_new_tokens=max_new_tokens, do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
        )
    generated = out[0][enc["input_ids"].shape[1]:]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluación de Itzel-1B.")
    parser.add_argument("--config", default=str(_THIS_DIR / "config.yaml"))
    parser.add_argument("--adapter", default=None,
                        help="Ruta al adapter (default: outputs/adapter)")
    parser.add_argument("--json", action="store_true", help="Salida en JSON")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limita el nº de ejemplos (pruebas rápidas)")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    eval_cfg = cfg["evaluation"]
    base = cfg["model"]["base"]
    adapter_dir = (Path(args.adapter) if args.adapter
                   else _THIS_DIR / cfg["training"]["output_dir"] / "adapter")

    records = load_val_records(cfg)
    if args.limit:
        records = records[:args.limit]

    if not args.json:
        print("\nItzel-1B — evaluación")
        print("─" * 44)
        print(f"  Ejemplos de validación: {len(records)}\n")

    # Detectar device para las métricas que no son CPU-only.
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"

    results: dict[str, Any] = {}
    metrics = eval_cfg["metrics"]
    mnt = eval_cfg["max_new_tokens"]

    # cpu_latency carga su propio modelo en CPU; el resto comparten uno.
    needs_shared = [m for m in metrics if m != "cpu_latency"]
    if needs_shared:
        model, tokenizer = load_model(base, adapter_dir, device)

    if "perplexity" in metrics:
        results["perplexity"] = round(metric_perplexity(model, tokenizer, records, device), 3)
    if "bleu" in metrics:
        results["bleu"] = round(metric_bleu(model, tokenizer, records, device, mnt), 4)
    if "task_success" in metrics:
        ts = metric_task_success(model, tokenizer, records, device, mnt)
        results["task_success"] = None if math.isnan(ts) else round(ts, 4)
    if "cpu_latency" in metrics:
        lat = metric_cpu_latency(base, adapter_dir, records, mnt,
                                 eval_cfg["cpu_latency_samples"])
        results["cpu_latency_s"] = round(lat, 3)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("\n  Resultados / Results:")
        labels = {
            "perplexity": "Perplejidad (↓ mejor)",
            "bleu": "BLEU (↑ mejor)",
            "task_success": "Tasa de éxito sistema (↑ mejor)",
            "cpu_latency_s": "Latencia CPU seg (↓ mejor)",
        }
        for key, value in results.items():
            shown = "N/A" if value is None else value
            print(f"    {labels.get(key, key):32s} {shown}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
