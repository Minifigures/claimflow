"""Chroma persistent client and collection accessors.

Both collections are created with cosine HNSW space, and every write/query in this
package passes vectors explicitly (see embedder.embed_texts). We NEVER rely on
Chroma's default embedding function: it silently downloads its own ONNX MiniLM
model on first use, which would bypass our pinned sentence-transformers model and
break offline operation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import Settings

if TYPE_CHECKING:
    from chromadb.api import ClientAPI
    from chromadb.api.models.Collection import Collection

CASE_DOCUMENTS = "case_documents"
ADJUDICATED_CASES = "adjudicated_cases"

_COSINE = {"hnsw:space": "cosine"}


def get_client(settings: Settings) -> ClientAPI:
    """Return a persistent Chroma client rooted at settings.chroma_dir."""
    import chromadb

    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.chroma_dir))


def get_case_documents_collection(client: ClientAPI) -> Collection:
    """Per-claimant document chunks (metadata carries claimant_id for isolation)."""
    return client.get_or_create_collection(name=CASE_DOCUMENTS, metadata=dict(_COSINE))


def get_adjudicated_cases_collection(client: ClientAPI) -> Collection:
    """Anonymized summaries of closed cases, one document per case_ref."""
    return client.get_or_create_collection(name=ADJUDICATED_CASES, metadata=dict(_COSINE))
