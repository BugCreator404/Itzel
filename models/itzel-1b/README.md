# Itzel-1B 🦎

> **ES:** Pipeline de entrenamiento del modelo de IA personal de Itzel.
> **EN:** Training pipeline for Itzel's personal AI model.

Itzel-1B es un fine-tune de **Qwen2.5-1.5B-Instruct** optimizado para correr
**100% local** como asistente personal, con español mexicano nativo y soporte
para tareas de productividad, control del sistema y código.

Itzel-1B is a fine-tune of **Qwen2.5-1.5B-Instruct** optimized to run **100%
locally** as a personal assistant, with native Mexican Spanish.

---

## Contenido / Contents

```
models/itzel-1b/
├── config.yaml          # Hiperparámetros — única fuente de verdad
├── prepare_data.py      # Limpieza, dedup, split y formato ChatML
├── train.py             # Fine-tuning LoRA (Unsloth o TRL+PEFT)
├── evaluate.py          # Perplejidad, BLEU, tasa de éxito, latencia CPU
├── export_gguf.py       # Merge LoRA + GGUF Q4_K_M + SHA256
├── upload_hf.py         # Publicación en Hugging Face Hub
├── requirements-train.txt
└── README.md            # (este archivo)
```

---

## Reproducir el entrenamiento desde cero / Reproduce from scratch

### 1. Requisitos / Requirements

- **GPU recomendada:** NVIDIA con ≥8 GB VRAM (entrenable también en CPU, lento).
- Python 3.11+
- (Opcional, ruta rápida) [Unsloth](https://github.com/unslothai/unsloth) en Linux/CUDA.
- Para exportar GGUF: [llama.cpp](https://github.com/ggerganov/llama.cpp) compilado.

```bash
pip install -r requirements-train.txt
```

### 2. Preparar los datos / Prepare data

```bash
python prepare_data.py
# → models/datasets/_prepared/train.jsonl  +  val.jsonl
```

### 3. Entrenar / Train

```bash
python train.py
# Usa Unsloth si está instalado; si no, cae a TRL+PEFT automáticamente.
# → models/itzel-1b/outputs/adapter/
```

Reanudar desde checkpoint / Resume: `python train.py --resume`
Forzar ruta compatible / Force fallback path: `python train.py --force-trl`

### 4. Evaluar / Evaluate

```bash
python evaluate.py            # tabla legible
python evaluate.py --json     # para CI/dashboards
```

### 5. Exportar a GGUF / Export to GGUF

```bash
python export_gguf.py --llamacpp /ruta/a/llama.cpp
# → models/itzel-1b/gguf/itzel-1b-Q4_K_M.gguf  +  .sha256
```

### 6. Publicar (opcional) / Publish (optional)

```bash
export HF_TOKEN=hf_xxx
python upload_hf.py                 # sube GGUF + model card bilingüe
python upload_hf.py --dry-run       # solo genera la model card
```

---

## Cómo contribuir datos / How to contribute data

ES: El dataset es **abierto y comunitario**. Para aportar ejemplos:

1. Elige una categoría en `models/datasets/<categoria>/`.
2. Añade un `.jsonl` con uno o más ejemplos (un JSON por línea):

```json
{"instruction": "Organiza Downloads por tipo", "input": "", "output": "Voy a revisar tu carpeta Downloads y agrupar los archivos por extensión...", "language": "es-MX", "category": "file_management"}
```

3. Valida sin escribir: `python prepare_data.py --dry-run`
4. Abre un Pull Request. Lee `models/datasets/README.md` para el formato completo.

**Reglas de calidad / Quality rules:**
- Español mexicano natural (no traducciones literales del inglés).
- Sin datos personales reales (PII). Sin contenido dañino.
- `output` útil, conciso y en el tono cálido de Itzel.
- Confirma acciones irreversibles en el `output` (principio #5).

---

## Benchmarks y métricas / Benchmarks

`evaluate.py` reporta cuatro métricas sobre el set de validación:

| Métrica | Descripción | Mejor |
|---|---|---|
| Perplejidad | Qué tan bien predice el texto de validación ES-MX | ↓ |
| BLEU | Similitud léxica vs respuestas de referencia | ↑ |
| Tasa de éxito | % de tareas `system_control` con la acción correcta | ↑ |
| Latencia CPU | Tiempo medio de respuesta forzando CPU (seg) | ↓ |

> Los números de referencia se publican en cada *release* del modelo, junto con
> el commit y el `config.yaml` exactos usados para entrenar (reproducibilidad).

---

## Arquitectura del fine-tuning / Fine-tuning architecture

- **LoRA** (r=16, α=32) sobre las proyecciones de atención `q/k/v/o`.
- **Doble ruta:** Unsloth (rápida, CUDA) con *fallback* transparente a TRL+PEFT
  (cualquier GPU/CPU). Ambas leen el mismo `config.yaml`.
- **Sin telemetría** en el entrenamiento (`report_to="none"`).
- **Aprendizaje personal on-device:** ver
  [`packages/core/itzel_core/learning/`](../../packages/core/itzel_core/learning/) —
  adapta el modelo a cada usuario sin que sus datos salgan del equipo.

---

## Licencia / License

**Apache 2.0** — pesos del modelo y scripts de este directorio.

> El código de la aplicación Itzel es MIT; los pesos del modelo se publican bajo
> Apache 2.0 para alinearse con la licencia del modelo base (Qwen2.5).

---

*Hecho con cariño en Tijuana, México · Made with care in Tijuana, Mexico* 🦎
