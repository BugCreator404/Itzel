"""personal_trainer.py — Fine-tuning personal on-device de Itzel.

ES: Recopila las correcciones del usuario y, semanalmente, entrena un adapter
    LoRA *adicional* sobre Itzel-1B base. Nada sale del equipo (principios #1 y
    #2). El entrenamiento solo corre si hay GPU (torch.cuda). El usuario puede
    ver qué aprendió, desactivarlo o borrarlo (principio #5).

    Flujo:
      1. record_correction(...)  → añade a ~/.itzel/data/corrections.jsonl
      2. should_train()          → True si >N correcciones nuevas + GPU + pasó 1 semana
      3. train()                 → adapter LoRA en ~/.itzel/models/personal/
      4. get_learning_summary()  → resumen para 'itzel memory learning'
      5. reset()                 → borra el adapter y olvida lo aprendido

    Nota de arquitectura: el adapter se guarda SIN fusionar. Aplicarlo en
    inferencia requiere base en formato HF (PEFT no entrena sobre GGUF). Para
    usarlo con el runtime GGUF, se fusiona y reexporta con export_gguf.py.

EN: Collects user corrections and weekly-trains an additional LoRA adapter on
    top of Itzel-1B. Fully on-device, GPU-gated, inspectable and resettable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("itzel.learning")

# ─── rutas (todo bajo ~/.itzel, nunca fuera del equipo) ───────────────────────

_ITZEL_DIR        = Path.home() / ".itzel"
_DATA_DIR         = _ITZEL_DIR / "data"
_CORRECTIONS_PATH = _DATA_DIR / "corrections.jsonl"
_STATE_PATH       = _DATA_DIR / "learning_state.json"
_PERSONAL_ADAPTER = _ITZEL_DIR / "models" / "personal"

# ─── parámetros de cadencia ───────────────────────────────────────────────────

_MIN_NEW_CORRECTIONS = 10        # nº mínimo de correcciones nuevas para entrenar
_RETRAIN_EVERY_DAYS  = 7         # cadencia semanal


# ─── estado persistente ───────────────────────────────────────────────────────

@dataclass
class LearningState:
    """Estado del aprendizaje personal, persistido en learning_state.json."""
    last_trained_at: str | None = None      # ISO-8601 UTC
    trained_count: int = 0                      # correcciones ya entrenadas
    adapter_version: int = 0                    # se incrementa en cada entrenamiento
    categories: dict[str, int] = field(default_factory=dict)  # categoría → nº

    @classmethod
    def load(cls) -> LearningState:
        if _STATE_PATH.exists():
            try:
                data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
                return cls(**data)
            except Exception as exc:                       # estado corrupto → reset suave
                logger.warning("learning_state.json ilegible (%s); se reinicia.", exc)
        return cls()

    def save(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ─── API pública de recolección ───────────────────────────────────────────────

def record_correction(
    instruction: str,
    corrected_output: str,
    *,
    original_output: str = "",
    input_context: str = "",
    language: str = "es-MX",
    category: str = "general",
) -> None:
    """Registra una corrección del usuario en corrections.jsonl.

    ES: Se llama cuando el usuario corrige una respuesta de Itzel. No entrena;
        solo acumula. El entrenamiento ocurre después, en lote.
    EN: Called when the user corrects an answer. Append-only; training is batched.
    """
    if not instruction.strip() or not corrected_output.strip():
        return                                  # nada que aprender de un vacío

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "instruction": instruction.strip(),
        "input": input_context.strip(),
        "output": corrected_output.strip(),
        "original_output": original_output.strip(),
        "language": language,
        "category": category,
        "ts": datetime.now(UTC).isoformat(),
    }
    with _CORRECTIONS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info("Corrección registrada (categoría=%s).", category)


def _load_corrections() -> list[dict[str, Any]]:
    """Lee todas las correcciones acumuladas."""
    if not _CORRECTIONS_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    with _CORRECTIONS_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# ─── resumen (para 'itzel memory learning') ───────────────────────────────────

def get_learning_summary() -> dict[str, Any]:
    """Resumen legible de lo que Itzel ha aprendido de este usuario."""
    state = LearningState.load()
    corrections = _load_corrections()
    new_count = max(len(corrections) - state.trained_count, 0)

    return {
        "total_corrections": len(corrections),
        "trained_corrections": state.trained_count,
        "pending_corrections": new_count,
        "adapter_version": state.adapter_version,
        "last_trained_at": state.last_trained_at,
        "categories": dict(state.categories),
        "adapter_present": _PERSONAL_ADAPTER.exists(),
        "gpu_available": PersonalTrainer.gpu_available(),
        "will_train_next": PersonalTrainer().should_train(),
    }


# ─── entrenador ───────────────────────────────────────────────────────────────

class PersonalTrainer:
    """Orquesta el entrenamiento personal on-device."""

    def __init__(self, base_model: str | None = None) -> None:
        # Base HF para entrenar. Por defecto el mismo que el pipeline de Itzel-1B.
        self.base_model = base_model or "Qwen/Qwen2.5-1.5B-Instruct"
        self.state = LearningState.load()

    # ── condiciones ───────────────────────────────────────────────────────────

    @staticmethod
    def gpu_available() -> bool:
        """True solo si hay GPU CUDA. Sin GPU no se entrena (sería demasiado lento)."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    def new_corrections(self) -> list[dict[str, Any]]:
        """Correcciones registradas desde el último entrenamiento."""
        return _load_corrections()[self.state.trained_count:]

    def _enough_time_passed(self) -> bool:
        """True si nunca se entrenó o ya pasó la cadencia semanal."""
        if not self.state.last_trained_at:
            return True
        try:
            last = datetime.fromisoformat(self.state.last_trained_at)
        except ValueError:
            return True
        return datetime.now(UTC) - last >= timedelta(days=_RETRAIN_EVERY_DAYS)

    def should_train(self) -> bool:
        """Decide si toca entrenar: GPU + suficientes correcciones nuevas + cadencia."""
        return (
            self.gpu_available()
            and len(self.new_corrections()) >= _MIN_NEW_CORRECTIONS
            and self._enough_time_passed()
        )

    # ── entrenamiento ─────────────────────────────────────────────────────────

    def train(self, *, force: bool = False) -> dict[str, Any]:
        """Entrena el adapter LoRA personal si corresponde.

        Devuelve un dict con el resultado. No lanza si faltan deps o GPU: en su
        lugar devuelve {"trained": False, "reason": ...} para que el scheduler
        del engine lo ignore silenciosamente.
        """
        if not force and not self.should_train():
            return {"trained": False, "reason": self._skip_reason()}

        if not self.gpu_available():
            return {"trained": False, "reason": "sin_gpu"}

        corrections = _load_corrections()
        if not corrections:
            return {"trained": False, "reason": "sin_correcciones"}

        try:
            self._run_training(corrections)
        except ImportError as exc:
            logger.warning("Faltan dependencias de entrenamiento: %s", exc)
            return {"trained": False, "reason": f"faltan_deps:{exc}"}
        except Exception as exc:                            # noqa: BLE001
            logger.exception("Entrenamiento personal falló.")
            return {"trained": False, "reason": f"error:{exc}"}

        # Actualizar estado.
        self.state.trained_count = len(corrections)
        self.state.adapter_version += 1
        self.state.last_trained_at = datetime.now(UTC).isoformat()
        for c in corrections:
            cat = c.get("category", "general")
            self.state.categories[cat] = self.state.categories.get(cat, 0) + 1
        self.state.save()

        logger.info("Adapter personal v%d entrenado (%d correcciones).",
                    self.state.adapter_version, len(corrections))
        return {
            "trained": True,
            "adapter_version": self.state.adapter_version,
            "corrections_used": len(corrections),
            "adapter_path": str(_PERSONAL_ADAPTER),
        }

    def _skip_reason(self) -> str:
        """Razón legible de por qué no se entrena (para logs/UI)."""
        if not self.gpu_available():
            return "sin_gpu"
        if len(self.new_corrections()) < _MIN_NEW_CORRECTIONS:
            return f"pocas_correcciones ({len(self.new_corrections())}/{_MIN_NEW_CORRECTIONS})"
        if not self._enough_time_passed():
            return "cadencia_semanal_no_cumplida"
        return "ok"

    def _run_training(self, corrections: list[dict[str, Any]]) -> None:
        """Entrenamiento LoRA real. Ligero: 1 epoch sobre las correcciones."""
        import torch
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import SFTConfig, SFTTrainer

        tokenizer = AutoTokenizer.from_pretrained(self.base_model)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        # Construir dataset de mensajes ChatML a partir de las correcciones.
        def _to_messages(c: dict[str, Any]) -> dict[str, Any]:
            user = c["instruction"]
            if c.get("input"):
                user = f"{user}\n\n{c['input']}"
            return {"messages": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": c["output"]},
            ]}

        dataset = Dataset.from_list([_to_messages(c) for c in corrections])

        def _fmt(ex: dict[str, Any]) -> str:
            return tokenizer.apply_chat_template(
                ex["messages"], tokenize=False, add_generation_prompt=False)

        bf16 = torch.cuda.is_bf16_supported()
        model = AutoModelForCausalLM.from_pretrained(
            self.base_model, torch_dtype="auto", device_map="auto")

        lora = LoraConfig(
            r=8, lora_alpha=16, lora_dropout=0.0, bias="none",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            task_type="CAUSAL_LM",
        )

        _PERSONAL_ADAPTER.parent.mkdir(parents=True, exist_ok=True)
        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=dataset,
            peft_config=lora,
            formatting_func=_fmt,
            args=SFTConfig(
                output_dir=str(_PERSONAL_ADAPTER / "_checkpoints"),
                num_train_epochs=1,                 # ligero: el usuario espera poco
                per_device_train_batch_size=1,
                gradient_accumulation_steps=4,
                learning_rate=1.0e-4,               # LR conservador (no sobreajustar)
                lr_scheduler_type="cosine",
                warmup_ratio=0.05,
                logging_steps=5,
                save_strategy="no",                 # solo nos interesa el adapter final
                seed=3407,
                bf16=bf16,
                fp16=not bf16,
                report_to="none",                   # sin telemetría (principio #2)
            ),
        )
        trainer.train()
        model.save_pretrained(str(_PERSONAL_ADAPTER))
        tokenizer.save_pretrained(str(_PERSONAL_ADAPTER))

    # ── borrado ───────────────────────────────────────────────────────────────

    def reset(self, *, keep_corrections: bool = True) -> dict[str, Any]:
        """Borra el adapter personal (y opcionalmente las correcciones).

        ES: 'Olvida' lo aprendido. Confirmar antes en la capa de UI (principio #5).
        EN: Forgets what was learned. UI layer must confirm first.
        """
        import shutil
        removed = []
        if _PERSONAL_ADAPTER.exists():
            shutil.rmtree(_PERSONAL_ADAPTER, ignore_errors=True)
            removed.append("adapter")
        if not keep_corrections and _CORRECTIONS_PATH.exists():
            _CORRECTIONS_PATH.unlink(missing_ok=True)
            removed.append("corrections")

        # Reiniciar estado conservando o no las correcciones.
        self.state = LearningState()
        self.state.save()
        logger.info("Aprendizaje personal reiniciado (%s).", ", ".join(removed) or "nada")
        return {"reset": True, "removed": removed}
