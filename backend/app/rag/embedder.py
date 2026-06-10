"""Lazy module-level singleton around the sentence-transformers embedding model."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_model: SentenceTransformer | None = None


def get_embedder() -> SentenceTransformer:
    """Return the shared SentenceTransformer instance, loading it exactly once.

    The import lives inside the function so the app imports cleanly when the
    `rag` extra is not installed. The first call downloads ~90MB from Hugging
    Face (acceptable here; offline vendoring of the weights happens later).
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts as unit-norm vectors (cosine-ready)."""
    if not texts:
        return []
    vectors = get_embedder().encode(texts, normalize_embeddings=True)
    return [vector.tolist() for vector in vectors]
