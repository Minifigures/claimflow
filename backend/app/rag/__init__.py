"""RAG layer: embeddings, Chroma store, anonymization, indexing, retrieval.

Heavy deps (chromadb, sentence-transformers) are imported lazily inside functions
so the app package imports cleanly without the `rag` extra installed.
"""

from app.rag.anonymizer import (
    ALLOWED_METADATA,
    anonymized_metadata,
    build_case_summary,
    make_case_ref,
)
from app.rag.embedder import embed_texts, get_embedder
from app.rag.indexer import index_case_document, index_closed_case
from app.rag.retriever import find_similar_cases, get_case_documents
from app.rag.store import (
    get_adjudicated_cases_collection,
    get_case_documents_collection,
    get_client,
)

__all__ = [
    "ALLOWED_METADATA",
    "anonymized_metadata",
    "build_case_summary",
    "embed_texts",
    "find_similar_cases",
    "get_adjudicated_cases_collection",
    "get_case_documents",
    "get_case_documents_collection",
    "get_client",
    "get_embedder",
    "index_case_document",
    "index_closed_case",
    "make_case_ref",
]
