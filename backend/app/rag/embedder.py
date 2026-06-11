"""Lazy module-level singleton around the sentence-transformers embedding model."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
# Vendored snapshot (backend/weights/all-MiniLM-L6-v2) so the demo runs fully
# offline; the hub name is only a fallback for environments without the checkout.
VENDORED_DIR = Path(__file__).resolve().parents[2] / "weights" / "all-MiniLM-L6-v2"

_model: SentenceTransformer | None = None


def get_embedder() -> SentenceTransformer:
    """Return the shared SentenceTransformer instance, loading it exactly once.

    The import lives inside the function so the app imports cleanly when the
    `rag` extra is not installed.
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        source = str(VENDORED_DIR) if VENDORED_DIR.is_dir() else MODEL_NAME
        _model = SentenceTransformer(source)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts as unit-norm vectors (cosine-ready)."""
    if not texts:
        return []
    vectors = get_embedder().encode(texts, normalize_embeddings=True)
    return [vector.tolist() for vector in vectors]
