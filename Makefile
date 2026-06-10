.PHONY: install test lint dev-api seed demo demo-down

install:
	cd backend && uv venv && uv pip install -e ".[dev]"

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check app tests scripts

dev-api:
	cd backend && uv run uvicorn app.main:get_application --factory --reload --port 8000

seed:
	cd backend && uv run python -m scripts.seed

demo:
	docker compose up --build -d
	@echo "API health: http://localhost:8000/api/health"
	@echo "API docs:   http://localhost:8000/docs"
	@echo "Portal:     http://localhost:3000 (frontend service lands in a later stage)"

demo-down:
	docker compose down
