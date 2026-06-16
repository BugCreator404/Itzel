"""itzel_core.learning — Aprendizaje personal on-device de Itzel.

ES: Subsistema que adapta Itzel a cada usuario a partir de sus correcciones,
    SIN que ningún dato salga del equipo (principios #1 y #2). Todo el
    entrenamiento ocurre localmente y solo si hay GPU disponible.

EN: On-device personal learning. Adapts Itzel to each user from their
    corrections without any data leaving the machine. Local-only, GPU-gated.
"""

from .personal_trainer import (
    PersonalTrainer,
    LearningState,
    record_correction,
    get_learning_summary,
)

__all__ = [
    "PersonalTrainer",
    "LearningState",
    "record_correction",
    "get_learning_summary",
]
