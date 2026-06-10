FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml ./
RUN pip install --no-cache-dir uv \
    && uv pip install --system -r pyproject.toml

COPY app/ app/
COPY scripts/ scripts/
RUN pip install --no-cache-dir --no-deps . \
    && mkdir -p var

EXPOSE 8000

CMD ["uvicorn", "app.main:get_application", "--factory", "--host", "0.0.0.0", "--port", "8000"]
