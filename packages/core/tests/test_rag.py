"""Tests de la memoria semántica (itzel_core.rag).

Cubre los 3 ejes del deliverable de la sesión:

  1. INDEXADO   — index_all() trocea y guarda los documentos de index_dirs.
  2. RETRIEVAL  — búsqueda semántica con queries conocidas (sinónimos) + filtros.
  3. AISLAMIENTO — NUNCA se indexa nada fuera de index_dirs (principio #5).

Y además: incremental (skip sin cambios), exclude_globs (node_modules), poda
de borrados, filtro por fecha (epoch) y telemetría apagada (principio #2).

Todo es local y aislado: índice en tmp_path, embeddings con el modelo ONNX
incluido en ChromaDB (all-MiniLM-L6-v2), sin Ollama ni red obligatoria. No se
tocan ni la BD del usuario ni los singletons globales (get_store/get_indexer).

Si faltan las dependencias opcionales [rag], el módulo entero se omite.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

# Gate: sin chromadb no hay nada que probar.
pytest.importorskip("chromadb")

from itzel_core.config import config
from itzel_core.rag.embeddings import OnnxEmbeddingProvider
from itzel_core.rag.indexer import Indexer
from itzel_core.rag.retriever import Retriever
from itzel_core.rag.store import VectorStore

# ─── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def provider() -> OnnxEmbeddingProvider:
    """Proveedor ONNX (all-MiniLM, 384d) — determinista y offline.

    Hace un embed de prueba para warm-up; si el modelo no se puede cargar
    (p.ej. CI totalmente sin red y sin caché), se omite el módulo.
    """
    p = OnnxEmbeddingProvider()
    try:
        p.embed_documents(["probe"])
    except Exception as exc:  # pragma: no cover - depende del entorno
        pytest.skip(f"Embeddings ONNX no disponibles: {exc}")
    return p


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    """Carpeta de documentos conocida que SÍ se indexa."""
    d = tmp_path / "docs"
    d.mkdir()
    (d / "agenda.md").write_text(
        "# Agenda de la semana\n\n"
        "Reunión del lunes a las 10am con el equipo de diseño para revisar "
        "el prototipo del ajolote y cerrar el plan del sprint.\n",
        encoding="utf-8",
    )
    (d / "receta.txt").write_text(
        "Receta de pozole rojo: maíz cacahuazintle, chile guajillo, "
        "carne de cerdo, ajo, cebolla y rábanos para acompañar.\n",
        encoding="utf-8",
    )
    (d / "notas.md").write_text(
        "Pendientes personales: comprar café, llamar al banco y "
        "actualizar el currículum antes del viernes.\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def outside_dir(tmp_path: Path) -> Path:
    """Carpeta hermana que NUNCA está en index_dirs (control de aislamiento)."""
    d = tmp_path / "outside"
    d.mkdir()
    (d / "secreto.md").write_text(
        "Contraseña del wifi y notas privadas que jamás deben indexarse.\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def store(tmp_path: Path, provider: OnnxEmbeddingProvider) -> VectorStore:
    """VectorStore aislado en tmp_path/vectors, con provider ONNX explícito."""
    return VectorStore(
        store_dir=str(tmp_path / "vectors"),
        collection="test_docs",
        provider=provider,
    )


@pytest.fixture
def configured(docs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Apunta config.rag.index_dirs SOLO a docs_dir (se restaura al terminar)."""
    monkeypatch.setattr(config.rag, "index_dirs", [str(docs_dir)])
    return docs_dir


@pytest.fixture
def indexer(store: VectorStore, configured: Path) -> Indexer:
    """Indexer aislado: usa el store de tmp y los index_dirs parcheados."""
    return Indexer(store=store)


@pytest.fixture
def indexed(indexer: Indexer) -> Iterator[Indexer]:
    """Indexer con los 3 documentos ya indexados."""
    indexer.index_all()
    yield indexer


# ─── 1. INDEXADO ────────────────────────────────────────────────────────────────

def test_index_all_indexa_los_documentos(indexer: Indexer):
    result = indexer.index_all()
    assert result.indexed == 3            # 3 documentos nuevos
    assert result.errors == 0
    assert indexer.store.count() >= 3     # al menos un fragmento por doc


def test_sources_lista_los_tres_archivos(indexed: Indexer):
    sources = indexed.store.sources()
    names = {Path(s["source"]).name for s in sources}
    assert names == {"agenda.md", "receta.txt", "notas.md"}
    assert all(s["chunks"] >= 1 for s in sources)


def test_metadata_incluye_modified_ts_numerico(indexed: Indexer):
    """El filtro por fecha depende de un epoch numérico en cada fragmento."""
    res = indexed.store._collection.get(include=["metadatas"])
    for meta in res["metadatas"]:
        assert isinstance(meta["modified_ts"], int)
        assert meta["modified_ts"] > 0


# ─── 2. RETRIEVAL ───────────────────────────────────────────────────────────────

def test_busqueda_semantica_por_sinonimo(indexed: Indexer):
    """'junta del lunes' debe recuperar agenda.md aunque dice 'reunión'."""
    retriever = Retriever(store=indexed.store)
    hits = retriever.search("junta del lunes", top_k=3)
    assert hits, "la búsqueda no devolvió resultados"
    assert Path(hits[0].source).name == "agenda.md"
    assert 0.0 <= hits[0].score <= 1.0
    assert hits[0].snippet                     # trae extracto para mostrar


def test_busqueda_devuelve_contenido_relevante(indexed: Indexer):
    retriever = Retriever(store=indexed.store)
    hits = retriever.search("cómo preparar pozole", top_k=3)
    assert hits
    assert Path(hits[0].source).name == "receta.txt"


def test_filtro_por_tipo_de_archivo(indexed: Indexer):
    retriever = Retriever(store=indexed.store)
    hits = retriever.search("pendientes y recetas", top_k=5, file_types=[".txt"])
    assert hits
    assert all(h.file_type == ".txt" for h in hits)


def test_filtro_por_fecha_epoch(indexed: Indexer):
    """Regresión: ChromaDB exige operandos numéricos en $gte/$lte."""
    retriever = Retriever(store=indexed.store)
    # En el futuro lejano → 0 resultados.
    assert retriever.search("reunión", top_k=5, modified_after="2099-01-01") == []
    # Desde el pasado → sí hay resultados.
    assert retriever.search("reunión", top_k=5, modified_after="2000-01-01")


def test_query_vacia_no_falla(indexed: Indexer):
    retriever = Retriever(store=indexed.store)
    assert retriever.search("   ") == []


# ─── 3. AISLAMIENTO (el más importante) ─────────────────────────────────────────

def test_index_file_rechaza_archivo_fuera_de_index_dirs(
    indexer: Indexer, outside_dir: Path,
):
    """Un archivo fuera de index_dirs NO se indexa, ni siquiera si se pide explícito."""
    secret = outside_dir / "secreto.md"
    assert indexer.index_file(secret) is False
    assert indexer.index_file(secret, force=True) is False
    assert indexer.store.count() == 0


def test_index_all_solo_toca_index_dirs(
    indexer: Indexer, outside_dir: Path,
):
    """Tras un escaneo completo, ningún archivo externo aparece en el índice."""
    indexer.index_all()
    indexed_sources = {s["source"] for s in indexer.store.sources()}
    secret = str((outside_dir / "secreto.md").resolve())
    assert secret not in indexed_sources
    # Y ninguna fuente cuelga de la carpeta prohibida.
    assert not any(str(outside_dir) in s for s in indexed_sources)


def test_excluye_node_modules(indexer: Indexer, docs_dir: Path):
    """Aunque .js es indexable, node_modules está en exclude_globs."""
    nm = docs_dir / "node_modules"
    nm.mkdir()
    (nm / "lib.js").write_text("export const x = 42;\n", encoding="utf-8")

    indexer.index_all()
    names = {Path(s["source"]).name for s in indexer.store.sources()}
    assert "lib.js" not in names
    assert "agenda.md" in names   # lo legítimo sí se indexó


# ─── incremental, poda y mantenimiento ──────────────────────────────────────────

def test_incremental_salta_sin_cambios(indexer: Indexer):
    indexer.index_all()
    second = indexer.index_all()
    assert second.indexed == 0
    assert second.skipped == 3


def test_reindexa_archivo_modificado(indexer: Indexer, docs_dir: Path):
    indexer.index_all()
    # Modificar el contenido cambia tamaño/mtime → debe re-indexar solo ese.
    (docs_dir / "notas.md").write_text(
        "Pendientes nuevos: renovar pasaporte y comprar boletos de avión.\n",
        encoding="utf-8",
    )
    result = indexer.index_all()
    assert result.indexed == 1
    assert result.skipped == 2


def test_poda_archivos_borrados(indexer: Indexer, docs_dir: Path):
    indexer.index_all()
    (docs_dir / "receta.txt").unlink()
    result = indexer.index_all()
    assert result.removed >= 1
    names = {Path(s["source"]).name for s in indexer.store.sources()}
    assert "receta.txt" not in names


def test_reset_vacia_el_indice(indexed: Indexer):
    assert indexed.store.count() > 0
    removed = indexed.store.reset()
    assert removed > 0
    assert indexed.store.count() == 0


# ─── privacidad / telemetría (principio #2) ─────────────────────────────────────

def test_telemetria_apagada(store: VectorStore):
    assert store.stats()["telemetry"] == "off"


def test_variables_de_entorno_de_telemetria():
    import os
    assert os.environ.get("ANONYMIZED_TELEMETRY") == "False"
    assert os.environ.get("CHROMA_TELEMETRY_ENABLED") == "False"
