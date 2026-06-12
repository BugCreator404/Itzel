"""
Skill oficial: clipboard — leer/escribir el portapapeles del sistema.

Intenciones soportadas (por frase):
    "copia <texto>"                → escribe <texto> al portapapeles
    "qué hay en el portapapeles"   → lee y devuelve el contenido
    "copy <text>" / "what's in the clipboard" → equivalentes en inglés

Requiere pyperclip (ver requirements.txt). Si falta, devuelve la instrucción
de instalación en vez de fallar.

English: read/write the system clipboard via pyperclip, with graceful
degradation when the dependency is missing.
"""

from __future__ import annotations

import re

_MAX_SHOW = 500   # caracteres máximos a mostrar al leer

#: "copia <texto>" / "copy <text>" — captura el texto a escribir.
_WRITE_RE = re.compile(
    r"^(?:copia(?:r)?|copy)\s+(?:al portapapeles\s+)?[\"']?(.+?)[\"']?$",
    re.IGNORECASE | re.DOTALL,
)


def run(query: str, context: dict) -> str:
    """Despacha entre leer y escribir según la frase del usuario."""
    try:
        import pyperclip
    except ImportError:
        return (
            "Esta skill necesita pyperclip. Instálalo con: pip install pyperclip "
            "(o: pip install -r skills/official/clipboard/requirements.txt)"
        )

    text = query.strip()

    # ── escribir: "copia hola mundo" ──────────────────────────────────
    match = _WRITE_RE.match(text)
    if match:
        payload = match.group(1).strip()
        if payload and not _is_read_request(payload):
            try:
                pyperclip.copy(payload)
            except pyperclip.PyperclipException as exc:
                return f"No pude escribir al portapapeles: {exc}"
            shown = payload if len(payload) <= 80 else payload[:80] + "…"
            return f"Copiado al portapapeles: «{shown}» ({len(payload)} caracteres)."

    # ── leer (default) ────────────────────────────────────────────────
    try:
        content = pyperclip.paste()
    except pyperclip.PyperclipException as exc:
        return f"No pude leer el portapapeles: {exc}"

    if not content:
        return "El portapapeles está vacío."
    shown = content if len(content) <= _MAX_SHOW else content[:_MAX_SHOW] + "…"
    return f"En el portapapeles ({len(content)} caracteres):\n{shown}"


def _is_read_request(payload: str) -> bool:
    """Distingue 'copia X' (escritura) de frases tipo 'copiado actual' (lectura)."""
    lowered = payload.lower()
    return any(
        kw in lowered
        for kw in ("portapapeles", "clipboard", "qué hay", "que hay", "what's in")
    )
