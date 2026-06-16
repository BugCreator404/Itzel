"""pipeline.py — Orquestación RAG: contexto de documentos + citación de fuentes.

ES: Une las piezas del RAG en un flujo:

      consulta → embed → buscar en Chroma → contexto relevante
               → [contexto] + [consulta] → modelo → respuesta CITANDO fuentes

    Dos puntos de integración, sin duplicar la lógica de streaming del chat:

      • augment_messages(messages, …)
          Toma la lista de mensajes del chat, recupera contexto para el último
          turno del usuario e INYECTA ese contexto + la instrucción de citar
          fuentes en el mensaje del usuario. /chat sigue haciendo su streaming
          normal. Es lo que se activa cuando rag.auto_context=True.

      • answer(query, …)  (async)
          RAG completo de un tiro: recupera, arma el prompt, llama al modelo y
          devuelve la respuesta + las fuentes. Lo usa el CLI / una respuesta
          puntual con citación.

    Instrucción bilingüe (ES-MX / EN-US) y honesta: si los documentos no
    contienen la respuesta, el modelo debe decirlo en vez de inventar.

EN: RAG orchestration. Inject retrieved context + citation instruction into the
    user turn (augment_messages) or run a full retrieve-then-generate (answer).

Uso / Usage:
    from itzel_core.rag.pipeline import get_pipeline

    ctx = get_pipeline().retrieve("reunión del lunes")
    print(ctx.context_text, ctx.citations())
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from ..logger import log_engine
from .retriever import RetrievedChunk, Retriever, get_retriever


# ─── contexto recuperado ───────────────────────────────────────────────────────

@dataclass
class RetrievalContext:
    """El contexto recuperado para una consulta, listo para citar."""
    query:        str
    chunks:       list[RetrievedChunk]
    language:     str
    context_text: str = ""
    sources:      list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    def citations(self) -> list[dict[str, Any]]:
        """Lista de fuentes únicas numeradas: [{n, source, filename}, …]."""
        return self.sources

    def to_dict(self) -> dict[str, Any]:
        return {
            "query":     self.query,
            "language":  self.language,
            "sources":   self.sources,
            "chunks":    [c.to_dict() for c in self.chunks],
        }


@dataclass
class RAGAnswer:
    """Respuesta de answer(): texto generado + fuentes citadas."""
    text:     str
    sources:  list[dict[str, Any]]
    used_rag: bool
    query:    str


# ─── textos bilingües ──────────────────────────────────────────────────────────

def _is_spanish(language: Optional[str]) -> bool:
    return (language or "es").lower().startswith("es")


_INSTRUCTION_ES = (
    "Usa ÚNICAMENTE la información de los documentos de arriba para responder. "
    "Cita cada dato con el número del documento entre corchetes, por ejemplo [1]. "
    "Si los documentos no contienen la respuesta, dilo claramente; no inventes."
)
_INSTRUCTION_EN = (
    "Use ONLY the information from the documents above to answer. "
    "Cite each fact with the document number in brackets, e.g. [1]. "
    "If the documents do not contain the answer, say so clearly; do not make it up."
)

_CONTEXT_HEADER_ES = "Contexto de tus documentos:"
_CONTEXT_HEADER_EN = "Context from your documents:"

_QUESTION_LABEL_ES = "Pregunta:"
_QUESTION_LABEL_EN = "Question:"

_SOURCES_LABEL_ES = "Fuentes:"
_SOURCES_LABEL_EN = "Sources:"

_NO_CONTEXT_ES = "(No encontré documentos relevantes en tu índice para esta consulta.)"
_NO_CONTEXT_EN = "(I found no relevant documents in your index for this query.)"


# ─── pipeline ──────────────────────────────────────────────────────────────────

class RAGPipeline:
    """Orquesta recuperación + ensamblado de contexto + citación."""

    def __init__(self, retriever: Optional[Retriever] = None) -> None:
        self._retriever = retriever

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = get_retriever()
        return self._retriever

    def _cfg(self):
        from ..config import config
        return config.rag

    # ── recuperación + formato de contexto ──────────────────────────────────────

    def retrieve(
        self,
        query:    str,
        language: Optional[str] = None,
        **filters: Any,
    ) -> RetrievalContext:
        """Recupera fragmentos y arma el bloque de contexto numerado + fuentes."""
        try:
            chunks = self.retriever.search(query, **filters)
        except Exception as exc:
            log_engine.warning(
                "Recuperación RAG falló: %s", exc, extra={"component": "rag"},
            )
            chunks = []

        lang = language or self._language_default()
        ctx = RetrievalContext(query=query, chunks=chunks, language=lang)
        ctx.context_text, ctx.sources = self._format_context(chunks, lang)
        return ctx

    def _format_context(
        self, chunks: list[RetrievedChunk], language: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Numera fuentes únicas y construye el bloque de contexto para el prompt.

        Varios fragmentos del mismo archivo comparten el mismo número de fuente.
        """
        if not chunks:
            return "", []

        source_num: dict[str, int] = {}
        sources: list[dict[str, Any]] = []
        lines: list[str] = []

        for ch in chunks:
            if ch.source not in source_num:
                n = len(source_num) + 1
                source_num[ch.source] = n
                sources.append({
                    "n":        n,
                    "source":   ch.source,
                    "filename": ch.filename,
                    "file_type": ch.file_type,
                    "modified": ch.modified,
                })
            n = source_num[ch.source]
            lines.append(f"[{n}] ({ch.filename})\n{ch.text}")

        context_text = "\n\n".join(lines)
        return context_text, sources

    # ── construcción del prompt aumentado ────────────────────────────────────────

    def build_prompt(self, context: RetrievalContext, query: str) -> str:
        """Mensaje de usuario aumentado: contexto + instrucción de citas + pregunta."""
        es = _is_spanish(context.language)
        header   = _CONTEXT_HEADER_ES if es else _CONTEXT_HEADER_EN
        instr    = _INSTRUCTION_ES   if es else _INSTRUCTION_EN
        q_label  = _QUESTION_LABEL_ES if es else _QUESTION_LABEL_EN

        body = context.context_text or (_NO_CONTEXT_ES if es else _NO_CONTEXT_EN)
        return (
            f"{header}\n\n{body}\n\n"
            f"{instr}\n\n"
            f"{q_label} {query}"
        )

    def format_sources_footer(self, context: RetrievalContext) -> str:
        """Pie de respuesta con la lista de fuentes citadas (para el CLI)."""
        if context.is_empty:
            return ""
        es = _is_spanish(context.language)
        label = _SOURCES_LABEL_ES if es else _SOURCES_LABEL_EN
        lines = [f"  [{s['n']}] {s['filename']}  —  {s['source']}" for s in context.sources]
        return f"\n\n{label}\n" + "\n".join(lines)

    # ── integración con /chat (inyección en mensajes) ────────────────────────────

    def augment_messages(
        self,
        messages: list[dict[str, str]],
        language: Optional[str] = None,
        **filters: Any,
    ) -> tuple[list[dict[str, str]], RetrievalContext]:
        """Inyecta contexto de documentos en el último turno de usuario.

        Devuelve (mensajes_aumentados, contexto). Si no hay último mensaje de
        usuario o no hay contexto relevante, devuelve los mensajes sin cambios
        (pero con el contexto vacío, para que el caller decida).
        """
        # Localizar el último mensaje de usuario.
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            return messages, RetrievalContext(query="", chunks=[], language=language or self._language_default())

        query = messages[last_user_idx].get("content", "")
        context = self.retrieve(query, language=language, **filters)

        if context.is_empty:
            # Sin contexto relevante: no tocamos el mensaje (chat normal).
            return messages, context

        augmented = list(messages)
        augmented[last_user_idx] = {
            "role": "user",
            "content": self.build_prompt(context, query),
        }
        log_engine.info(
            "RAG: inyectado contexto de %d documento(s) en el chat",
            len(context.sources), extra={"component": "rag"},
        )
        return augmented, context

    # ── RAG completo (recuperar + generar) ───────────────────────────────────────

    async def answer(
        self,
        query:    str,
        language: Optional[str] = None,
        adapter:  Any = None,
        **filters: Any,
    ) -> RAGAnswer:
        """RAG de un tiro: recupera, genera con el modelo y devuelve texto + fuentes.

        Si no hay contexto relevante, responde igual (sin contexto) pero marca
        used_rag=False. Nunca lanza por culpa del RAG: si el retriever falla,
        cae a una respuesta directa del modelo.
        """
        lang = language or self._language_default()
        context = self.retrieve(query, language=lang, **filters)

        # Construir mensajes [system, user-aumentado].
        system_prompt = self._system_prompt(lang)
        user_content = (
            self.build_prompt(context, query) if not context.is_empty else query
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # Adaptador del modelo (perezoso para evitar import circular).
        if adapter is None:
            from ..router import get_adapter
            adapter = await get_adapter()

        from ..models.base import GenerationParams
        try:
            from ..config import config
            params = GenerationParams(
                temperature=config.model.temperature,
                top_p=config.model.top_p,
                max_tokens=config.model.max_tokens,
            )
        except Exception:
            params = GenerationParams()

        parts: list[str] = []
        async for token in adapter.stream(messages, params):
            parts.append(token)
        text = "".join(parts).strip()

        return RAGAnswer(
            text=text,
            sources=context.sources,
            used_rag=not context.is_empty,
            query=query,
        )

    # ── helpers ──────────────────────────────────────────────────────────────────

    def _language_default(self) -> str:
        try:
            from ..config import config
            return config.language
        except Exception:
            return "es-MX"

    @staticmethod
    def _system_prompt(language: str) -> str:
        if _is_spanish(language):
            return (
                "Eres Itzel, una asistente personal local. Respondes en español "
                "mexicano, de forma clara y honesta, citando las fuentes cuando "
                "uses información de los documentos del usuario."
            )
        return (
            "You are Itzel, a local personal assistant. Answer clearly and "
            "honestly, citing sources when you use information from the user's "
            "documents."
        )


# ─── singleton ─────────────────────────────────────────────────────────────────

_pipeline: Optional[RAGPipeline] = None
_pipeline_lock = threading.Lock()


def get_pipeline() -> RAGPipeline:
    """Devuelve el pipeline RAG global (singleton thread-safe)."""
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                _pipeline = RAGPipeline()
    return _pipeline
