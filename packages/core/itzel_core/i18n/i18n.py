"""Carga el idioma según configuración o locale del OS."""

from __future__ import annotations

import json
import locale
from functools import lru_cache
from pathlib import Path
from typing import Any

_I18N_DIR = Path(__file__).parent
_SUPPORTED = {"es-MX", "en-US"}
_FALLBACK = "en-US"


@lru_cache(maxsize=4)
def _load(lang: str) -> dict[str, Any]:
    path = _I18N_DIR / f"{lang}.json"
    if not path.exists():
        path = _I18N_DIR / f"{_FALLBACK}.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def detect_lang() -> str:
    """Detecta el idioma del sistema; devuelve 'es-MX' si hay match parcial."""
    try:
        sys_lang = locale.getlocale()[0] or ""
        if sys_lang.startswith("es"):
            return "es-MX"
        if sys_lang.startswith("en"):
            return "en-US"
    except Exception:
        pass
    return "es-MX"  # Itzel es ES-MX por defecto


class T:
    """Traductor de strings. Uso: T("es-MX").get("cli.backend_offline")"""

    def __init__(self, lang: str | None = None) -> None:
        self._lang = lang or detect_lang()
        if self._lang not in _SUPPORTED:
            self._lang = _FALLBACK
        self._data = _load(self._lang)

    def get(self, key: str, **kwargs: Any) -> str:
        """
        Obtiene el string por clave con punto (ej: "cli.backend_offline").
        Aplica kwargs como format si se pasan.
        """
        parts = key.split(".")
        node: Any = self._data
        for part in parts:
            if isinstance(node, dict):
                node = node.get(part, key)
            else:
                return key
        result = str(node)
        if kwargs:
            result = result.format(**kwargs)
        return result

    @property
    def lang(self) -> str:
        return self._lang
