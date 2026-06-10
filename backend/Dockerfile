FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml ./
# CPU-only torch first (the rag extra's sentence-transformers depends on torch; the
# default PyPI wheel drags in the CUDA stack), then base deps + the rag extra so the
# demo seeder can index precedents into Chroma.
RUN pip install --no-cache-dir uv \
    && uv pip install --system torch --index-url https://download.pytorch.org/whl/cpu \
    && uv pip install --system -r pyproject.toml --extra rag

# Pre-bake the embedding model so seeding works offline on first boot.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY app/ app/
COPY scripts/ scripts/
COPY seed-assets/ seed-assets/
RUN pip install --no-cache-dir --no-deps . \
    && mkdir -p var

EXPOSE 8000

CMD ["uvicorn", "app.main:get_application", "--factory", "--host", "0.0.0.0", "--port", "8000"]
