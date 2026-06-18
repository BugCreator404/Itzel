"""chunking.py — Extracción de texto y troceado de documentos para el RAG.

ES: Convierte un archivo en fragmentos (chunks) listos para embedear. Dos partes:

      1. Extracción por tipo de archivo:
           .txt .md .py .js .ts → lectura directa (UTF-8, errores reemplazados)
           .pdf                 → pypdf        (opcional)
           .docx                → python-docx  (opcional)
         Si falta la librería opcional, se salta con aviso — nunca crashea.

      2. Troceado con ventana deslizante que AJUSTA el corte a un límite natural
         (párrafo → línea → fin de oración → espacio) para no partir palabras.
         Mantiene offsets de carácter exactos y un solape configurable entre
         fragmentos contiguos, de modo que el contexto no se pierda en los bordes.

    Cada chunk lleva un content_hash (SHA-256) que el indexador usa para detectar
    qué cambió y re-indexar de forma incremental.

EN: Per-extension text extraction (pypdf/python-docx optional) + boundary-aware
    sliding-window chunking with overlap and exact char offsets.

Uso / Usage:
    from itzel_core.rag.chunking import read_and_chunk, SUPPORTED_EXTENSIONS

    file_hash, chunks = read_and_chunk(Path("doc.md"), size=512, overlap=64)
    for ch in chunks:
        print(ch.index, ch.content_hash, ch.text[:50])
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..logger import log_engine

# ─── constantes ───────────────────────────────────────────────────────────────

# Estimación consistente con memory.py (_estimate_tokens): ~4 chars por token.
CHARS_PER_TOKEN = 4

# Extensiones que sabemos extraer. El indexador filtra además por config.file_types.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".md", ".pdf", ".docx", ".py", ".js", ".ts"}
)

# Extensiones que se leen como texto plano directo.
_PLAINTEXT = frozenset({".txt", ".md", ".py", ".js", ".ts"})


# ─── modelo ────────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """Un fragmento de documento listo para indexar."""
    text:         str
    index:        int     # ordinal dentro del documento (0, 1, 2, …)
    start_char:   int     # offset inicial en el texto extraído
    end_char:     int     # offset final
    content_hash: str     # sha256 del texto del fragmento


# ─── hashing ───────────────────────────────────────────────────────────────────

def sha256_text(text: str) -> str:
    """SHA-256 hex de un texto (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_signature(path: Path) -> str:
    """Firma barata del archivo (tamaño + mtime) para detectar cambios sin leerlo.

    No es criptográfica; sirve para saltarse archivos que no cambiaron.
    """
    try:
        st = path.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


# ─── extracción de texto ───────────────────────────────────────────────────────

def extract_text(path: Path) -> str | None:
    """Extrae el texto de un archivo soportado. None si no se pudo / no soportado.

    Las dependencias de PDF/DOCX son opcionales: si faltan, registra un aviso y
    devuelve None (el indexador simplemente lo salta).
    """
    ext = path.suffix.lower()
    try:
        if ext in _PLAINTEXT:
            return path.read_text(encoding="utf-8", errors="replace")
        if ext == ".pdf":
            return _extract_pdf(path)
        if ext == ".docx":
            return _extract_docx(path)
    except Exception as exc:
        log_engine.warning(
            "No se pudo extraer texto de %s: %s", path.name, exc,
            extra={"component": "rag"},
        )
        return None

    log_engine.debug("Extensión no soportada para RAG: %s", ext)
    return None


def _extract_pdf(path: Path) -> str | None:
    """Texto de un PDF vía pypdf (opcional)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        log_engine.warning(
            "pypdf no instalado — se omite el PDF %s. "
            "Instala con: pip install 'itzel-core[rag]'", path.name,
            extra={"component": "rag"},
        )
        return None
    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n\n".join(pages).strip() or None


def _extract_docx(path: Path) -> str | None:
    """Texto de un .docx vía python-docx (opcional)."""
    try:
        import docx  # python-docx
    except ImportError:
        log_engine.warning(
            "python-docx no instalado — se omite %s. "
            "Instala con: pip install 'itzel-core[rag]'", path.name,
            extra={"component": "rag"},
        )
        return None
    document = docx.Document(str(path))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    return "\n".join(paragraphs).strip() or None


# ─── troceado con ventana deslizante ───────────────────────────────────────────

# Preferencia de cortes: del más "limpio" al menos. Se busca hacia atrás desde
# el final tentativo de la ventana, dentro de un margen de retroceso.
_BOUNDARIES = ("\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ")


def _snap_boundary(text: str, start: int, end: int, lookback: int) -> int:
    """Mueve `end` hacia atrás hasta el límite natural más cercano.

    Busca dentro de [end - lookback, end). Si no encuentra ninguno, devuelve
    `end` tal cual (corte duro). Garantiza progreso: nunca devuelve <= start.
    """
    if end >= len(text):
        return len(text)
    floor = max(start + 1, end - lookback)
    window = text[floor:end]
    for sep in _BOUNDARIES:
        pos = window.rfind(sep)
        if pos != -1:
            # cortar justo después del separador
            return floor + pos + len(sep)
    return end


def chunk_text(
    text:           str,
    chunk_size_tokens: int = 512,
    overlap_tokens:    int = 64,
) -> list[Chunk]:
    """Trocea `text` en fragmentos con solape, respetando límites naturales.

    Devuelve una lista de Chunk con offsets exactos sobre el texto recibido.
    """
    text = text or ""
    if not text.strip():
        return []

    max_chars     = max(1, chunk_size_tokens * CHARS_PER_TOKEN)
    overlap_chars = max(0, min(overlap_tokens, chunk_size_tokens - 1) * CHARS_PER_TOKEN)
    lookback      = max(1, max_chars // 4)   # margen para buscar el corte limpio

    chunks: list[Chunk] = []
    i = 0
    idx = 0
    n = len(text)

    while i < n:
        tentative_end = min(i + max_chars, n)
        end = _snap_boundary(text, i, tentative_end, lookback)
        piece = text[i:end].strip()
        if piece:
            chunks.append(Chunk(
                text=piece,
                index=idx,
                start_char=i,
                end_char=end,
                content_hash=sha256_text(piece),
            ))
            idx += 1

        if end >= n:
            break
        # Avanzar con solape, garantizando progreso estricto.
        i = max(end - overlap_chars, i + 1)

    return chunks


# ─── conveniencia ──────────────────────────────────────────────────────────────

def read_and_chunk(
    path:           Path,
    chunk_size_tokens: int = 512,
    overlap_tokens:    int = 64,
) -> tuple[str | None, list[Chunk]]:
    """Extrae y trocea un archivo. Devuelve (hash_del_texto, chunks).

    Si no se pudo extraer (no soportado / dep faltante / error), devuelve
    (None, []). El hash es del texto completo extraído — el indexador lo compara
    con lo almacenado para decidir si re-indexa.
    """
    text = extract_text(path)
    if text is None or not text.strip():
        return None, []
    file_hash = sha256_text(text)
    chunks = chunk_text(text, chunk_size_tokens, overlap_tokens)
    return file_hash, chunks
