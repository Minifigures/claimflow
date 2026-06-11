FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY pyproject.toml ./
# CPU-only torch/torchvision first (sentence-transformers and timm depend on them;
# the default PyPI wheels drag in the CUDA stack), then base deps + the rag extra
# (Chroma precedent indexing) + the ml extra (MODEL_BACKEND=real CNN inference).
RUN pip install --no-cache-dir uv \
    && uv pip install --system torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && uv pip install --system -r pyproject.toml --extra rag --extra ml

# Vendored models: the MiniLM embedder snapshot + the trained CNN weights ship in
# the repo, so the image builds and serves with no Hugging Face network access.
COPY weights/ weights/

COPY app/ app/
COPY scripts/ scripts/
COPY seed-assets/ seed-assets/
RUN pip install --no-cache-dir --no-deps . \
    && mkdir -p var \
    && chown -R 1000:1000 /app

EXPOSE 8000

# Standalone runs (e.g. Hugging Face Spaces, which runs containers as uid 1000)
# materialize any LFS-pointer assets, seed the demo data, then serve; compose
# overrides this with its own seed-and-serve chain.
CMD ["sh", "-c", "python -m scripts.bootstrap_assets && python -m scripts.seed && uvicorn app.main:get_application --factory --host 0.0.0.0 --port 8000"]
