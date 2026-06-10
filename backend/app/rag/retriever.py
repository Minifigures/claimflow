"""Read paths over the two Chroma collections. Every retrieval is audited.

Audit payloads carry a SHA-256 of the query, never the query text itself (the
query can quote clinical document content).
"""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.claimguard import audit
from app.config import Settings
from app.models import AuditEventType
from app.rag.embedder import embed_texts
from app.rag.store import (
    ADJUDICATED_CASES,
    CASE_DOCUMENTS,
    get_adjudicated_cases_collection,
    get_case_documents_collection,
    get_client,
)


def _query_sha256(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _similarity(distance: float) -> float:
    """Cosine distance -> similarity in [0, 1], rounded to 4dp."""
    return round(max(0.0, 1.0 - distance), 4)


def get_case_documents(
    settings: Settings,
    session: Session,
    *,
    claimant_id: int,
    query: str,
    top_k: int = 6,
) -> list[dict]:
    """Semantic search over one claimant's document chunks.

    The where={"claimant_id": ...} filter is applied INSIDE this function,
    claimant isolation is not caller-optional, so no call site can ever retrieve
    another claimant's documents.

    Appends a RAG_RETRIEVAL audit event inside the caller's transaction; the
    caller commits.
    """
    collection = get_case_documents_collection(get_client(settings))
    result = collection.query(
        query_embeddings=embed_texts([query]),
        n_results=top_k,
        where={"claimant_id": claimant_id},
        include=["documents", "metadatas", "distances"],
    )
    rows: list[dict] = []
    documents = result["documents"][0] if result["documents"] else []
    metadatas = result["metadatas"][0] if result["metadatas"] else []
    distances = result["distances"][0] if result["distances"] else []
    for text, meta, distance in zip(documents, metadatas, distances, strict=True):
        rows.append(
            {
                "text": text,
                "filename": meta["filename"],
                "doc_type": meta["doc_type"],
                "claim_id": meta["claim_id"],
                "similarity": _similarity(distance),
            }
        )
    audit.append(
        session,
        AuditEventType.RAG_RETRIEVAL,
        payload={
            "collection": CASE_DOCUMENTS,
            "query_sha256": _query_sha256(query),
            "top_k": top_k,
            "returned": len(rows),
            "claim_ids": sorted({row["claim_id"] for row in rows}),
        },
    )
    return rows


def find_similar_cases(
    settings: Settings,
    session: Session,
    *,
    query: str,
    modality: str | None,
    top_k: int = 5,
    floor: float = 0.35,
    exclude_case_ref: str | None = None,
) -> list[dict]:
    """Find anonymized adjudicated cases similar to the query.

    Metadata prefilter on modality when given; results below the similarity floor
    are dropped, as is exclude_case_ref (the case being adjudicated). An empty
    list is a VALID result (the dossier shows "no sufficiently similar
    precedent", honesty over noise).

    Appends a RAG_RETRIEVAL audit event inside the caller's transaction; the
    caller commits.
    """
    collection = get_adjudicated_cases_collection(get_client(settings))
    # Ask for one extra so excluding the current case still leaves up to top_k.
    n_results = top_k + 1 if exclude_case_ref else top_k
    result = collection.query(
        query_embeddings=embed_texts([query]),
        n_results=n_results,
        where={"modality": modality} if modality is not None else None,
        include=["documents", "metadatas", "distances"],
    )
    ids = result["ids"][0] if result["ids"] else []
    documents = result["documents"][0] if result["documents"] else []
    metadatas = result["metadatas"][0] if result["metadatas"] else []
    distances = result["distances"][0] if result["distances"] else []

    rows: list[dict] = []
    audited: list[dict] = []
    for case_ref, summary, meta, distance in zip(ids, documents, metadatas, distances, strict=True):
        audited.append({"case_ref": case_ref, "distance": round(distance, 4)})
        if case_ref == exclude_case_ref:
            continue
        similarity = _similarity(distance)
        if similarity < floor:
            continue
        rows.append(
            {
                "case_ref": case_ref,
                "similarity": similarity,
                "outcome": meta["decision"],
                "summary": summary,
            }
        )
    rows = rows[:top_k]
    audit.append(
        session,
        AuditEventType.RAG_RETRIEVAL,
        payload={
            "collection": ADJUDICATED_CASES,
            "query_sha256": _query_sha256(query),
            "top_k": top_k,
            "floor": floor,
            "returned": len(rows),
            "candidates": audited,
        },
    )
    return rows
