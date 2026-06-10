"""Write paths into the two Chroma collections (embeddings always passed explicitly)."""

from __future__ import annotations

import hashlib

from app.config import Settings
from app.rag.anonymizer import anonymized_metadata, build_case_summary, make_case_ref
from app.rag.embedder import embed_texts
from app.rag.store import (
    get_adjudicated_cases_collection,
    get_case_documents_collection,
    get_client,
)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if not text:
        return []
    step = size - overlap
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        if start + size >= len(text):
            break
        start += step
    return chunks


def index_case_document(
    settings: Settings,
    *,
    claimant_id: int,
    claim_id: int,
    doc_type: str,
    filename: str,
    text: str,
) -> int:
    """Chunk + embed one claim document into case_documents. Returns chunk count.

    Upsert keyed on (claim_id, filename hash, chunk index) so re-indexing the same
    file is idempotent.
    """
    chunks = _chunk_text(text)
    if not chunks:
        return 0
    filename_hash = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:8]
    ids = [f"doc-{claim_id}-{filename_hash}-{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "claimant_id": claimant_id,
            "claim_id": claim_id,
            "doc_type": doc_type,
            "filename": filename,
        }
        for _ in chunks
    ]
    collection = get_case_documents_collection(get_client(settings))
    collection.upsert(
        ids=ids,
        documents=chunks,
        embeddings=embed_texts(chunks),
        metadatas=metadatas,
    )
    return len(chunks)


def index_closed_case(
    settings: Settings,
    *,
    claim_ref: str,
    modality: str | None,
    claim_type: str,
    procedure_code: str,
    diagnosis_code: str,
    recommendation: str | None,
    key_findings: list[str],
    decision: str,
) -> str:
    """Index one anonymized closed case into adjudicated_cases. Returns the case_ref."""
    case_ref = make_case_ref(claim_ref)
    summary = build_case_summary(
        modality=modality,
        claim_type=claim_type,
        procedure_code=procedure_code,
        diagnosis_code=diagnosis_code,
        recommendation=recommendation,
        key_findings=key_findings,
        decision=decision,
    )
    metadata = anonymized_metadata(
        case_ref=case_ref,
        procedure_code=procedure_code,
        diagnosis_code=diagnosis_code,
        modality=modality,
        claim_type=claim_type,
        recommendation=recommendation,
        decision=decision,
    )
    collection = get_adjudicated_cases_collection(get_client(settings))
    collection.upsert(
        ids=[case_ref],
        documents=[summary],
        embeddings=embed_texts([summary]),
        metadatas=[metadata],
    )
    return case_ref
